# -*- coding: utf-8 -*-
"""netplay regression tests. Run: python test_netplay.py (ASCII output only)."""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import netplay as N

PASS_COUNT = 0
FAIL_COUNT = 0


def check(name, cond, detail=""):
    global PASS_COUNT, FAIL_COUNT
    if cond:
        PASS_COUNT += 1
        print("  [ ok ] %s" % name)
    else:
        FAIL_COUNT += 1
        print("  [FAIL] %s %s" % (name, detail))


def replay(rules, moves):
    for c, x, y in moves:
        if x < 0:
            rules.pass_move(c)
        else:
            rules.play(c, x, y)


# ---------------------------------------------------------------------------
print("=== GoRules ===")

# corner capture: W(0,0) surrounded by B(1,0)+B(0,1)
r = N.GoRules(9, 5.5)
replay(r, [("B", 5, 5), ("W", 0, 0), ("B", 1, 0), ("W", 6, 6), ("B", 0, 1)])
check("corner capture", r.get(0, 0) is None and r.captures["B"] == 1
      and r.turn() == "W", "caps=%r" % (r.captures,))

# wrong turn rejected
r = N.GoRules(9)
try:
    r.play("W", 1, 1)
    wrong_turn = False
except N.RuleError:
    wrong_turn = True
check("wrong turn rejected", wrong_turn)

# occupied point rejected
r = N.GoRules(9)
r.play("B", 1, 1)
try:
    r.play("W", 1, 1)
    occupied = False
except N.RuleError:
    occupied = True
check("occupied point rejected", occupied)

# center group capture: W group (1,1),(2,1) captured
r = N.GoRules(9)
replay(r, [("B", 0, 1), ("W", 1, 1), ("B", 3, 1), ("W", 2, 1), ("B", 1, 0),
           ("W", 8, 8), ("B", 1, 2), ("W", 8, 7), ("B", 2, 0), ("W", 8, 6),
           ("B", 2, 2)])
check("group capture removes stones",
      r.get(1, 1) is None and r.get(2, 1) is None and r.captures["B"] == 2)

# recapture after capture is legal
r = N.GoRules(9)
replay(r, [("B", 0, 0), ("W", 1, 0), ("B", 3, 3), ("W", 0, 1), ("B", 8, 8),
           ("W", 0, 0)])
check("recapture after capture is legal", r.get(0, 0) == "W")

# capturing placement is not suicide
r = N.GoRules(5)
replay(r, [("B", 1, 0), ("W", 2, 0), ("B", 0, 1), ("W", 1, 1), ("B", 4, 4)])
try:
    r.play("W", 0, 0)  # captures B(1,0), so not suicide
    not_suicide = True
except N.RuleError:
    not_suicide = False
check("capturing placement not suicide",
      not_suicide and r.get(1, 0) is None and r.get(0, 0) == "W")

# pure suicide rejected: W plays into a live black eye
r2 = N.GoRules(5)
replay(r2, [("B", 1, 0), ("W", 4, 0), ("B", 0, 1), ("W", 4, 1), ("B", 1, 2),
            ("W", 4, 2), ("B", 2, 1), ("W", 4, 3)])
try:
    r2.play("W", 1, 1)  # all 4 neighbours are live B stones -> suicide
    suicide = False
except N.RuleError:
    suicide = True
check("suicide rejected", suicide)

# canonical ko (positional superko), ko point at (2,1):
#   row0:  . B W . .
#   row1:  B W * W .
#   row2:  . B W . .
r = N.GoRules(5)
replay(r, [("B", 1, 0), ("W", 2, 0), ("B", 0, 1), ("W", 1, 1), ("B", 1, 2),
           ("W", 2, 2), ("B", 3, 3), ("W", 3, 1), ("B", 2, 1)])
check("ko take captures one stone", r.get(1, 1) is None and r.captures["B"] == 1)
try:
    r.play("W", 1, 1)  # immediate recapture -> positional superko
    ko_blocked = False
