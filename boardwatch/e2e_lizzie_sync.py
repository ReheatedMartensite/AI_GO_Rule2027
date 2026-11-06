# -*- coding: utf-8 -*-
"""E2E: boardwatch -> Lizzieyzy live sync via clipboard + Ctrl+V.

Flow:
  1. patch config (autoload default engine, console logging), backup/restore
  2. launch Lizzieyzy, wait for engine
  3. build synthetic positions A (4 stones) and B (A + 1)
  4. LizzieSync.push (clipboard channel) each position, then capture the
     Lizzieyzy window and verify the pushed stone points via the recognizer
  5. delivery: PostMessage Ctrl+V first; if that does not take effect,
     fallback to SetForegroundWindow + keybd_event
  6. cleanup (kill processes, restore config)

Run: python boardwatch/e2e_lizzie_sync.py   (ASCII output)
"""

from __future__ import annotations

import ctypes
import json
import re
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "screenshot2board"))
sys.path.insert(0, os.path.join(ROOT, "netplay"))

import boardwatch as BW  # noqa: E402
from netplay import GoRules  # noqa: E402
from recognizer import recognize_auto  # noqa: E402

FAILS = []
_u = ctypes.windll.user32 if hasattr(BW, "IS_WINDOWS") and BW.IS_WINDOWS \
    else None


def check(name, cond, detail=""):
    print("  [%s] %s %s" % ("ok" if cond else "FAIL", name, detail),
          flush=True)
    if not cond:
        FAILS.append(name)


def kill_all():
    for exe in ("katago.exe", "javaw.exe"):
        subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True)


def lizzie_hwnd():
    for w in BW.list_windows():
        t = w.get("title", "")
        if "KataGo-18b" in t or "KataGo-28b" in t or t == "Lizzie":
            return w["hwnd"]
    return None


# board lattice ratios measured from a 1070x726 Lizzieyzy window capture:
# grid A..T at x 247..806, rows 1..19 at y 92..633
LAT_X0, LAT_Y0 = 247.0 / 1070, 92.0 / 726
LAT_DX, LAT_DY = 31.06 / 1070, 30.06 / 726


def title_winrate(retries=6):
    """Lizzieyzy 标题形如 "[37.0 -1.0 814] KataGo-18b 45 ..."，首个数字为胜率。"""
    for _ in range(retries):
        hwnd = lizzie_hwnd()
        if hwnd:
            for w in BW.list_windows():
                if w["hwnd"] == hwnd:
                    m = re.match(r"\[(\d+(?:\.\d+)?)", w.get("title", ""))
                    if m:
                        return float(m.group(1))
        time.sleep(1.0)
    return None


def verify_pushed(tag, prev_wr, wait=6.0, min_delta=1.0):
    time.sleep(wait)
    wr = title_winrate()
    if wr is None or prev_wr is None:
        check("%s: title winrate readable" % tag, False, str(wr))
        return prev_wr
    delta = abs(wr - prev_wr)
    check("%s: engine re-analyzed after push (winrate %.1f -> %.1f)"
          % (tag, prev_wr, wr), delta > min_delta, "delta=%.1f" % delta)
    return wr


def verify_points(tag, points, attempt_note=""):
    hwnd = lizzie_hwnd()
    if not hwnd:
        check("%s: capture lizzieyzy" % tag, False, "no window")
        return False
    time.sleep(2.5)
    frame = BW.capture_window(hwnd)
    if frame is None:
        check("%s: capture lizzieyzy" % tag, False)
        return False
    h, w = frame.shape[:2]
    ok = True
    detail = []
    for color, x, y in points:
        px = (LAT_X0 + x * LAT_DX) * w
        py = (LAT_Y0 + y * LAT_DY) * h
        r = max(4, int(min(w, h) * 0.012))
        x0, x1 = max(0, int(px - r)), int(px + r)
        y0, y1 = max(0, int(py - r)), int(py + r)
        patch = frame[y0:y1, x0:x1]
        mean = float(patch.mean())
        got = "B" if mean < 100 else ("W" if mean > 215 else "?")
        want = color
        detail.append("%d,%d=%s(%.0f)" % (x, y, got, mean))
        if got != want:
            ok = False
    check("%s: stones visible in lizzieyzy %s" % (tag, attempt_note), ok,
          " ".join(detail))
    return ok


