# -*- coding: utf-8 -*-
"""Go board screenshot recognition core.

Pure OpenCV/numpy implementation.  It detects the 19x19 / 13x13 / 9x9 grid
of a *full board* screenshot, then classifies every intersection as
empty / black / white.

The recognition is intentionally conservative: if the automatic grid fit is
not confident, the caller can ask the user to click the four outer grid
corners and use ``recognize_from_corners`` instead.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import cv2
import numpy as np

SGF_LETTERS = "abcdefghijklmnopqrs"


# ---------------------------------------------------------------------------
# Grid detection
# ---------------------------------------------------------------------------


def _line_profiles(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return normalized vertical/horizontal line-evidence profiles.

    Long, thin line structures are isolated with a directional morphological
    opening.  This is important: coordinate labels and wood grain produce
    short blobs, but not long lines, and would otherwise create fake grid
    lines in a simple black-hat projection.
    """
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    black = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, k)
    white = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, k)
    raw = np.maximum(black, white)
    # Relative thresholds keep faint grid lines on light boards while
    # rejecting low-contrast wood grain.  The 99.5th percentile is a robust
    # estimate of the strongest thin-line response.
    tb = max(12.0, 0.30 * float(np.percentile(black, 99.5)))
    tw = max(12.0, 0.30 * float(np.percentile(white, 99.5)))
    mask = (((black > tb) | (white > tw)).astype(np.uint8)) * 255
    open_len = max(9, int(min(gray.shape) * 0.022))
    vm = cv2.morphologyEx(
        mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, open_len))
    )
    hm = cv2.morphologyEx(
        mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (open_len, 1))
    )

    def norm(a: np.ndarray) -> np.ndarray:
        a = a.astype(np.float32)
        m = float(a.max())
        return a / m if m > 0 else a

    v_open = norm(vm.sum(axis=0, dtype=np.float64))
    h_open = norm(hm.sum(axis=1, dtype=np.float64))
    v_raw = norm(raw.sum(axis=0, dtype=np.float64))
    h_raw = norm(raw.sum(axis=1, dtype=np.float64))
    # Mostly long-line evidence, with a small amount of raw evidence so that
    # grid lines heavily covered by stones are not lost completely.
    return (0.9 * v_open + 0.1 * v_raw).astype(np.float32), (
        0.9 * h_open + 0.1 * h_raw
    ).astype(np.float32)


def _smooth_profile(p: np.ndarray, radius: int = 2) -> np.ndarray:
    if radius <= 0:
        return p
    padded = np.pad(p, (radius, radius), mode="edge")
    out = p.copy()
    for i in range(len(p)):
        out[i] = padded[i : i + 2 * radius + 1].max()
    return out


def _profile_at(profile: np.ndarray, positions: np.ndarray) -> np.ndarray:
    pos = np.rint(positions).astype(np.int32)
    pos = np.clip(pos, 0, len(profile) - 1)
    return profile[pos]


@dataclass
class AxisFit:
    score: float
    spacing: float
    offset: float
    n: int
    line_values: np.ndarray = field(default_factory=lambda: np.zeros(0))
    coverage: float = 1.0

    @property
    def positions(self) -> np.ndarray:
        return self.offset + np.arange(self.n) * self.spacing


