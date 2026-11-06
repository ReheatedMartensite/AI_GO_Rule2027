"""Regression test suite for Screenshot2Board entry tools.

Run directly (no pytest needed):

    python screenshot2board/test_s2b.py

Checks (ASCII output only):
  1. 19x19 ten-theme corpus vs game1.sgf  -> every image must be 100%
  2. 9x9 / 13x13 *_1.png vs their GT      -> aggregate accuracy >= 99%
  3. empty_{9,13,19}_*.png                -> all-zero board, correct size
  4. save/autoGame{1,2}.bmp               -> runs through, prints counts
  5. SGF roundtrip (recognize -> to_sgf -> sgfmill -> board) for Lizzie-look
  6. CLI smoke test via subprocess
  7. open_in_lizzie argument construction (mocked, never launches java)

Exit code 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

import numpy as np
from sgfmill import boards, sgf

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

import recognizer  # noqa: E402
import s2b_core as core  # noqa: E402

TESTIMGS = THIS_DIR.parent / "_research" / "image2sgf" / "testimgs"
SAVE_DIR = THIS_DIR.parent / "save"

THEMES_19 = ["Lizzie-look", "antique", "baduktv", "cartoon", "foxwq",
             "print", "real-stones", "subdued", "woodstone", "yunzi"]
SMALL_THEMES = ["Lizzie-look", "foxwq", "print"]
EMPTY_THEMES = ["Lizzie-look", "foxwq", "print", "real-stones"]

failures: list[str] = []


def fail(msg: str):
    failures.append(msg)
    print("  [FAIL] %s" % msg)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def gt_board_from_sgf(path: Path) -> np.ndarray:
    """Replay an SGF (setup stones + moves) into an (n, n) int8 array.

    Row 0 of the returned array is the TOP row, matching recognizer output.
    sgfmill counts rows from the bottom, hence the side-1-row flip.
    """
    with open(path, "rb") as fh:
        game = sgf.Sgf_game.from_bytes(fh.read())
    side = game.get_size()
    board = boards.Board(side)
    root = game.get_root()
    if root.has_setup_stones():
        black_pts, white_pts, _empty = root.get_setup_stones()
        for (r, c) in black_pts:
            board.play(r, c, "b")
        for (r, c) in white_pts:
            board.play(r, c, "w")
    for node in game.get_main_sequence()[1:]:
        colour, move = node.get_move()
        if colour is None or move is None:
            continue
        board.play(move[0], move[1], colour)
    arr = np.zeros((side, side), dtype=np.int8)
    for colour, (r, c) in board.list_occupied_points():
        arr[side - 1 - r, c] = 1 if colour == "b" else 2
    return arr


def board_from_abaw_sgf(path: Path) -> np.ndarray:
    """Rebuild a board from an AB/AW setup-position SGF (no moves)."""
    with open(path, "rb") as fh:
        game = sgf.Sgf_game.from_bytes(fh.read())
    side = game.get_size()
    arr = np.zeros((side, side), dtype=np.int8)
    root = game.get_root()
    if root.has_setup_stones():
        black_pts, white_pts, _empty = root.get_setup_stones()
        for (r, c) in black_pts:
            arr[side - 1 - r, c] = 1
        for (r, c) in white_pts:
            arr[side - 1 - r, c] = 2
    return arr


def compare(rec_board: np.ndarray, gt: np.ndarray):
    """Return (mismatch_count, accuracy_percent)."""
    if rec_board.shape != gt.shape:
        return int(gt.size), 0.0
    mism = int((rec_board != gt).sum())
    return mism, 100.0 * (gt.size - mism) / gt.size


def recognize_img(name: str, n=None):
    img = core.load_image(TESTIMGS / name)
    return core.recognize(img, n)


# ---------------------------------------------------------------------------
# test sections
# ---------------------------------------------------------------------------


def test_19_themes():
    print("=== 19x19 themes (GT: game1.sgf, require 100% each) ===")
    gt = gt_board_from_sgf(TESTIMGS / "game1.sgf")
    assert gt.shape == (19, 19)
    for theme in THEMES_19:
        name = "%s.png" % theme
        try:
            rec, err = recognize_img(name)
            if rec is None:
                fail("%s: recognition failed: %s" % (name, err))
                continue
            if rec.n != 19:
                fail("%s: wrong size %d (expected 19)" % (name, rec.n))
                continue
            mism, acc = compare(rec.board, gt)
            b, w = recognizer.board_counts(rec.board)
            print("  %-18s n=%2d acc=%6.2f%% errors=%3d (B=%d W=%d, %s)"
                  % (name, rec.n, acc, mism, b, w, rec.method))
            if mism != 0:
                fail("%s: %d mismatched points (accuracy %.2f%% < 100%%)"
                     % (name, mism, acc))
        except Exception:
            fail("%s: exception\n%s" % (name, traceback.format_exc()))


def test_small_boards():
    for side, gt_name in ((9, "9x9_1.sgf"), (13, "13x13_1.sgf")):
        print("=== %dx%d (*_1.png, GT: %s, require aggregate >= 99%%) ==="
              % (side, side, gt_name))
        gt = gt_board_from_sgf(TESTIMGS / gt_name)
        assert gt.shape == (side, side)
        total_mism = 0
        total_cells = 0
        for theme in SMALL_THEMES:
            name = "%dx%d_%s_1.png" % (side, side, theme)
            try:
                rec, err = recognize_img(name)
                if rec is None:
                    fail("%s: recognition failed: %s" % (name, err))
                    total_mism += gt.size
                    total_cells += gt.size
                    continue
                mism, acc = compare(rec.board, gt)
                total_mism += mism
                total_cells += gt.size
                size_ok = "ok" if rec.n == side else "WRONG(%d)" % rec.n
                print("  %-26s n=%2d[%5s] acc=%6.2f%% errors=%3d (%s)"
                      % (name, rec.n, size_ok, acc, mism, rec.method))
                if rec.n != side:
                    fail("%s: auto size %d != %d" % (name, rec.n, side))
            except Exception:
                fail("%s: exception\n%s" % (name, traceback.format_exc()))
                total_mism += gt.size
                total_cells += gt.size
        agg = 100.0 * (total_cells - total_mism) / max(1, total_cells)
        print("  aggregate accuracy: %.2f%% (%d/%d cells wrong)"
              % (agg, total_mism, total_cells))
        if agg < 99.0:
            fail("%dx%d aggregate accuracy %.2f%% < 99%%" % (side, side, agg))


def test_empty_boards():
    print("=== empty boards (must be all-zero with correct n) ===")
    for side in (9, 13, 19):
        for theme in EMPTY_THEMES:
            name = "empty_%d_%s.png" % (side, theme)
            try:
                rec, err = recognize_img(name)
                if rec is None:
                    fail("%s: recognition failed: %s" % (name, err))
                    continue
                nz = int((rec.board != 0).sum())
                b, w = recognizer.board_counts(rec.board)
                print("  %-28s n=%2d stones=B%d/W%d (%s)"
                      % (name, rec.n, b, w, rec.method))
                if rec.n != side:
                    fail("%s: wrong size %d (expected %d)" % (name, rec.n, side))
                if nz != 0:
                    fail("%s: %d phantom stones on empty board" % (name, nz))
            except Exception:
                fail("%s: exception\n%s" % (name, traceback.format_exc()))


def test_autogame():
    print("=== real app screenshots (run-through only) ===")
    for i in (1, 2):
        path = SAVE_DIR / ("autoGame%d.bmp" % i)
        try:
            img = core.load_image(path)
            rec, err = core.recognize(img, None)
            if rec is None:
                fail("autoGame%d.bmp: recognition failed: %s" % (i, err))
                continue
            b, w = recognizer.board_counts(rec.board)
            print("  autoGame%d.bmp            n=%2d black=%3d white=%3d (%s)"
                  % (i, rec.n, b, w, rec.method))
        except Exception:
            fail("autoGame%d.bmp: exception\n%s" % (i, traceback.format_exc()))


def test_sgf_roundtrip():
    print("=== SGF roundtrip (Lizzie-look.png -> to_sgf -> sgfmill) ===")
    try:
        rec, err = recognize_img("Lizzie-look.png")
        if rec is None:
            fail("roundtrip: recognition failed: %s" % err)
            return
        sgf_text = recognizer.to_sgf(rec, komi=7.5, next_player="B",
                                     rules="Chinese")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "roundtrip.sgf"
            path.write_bytes(sgf_text.encode("utf-8"))
            rebuilt = board_from_abaw_sgf(path)
        mism, acc = compare(rec.board, rebuilt)
        print("  rebuilt vs recognized: acc=%.2f%% errors=%d" % (acc, mism))
        if mism != 0:
            fail("SGF roundtrip mismatch: %d cells differ" % mism)
        # sanity: header fields survive
        game = sgf.Sgf_game.from_bytes(sgf_text.encode("utf-8"))
        root = game.get_root()
        assert game.get_size() == 19
        assert root.get("KM") == 7.5
        assert root.get("PL") == "b"
        assert root.get("RU") == "Chinese"
    except Exception:
        fail("roundtrip exception\n%s" % traceback.format_exc())


def test_cli_smoke():
    print("=== CLI smoke (subprocess) ===")
    try:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "smoke.sgf"
            res = subprocess.run(
                [sys.executable, str(THIS_DIR / "s2b.py"),
                 str(TESTIMGS / "Lizzie-look.png"), "-o", str(out)],
                capture_output=True, text=True, timeout=180,
                cwd=str(THIS_DIR.parent))
            sys.stdout.write("".join(
                "  | %s\n" % ln for ln in
                (res.stdout or "").splitlines()[:6]))
            if res.returncode != 0:
                fail("CLI exit code %d\nstderr: %s" % (res.returncode,
                                                       res.stderr or ""))
                return
            if not out.is_file():
                fail("CLI produced no output file at %s" % out)
                return
            game = sgf.Sgf_game.from_bytes(out.read_bytes())
            b, w, _e = game.get_root().get_setup_stones()
            print("  CLI sgf parses: size=%d AB=%d AW=%d"
                  % (game.get_size(), len(b), len(w)))
    except Exception:
        fail("CLI smoke exception\n%s" % traceback.format_exc())


def test_open_in_lizzie_mocked():
    print("=== open_in_lizzie / lizzie_running (mocked, no java launch) ===")
    # 1) detection returns a bool and does not raise
    try:
        val = core.lizzie_running()
        print("  lizzie_running() -> %s" % val)
    except Exception:
        fail("lizzie_running() raised\n%s" % traceback.format_exc())

    # 2) already running -> returns False and never spawns
    orig_running = core.lizzie_running
    orig_popen = core.subprocess.Popen
    try:
        core.lizzie_running = lambda: True
        assert core.open_in_lizzie("x:\\nowhere\\a.sgf") is False
        print("  already-running path: returns False (no spawn)")
    finally:
        core.lizzie_running = orig_running

    # 3) not running -> constructs the right argv/cwd/flags, Popen mocked
    calls = []

    def fake_popen(args, **kw):
        calls.append((args, kw))
        return "fake-proc"

    try:
        core.lizzie_running = lambda: False
        core.subprocess.Popen = fake_popen
        sgf_path = THIS_DIR / "out" / "mock_test.sgf"
        result = core.open_in_lizzie(sgf_path)
        assert result is True
        assert len(calls) == 1
        args, kw = calls[0]
        exp_javaw = str(core.BUNDLED_JAVAW)
        assert args == [exp_javaw, "-jar", core.JAR_NAME,
                        str(sgf_path.resolve())], args
        assert kw.get("cwd") == str(core.ROOT_DIR), kw.get("cwd")
        assert kw.get("creationflags") == core.DETACHED_PROCESS
        print("  launch args ok: %s (cwd=%s, DETACHED_PROCESS)"
              % (" ".join(args[:2]) + " ... " + args[-1], kw.get("cwd")))
    finally:
        core.lizzie_running = orig_running
        core.subprocess.Popen = orig_popen


def test_save_defaults():
    print("=== save_sgf / default_out_path ===")
    try:
        with tempfile.TemporaryDirectory() as td:
            nested = Path(td) / "a" / "b" / "x.sgf"
            rec, err = recognize_img("Lizzie-look.png")
            if rec is None:
                fail("save defaults: recognition failed")
                return
            core.save_sgf(rec, nested, komi=6.5, next_player="W", rules="AGA")
            data = nested.read_bytes()
            assert data.startswith(b"(;GM[1]FF[4]CA[UTF-8]"), data[:40]
            assert not data.startswith(b"\xef\xbb\xbf"), "BOM present!"
            assert b"\xef\xbb\xbf" not in data, "BOM inside file!"
            p = core.default_out_path("demo_board")
            assert p.parent == core.OUT_DIR
            assert p.name.startswith("demo_")
            assert p.suffix == ".sgf"
            import re as _re
            assert _re.match(r"demo_board_\d{8}_\d{6}\.sgf$", p.name), p.name
            print("  utf-8 no BOM ok; nested dirs ok; default path pattern ok"
                  " (<stem>_<YYYYmmdd_HHMMSS>.sgf)")
    except Exception:
        fail("save defaults exception\n%s" % traceback.format_exc())


def main() -> int:
    try:  # never die on a non-ASCII character in a console code page
        sys.stdout.reconfigure(errors="backslashreplace")
        sys.stderr.reconfigure(errors="backslashreplace")
    except Exception:
        pass
    print("Screenshot2Board regression suite")
    print("python %s" % sys.version.split()[0])
    print("corpus: %s" % TESTIMGS)
    print()
    test_19_themes()
    print()
    test_small_boards()
    print()
    test_empty_boards()
    print()
    test_autogame()
    print()
    test_sgf_roundtrip()
    print()
    test_cli_smoke()
    print()
    test_open_in_lizzie_mocked()
    test_save_defaults()
    print()
    print("=" * 62)
    if failures:
        print("RESULT: FAIL (%d problem(s))" % len(failures))
        for f in failures:
            print("  - %s" % f.splitlines()[0])
        return 1
    print("RESULT: PASS (all checks passed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