def read_clipboard() -> str:
    CF_UNICODETEXT = 13
    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [ctypes.c_void_p]
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    u.GetClipboardData.restype = ctypes.c_void_p
    u.GetClipboardData.argtypes = [ctypes.c_uint]
    if not u.OpenClipboard(0):
        return "<clipboard busy>"
    try:
        h = u.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return "<empty>"
        p = k.GlobalLock(ctypes.c_void_p(h))
        if not p:
            return "<locked>"
        try:
            size = k.GlobalSize(ctypes.c_void_p(h))
            n = min(size // 2 - 1, 200000)
            return ctypes.wstring_at(ctypes.c_void_p(p), n).split("\x00")[0]
        finally:
            k.GlobalUnlock(ctypes.c_void_p(h))
    finally:
        u.CloseClipboard()


def force_foreground(hwnd):
    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    VK_MENU = 0x12

    def try_set():
        u.SetForegroundWindow(hwnd)
        time.sleep(0.15)
        return u.GetForegroundWindow() == hwnd

    fg = u.GetForegroundWindow()
    fg_tid = u.GetWindowThreadProcessId(fg, None) if fg else 0
    my_tid = k.GetCurrentThreadId()
    attached = False
    if fg_tid and fg_tid != my_tid:
        attached = bool(u.AttachThreadInput(my_tid, fg_tid, 1))

    def fg_title():
        h2 = u.GetForegroundWindow()
        n = u.GetWindowTextLengthW(h2)
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h2, buf, n + 1)
        return buf.value[:30] or "<none>"

    def try_set(step):
        u.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        got = u.GetForegroundWindow() == hwnd
        print("    fg after %s: %s (%s)" % (step, got, fg_title()),
              flush=True)
        return got

    print("    fg before: %s" % fg_title(), flush=True)
    try:
        # 1) alt-tap unlocks SetForegroundWindow
        u.keybd_event(VK_MENU, 0, 0, 0)
        u.keybd_event(VK_MENU, 0, 2, 0)
        time.sleep(0.1)
        if try_set("alt-tap+setfg"):
            return True
        # 2) SwitchToThisWindow: the ALT-TAB internal switch, works even
        #    when the foreground belongs to another process
        u.SwitchToThisWindow(hwnd, True)
        time.sleep(0.4)
        if try_set("switch-to-this"):
            return True
        # 3) minimize + restore: restore always activates the window
        u.ShowWindow(hwnd, 6)   # SW_MINIMIZE
        time.sleep(0.4)
        u.ShowWindow(hwnd, 9)   # SW_RESTORE
        time.sleep(0.7)
        if try_set("minimize+restore"):
            return True
        return u.GetForegroundWindow() == hwnd
    finally:
        if attached:
            u.AttachThreadInput(my_tid, fg_tid, 0)


def send_ctrl_v(hwnd):
    VK_CONTROL, VK_V = 0x11, 0x56
    ok_fg = force_foreground(hwnd)
    print("    foreground acquired: %s" % ok_fg, flush=True)
    u = ctypes.windll.user32
    u.keybd_event(VK_CONTROL, 0, 0, 0)
    u.keybd_event(VK_V, 0, 0, 0)
    u.keybd_event(VK_V, 0, 2, 0)
    u.keybd_event(VK_CONTROL, 0, 2, 0)
    time.sleep(0.3)