def _fit_axis(
    profile: np.ndarray,
    n: int,
    length: int,
    min_frac: float = 0.34,
    max_frac: float = 1.03,
) -> Optional[AxisFit]:
    """Fit n equally spaced lines to a one-dimensional evidence profile."""
    if n < 2 or length < 40:
        return None

    p = _smooth_profile(profile, 1)
    base = length / float(n - 1)
    s_min = max(4.0, base * min_frac)
    s_max = base * max_frac
    if s_max <= s_min:
        return None

    # Robust extent of the line evidence.  The fitted lattice is expected to
    # cover this extent: without this term a half-spacing lattice covering
    # only half of a full board can score as well as the correct lattice.
    q = np.maximum(p - 0.03 * float(p.max()), 0.0)
    total = float(q.sum())
    if total > 0:
        cum = np.cumsum(q)
        ext_lo = float(np.searchsorted(cum, 0.02 * total))
        ext_hi = float(np.searchsorted(cum, 0.98 * total))
    else:
        ext_lo, ext_hi = 0.0, float(length - 1)
    ext_span = max(1.0, ext_hi - ext_lo)

    def score_at(s: float, o: float) -> tuple[float, np.ndarray]:
        pos = o + np.arange(n) * s
        if pos[0] < 0 or pos[-1] > length - 1:
            return -1.0, np.zeros(n)
        vals = _profile_at(p, pos)
        if n >= 3:
            mids = _profile_at(p, o + (np.arange(n - 1) + 0.5) * s)
            mid = float(mids.mean())
        else:
            mid = 0.0
        # Reward strong lines, punish missing lines.  The midpoint term is
        # deliberately weak: on a dense board the edges of aligned stones can
        # create half-spacing vertical/horizontal structure, which would
        # otherwise make the correct spacing look like a harmonic.
        s1 = 0.55 * float(vals.mean()) + 0.35 * float(np.percentile(vals, 25)) - 0.10 * mid
        # Coverage / overflow relative to the line-evidence extent.
        overlap = max(0.0, min(pos[-1], ext_hi) - max(pos[0], ext_lo))
        coverage = overlap / ext_span
        overflow = (
            max(0.0, pos[0] - ext_lo) + max(0.0, pos[-1] - ext_hi)
        ) / ext_span
        s1 = s1 * (0.65 + 0.35 * coverage) - 0.15 * overflow
        return s1, vals

    best = (-1.0, 0.0, 0.0)
    steps = np.arange(s_min, s_max + 1e-9, max(0.12, base * 0.0015))
    for s in steps:
        max_o = length - 1 - (n - 1) * s
        if max_o < 0:
            continue
        offs = np.arange(0.0, max_o + 1e-9, 0.5)
        acc = np.zeros_like(offs)
        for k in range(n):
            pos = np.rint(offs + k * s).astype(np.int32)
            np.clip(pos, 0, length - 1, out=pos)
            acc += p[pos]
        # Use the mean evidence; the final score is re-evaluated with the
        # midpoint penalty for the best few candidates.
        idx = int(np.argmax(acc))
        o = float(offs[idx])
        sc, vals = score_at(s, o)
        if sc > best[0]:
            best = (sc, float(s), o)

    if best[0] < 0:
        return None

    # Local refinement around the coarse optimum.
    s0, o0 = best[1], best[2]
    s_lo = max(s_min, s0 - max(0.3, base * 0.004))
    s_hi = min(s_max, s0 + max(0.3, base * 0.004))
    o_pad = max(2.0, base * 0.03)
    best_ref = best
    for s in np.arange(s_lo, s_hi + 1e-9, 0.02):
        max_o = length - 1 - (n - 1) * s
        if max_o < 0:
            continue
        o_lo = max(0.0, o0 - o_pad)
        o_hi = min(max_o, o0 + o_pad)
        for o in np.arange(o_lo, o_hi + 1e-9, 0.25):
            sc, vals = score_at(s, o)
            if sc > best_ref[0]:
                best_ref = (sc, float(s), float(o))

    sc, s, o = best_ref
    pos = o + np.arange(n) * s
    overlap = max(0.0, min(pos[-1], ext_hi) - max(pos[0], ext_lo))
    coverage = overlap / ext_span
    return AxisFit(
        score=sc,
        spacing=s,
        offset=o,
        n=n,
        line_values=_profile_at(p, pos),
        coverage=coverage,
    )


@dataclass
class LatticeFit:
    n: int
    x: AxisFit
    y: AxisFit
    scale: float
    width: int
    height: int

    @property
    def score(self) -> float:
        return 0.5 * (self.x.score + self.y.score)

    def image_points(self, i: int, j: int) -> tuple[float, float]:
        return (
            (self.x.offset + i * self.x.spacing) / self.scale,
            (self.y.offset + j * self.y.spacing) / self.scale,
        )

    def corners(self) -> np.ndarray:
        p0 = self.image_points(0, 0)
        p1 = self.image_points(self.n - 1, 0)
        p2 = self.image_points(self.n - 1, self.n - 1)
        p3 = self.image_points(0, self.n - 1)
        return np.array([p0, p1, p2, p3], dtype=np.float32)

    @property
    def cell(self) -> float:
        return min(self.x.spacing, self.y.spacing) / self.scale