except N.RuleError:
    ko_blocked = True
check("immediate ko recapture rejected", ko_blocked)
# after a ko threat exchange elsewhere, recapture is allowed
r.play("W", 4, 4)
r.play("B", 0, 4)
try:
    r.play("W", 1, 1)
    ko_later = True
except N.RuleError:
    ko_later = False
check("recapture allowed after threat", ko_later and r.get(2, 1) is None)

r = N.GoRules(9)
r.pass_move("B")
check("pass switches turn", r.turn() == "W")
r.pass_move("W")
check("two passes end game", bool(r.ended))

# ---------------------------------------------------------------------------
print("=== 2027 Chinese rules judge ===")

def full_board(size, black_n, white_n):
    """全填盘面：前 black_n 点黑、随后 white_n 点白（判定用，非对局）。"""
    r = N.GoRules(size, 7.5)
    pts = [(x, y) for y in range(size) for x in range(size)]
    r.board = [None] * (size * size)
    for i, (x, y) in enumerate(pts):
        r.board[y * size + x] = "B" if i < black_n else (
            "W" if i < black_n + white_n else None)
    return r

# 19 路：黑 184 / 白 177 = 平局（2027 新规贴 3.5 子）
r = full_board(19, 184, 177)
res = r.judge_chinese()
check("2027: black 184 -> draw", res["result"] == "draw" and res["sgf_re"] == "0",
      "%s %s" % (res["result"], res["sgf_re"]))
check("2027: draw text mentions rule", "和棋" in res["text"], res["text"])

# 19 路：黑 185 -> 黑胜 1 子；黑 183 -> 白胜 1 子
r = full_board(19, 185, 176)
res = r.judge_chinese()
check("2027: black 185 -> black wins 1 zi",
      res["result"] == "B" and res["margin_zi"] == 1.0 and res["sgf_re"] == "B+1.0",
      "%s %s" % (res["result"], res["sgf_re"]))
r = full_board(19, 183, 178)
res = r.judge_chinese()
check("2027: black 183 -> white wins 1 zi",
      res["result"] == "W" and res["margin_zi"] == -1.0 and res["sgf_re"] == "W+1.0",
      "%s %s" % (res["result"], res["sgf_re"]))

# 旧规对照：komi_zi=3.75 时黑 184 -> 白胜 0.25 子
r = full_board(19, 184, 177)
res = r.judge_chinese(komi_zi=3.75)
check("old rules: black 184 -> white +0.25 zi",
      res["result"] == "W" and abs(res["margin_zi"] + 0.25) < 1e-9,
      "%s %.2f" % (res["result"], res["margin_zi"]))

# 死子：嵌在白地域里的 2 颗死黑子 -> 移除后白地域 +2 点（黑 -2 点 = 白 +1 子）
r = N.GoRules(19, 7.5)
r.board = [None] * 361
b_pts = [(x, y) for y in range(19) for x in range(19)]
b_pts = [pt for pt in b_pts if pt not in ((2, 0), (0, 1), (1, 1))]
# 183 颗黑（含 (0,0),(1,0) 两颗待判死子），其余 178 点全白（含围住死子的 3 颗白）
for i, pt in enumerate(b_pts[:183]):
    r.board[pt[1] * 19 + pt[0]] = "B"
for pt in b_pts[183:]:
    r.board[pt[1] * 19 + pt[0]] = "W"
r.board[0 * 19 + 2] = "W"; r.board[1 * 19 + 0] = "W"; r.board[1 * 19 + 1] = "W"
res = r.judge_chinese()
check("2027: baseline 183B/178W -> W+1", res["margin_zi"] == -1.0,
      "%.1f" % res["margin_zi"])
res = r.judge_chinese(dead=[(0, 0), (1, 0)])
check("dead stones in white area give white +1 zi",
      res["result"] == "W" and res["margin_zi"] == -3.0,
      "%s %.2f" % (res["result"], res["margin_zi"]))

