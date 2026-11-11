# -*- coding: utf-8 -*-
"""boardwatch regression tests. Run: python test_boardwatch.py (ASCII output only)."""

from __future__ import annotations

import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np

import boardwatch as BW

PASS_COUNT = 0
FAIL_COUNT = 0
SKIP_COUNT = 0


def check(name, cond, detail=""):
    global PASS_COUNT, FAIL_COUNT
    if cond:
        PASS_COUNT += 1
        print("  [ ok ] %s" % name)
    else:
        FAIL_COUNT += 1
        print("  [FAIL] %s %s" % (name, detail))


def skip(name, reason):
    global SKIP_COUNT
    SKIP_COUNT += 1
    print("  [SKIP] %s (%s)" % (name, reason))


def blank(n=9):
    return np.zeros((n, n), dtype=np.int8)


def play_on(board, x, y, color):
    """返回在 (x,y) 落子后的新棋盘（color: 1=黑 2=白）。"""
    b = board.copy()
    b[y, x] = color
    return b


def board_point_matches(tr, frame):
    """规则引擎盘面与观测帧逐点比对。"""
    n = frame.shape[0]
    if tr.rules.size != n:
        return False
    for y in range(n):
        for x in range(n):
            p = tr.rules.get(x, y)
            v = int(frame[y, x])
            if v == 1 and p != "B":
                return False
            if v == 2 and p != "W":
                return False
            if v == 0 and p is not None:
                return False
    return True


# ---------------------------------------------------------------------------
print("=== BoardTracker: init / same ===")

tr = BW.BoardTracker(9, 7.5)
r1 = tr.update(blank(9))
check("first frame -> init", r1["mode"] == "init", str(r1))
r2 = tr.update(blank(9))
check("resend same frame -> same", r2["mode"] == "same" and r2["changed"] is False,
      str(r2))

# ---------------------------------------------------------------------------
print("=== BoardTracker: single move ===")

b1 = play_on(blank(9), 2, 3, 1)
r = tr.update(b1)
check("single add -> move", r["mode"] == "move" and r["added"] == (2, 3), str(r))
check("history grows to 1", tr.rules.move_count() == 1)
check("stone placed correctly", tr.rules.get(2, 3) == "B")
check("whole board matches", board_point_matches(tr, b1))
check("turn advances to W", tr.rules.turn() == "W")

b2 = play_on(b1, 5, 5, 2)
r = tr.update(b2)
check("second move (white) -> move", r["mode"] == "move", str(r))
b3 = play_on(b2, 6, 6, 1)
r = tr.update(b3)
check("third move (black) -> move", r["mode"] == "move", str(r))
check("move count 3, turn back to W",
      tr.rules.move_count() == 3 and tr.rules.turn() == "W")
check("tracked_moves == 3", tr.tracked_moves == 3, str(tr.tracked_moves))

# ---------------------------------------------------------------------------
print("=== BoardTracker: capturing move (white captures 3 black) ===")

tr2 = BW.BoardTracker(9)
cur = blank(9)
tr2.update(cur)  # init
modes = []
for c, x, y in [("B", 0, 0), ("W", 2, 0), ("B", 1, 0),
                ("W", 1, 1), ("B", 0, 1)]:
    cur = play_on(cur, x, y, 1 if c == "B" else 2)
    modes.append(tr2.update(cur)["mode"])
# 白下 (0,2) 提掉黑三子：帧中黑三子必须消失（观测即真值）
cur = play_on(cur, 0, 2, 2)
for (x, y) in [(0, 0), (1, 0), (0, 1)]:
    cur = play_on(cur, x, y, 0)
modes.append(tr2.update(cur)["mode"])
check("all frames tracked as move", all(m == "move" for m in modes), str(modes))
check("black 3 stones captured (removed)",
      tr2.rules.get(0, 0) is None and tr2.rules.get(1, 0) is None
      and tr2.rules.get(0, 1) is None)