def detect_lattice(image: np.ndarray, n: Optional[int] = None) -> Optional[LatticeFit]:
    """Detect a full-board grid in an axis-aligned screenshot.

    Returns ``None`` when no trustworthy grid was found.  ``n`` may be one of
    9, 13, 19, or ``None`` for automatic board-size detection.
    """
    if image is None or image.size == 0:
        return None
    h, w = image.shape[:2]
    if min(h, w) < 160:
        return None

    scale = min(1.0, 1400.0 / float(max(h, w)))
    if scale < 1.0:
        small = cv2.resize(
            image, (int(round(w * scale)), int(round(h * scale))), interpolation=cv2.INTER_AREA
        )
    else:
        small = image
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    vp, hp = _line_profiles(gray)
    sh, sw = gray.shape
    bbox, _ = _detect_board_bbox(image)
    bw, bh = max(1, bbox[2]), max(1, bbox[3])

    candidates = [n] if n else [19, 13, 9]
    best: Optional[LatticeFit] = None
    for cand in candidates:
        if cand is None or cand < 2:
            continue
        if min(sw, sh) < (cand - 1) * 6:
            continue
        fx = _fit_axis(vp, cand, sw)
        fy = _fit_axis(hp, cand, sh)
        if fx is None or fy is None:
            continue
        if n is None:
            # Automatic board-size selection: the fitted spacing must match
            # the board size inferred from the largest square object, and the
            # lattice must cover most of the visible line evidence.
            if fx.coverage < 0.55 or fy.coverage < 0.55:
                continue
            ex = bw / float(cand)
            ey = bh / float(cand)
            if abs(fx.spacing / scale - ex) / ex > 0.18:
                continue
            if abs(fy.spacing / scale - ey) / ey > 0.18:
                continue
            if (
                fx.line_values.size
                and np.median(fx.line_values) > 0
                and np.percentile(fx.line_values, 25) < 0.15 * np.median(fx.line_values)
            ):
                continue
            if (
                fy.line_values.size
                and np.median(fy.line_values) > 0
                and np.percentile(fy.line_values, 25) < 0.15 * np.median(fy.line_values)
            ):
                continue
        fit = LatticeFit(n=cand, x=fx, y=fy, scale=scale, width=w, height=h)
        if best is None or fit.score > best.score:
            best = fit

    if best is None:
        return None

    # Validation.  A screenshot of a real board should have nearly square
    # cell spacing and occupy a substantial part of the image.
    if best.x.spacing < 5 or best.y.spacing < 5:
        return None
    ratio = best.x.spacing / max(1e-6, best.y.spacing)
    if not (0.70 <= ratio <= 1.43):
        return None
    span_x = (best.n - 1) * best.x.spacing / sw
    span_y = (best.n - 1) * best.y.spacing / sh
    if min(span_x, span_y) < 0.27 or max(span_x, span_y) < 0.42:
        return None
    if best.score < 0.10:
        return None
    # Every line should have at least a little evidence.
    if best.x.line_values.size and float(np.percentile(best.x.line_values, 25)) < 0.015:
        return None
    if best.y.line_values.size and float(np.percentile(best.y.line_values, 25)) < 0.015:
        return None
    return best


# ---------------------------------------------------------------------------
# Stone-based board detection
#
# On a dense board the grid lines are mostly hidden by stones, so line-profile
# fitting can fail.  The stones themselves still form a regular lattice.  The
# functions below detect circular stones with a Hough transform and fit an
# n x n lattice to their centres.  This is the primary auto-detection path;
# line detection remains the fallback for empty/sparse boards.
# ---------------------------------------------------------------------------


def _detect_board_bbox(image: np.ndarray) -> tuple[tuple[int, int, int, int], bool]:
    """Return a bounding box around the largest square-ish object.

    Most screenshots contain the board as a large square (possibly inside a
    phone/desktop app frame).  If no such square is found, the whole image is
    used as the board area.
    """
    h, w = image.shape[:2]
    scale = min(1.0, 1200.0 / max(h, w))
    if scale < 1.0:
        im = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    else:
        im = image
    gray = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 1.2), 40, 120)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    cnts, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    img_area = im.shape[0] * im.shape[1]
    best = None
    for c in cnts:
        a = cv2.contourArea(c)
        if a < 0.08 * img_area:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        ar = bw / max(1.0, bh)
        if not (0.6 < ar < 1.7):
            continue
        score = a * (1.0 - min(0.5, abs(ar - 1.0)))
        if best is None or score > best[0]:
            best = (score, (x, y, bw, bh))
    if best is None:
        return (0, 0, w, h), False
    x, y, bw, bh = best[1]
    return (int(x / scale), int(y / scale), int(bw / scale), int(bh / scale)), True