# 平局 tiebreak：构造双虚着收束（黑先停手）
r = full_board(19, 184, 177)
r.history.append(("B", -1, -1))
r.history.append(("W", -1, -1))
check("first final passer is B", r.first_final_passer() == "B")
res = r.judge_chinese(tiebreak="first_passer_wins")
check("2027: tiebreak first-passer-wins -> black wins",
      res["result"] == "B" and "先停手" in res["text"], res["text"])
res = r.judge_chinese(tiebreak="white_wins")
check("2027: tiebreak white-wins -> white wins", res["result"] == "W")

# 公气半子：9 路 40B/40W + 1 公气点（四邻 2黑2白）
r = N.GoRules(9, 7.5)
r.board = [None] * 81
others = [(x, y) for y in range(9) for x in range(9)
          if (x, y) not in ((4, 4), (3, 4), (5, 4), (4, 3), (4, 5))]
for i, pt in enumerate(others):
    r.board[pt[1] * 9 + pt[0]] = "B" if i % 2 == 0 else "W"
for x, y in ((3, 4), (4, 3)):
    r.board[y * 9 + x] = "B"
for x, y in ((5, 4), (4, 5)):
    r.board[y * 9 + x] = "W"
nb = sum(1 for v in r.board if v == "B")
nw = sum(1 for v in r.board if v == "W")
res = r.judge_chinese()
check("dame point splits 0.5 zi each",
      res["score_b"] == nb + 0.5 and res["score_w"] == nw + 0.5,
      "score_b=%.1f score_w=%.1f nb=%d nw=%d" % (
          res["score_b"], res["score_w"], nb, nw))

r = N.GoRules(9)
replay(r, [("B", 2, 2), ("W", 3, 3), ("B", 4, 4), ("W", 5, 5)])
r.undo()
check("undo removes last move", r.move_count() == 3 and r.turn() == "W"
      and r.get(5, 5) is None)
r.play("W", 6, 6)
check("play after undo works", r.get(6, 6) == "W")

# ---------------------------------------------------------------------------
print("=== SGF ===")
r = N.GoRules(19, 7.5)
replay(r, [("B", 15, 3), ("W", 3, 3), ("B", -1, -1), ("W", 2, 2)])
sgf = r.to_sgf("Alice", "Bob", "B+R")
check("sgf header", "SZ[19]" in sgf and "KM[7.5]" in sgf and "PB[Alice]" in sgf
      and "RE[B+R]" in sgf)
check("sgf moves", ";B[pd]" in sgf and ";W[dd]" in sgf and ";B[]" in sgf and ";W[cc]" in sgf)

# ---------------------------------------------------------------------------
print("=== protocol loopback (real sockets) ===")


class FakePeer:
    """Acts as the far end (host): applies same rules, auto-answers moves."""

    def __init__(self, conn):
        self.rules = N.GoRules(9, 5.5)
        self.conn = conn
        self.moves_seen = []
        self.chats = []
        threading.Thread(target=self._run, daemon=True).start()

    def send(self, obj):
        self.conn.sendall((json.dumps(obj) + "\n").encode("utf-8"))

    def _run(self):
        buf = b""
        try:
            while True:
                chunk = self.conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    self._handle(json.loads(line.decode("utf-8")))
        except OSError:
            pass

    def _handle(self, obj):
        t = obj.get("t")
        if t == "hello":
            self.send({"t": "start", "color": "W", "size": 9, "komi": 5.5,
                       "name": "Host"})
        elif t == "move":
            x, y, c = obj["x"], obj["y"], obj["color"]
            if x < 0:
                self.rules.pass_move(c)
            else:
                self.rules.play(c, x, y)
            self.moves_seen.append((c, x, y))
            n = self.rules.move_count()
            answers = [(0, 0), (8, 8), (0, 1), (8, 7), (0, 2), (8, 6),
                       (0, 3), (8, 5), (0, 4)]
            if n <= len(answers):
                ax, ay = answers[n - 1]
                self.rules.play(self.rules.turn(), ax, ay)
                self.moves_seen.append((self.rules.history[-1][0], ax, ay))
                self.send({"t": "move", "seq": self.rules.move_count(),
                           "x": ax, "y": ay,
                           "color": self.rules.history[-1][0]})
        elif t == "undo_req":
            self.rules.undo()
            self.send({"t": "undo_ack"})
        elif t == "chat":
            self.chats.append(obj["text"])
            self.send({"t": "chat", "text": "echo:%s" % obj["text"]})
        elif t == "bye":
            self.conn.close()


srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", 0))
srv.listen(1)
port = srv.getsockname()[1]

peer_holder = {}


def accept_job():
    conn, _ = srv.accept()
    peer_holder["peer"] = FakePeer(conn)


threading.Thread(target=accept_job, daemon=True).start()

client_sock = socket.create_connection(("127.0.0.1", port), timeout=5)
got = {"start": None, "moves": [], "chat": None, "undo": False}
done = threading.Event()


def client_on_recv(obj):
    t = obj.get("t")
    if t == "start":
        got["start"] = obj
    elif t == "move":
        got["moves"].append(obj)
    elif t == "chat":
        got["chat"] = obj
    elif t == "undo_ack":
        got["undo"] = True
        done.set()


ep = N.NetEndpoint(client_sock, client_on_recv, lambda: None)
ep.start()
ep.send({"t": "hello", "name": "Guest", "ver": N.PROTOCOL_VERSION, "pass": ""})
deadline = time.time() + 5
while got["start"] is None and time.time() < deadline:
    time.sleep(0.05)
check("hello -> start handshake", got["start"] is not None
      and got["start"]["color"] == "W" and got["start"]["size"] == 9)

guest_moves = [(2, 2), (2, 3), (2, 4), (2, 5)]
seq = 0
for gx, gy in guest_moves:
    seq += 1
    ep.send({"t": "move", "seq": seq, "x": gx, "y": gy, "color": "B"})
    time.sleep(0.15)
peer = peer_holder["peer"]
time.sleep(0.5)
check("host applied guest moves",
      all(m in peer.moves_seen for m in [("B", x, y) for x, y in guest_moves]))
check("host answers arrived", len(got["moves"]) >= 4)
check("host board consistent (8 moves)",
      peer.rules.move_count() == 8 and peer.rules.get(2, 2) == "B")

ep.send({"t": "chat", "text": "hi"})
deadline = time.time() + 3
while got["chat"] is None and time.time() < deadline:
    time.sleep(0.05)
check("chat round trip", got["chat"] is not None and got["chat"]["text"] == "echo:hi")

ep.send({"t": "undo_req"})
deadline = time.time() + 3
while not got["undo"] and time.time() < deadline:
    time.sleep(0.05)
check("undo request/ack", got["undo"] and peer.rules.move_count() == 7)

ep.send({"t": "bye"})
ep.close()
srv.close()

# ---------------------------------------------------------------------------
print("=== GUI smoke ===")
try:
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    app = N.App(root)
    root.update_idletasks()
    app.rules = N.GoRules(9)
    root.destroy()
    check("GUI constructs", True)
except Exception as e:  # headless environment
    print("  [SKIP] GUI smoke (%s)" % e)

# ---------------------------------------------------------------------------
print("=== App-level integration (join vs FakePeer host, real events) ===")