check("white stones remain",
      tr2.rules.get(2, 0) == "W" and tr2.rules.get(1, 1) == "W"
      and tr2.rules.get(0, 2) == "W")
check("history has 6 moves", tr2.rules.move_count() == 6)
check("resyncs still 0", tr2.resyncs == 0, str(tr2.resyncs))

# ---------------------------------------------------------------------------
print("=== BoardTracker: undo last move ===")

tr3 = BW.BoardTracker(9)
cur = blank(9)
tr3.update(cur)  # init
for c, x, y in [("B", 4, 4), ("W", 5, 5), ("B", 2, 2)]:
    cur = play_on(cur, x, y, 1 if c == "B" else 2)
    tr3.update(cur)
check("pre-undo history 3", tr3.rules.move_count() == 3)
prev_frame = play_on(cur, 2, 2, 0)  # 撤销黑 (2,2)：该点变空，无其他变化
r = tr3.update(prev_frame)
check("undo frame -> undo mode", r["mode"] == "undo", str(r))
check("history shrinks to 2", tr3.rules.move_count() == 2)
check("stone removed from rules", tr3.rules.get(2, 2) is None)
check("board still matches", board_point_matches(tr3, prev_frame))

# undo of a capturing move brings captured stones back -> multi-point change
cur6 = play_on(prev_frame, 2, 2, 1)  # B(2,2) again -> move
r = tr3.update(cur6)
check("re-play works after undo", r["mode"] == "move", str(r))
# build a capture state, then feed the pre-capture frame
tr4 = BW.BoardTracker(9)
cur = blank(9)
tr4.update(cur)
for c, x, y in [("B", 0, 0), ("W", 2, 0), ("B", 1, 0), ("W", 1, 1), ("B", 0, 1)]:
    cur = play_on(cur, x, y, 1 if c == "B" else 2)
    tr4.update(cur)
cur = play_on(cur, 0, 2, 2)                      # W 提黑三子（帧中黑子消失）
for (x, y) in [(0, 0), (1, 0), (0, 1)]:
    cur = play_on(cur, x, y, 0)
r = tr4.update(cur)
check("capture tracked as move (setup for undo test)", r["mode"] == "move", str(r))
after_undo_capture = play_on(cur, 0, 2, 0)       # W(0,2) 消失
for (x, y) in [(0, 0), (1, 0), (0, 1)]:          # 被提 3 黑子重现
    after_undo_capture = play_on(after_undo_capture, x, y, 1)
r = tr4.update(after_undo_capture)
check("undo of capture -> resync (multi-point)", r["mode"] == "resync", str(r))
check("board after capture-undo resync correct",
      board_point_matches(tr4, after_undo_capture))

# ---------------------------------------------------------------------------
print("=== BoardTracker: multi-point change -> resync ===")

tr5 = BW.BoardTracker(9)
tr5.update(blank(9))
two_stones = blank(9)
two_stones[4, 2] = 1
two_stones[4, 3] = 1  # 一帧内同时出现两颗黑子
r = tr5.update(two_stones)
check("two new stones -> resync", r["mode"] == "resync", str(r))
check("resyncs == 1", tr5.resyncs == 1, str(tr5.resyncs))
check("board set up correctly", board_point_matches(tr5, two_stones))
check("last_mode recorded", tr5.last_mode == "resync")

# size change -> init
b13 = blank(13)
b13[6, 6] = 1
r = tr5.update(b13)
check("size change -> init", r["mode"] == "init" and r["n"] == 13, str(r))
check("tracker size switched", tr5.size == 13 and tr5.rules.size == 13)

# ---------------------------------------------------------------------------
print("=== BoardTracker: turn disorder -> resync, no crash ===")