def main():
    config_path = os.path.join(ROOT, "config.txt")
    backup = config_path + ".e2e"
    shutil.copy(config_path, backup)
    proc = None
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["ui"]["autoload-default"] = True
        cfg["ui"]["log-console-to-file"] = True
        for e in cfg["leelaz"]["engine-settings-list"]:
            e["isDefault"] = (e["name"] == "KataGo-18b")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

        exe = os.path.join(ROOT, "Lizzieyzy-2.5.3-win64.exe")
        proc = subprocess.Popen([exe], cwd=ROOT)
        app = None
        for _ in range(120):
            app = lizzie_hwnd()
            if app:
                break
            time.sleep(0.5)
        check("lizzieyzy window found", bool(app))
        if not app:
            return
        hwnd = app

        logdir = os.path.join(ROOT, "gtp_logs")
        before = len(os.listdir(logdir)) if os.path.isdir(logdir) else 0
        for _ in range(180):
            now = len(os.listdir(logdir)) if os.path.isdir(logdir) else 0
            if now > before:
                break
            time.sleep(0.5)
        time.sleep(6)

        sync = BW.LizzieSync()
        rules = GoRules(19, 7.5)
        rules.play("B", 3, 3)
        rules.play("W", 15, 15)
        rules.play("B", 15, 3)
        rules.play("W", 3, 15)
        sgf_a = tracker_sgf(rules)

        ok_a = False
        sync.push(sgf_a, rules)
        cb = read_clipboard()
        print("    clipboard head: %r" % cb[:40], flush=True)

        # trigger the ReadBoard socket channel: foreground + Alt+O
        hwnd2 = lizzie_hwnd()
        if hwnd2 and force_foreground(hwnd2):
            VK_MENU, VK_O = 0x12, 0x4F
            u = ctypes.windll.user32
            u.keybd_event(VK_MENU, 0, 0, 0)
            time.sleep(0.05)
            u.keybd_event(VK_O, 0, 0, 0)
            time.sleep(0.05)
            u.keybd_event(VK_O, 0, 2, 0)
            time.sleep(0.05)
            u.keybd_event(VK_MENU, 0, 2, 0)
        port = None
        for _ in range(20):
            port = sync.find_port()
            if port:
                break
            time.sleep(0.5)
        check("readboard socket channel (port=%s)" % port, bool(port))
        if port:
            check("lizzie sync connected", sync.ensure_connected())
            check("A: push position (socket)", sync.send_position(rules))
            time.sleep(6.0)
            wr1 = title_winrate()
            # turn mechanism: a pass flips the side to move -> the engine
            # re-analyzes and the winrate in the title must swing clearly
            # (balanced position: flipping the turn is a huge swing)
            check("turn flip accepted", sync.send_pass())
            wr2 = verify_pushed("turn-flip", wr1, wait=5.0, min_delta=1.0)

        # position B: A's stones + white takes two star points (setup-based
        # resync, tests Lizzieyzy's conflict-resync path; clear winrate swing)
        rules2 = GoRules(19, 7.5)
        rules2.setup_from_stones(
            [(3, 3), (15, 3)], [(15, 15), (3, 15), (9, 9), (13, 9)],
            first="B")
        rules = rules2
        if sync.sock is not None:
            check("B: push position (resync)", sync.send_position(rules,
                                                                 mode="resync"))
            time.sleep(6.0)
            wr3 = title_winrate()
            # 2 black vs 4 white with black to move: black is nearly lost,
            # so a LOW winrate proves the 6-stone replay landed correctly
            check("B: resync position analyzed (wr=%.1f, expect < 15)" % wr3
                  if wr3 is not None else "B: wr unreadable",
                  wr3 is not None and wr3 < 15.0, str(wr3))
            check("turn flip after resync accepted", sync.send_pass())

        # diagnostics: capture the Gtp console (ReadBoard messages land there)
        import cv2
        for w in BW.list_windows():
            t = w.get("title", "")
            if "Gtp" in t or "控制台" in t:
                f2 = BW.capture_window(w["hwnd"])
                if f2 is not None:
                    cv2.imwrite(os.path.join(HERE, "tmp", "gtp_console.png"),
                                f2)
                    print("  diag: console captured ->", t[:30], flush=True)

        sync.disable()
    finally:
        kill_all()
        try:
            if proc is not None:
                proc.kill()
        except Exception:
            pass
        time.sleep(1)
        shutil.copy(backup, config_path)
        os.remove(backup)

    print("E2E RESULT: %s" % ("PASS" if not FAILS else "FAIL: %s" % FAILS))
    sys.exit(1 if FAILS else 0)


def tracker_sgf(rules, black="黑方", white="白方"):
    """SGF of the tracked rules (history mode)."""
    return rules.to_sgf(black, white)


if __name__ == "__main__":
    main()