def app_integration():
    # host side
    srv2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv2.bind(("127.0.0.1", 0))
    srv2.listen(1)
    port2 = srv2.getsockname()[1]
    peer2 = {}

    def accept2():
        conn, _ = srv2.accept()
        peer2["peer"] = FakePeer(conn)

    threading.Thread(target=accept2, daemon=True).start()

    # App in join mode (window mapped offscreen so canvas events are delivered)
    root = tk.Tk()
    root.geometry("+2500+2500")
    root.update()
    app = N.App(root)
    app.name_var.set("Guest")
    app.mode_var.set("join")
    app._switch_mode()
    app.host_ip_var.set("127.0.0.1")
    app.join_port_var.set(str(port2))
    app.pass_var.set("")
    app.on_start()
    deadline = time.time() + 6
    while (app.endpoint is None or not getattr(app, "status_var", None)
           or app.my_color is None) and time.time() < deadline:
        root.update()
        time.sleep(0.03)
    ok_start = app.my_color == "W" and app.rules.size == 9
    # at game start it is host(B)'s turn -> our click must be ignored
    x5, y5 = int(app._pos(5)), int(app._pos(5))
    app.board_cv.event_generate("<Button-1>", x=x5, y=y5)
    root.update()
    time.sleep(0.2)
    root.update()
    ok_turn = app.rules.move_count() == 0
    # host (B) plays first: record on FakePeer rules, then send to the App
    peer2["peer"].rules.play("B", 2, 2)
    peer2["peer"].send({"t": "move", "seq": 1, "x": 2, "y": 2, "color": "B"})
    deadline = time.time() + 5
    while app.rules.move_count() < 1 and time.time() < deadline:
        root.update()
        time.sleep(0.03)
    ok_peer_move = app.rules.move_count() == 1 and app.rules.get(2, 2) == "B"
    # now it IS our turn (W); click (3,3)
    x, y = int(app._pos(3)), int(app._pos(3))
    app.board_cv.event_generate("<Button-1>", x=x, y=y)
    deadline = time.time() + 5
    while ("peer" not in peer2 or not peer2["peer"].moves_seen) and time.time() < deadline:
        root.update()
        time.sleep(0.03)
    sent = "peer" in peer2 and len(peer2["peer"].moves_seen) >= 1
    ok_own_move = app.rules.get(3, 3) == "W"
    # host auto-answered; ensure App applied the reply
    deadline = time.time() + 5
    while app.rules.move_count() < 3 and time.time() < deadline:
        root.update()
        time.sleep(0.03)
    ok_reply = app.rules.move_count() == 3
    root.destroy()
    srv2.close()
    return ok_start, ok_turn, ok_peer_move, sent, ok_own_move, ok_reply


try:
    results = app_integration()
    for name, ok in zip(
            ("app joins and enters game", "app ignores click out of turn",
             "app applies peer move", "app click sends move to peer",
             "app applies own move locally", "app applies host auto-reply"),
            results):
        check(name, ok)
except Exception as e:
    import traceback
    traceback.print_exc()
    check("app-level integration", False, str(e))

# ---------------------------------------------------------------------------
print("=== KataPanel (real engine, optional) ===")
if os.path.isfile(N.KATAGO_EXE):
    rules = N.GoRules(19, 7.5)
    replay(rules, [("B", 15, 3), ("W", 3, 3), ("B", 3, 15), ("W", 15, 15),
                   ("B", 9, 9)])
    kp = N.KataPanel()
    kp.start()
    deadline = time.time() + 30
    ok = False
    for _ in range(100):
        ok = kp.update(rules)
        if ok or kp.failed:
            break
        time.sleep(0.5)
    have = (kp.last_white_win is not None
            and kp.last_ownership and len(kp.last_ownership) == 361)
    check("kata panel returns winrate+ownership", bool(ok and have),
          "failed=%s" % kp.failed)
    kp.stop()
else:
    print("  [SKIP] KataPanel (engine not found)")

print()
print("=" * 60)
print("RESULT: %s (%d passed, %d failed)" % (
    "PASS" if FAIL_COUNT == 0 else "FAIL", PASS_COUNT, FAIL_COUNT))
sys.exit(0 if FAIL_COUNT == 0 else 1)