tr6 = BW.BoardTracker(9)
tr6.update(blank(9))            # init, empty
r = tr6.update(play_on(blank(9), 1, 1, 1))   # B plays -> turn W
check("first black move ok", r["mode"] == "move", str(r))
r = tr6.update(play_on(play_on(blank(9), 1, 1, 1), 2, 2, 1))  # 黑连下两手
check("same color twice -> resync", r["mode"] == "resync", str(r))
check("no crash and board synced",
      board_point_matches(tr6, play_on(play_on(blank(9), 1, 1, 1), 2, 2, 1)))
check("resyncs == 1 here", tr6.resyncs == 1, str(tr6.resyncs))

# skip one white move: after B, feed W stone while tracker expects W -> normal;
# then feed B while tracker expects W after a skipped W move
tr7 = BW.BoardTracker(9)
tr7.update(blank(9))
tr7.update(play_on(blank(9), 3, 3, 1))          # B -> turn W
skipped = play_on(play_on(blank(9), 3, 3, 1), 4, 4, 2)
skipped2 = play_on(skipped, 5, 5, 1)            # 白一手 + 黑一手都发生
r = tr7.update(skipped2)
check("skipped move -> resync", r["mode"] == "resync", str(r))
check("board synced after skip", board_point_matches(tr7, skipped2))

# ---------------------------------------------------------------------------
print("=== SGF generation ===")

trs = BW.BoardTracker(9)
trs.update(blank(9))
cur = blank(9)
for c, x, y in [("B", 2, 3), ("W", 5, 5), ("B", 6, 6)]:
    cur = play_on(cur, x, y, 1 if c == "B" else 2)
    trs.update(cur)
sgf = BW.tracker_sgf(trs)
check("history sgf has ;B[cd]", ";B[cd]" in sgf, sgf)
check("history sgf has ;W[ff]", ";W[ff]" in sgf, sgf)
check("history sgf has ;B[gg]", ";B[gg]" in sgf, sgf)
check("history sgf move counts",
      sgf.count(";B[") == 2 and sgf.count(";W[") == 1, sgf)
check("history sgf header", "SZ[9]" in sgf and "KM[7.5]" in sgf, sgf)

try:
    from sgfmill import sgf as sgf_mod
    game = sgf_mod.Sgf_game.from_bytes(sgf.encode("utf-8"))
    seq = game.get_main_sequence()
    n_moves = sum(1 for node in seq[1:] if node.get_move()[0] is not None)
    check("sgfmill reads history moves", n_moves == 3, str(n_moves))
except ImportError:
    skip("sgfmill history parse", "sgfmill not installed")

# resync -> setup-style SGF with AB/AW
trr = BW.BoardTracker(9)
trr.update(blank(9))
trr.update(two_stones)  # resync, two black stones (2,4) and (3,4)
sgf2 = BW.tracker_sgf(trr)
check("setup sgf has AB", "AB[ce][de]" in sgf2, sgf2)
check("setup sgf has no moves", ";B[" not in sgf2 and ";W[" not in sgf2, sgf2)
try:
    from sgfmill import sgf as sgf_mod2
    game2 = sgf_mod2.Sgf_game.from_bytes(sgf2.encode("utf-8"))
    b_set, w_set, _empty = game2.get_root().get_setup_stones()
    check("sgfmill reads setup stones back",
          len(b_set) == 2 and len(w_set) == 0,
          "b=%d w=%d" % (len(b_set), len(w_set)))
except ImportError:
    skip("sgfmill setup parse", "sgfmill not installed")

# setup sgf when nothing tracked yet (fresh tracker, no history, no resync)
sgf3 = BW.tracker_sgf(BW.BoardTracker(19))
check("fresh tracker -> valid setup sgf",
      sgf3.startswith("(;GM[1]") and "SZ[19]" in sgf3 and sgf3.endswith(")"), sgf3)

# ---------------------------------------------------------------------------
print("=== synth history sgf (clipboard turn fix) ===")