def _detect_stone_circles(
    image: np.ndarray, cell_hint: float, bbox: tuple[int, int, int, int], param2: int = 30
) -> list[tuple[float, float, float]]:
    """Detect circular stones inside/near *bbox* using Hough circles."""
    x0, y0, bw, bh = bbox
    pad = int(0.08 * max(bw, bh))
    xa = max(0, x0 - pad)
    ya = max(0, y0 - pad)
    xb = min(image.shape[1], x0 + bw + pad)
    yb = min(image.shape[0], y0 + bh + pad)
    crop = image[ya:yb, xa:xb]
    h, w = crop.shape[:2]
    scale = min(1.0, 1100.0 / max(h, w))
    if scale < 1.0:
        im = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    else:
        im = crop
    gray = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    rmin = max(3, int(round(0.24 * cell_hint * scale)))
    rmax = max(rmin + 3, int(round(0.66 * cell_hint * scale)))
    cs = cv2.HoughCircles(
        cv2.GaussianBlur(gray, (5, 5), 1.1),
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=max(6.0, rmin * 1.25),
        param1=90,
        param2=param2,
        minRadius=rmin,
        maxRadius=rmax,
    )
    if cs is None:
        return []
    lab = cv2.cvtColor(im, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.float32)
    H, W = lab.shape
    out = []
    for x, y, r in cs[0]:
        xi, yi = int(round(x)), int(round(y))
        ri = max(2, int(round(r)))
        y0p, y1p = max(0, yi - ri), min(H, yi + ri + 1)
        x0p, x1p = max(0, xi - ri), min(W, xi + ri + 1)
        yy, xx = np.mgrid[y0p:y1p, x0p:x1p]
        d = np.sqrt((xx - x) ** 2 + (yy - y) ** 2)
        patch = lab[y0p:y1p, x0p:x1p]
        core = patch[d <= r * 0.55]
        ring = patch[(d >= r * 1.2) & (d <= r * 1.5)]
        if core.size < 10 or ring.size < 10:
            continue
        # NOTE: no contrast filter here.  There are legitimate themes where a
        # white stone is only slightly brighter than a white board (and the
        # same for black stones on dark boards).  False circles are rejected
        # later by the lattice fit, which requires them to line up on a grid.
        out.append((x / scale + xa, y / scale + ya, r / scale))
    return out


def _phase_match(
    vals: np.ndarray, spacing: float, n: int, tol: float
) -> tuple[float, np.ndarray]:
    """Find the best lattice phase for one axis using a circular histogram."""
    bins = 180
    bin_w = spacing / bins
    res = np.mod(vals, spacing)
    idx = np.floor(res / bin_w).astype(np.int64) % bins
    hist = np.bincount(idx, minlength=bins).astype(np.int64)
    rad = max(1, int(round(tol / bin_w)))
    scores = np.array(
        [
            hist.take(range(i - rad, i + rad + 1), mode="wrap").sum()
            for i in range(bins)
        ]
    )
    b = int(np.argmax(scores))
    phase = b * bin_w + bin_w / 2.0
    k = np.rint((vals - phase) / spacing).astype(np.int64)
    m = (np.abs(vals - (phase + k * spacing)) <= tol) & (k >= -1) & (k <= n)
    return phase, m


