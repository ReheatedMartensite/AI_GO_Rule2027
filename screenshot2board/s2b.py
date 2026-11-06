"""Screenshot2Board command line interface.

Usage:
    python s2b.py <image> [-o OUT.sgf] [-n auto|19|13|9] [--komi 7.5]
                 [--player B|W] [--open] [--debug DIR]

Console output is ASCII-only so it renders on any Windows code page.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

import s2b_core as core
import recognizer

EXIT_OK = 0
EXIT_RECOGNIZE_FAILED = 2
EXIT_ERROR = 3


def _ascii(text: str) -> str:
    """Make recognizer error messages console-safe (they may be Chinese)."""
    try:
        text.encode("ascii")
        return text
    except UnicodeEncodeError:
        return text.encode("ascii", errors="backslashreplace").decode("ascii")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="s2b.py",
        description="Recognize a Go board screenshot and save it as SGF.")
    p.add_argument("image", help="input image (png/jpg/bmp/...)")
    p.add_argument("-o", "--output", default=None,
                   help="output SGF path (default: screenshot2board/out/)")
    p.add_argument("-n", "--size", default="auto",
                   choices=["auto", "19", "13", "9"],
                   help="board size (default: auto)")
    p.add_argument("--komi", type=float, default=7.5, help="komi (default 7.5)")
    p.add_argument("--player", default="B", choices=["B", "W"],
                   help="player to move next (default B)")
    p.add_argument("--rules", default="Chinese", help="rules (default Chinese)")
    p.add_argument("--open", action="store_true", dest="open_in_lizzie",
                   help="open the SGF in Lizzieyzy after saving")
    p.add_argument("--debug", metavar="DIR", default=None,
                   help="save an annotated debug image into DIR")
    return p


def annotate(rec, image) -> "cv2.Mat":
    """Overlay the detected grid and stones on a copy of *image*."""
    vis = image.copy()
    n = rec.n
    corners = rec.corners
    pts = None
    if corners is not None:
        try:
            pts = recognizer.grid_points(corners.tolist() if hasattr(
                corners, "tolist") else corners, n)
        except Exception:
            pts = None
    if pts is None:
        # Fallback: derive grid from the lattice, else skip drawing.
        h, w = vis.shape[:2]
        step_x = (w - 1) / max(1, n - 1)
        step_y = (h - 1) / max(1, n - 1)
        import numpy as np
        xs = np.arange(n) * step_x
        ys = np.arange(n) * step_y
        gx, gy = np.meshgrid(xs, ys)
        pts = np.stack([gx, gy], axis=-1)

    for k in range(n):  # grid lines
        cv2.line(vis,
                 (int(round(pts[k, 0, 0])), int(round(pts[k, 0, 1]))),
                 (int(round(pts[k, n - 1, 0])), int(round(pts[k, n - 1, 1]))),
                 (0, 0, 255), 2, cv2.LINE_AA)
        cv2.line(vis,
                 (int(round(pts[0, k, 0])), int(round(pts[0, k, 1]))),
                 (int(round(pts[n - 1, k, 0])), int(round(pts[n - 1, k, 1]))),
                 (0, 0, 255), 2, cv2.LINE_AA)
    cell = float("inf")
    for a in range(n - 1):
        for b in range(n - 1):
            d = float(((pts[a + 1, b] - pts[a, b]) ** 2).sum() ** 0.5)
            cell = min(cell, d)
    if cell == float("inf"):
        cell = max(4.0, min(vis.shape[:2]) / (n + 1.0))
    r = max(3, int(round(cell * 0.42)))
    for j in range(n):
        for i in range(n):
            v = rec.board[j, i]
            if not v:
                continue
            x, y = int(round(pts[j, i, 0])), int(round(pts[j, i, 1]))
            color = (0, 255, 0) if v == 1 else (255, 0, 255)
            cv2.circle(vis, (x, y), r, color, 2, cv2.LINE_AA)
    if corners is not None:
        for (cx, cy) in np_corners(corners):
            cv2.circle(vis, (int(round(cx)), int(round(cy))),
                       max(4, r), (0, 255, 255), 3, cv2.LINE_AA)
    return vis


def np_corners(corners):
    import numpy as np
    arr = np.asarray(corners, dtype=float)
    return arr.reshape(-1, 2)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        image = core.load_image(args.image)
    except Exception as exc:
        print("[ERROR] %s" % _ascii(str(exc)))
        return EXIT_ERROR

    n = None if args.size == "auto" else int(args.size)
    rec, err = core.recognize(image, n)
    if rec is None:
        print("[ERROR] recognition failed: %s" % _ascii(err or "unknown"))
        print("HINT: use the GUI (s2b_gui.py) and click the four corner "
              "intersections for photos taken at an angle.")
        return EXIT_RECOGNIZE_FAILED

    black, white = recognizer.board_counts(rec.board)
    stem = Path(args.image).stem
    out_path = Path(args.output) if args.output else core.default_out_path(stem)
    try:
        core.save_sgf(rec, out_path, komi=args.komi,
                      next_player=args.player, rules=args.rules)
    except Exception as exc:
        print("[ERROR] cannot write SGF: %s" % _ascii(str(exc)))
        return EXIT_ERROR

    print("board size : %d" % rec.n)
    print("method     : %s" % rec.method)
    print("stones     : black=%d white=%d" % (black, white))
    print("sgf saved  : %s" % out_path)

    if args.debug:
        dbg_dir = Path(args.debug)
        dbg_dir.mkdir(parents=True, exist_ok=True)
        dbg_path = dbg_dir / ("%s_annotated.png" % stem)
        # cv2.imwrite cannot handle non-ASCII paths on Windows; encode in
        # memory and write the bytes ourselves.
        ok, buf = cv2.imencode(".png", annotate(rec, image))
        if ok:
            with open(dbg_path, "wb") as fh:
                fh.write(buf.tobytes())
        if ok and dbg_path.is_file():
            print("debug image: %s" % dbg_path)
        else:
            print("[ERROR] cannot write debug image: %s" % dbg_path)

    if args.open_in_lizzie:
        if core.open_in_lizzie(out_path):
            print("lizzie     : launched with the SGF")
        else:
            print("lizzie     : already running - drag the SGF onto the "
                  "Lizzieyzy window")
            core.open_explorer(out_path)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