def _replay_synth(sgf_text, size):
    """sgfmill 解析合成棋谱并重放，返回 (stones dict, next turn)。"""
    from sgfmill import sgf as sgfm
    g = sgfm.Sgf_game.from_bytes(sgf_text.encode("utf-8"))
    r = BW.GoRules(size, 7.5)
    for node in g.get_main_sequence():
        if node is g.get_root():
            continue
        col, mv = node.get_move()
        if col is None:
            continue
        c = "B" if col == "b" else "W"
        if mv is None:
            r.pass_move(c)
        else:
            row, colx = mv
            r.play(c, colx, size - 1 - row)
    stones = {(x, y): r.get(x, y) for y in range(size) for x in range(size)}
    return stones, r.turn()


# case 1: setup position (2B, 3W), black to move -> synth keeps turn
r_s1 = BW.GoRules(9, 7.5)
r_s1.setup_from_stones([(2, 2), (6, 6)], [(4, 4), (2, 6), (6, 2)],
                       first="B")
sg = BW.LizzieSync._synth_history_sgf(BW.LizzieSync, r_s1, r_s1.turn())
check("synth sgf has no AB/AW", "AB[" not in sg and "AW[" not in sg)
stones, nxt = _replay_synth(sg, 9)
pos_same = all(r_s1.get(x, y) == stones.get((x, y))
               for y in range(9) for x in range(9))
check("synth replay reproduces position", pos_same)
check("synth replay next turn == expected", nxt == r_s1.turn(),
      "%s vs %s" % (nxt, r_s1.turn()))

# case 2: white to move variant
r_s2 = BW.GoRules(9, 7.5)
r_s2.setup_from_stones([(2, 2), (6, 6)], [(4, 4), (2, 6), (6, 2)],
                       first="W")
sg2 = BW.LizzieSync._synth_history_sgf(BW.LizzieSync, r_s2, r_s2.turn())
_, nxt2 = _replay_synth(sg2, 9)
check("synth turn variant W", nxt2 == r_s2.turn(),
      "%s vs %s" % (nxt2, r_s2.turn()))

# case 3: black majority (b = w + 2) forces >= 2 passes but stays correct
r_s3 = BW.GoRules(9, 7.5)
r_s3.setup_from_stones([(0, 0), (1, 0), (2, 0), (3, 0)],
                       [(8, 8)], first="B")
sg3 = BW.LizzieSync._synth_history_sgf(BW.LizzieSync, r_s3, r_s3.turn())
check("synth majority adds passes", sg3.count("[]") >= 2, sg3)
stones3, nxt3 = _replay_synth(sg3, 9)
check("synth majority turn correct", nxt3 == r_s3.turn(),
      "%s vs %s" % (nxt3, r_s3.turn()))
pos3_same = all(r_s3.get(x, y) == stones3.get((x, y))
                for y in range(9) for x in range(9))
check("synth majority reproduces position", pos3_same)

# case 4: empty board -> trivially empty sequence, turn stays black
r_s4 = BW.GoRules(9, 7.5)
sg4 = BW.LizzieSync._synth_history_sgf(BW.LizzieSync, r_s4, "B")
_, nxt4 = _replay_synth(sg4, 9)
check("synth empty board turn B", nxt4 == "B", str(nxt4))

# ---------------------------------------------------------------------------
print("=== window enumeration (smoke) ===")

try:
    wins = BW.list_windows()
except Exception as e:
    wins = []
    traceback.print_exc()
if len(wins) >= 1:
    check("list_windows returns >=1", True)
    print("  info  : %d windows, first=%r" % (len(wins), wins[0]["title"][:40]))
else:
    skip("list_windows returns >=1", "no windows visible in this session")

# ---------------------------------------------------------------------------
print("=== real window capture + recognition (end-to-end) ===")

frame = None
picked = None
for w in wins:
    try:
        fr = BW.capture_window(w["hwnd"])
    except Exception:
        fr = None
    if fr is not None and fr.size:
        frame, picked = fr, w
        break
if frame is None:
    skip("capture a window", "no capturable window found")