def _fit_axis_stones(
    vals: np.ndarray,
    n: int,
    board_lo: float,
    board_len: float,
    s_lo: float,
    s_hi: float,
    profile: Optional[np.ndarray],
    tol_frac: float = 0.12,
) -> Optional[tuple[float, float, float, float, float, float]]:
    """Fit one axis of a stone lattice; returns (score, spacing, offset, ...)."""
    if len(vals) < 4:
        return None
    tol = tol_frac * 0.5 * (s_lo + s_hi)

    def evaluate(s: float):
        phase, m = _phase_match(vals, s, n, tol)
        if m.sum() < 4:
            return None
        # Refine spacing/phase with least squares on the inliers.
        for _ in range(3):
            k = np.rint((vals - phase) / s).astype(np.int64)
            mm = (np.abs(vals - (phase + k * s)) <= tol * 1.3) & (k >= -1) & (k <= n)
            if mm.sum() < 3:
                break
            A = np.stack([np.ones(mm.sum()), k[mm].astype(float)], axis=1)
            sol, *_ = np.linalg.lstsq(A, vals[mm], rcond=None)
            if sol[1] <= 0:
                break
            phase, s = float(sol[0]), float(sol[1])
        k = np.rint((vals - phase) / s).astype(np.int64)
        m = (np.abs(vals - (phase + k * s)) <= tol * 1.3) & (k >= -1) & (k <= n)
        return s, phase, int(m.sum())

    # Coarse search followed by a fine search around the best spacing.
    best = None
    for s in np.linspace(s_lo, s_hi, 90):
        ev = evaluate(float(s))
        if ev is None:
            continue
        ss, phase, nm = ev
        if best is None or nm > best[0]:
            best = (nm, ss, phase)
    if best is None:
        return None
    _, s_best, _ = best
    step = (s_hi - s_lo) / 89.0
    for s in np.linspace(max(s_lo, s_best - step), min(s_hi, s_best + step), 31):
        ev = evaluate(float(s))
        if ev is None:
            continue
        ss, phase, nm = ev
        if nm > best[0]:
            best = (nm, ss, phase)
    _, spacing, phase = best

    # The phase is only defined modulo spacing.  Shift the lattice so that it
    # covers the board rectangle (rather than e.g. two rows above it).
    choices = []
    q0 = int(round((board_lo - phase) / spacing))
    for q in range(q0 - 2, q0 + 4):
        c = phase + q * spacing
        e0, e1 = c, c + (n - 1) * spacing
        outside = max(0.0, board_lo - e0) + max(0.0, e1 - (board_lo + board_len))
        center_off = abs((e0 + e1) / 2 - (board_lo + board_len / 2))
        k = np.rint((vals - c) / spacing).astype(np.int64)
        m = (np.abs(vals - (c + k * spacing)) <= tol * 1.3) & (k >= 0) & (k < n)
        support = float(m.sum()) / max(1, len(vals))
        line = 0.0
        if profile is not None:
            g = np.clip(
                np.rint(c + np.arange(n) * spacing).astype(int), 0, len(profile) - 1
            )
            line = float(profile[g].mean())
        score = (
            0.62 * support
            + 0.30 * line
            - 0.18 * outside / spacing
            - 0.03 * center_off / spacing
        )
        choices.append((score, c, support, line, outside))
    ch = max(choices, key=lambda t: t[0])
    return ch[0], spacing, ch[1], ch[2], ch[3], ch[4]


