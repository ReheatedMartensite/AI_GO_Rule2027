"""Shared logic for the Screenshot2Board entry tools (CLI + GUI).

Paths are derived from this file's location so the package stays portable:
this module lives in ``<repo>/screenshot2board/`` and the repository root is
its parent directory.  No drive letters are hardcoded.
"""

from __future__ import annotations

import datetime
import re
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = THIS_DIR.parent
OUT_DIR = THIS_DIR / "out"

JAR_NAME = "lizzie-yzy2.5.3-shaded.jar"
BUNDLED_JAVAW = ROOT_DIR / "jre" / "java17" / "bin" / "javaw.exe"

# DETACHED_PROCESS: don't kill the child when our console/CLI exits.
DETACHED_PROCESS = 0x00000008

if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import recognizer  # noqa: E402  (same-directory import)


# ---------------------------------------------------------------------------
# Image loading / recognition
# ---------------------------------------------------------------------------


def load_image(path: str | Path) -> np.ndarray:
    """Load an image as a BGR ndarray.

    ``cv2.imread`` cannot open paths containing non-ASCII characters on
    Windows, so the file is read as bytes and decoded with ``imdecode``.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError("image not found: %s" % p)
    data = np.fromfile(str(p), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("failed to decode image: %s" % p)
    return img


def recognize(image: np.ndarray, n: int | None = None):
    """Safe wrapper around recognizer.recognize_auto.

    Returns ``(recognition_or_None, error_string)``; unexpected exceptions are
    converted to an error string instead of propagating.
    """
    if image is None or image.size == 0:
        return None, "empty image"
    try:
        return recognizer.recognize_auto(image, n)
    except Exception as exc:  # pragma: no cover - defensive
        return None, "recognition error: %s" % exc


def parse_size(text: str) -> int | None:
    """Parse 'auto'/'' -> None, or '9'/'13'/'19' -> int."""
    t = str(text).strip().lower()
    if t in ("", "auto", "0"):
        return None
    val = int(t)
    if val not in (9, 13, 19):
        raise ValueError("board size must be auto, 9, 13 or 19")
    return val


# ---------------------------------------------------------------------------
# SGF output
# ---------------------------------------------------------------------------


def save_sgf(rec, out_path: str | Path, komi: float = 7.5,
             next_player: str = "B", rules: str = "Chinese") -> Path:
    """Serialize *rec* to SGF (UTF-8 without BOM), creating parent dirs."""
    sgf_text = recognizer.to_sgf(rec, komi=komi, next_player=next_player,
                                 rules=rules)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(sgf_text)
    return out


def default_out_path(stem: str) -> Path:
    """`screenshot2board/out/<stem>_<YYYYmmdd_HHMMSS>.sgf`."""
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", str(stem)).strip("_") or "board"
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return OUT_DIR / ("%s_%s.sgf" % (safe, stamp))


# ---------------------------------------------------------------------------
# Lizzieyzy integration
# ---------------------------------------------------------------------------

_JAR_MARKER = "lizzie-yzy"


def _process_command_lines() -> list[str]:
    """Command lines of running java/javaw processes (best effort)."""
    lines: list[str] = []

    def _add(out: bytes):
        try:
            lines.extend(
                ln for ln in out.decode("utf-8", errors="replace").splitlines()
                if ln.strip()
            )
        except Exception:
            pass

    # Prefer wmic; on newer Windows builds it may be removed, then fall back
    # to PowerShell CIM.  Neither of them starts anything.
    try:
        res = subprocess.run(
            ["wmic", "process", "where",
             "name='javaw.exe' or name='java.exe'", "get", "commandline"],
            capture_output=True, timeout=15)
        if res.returncode == 0 and res.stdout:
            _add(res.stdout)
            # wmic prints a header line "CommandLine"; drop it below by marker
            return [ln for ln in lines if "commandline" not in ln.lower()]
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='javaw.exe' "
             "or Name='java.exe'\" | ForEach-Object { $_.CommandLine }"],
            capture_output=True, timeout=20)
        if res.returncode == 0:
            _add(res.stdout)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return lines


def lizzie_running() -> bool:
    """True when a java(w) process whose command line references the
    Lizzieyzy jar is already running."""
    for ln in _process_command_lines():
        low = ln.lower()
        if _JAR_MARKER in low and ".jar" in low:
            return True
    return False


def open_in_lizzie(sgf_path: str | Path) -> bool:
    """Open *sgf_path* in Lizzieyzy.

    Returns True when a new process was launched.  Returns False when
    Lizzieyzy is already running (the caller should tell the user to drag
    the SGF onto the existing window) or when launch fails.
    """
    try:
        if lizzie_running():
            return False
    except Exception:
        # Detection failed - optimistically try to launch anyway.
        pass
    javaw = str(BUNDLED_JAVAW) if BUNDLED_JAVAW.is_file() else "javaw.exe"
    sgf_abs = str(Path(sgf_path).resolve())
    try:
        subprocess.Popen(
            [javaw, "-jar", JAR_NAME, sgf_abs],
            cwd=str(ROOT_DIR),
            creationflags=DETACHED_PROCESS,
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except OSError:
        return False


def open_explorer(path: str | Path) -> None:
    """Open a folder (or the folder containing a file) in Windows Explorer."""
    import os
    p = Path(path)
    target = p if p.is_dir() else p.parent
    try:
        os.startfile(str(target))  # noqa: S606 - Windows only
    except OSError:
        pass