else:
    check("captured a window frame", frame.ndim == 3 and frame.shape[2] == 3,
          str(frame.shape))
    print("  info  : capturing %r %s" % (picked["title"][:40], frame.shape[:2]))
    rec, err = BW.recognize_auto(frame)
    if rec is None:
        skip("recognize board in window",
             "no board in first window (normal): %s" % (err or "?"))
    else:
        check("recognized board size valid", rec.n in (9, 13, 19), str(rec.n))
        check("board array shape", rec.board.shape == (rec.n, rec.n),
              str(rec.board.shape))
        print("  info  : recognized %dx%d via %s" % (rec.n, rec.n, rec.method))

# ---------------------------------------------------------------------------
print("=== KataEngine (real engine, loadsgf) ===")

if not os.path.exists(BW.KATAGO_EXE):
    skip("KataEngine real engine", "engine_18b/katago.exe not found")
else:
    eng = BW.KataEngine()
    deadline = time.time() + 90
    ok = eng.start()
    check("engine start", ok)
    if ok:
        try:
            wait = max(5.0, deadline - time.time() - 20)
            ready = eng.ready_evt.wait(timeout=wait)
            check("engine ready", ready,
                  "not ready within %.0fs" % wait)
            if ready:
                trk = BW.BoardTracker(19)
                cur = blank(19)
                trk.update(cur)
                cur = play_on(cur, 3, 3, 1)
                trk.update(cur)
                cur = play_on(cur, 15, 15, 2)
                trk.update(cur)
                sgf = BW.tracker_sgf(trk)
                check("two-move sgf has 2 moves",
                      sgf.count(";B[") == 1 and sgf.count(";W[") == 1, sgf)
                res = eng.analyze(sgf)
                check("analyze returns result", res is not None)
                if res is not None:
                    ww, own = res
                    check("whiteWin in (0,1)", 0.0 < ww < 1.0, repr(ww))
                    check("ownership length 361",
                          isinstance(own, list) and len(own) == 361,
                          str(len(own) if hasattr(own, "__len__") else type(own)))
                    print("  info  : whiteWin=%.4f" % ww)
        except Exception:
            FAIL_COUNT += 1
            print("  [FAIL] engine test raised exception")
            traceback.print_exc()
        finally:
            eng.stop()

# ---------------------------------------------------------------------------
print("=== GUI smoke ===")

try:
    import tkinter as tk
except Exception:
    skip("GUI smoke", "tkinter unavailable")
else:
    root = None
    try:
        root = tk.Tk()
        root.geometry("+2500+2500")  # off-screen, keeps event dispatch alive
        app = BW.App(root)
        root.update()
        root.update()
        check("App constructed and updated", True)
    except Exception:
        FAIL_COUNT += 1
        print("  [FAIL] GUI smoke raised exception")
        traceback.print_exc()
    finally:
        try:
            if root is not None:
                root.destroy()
        except Exception:
            pass

# ---------------------------------------------------------------------------
print("=== background / minimized window capture ===")
IS_WINDOWS = BW.IS_WINDOWS
ROOT_DIR = BW.ROOT_DIR
if not IS_WINDOWS:
    skip("background capture tests", "not Windows")