def _fit_stone_lattice(
    image: np.ndarray,
    bbox: tuple[int, int, int, int],
    n: int,
    profiles: tuple[np.ndarray, np.ndarray],
    param2: int = 30,
) -> Optional[LatticeFit]:
    """Fit an n x n lattice to detected stone centres."""
    x0, y0, bw, bh = bbox
    sx0 = bw / float(n)
    sy0 = bh / float(n)
    cell_hint = 0.5 * (sx0 + sy0)
    circles = _detect_stone_circles(image, cell_hint, bbox, param2=param2)
    if len(circles) < 6:
        return None
    xs = np.array([c[0] for c in circles], dtype=np.float64)
    ys = np.array([c[1] for c in circles], dtype=np.float64)
    rr = np.array([c[2] for c in circles], dtype=np.float64)
    padx = 0.12 * bw
    pady = 0.12 * bh
    keep = (
        (xs > x0 - padx)
        & (xs < x0 + bw + padx)
        & (ys > y0 - pady)
        & (ys < y0 + bh + pady)
    )
    xs, ys, rr = xs[keep], ys[keep], rr[keep]
    if len(xs) < 6:
        return None
    s_mid = 0.5 * (sx0 + sy0)
    rmask = (rr > 0.20 * s_mid) & (rr < 0.70 * s_mid)
    if rmask.sum() >= 6:
        xs, ys, rr = xs[rmask], ys[rmask], rr[rmask]

    vp, hp = profiles
    fx = _fit_axis_stones(xs, n, x0, bw, 0.86 * sx0, 1.16 * sx0, vp)
    fy = _fit_axis_stones(ys, n, y0, bh, 0.86 * sy0, 1.16 * sy0, hp)
    if fx is None or fy is None:
        return None
    scx, sx, ox, supx, linx, outx = fx
    scy, sy, oy, supy, liny, outy = fy
    tol = 0.12 * max(sx, sy)
    kx = np.rint((xs - ox) / sx).astype(np.int64)
    ky = np.rint((ys - oy) / sy).astype(np.int64)
    m = (
        (np.abs(xs - (ox + kx * sx)) <= tol)
        & (np.abs(ys - (oy + ky * sy)) <= tol)
        & (kx >= 0)
        & (kx < n)
        & (ky >= 0)
        & (ky < n)
    )
    support = float(m.sum()) / max(1, len(xs))
    if support < 0.55 or m.sum() < 6:
        return None
    cols = len(np.unique(kx[m]))
    rows = len(np.unique(ky[m]))
    score = 0.45 * scx + 0.45 * scy + 0.10 * support
    # A real board should use most of the detected lattice lines.  This keeps
    # a small cluster of UI circles from being accepted as a board.
    if cols < max(3, n // 3) or rows < max(3, n // 3):
        return None
    ax = AxisFit(score=scx, spacing=float(sx), offset=float(ox), n=n)
    ay = AxisFit(score=scy, spacing=float(sy), offset=float(oy), n=n)
    fit = LatticeFit(n=n, x=ax, y=ay, scale=1.0, width=image.shape[1], height=image.shape[0])
    # An empty board can produce Hough circles from grid-line crossings.  Only
    # trust stone-based detection when there are real stones on the board.
    try:
        ext = FeatureExtractor(image)
        cell = min(float(sx), float(sy))
        occupied = 0
        for j in range(n):
            for i in range(n):
                cls, _, _ = ext.classify(ox + i * float(sx), oy + j * float(sy), cell)
                if cls:
                    occupied += 1
        if occupied < 3:
            return None
    except Exception:
        # Classification is a sanity check only; never let it break detection.
        pass
    fit.stone_support = support  # informative for callers/debugging
    fit.stone_score = score
    fit.stone_count = len(xs)
    fit.stone_cols = cols
    fit.stone_rows = rows
    return fit


def detect_lattice_from_stones(
    image: np.ndarray, n: Optional[int] = None
) -> Optional[LatticeFit]:
    """Detect a board lattice from stones (primary auto path)."""
    if image is None or image.size == 0 or min(image.shape[:2]) < 160:
        return None
    bbox, _ = _detect_board_bbox(image)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    profiles = _line_profiles(gray)
    candidates = [n] if n else [19, 13, 9]
    best = None
    for cand in candidates:
        fit = _fit_stone_lattice(image, bbox, cand, profiles)
        if fit is None:
            continue
        if best is None or fit.stone_score > best.stone_score:
            best = fit
    return best


# ---------------------------------------------------------------------------
# Stone classification
# ---------------------------------------------------------------------------


class FeatureExtractor:
    """Sample local features around board intersections.

    All heavy image conversions are done once, so classifying 361 points is
    fast even on 4K screenshots.
    """

    def __init__(self, image: np.ndarray):
        self.image = image
        self.h, self.w = image.shape[:2]
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        self.L = lab[:, :, 0].astype(np.float32)
        self.S = hsv[:, :, 1].astype(np.float32)

    def _pixels(self, x: float, y: float, r0: float, r1: float):
        x0 = max(0, int(math.floor(x - r1)))
        x1 = min(self.w, int(math.ceil(x + r1)) + 1)
        y0 = max(0, int(math.floor(y - r1)))
        y1 = min(self.h, int(math.ceil(y + r1)) + 1)
        if x1 <= x0 or y1 <= y0:
            return np.zeros(0, np.float32), np.zeros(0, np.float32), None, None
        yy, xx = np.mgrid[y0:y1, x0:x1]
        d2 = (xx - x) ** 2 + (yy - y) ** 2
        mask = (d2 >= r0 * r0) & (d2 <= r1 * r1)
        if not mask.any():
            return np.zeros(0, np.float32), np.zeros(0, np.float32), None, None
        return (
            self.L[y0:y1, x0:x1][mask],
            self.S[y0:y1, x0:x1][mask],
            (y0, y1, x0, x1),
            mask,
        )

    def _disk(self, x: float, y: float, r: float):
        L, S, _, _ = self._pixels(x, y, 0.0, r)
        return L, S

    def _annulus(self, x: float, y: float, r0: float, r1: float):
        L, S, _, _ = self._pixels(x, y, r0, r1)
        return L, S

    def at(self, x: float, y: float, cell: float) -> dict:
        inner_L, inner_S = self._disk(x, y, cell * 0.30)
        ring_L, ring_S = self._annulus(x, y, cell * 0.20, cell * 0.31)
        outer_L, outer_S = self._annulus(x, y, cell * 0.36, cell * 0.47)
        if ring_L.size == 0:
            ring_L, ring_S = inner_L, inner_S
        if outer_L.size == 0:
            outer_L = ring_L
        if ring_L.size == 0:
            return {
                "cL": 0.0,
                "bgL": 0.0,
                "dL": 0.0,
                "cStd": 0.0,
                "cS": 0.0,
                "bgS": 0.0,
                "cov": 0.0,
                "darkfrac": 0.0,
                "edge": 0.0,
            }

        bg_L_parts = []
        bg_S_parts = []
        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            bx = x + dx * cell * 0.40
            by = y + dy * cell * 0.40
            if 0 <= bx < self.w and 0 <= by < self.h:
                bl, bs = self._disk(bx, by, cell * 0.07)
                if bl.size:
                    bg_L_parts.append(bl)
                    bg_S_parts.append(bs)
        if bg_L_parts:
            bg_L = np.concatenate(bg_L_parts)
            bg_S = np.concatenate(bg_S_parts)
        else:
            bg_L = outer_L
            bg_S = outer_S

        cL = float(np.median(ring_L))
        bgL = float(np.median(bg_L))
        cS = float(np.median(ring_S))
        bgS = float(np.median(bg_S))
        cStd = float(np.std(ring_L))
        edge = float(np.median(ring_L) - np.median(outer_L)) if outer_L.size else 0.0

        # Angular coverage of a dark stone border (white stones on white
        # boards frequently have almost no brightness difference from the
        # background; their outline is then the only reliable clue).
        ang = np.linspace(0.0, 2.0 * np.pi, 72, endpoint=False)
        rr = cell * 0.40
        xs = np.rint(x + np.cos(ang) * rr).astype(np.int32)
        ys = np.rint(y + np.sin(ang) * rr).astype(np.int32)
        valid = (xs >= 0) & (xs < self.w) & (ys >= 0) & (ys < self.h)
        if valid.any():
            ov = self.L[ys[valid], xs[valid]]
            dark = min(cL, bgL) - 18.0
            cov = float((ov < dark).mean())
        else:
            cov = 0.0
        dark = min(cL, bgL) - 18.0
        darkfrac = float((outer_L < dark).mean()) if outer_L.size else 0.0

        return {
            "cL": cL,
            "bgL": bgL,
            "dL": cL - bgL,
            "cStd": cStd,
            "cS": cS,
            "bgS": bgS,
            "cov": cov,
            "darkfrac": darkfrac,
            "edge": edge,
        }

    def classify(self, x: float, y: float, cell: float) -> tuple[int, float, dict]:
        f = self.at(x, y, cell)
        cL, dL, edge = f["cL"], f["dL"], f["edge"]
        cStd, cS, bgS = f["cStd"], f["cS"], f["bgS"]
        cov, darkfrac = f["cov"], f["darkfrac"]

        if dL < -22.0 or (cL < 95.0 and dL < -8.0) or (edge < -10.0 and cL < 110.0):
            conf = min(1.0, max(0.35, (-dL - 8.0) / 70.0))
            return 1, conf, f
        if dL > 13.0 and cL > 150.0:
            conf = min(1.0, max(0.45, (dL - 8.0) / 45.0))
            return 2, conf, f
        if edge > 10.0 and cStd < 26.0 and cL > 150.0:
            return 2, 0.72, f
        if cL > 220.0 and cStd < 26.0 and (cov > 0.07 or darkfrac > 0.15):
            return 2, 0.62, f
        # Empty: confidence is high when the centre is close to the local
        # background and no stone edge is present.
        conf = min(1.0, max(0.4, 1.0 - (abs(dL) / 45.0)))
        return 0, conf, f


# ---------------------------------------------------------------------------
# Recognition results
# ---------------------------------------------------------------------------


@dataclass
class Recognition:
    n: int
    board: np.ndarray  # (n, n) int8, 0 empty, 1 black, 2 white, row 0 = top
    corners: Optional[np.ndarray] = None
    method: str = "auto"
    score: float = 0.0
    lattice: Optional[LatticeFit] = None
    confidence: Optional[np.ndarray] = None


def _empty_board(n: int) -> np.ndarray:
    return np.zeros((n, n), dtype=np.int8)


def recognize_lattice(image: np.ndarray, fit: LatticeFit) -> Recognition:
    ext = FeatureExtractor(image)
    n = fit.n
    board = _empty_board(n)
    conf = np.zeros((n, n), dtype=np.float32)
    cell = fit.cell
    for j in range(n):
        for i in range(n):
            x, y = fit.image_points(i, j)
            cls, c, _ = ext.classify(x, y, cell)
            board[j, i] = cls
            conf[j, i] = c
    return Recognition(
        n=n,
        board=board,
        corners=fit.corners(),
        method="auto",
        score=fit.score,
        lattice=fit,
        confidence=conf,
    )


def make_homography(corners: Sequence[Sequence[float]], n: int, side: float = 1000.0):
    src = np.array(
        [[0.0, 0.0], [side, 0.0], [side, side], [0.0, side]], dtype=np.float32
    )
    dst = np.asarray(corners, dtype=np.float32)
    return cv2.getPerspectiveTransform(src, dst)


def grid_points(corners: Sequence[Sequence[float]], n: int) -> np.ndarray:
    """Return an (n,n,2) array of intersection coordinates for a quad."""
    H = make_homography(corners, n)
    side = 1000.0
    xs = np.linspace(0.0, side, n, dtype=np.float32)
    grid = np.stack(np.meshgrid(xs, xs), axis=-1).reshape(-1, 1, 2)
    pts = cv2.perspectiveTransform(grid, H).reshape(n, n, 2)
    return pts


def recognize_from_corners(
    image: np.ndarray,
    corners: Sequence[Sequence[float]],
    n: int,
) -> Recognition:
    """Recognize a board given its four outer grid intersections.

    ``corners`` must be ordered top-left, top-right, bottom-right,
    bottom-left.  This works for rotated and perspective-distorted photos.
    """
    pts = grid_points(corners, n)
    ext = FeatureExtractor(image)
    board = _empty_board(n)
    conf = np.zeros((n, n), dtype=np.float32)

    def dist(a, b):
        return float(np.linalg.norm(np.asarray(a, np.float32) - np.asarray(b, np.float32)))

    for j in range(n):
        for i in range(n):
            x, y = float(pts[j, i, 0]), float(pts[j, i, 1])
            dxs = []
            dys = []
            if i > 0:
                dxs.append(dist(pts[j, i, :], pts[j, i - 1, :]))
            if i < n - 1:
                dxs.append(dist(pts[j, i, :], pts[j, i + 1, :]))
            if j > 0:
                dys.append(dist(pts[j, i, :], pts[j - 1, i, :]))
            if j < n - 1:
                dys.append(dist(pts[j, i, :], pts[j + 1, i, :]))
            cx = float(np.mean(dxs)) if dxs else (float(np.mean(dys)) if dys else 20.0)
            cy = float(np.mean(dys)) if dys else cx
            cell = max(3.0, min(cx, cy))
            cls, c, _ = ext.classify(x, y, cell)
            board[j, i] = cls
            conf[j, i] = c
    return Recognition(
        n=n,
        board=board,
        corners=np.asarray(corners, dtype=np.float32),
        method="corners",
        score=1.0,
        confidence=conf,
    )


def recognize_auto(
    image: np.ndarray, n: Optional[int] = None
) -> tuple[Optional[Recognition], str]:
    """Recognize a board, using stone-lattice detection then line fallback."""
    fit = detect_lattice_from_stones(image, n)
    method = "stones"
    if fit is None:
        fit = detect_lattice(image, n)
        method = "lines"
    if fit is None:
        return None, "未找到完整棋盘网格（可能需要手动点击四个角）"
    res = recognize_lattice(image, fit)
    res.method = method
    if method == "stones":
        res.score = float(getattr(fit, "stone_score", fit.score))
    return res, ""


# ---------------------------------------------------------------------------
# SGF
# ---------------------------------------------------------------------------


def to_sgf(
    rec: Recognition,
    komi: float = 7.5,
    next_player: str = "B",
    rules: str = "Chinese",
) -> str:
    n = rec.n
    black = []
    white = []
    for j in range(n):
        for i in range(n):
            if rec.board[j, i] == 1:
                black.append(SGF_LETTERS[i] + SGF_LETTERS[j])
            elif rec.board[j, i] == 2:
                white.append(SGF_LETTERS[i] + SGF_LETTERS[j])
    parts = [
        "(;GM[1]FF[4]CA[UTF-8]",
        "AP[Screenshot2Board:1.0]",
        "SZ[%d]" % n,
        "KM[%s]" % (("%g" % komi) if math.isfinite(komi) else "7.5"),
        "RU[%s]" % rules,
        "PL[%s]" % ("B" if next_player.upper().startswith("B") else "W"),
    ]
    if black:
        parts.append("AB" + "".join("[%s]" % s for s in black))
    if white:
        parts.append("AW" + "".join("[%s]" % s for s in white))
    parts.append(")")
    return "".join(parts)


def board_counts(board: np.ndarray) -> tuple[int, int]:
    return int((board == 1).sum()), int((board == 2).sum())