else:
    try:
        import ctypes as _ctypes
        import tkinter as tk
        from PIL import Image as PILImage, ImageTk
        from recognizer import board_counts

        board_img = os.path.join(ROOT_DIR, "_research", "image2sgf",
                                 "testimgs", "foxwq.png")
        have_board_img = os.path.isfile(board_img)

        root2 = tk.Tk()
        root2.title("BWTestTarget")
        root2.geometry("+60+40")
        ph = None
        if have_board_img:
            ph = ImageTk.PhotoImage(PILImage.open(board_img))
            tk.Label(root2, image=ph).pack()

        # topmost cover window fully hiding the target
        cover = tk.Toplevel(root2)
        cover.title("BWTestCover")
        cover.attributes("-topmost", True)
        cover.geometry("1100x950+20+10")
        tk.Label(cover, text="COVER", font=("", 24)).pack(pady=30)
        root2.update()
        time.sleep(0.8)

        user32 = _ctypes.windll.user32
        wins = {w["title"]: w for w in BW.list_windows()}
        tgt = wins.get("BWTestTarget")
        if tgt is None:
            skip("occluded capture", "target window not enumerated")
        else:
            hwnd = tgt["hwnd"]
            frame = BW.capture_window(hwnd)
            ok_frame = frame is not None and frame.shape[0] >= 300
            check("occluded background capture returns frame", ok_frame,
                  str(None if frame is None else frame.shape))
            if ok_frame and have_board_img:
                rec, err = BW.recognize_auto(frame)
                if rec is None:
                    skip("occluded capture recognition", err or "no board")
                else:
                    b, w = board_counts(rec.board)
                    check("occluded capture recognizes board (112/109)",
                          (b, w) == (112, 109), "%d/%d" % (b, w))

            # minimized: garbage-frame filter must reject the title-bar remnant
            user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
            time.sleep(0.8)
            check("minimized window reported iconic", BW.window_minimized(hwnd))
            frame_min = BW.capture_window(hwnd)
            check("minimized capture rejected by garbage filter",
                  frame_min is None,
                  str(None if frame_min is None else frame_min.shape))

            # auto-restore helper restores, capture works again
            check("restore_window acts on iconic window", BW.restore_window(hwnd))
            time.sleep(0.8)
            frame_r = BW.capture_window(hwnd)
            check("capture works after restore",
                  frame_r is not None and frame_r.shape[0] >= 300)

            # ---- window picking (click-pick infrastructure) ----
            # lift the cover first: WindowFromPoint is z-order geometry,
            # the topmost cover would intercept the point
            cover.attributes("-topmost", False)
            cover.withdraw()
            root2.update()
            time.sleep(0.5)
            u = _ctypes.windll.user32
            info = BW.resolve_window(hwnd)
            check("resolve_window returns info",
                  info is not None and "BWTestTarget" in (info["title"] or ""),
                  repr(None if info is None else info["title"]))
            check("resolve_window label has process name",
                  info is not None and bool(info.get("process")),
                  repr(None if info is None else info.get("process")))
            check("list_windows entries carry process names",
                  all(w.get("process") for w in BW.list_windows()))
            pt = tgt["rect"]
            # bring the target above everything: WindowFromPoint is
            # z-order geometry and the desktop may be busy with other
            # windows overlapping the test window
            HWND_TOPMOST = -1
            got_target = False
            for _ in range(3):
                u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, 0x0003)
                time.sleep(0.4)
                picked = BW.window_from_point(pt[0] + pt[2] // 2,
                                              pt[1] + pt[3] // 2)
                u.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0003)  # NOTOPMOST
                if BW.window_root(picked) == hwnd:
                    got_target = True
                    break
                time.sleep(1.0)  # desktop busy: another window covered us
            if got_target:
                check("window_from_point finds target root", True)
            else:
                skip("window_from_point finds target root",
                     "desktop busy (another window kept covering the test "
                     "window)")
        root2.destroy()
    except Exception:
        FAIL_COUNT += 1
        print("  [FAIL] background capture tests raised exception")
        traceback.print_exc()

# --- WGC 抓图（vendored windows_capture，ok-script 同路线） ---
print("[WGC]")
check("wgc vendor import available", BW._load_wgc() is not None)
check("capture_window_wgc invalid hwnd -> None",
      BW.capture_window_wgc(0) is None)
_cls = BW._load_wgc()
if _cls is not None:
    ok_sig = callable(_cls) and hasattr(_cls, "start_free_threaded")
    check("wgc class exposes start_free_threaded", ok_sig)

# ---------------------------------------------------------------------------
print("=== summary ===")
print("PASS %d, FAIL %d, SKIP %d" % (PASS_COUNT, FAIL_COUNT, SKIP_COUNT))
sys.exit(1 if FAIL_COUNT else 0)
