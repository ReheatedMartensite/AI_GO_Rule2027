# -*- coding: utf-8 -*-
"""联机对弈 - Lizzieyzy/KataGo 便携版的网络对局伴侣工具.

两位玩家通过局域网/互联网对弈围棋：
  - 主机方创建对局（显示 IP 与端口），客户端输入 IP 加入；
  - 纯 Python 规则引擎（提子/禁自杀/禁止全同形');
  - 可选 KataGo 面板：实时胜率 + 归属热图（默认关闭）；
  - 对局结束保存 SGF，并可一键在 Lizzieyzy 中打开复盘。

仅使用 Python 标准库 + tkinter。
"""

from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

APP_TITLE = "联机对弈 - Lizzieyzy 工具箱"
PROTOCOL_VERSION = 1
DEFAULT_PORT = 19864
SGF_LETTERS = "abcdefghijklmnopqrs"

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(THIS_DIR)
GAMES_DIR = os.path.join(THIS_DIR, "games")
JAVAW = os.path.join(ROOT_DIR, "jre", "java17", "bin", "javaw.exe")
JAR_NAME = "lizzie-yzy2.5.3-shaded.jar"
KATAGO_EXE = os.path.join(ROOT_DIR, "engine_18b", "katago.exe")
KATAGO_MODEL = os.path.join(
    ROOT_DIR, "engine_18b", "kata1-b18c384nbt-s9996604416-d4316597426.bin.gz")
KATAGO_CFG = os.path.join(ROOT_DIR, "engine_18b", "estimate.cfg")
DETACHED_PROCESS = 0x00000008


# ---------------------------------------------------------------------------
# 围棋规则引擎（中国规则行棋合法性；数子结果不做判定）
# ---------------------------------------------------------------------------


class RuleError(Exception):
    pass


class GoRules:
    """提子 / 禁自杀 / 禁全同形（positional superko）。棋盘坐标: x=列, y=行, 左上为 (0,0)。"""

    def __init__(self, size: int = 19, komi: float = 7.5):
        self.size = size
        self.komi = komi
        self.board: list[Optional[str]] = [None] * (size * size)
        self.history: list[tuple[str, int, int]] = []  # (color,x,y); pass 用 (-1,-1)
        self.captures = {"B": 0, "W": 0}
        self.snapshots = {self._snapshot()}
        self.consecutive_passes = 0
        self.ended = ""
        self.first_color = "B"  # 空盘时轮到谁（让子棋为 W）
        self._ko_snapshot = self._snapshot()

    # -- 基础 ---------------------------------------------------------------
    def idx(self, x: int, y: int) -> int:
        return y * self.size + x

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.size and 0 <= y < self.size

    def get(self, x: int, y: int) -> Optional[str]:
        return self.board[self.idx(x, y)]

    def turn(self) -> str:
        if not self.history:
            return self.first_color
        last = self.history[-1][0]
        return "W" if last == "B" else "B"

    def setup_from_stones(self, black, white, first: str = "W"):
        """直接摆放棋子（让子/摆子 SGF 的 AB/AW），清空历史并指定先行方。"""
        self.board = [None] * (self.size * self.size)
        for x, y in black:
            self.board[self.idx(x, y)] = "B"
        for x, y in white:
            self.board[self.idx(x, y)] = "W"
        self.history = []
        self.captures = {"B": 0, "W": 0}
        self.consecutive_passes = 0
        self.ended = ""
        self.first_color = first
        self.snapshots = {self._snapshot()}

    def _snapshot(self) -> str:
        return "".join(p or "." for p in self.board)

    def _group(self, x: int, y: int):
        """返回 (同色连串坐标集, 气坐标集)。"""
        color = self.get(x, y)
        stack, group, libs = [(x, y)], set(), set()
        while stack:
            cx, cy = stack.pop()
            if (cx, cy) in group:
                continue
            group.add((cx, cy))
            for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                if not self.in_bounds(nx, ny):
                    continue
                p = self.get(nx, ny)
                if p is None:
                    libs.add((nx, ny))
                elif p == color and (nx, ny) not in group:
                    stack.append((nx, ny))
        return group, libs

    # -- 行棋 ---------------------------------------------------------------
    def legal_moves_hint(self) -> str:
        return " occupied/suicide/superko 会被拒绝"

    def play(self, color: str, x: int, y: int) -> dict:
        """落子。非法抛 RuleError；成功返回 {'captured': [(x,y),...]}。"""
        if self.ended:
            raise RuleError("对局已结束")
        if color != self.turn():
            raise RuleError("还没轮到 %s" % color)
        if not self.in_bounds(x, y):
            raise RuleError("超出棋盘")
        if self.get(x, y) is not None:
            raise RuleError("该点已有棋子")

        i = self.idx(x, y)
        self.board[i] = color
        captured: list = []
        opp = "W" if color == "B" else "B"
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if self.in_bounds(nx, ny) and self.get(nx, ny) == opp:
                group, libs = self._group(nx, ny)
                if not libs:
                    for gx, gy in group:
                        self.board[self.idx(gx, gy)] = None
                        captured.append((gx, gy))
                    self.captures[color] += len(group)
        if not captured:
            group, libs = self._group(x, y)
            if not libs:  # 自杀
                self.board[i] = None
                raise RuleError("禁着点（自杀）")
        snap = self._snapshot()
        if snap in self.snapshots:  # 全同形（劫）
            # 回滚
            self.board[i] = None
            for gx, gy in captured:
                self.board[self.idx(gx, gy)] = opp
            self.captures[color] -= len(captured)
            raise RuleError("全局同形（劫争禁着）")
        self.snapshots.add(snap)
        self.history.append((color, x, y))
        self.consecutive_passes = 0
        return {"captured": captured}

    def pass_move(self, color: str):
        if self.ended:
            raise RuleError("对局已结束")
        if color != self.turn():
            raise RuleError("还没轮到 %s" % color)
        self.history.append((color, -1, -1))
        self.consecutive_passes += 1
        if self.consecutive_passes >= 2:
            self.ended = "双虚着，对局结束"

    def undo(self):
        """撤回最后一手；返回被撤的手 (color,x,y)。"""
        if not self.history:
            raise RuleError("没有可撤的着法")
        color, x, y = self.history.pop()
        self.consecutive_passes = max(0, self.consecutive_passes - 1)
        self.ended = ""
        if x >= 0:
            # 重演到上一状态（简单可靠：从空盘重放）
            keep = list(self.history)
            self.__init__(self.size, self.komi)
            for c, mx, my in keep:
                if mx >= 0:
                    self.play(c, mx, my)
                else:
                    self.pass_move(c)
        return (color, x, y)

    def resign(self, color: str):
        self.ended = "%s 认输" % ("黑" if color == "B" else "白")

    def move_count(self) -> int:
        return len(self.history)

    # -- 2027 中国规则判定 ---------------------------------------------------
    # 《围棋竞赛规则（2027 试行版）》：黑贴 3.5 子（7 点），黑总得 184 子
    # （9 路/13 路按 total/2 + 贴子数类推）即为平局；平局需分胜负时规程
    # 二选一：①白方胜；②最先放弃着手（先停手）一方胜。

    def first_final_passer(self) -> str | None:
        """终局连续虚着中先停手的一方（"B"/"W"）；非双虚着收束返回 None。"""
        passes = []
        for color, x, y in reversed(self.history):
            if x < 0:
                passes.append(color)
            else:
                break
        if len(passes) >= 2:
            return passes[-1]  # 末尾连续虚着的第一个（倒序收集的最后一个）
        return None

    def judge_chinese(self, dead=None, komi_zi: float = 3.5,
                      tiebreak: str = "none") -> dict:
        """中国规则数子判定（2027 试行版）。

        dead: 视为死子的坐标集合 {(x,y),...}（终局死子先移除再数子）。
        komi_zi: 黑贴还白方的子数（3.5 = 2027 新规，7 点；3.75 = 旧规）。
        tiebreak: 平局且需分胜负时的判定——"none"（维持平局，默认）、
                  "white_wins"（方案一：白方胜）、
                  "first_passer_wins"（方案二：先停手一方胜）。
        返回 dict: score_b/score_w（子，可为 0.5 尾数）、target、
                   margin_zi（黑方子数差，黑正白负）、result（"B"/"W"/"draw"）、
                   text（可读结果）、sgf_re（SGF RE 值）。
        """
        dead = set(dead or ())
        n = self.size
        # 活盘面视图：死子移除后视为空点（参与地域划分）
        live = {}
        for y in range(n):
            for x in range(n):
                p = self.get(x, y)
                if p is not None and (x, y) not in dead:
                    live[(x, y)] = p
        black_stones = sum(1 for v in live.values() if v == "B")
        white_stones = sum(1 for v in live.values() if v == "W")
        owner = {}  # 空点归属："B"/"W"/"dame"
        visited = set()
        for y in range(n):
            for x in range(n):
                if (x, y) in visited or (x, y) in live:
                    continue
                seen = {(x, y)}
                stack = [(x, y)]
                borders = set()
                while stack:
                    cx, cy = stack.pop()
                    for nx, ny in ((cx - 1, cy), (cx + 1, cy),
                                   (cx, cy - 1), (cx, cy + 1)):
                        if not self.in_bounds(nx, ny):
                            continue
                        q = live.get((nx, ny))
                        if (nx, ny) in seen:
                            continue
                        if q is None:
                            seen.add((nx, ny))
                            stack.append((nx, ny))
                        else:
                            borders.add(q)
                visited |= seen
                if borders == {"B"}:
                    owner.update({pt: "B" for pt in seen})
                elif borders == {"W"}:
                    owner.update({pt: "W" for pt in seen})
                else:
                    owner.update({pt: "dame" for pt in seen})
        black_terri = sum(1 for v in owner.values() if v == "B")
        white_terri = sum(1 for v in owner.values() if v == "W")
        dame = sum(1 for v in owner.values() if v == "dame")
        # 中国数子法：公气点双方各得一半子
        score_b = black_stones + black_terri + dame / 2.0
        score_w = white_stones + white_terri + dame / 2.0
        total = n * n
        target = total / 2.0 + komi_zi
        margin = score_b - target
        first_passer = self.first_final_passer()
        result = "B" if margin > 0 else ("W" if margin < 0 else "draw")
        text = ""
        sgf_re = ""
        if result == "draw":
            if tiebreak == "white_wins":
                result = "W"
                text = "和棋 → 按规程判白方胜"
                sgf_re = "W+"
            elif tiebreak == "first_passer_wins":
                if first_passer:
                    result = first_passer   # 先停手一方胜（2027 规程方案二）
                    text = "和棋 → 先停手一方（%s）胜" % ("黑" if first_passer == "B" else "白")
                    sgf_re = result + "+"
                else:
                    text = "和棋（无法确定先停手方，维持平局）"
                    sgf_re = "0"
            else:
                text = "和棋（2027 中国规则：黑 %d 子）" % round(score_b)
                sgf_re = "0"
        else:
            margin_zi = abs(margin)
            zi = int(margin_zi)
            ban = round((margin_zi - zi) * 2)  # 0.5 子尾数转目
            if ban:
                text = "%s胜 %d子%s目" % ("黑" if result == "B" else "白",
                                          zi, ban)
            else:
                text = "%s胜 %d子" % ("黑" if result == "B" else "白", zi)
            sgf_re = "%s+%.1f" % (result, margin_zi)
        return {"score_b": score_b, "score_w": score_w, "target": target,
                "dame": dame, "first_passer": first_passer,
                "margin_zi": margin, "result": result, "text": text,
                "sgf_re": sgf_re}

    def to_sgf(self, black_name="黑方", white_name="白方", result="",
               game_comment="") -> str:
        parts = [
            "(;GM[1]FF[4]CA[UTF-8]",
            "AP[Netplay:1.0]",
            "SZ[%d]" % self.size,
            "KM[%g]" % self.komi,
            "RU[Chinese]",
            "PB[%s]" % black_name,
            "PW[%s]" % white_name,
        ]
        if result:
            parts.append("RE[%s]" % result)
        if game_comment:
            parts.append("GC[%s]" % game_comment.replace("]", ")"))
        for color, x, y in self.history:
            coord = "" if x < 0 else SGF_LETTERS[x] + SGF_LETTERS[y]
            parts.append(";%s[%s]" % (color, coord))
        parts.append(")")
        return "".join(parts)


# ---------------------------------------------------------------------------
# KataGo AI 面板（可选）
# ---------------------------------------------------------------------------


class KataPanel:
    """kata-raw-nn 0 → 胜率 + 归属。失败时静默降级。"""

    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()
        self.failed = False
        self.ready_evt = threading.Event()
        self.last_white_win: Optional[float] = None
        self.last_ownership: Optional[list] = None

    def start(self) -> bool:
        if self.proc is not None or self.failed:
            return self.proc is not None
        try:
            self.proc = subprocess.Popen(
                [KATAGO_EXE, "gtp", "-model", KATAGO_MODEL, "-config", KATAGO_CFG],
                cwd=ROOT_DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                bufsize=1, creationflags=DETACHED_PROCESS if os.name == "nt" else 0)
        except OSError:
            self.failed = True
            return False
        threading.Thread(target=self._drain_until_ready, daemon=True).start()
        return True

    def _drain_until_ready(self):
        try:
            for line in self.proc.stdout:
                if "GTP ready" in line:
                    self.ready_evt.set()
                    return
                if "Error" in line or "error" in line:
                    self.failed = True
                    return
        except Exception:
            self.failed = True

    def update(self, rules: GoRules):
        """把整盘重放给引擎并取回胜率/归属（阻塞，建议在工作线程调用）。"""
        with self.lock:
            if self.proc is None or self.failed:
                return False
            # 等引擎就绪，确保读管道的只有本方法（避免与启动排水线程竞争）
            if not self.ready_evt.wait(timeout=30):
                return False
            try:
                cmds = ["clear_board", "boardsize %d" % rules.size]
                for c, x, y in rules.history:
                    if x >= 0:
                        coord = SGF_LETTERS[x] + SGF_LETTERS[y]
                    else:
                        coord = "pass"
                    cmds.append("play %s %s" % (c, coord))
                cmds.append("kata-raw-nn 0")
                for cm in cmds:
                    self.proc.stdin.write(cm + "\n")
                self.proc.stdin.flush()
                white_win, own, in_own = None, [], False
                deadline = time.time() + 60
                while time.time() < deadline:
                    line = self.proc.stdout.readline()
                    if not line:
                        self.failed = True
                        return False
                    s = line.strip()
                    if s.startswith("whiteWin"):
                        try:
                            white_win = float(s.split()[1])
                        except (IndexError, ValueError):
                            pass
                    elif s.startswith("whiteOwnership"):
                        in_own = True
                        own = []
                    elif in_own:
                        toks = s.split()
                        if len(toks) == rules.size:
                            try:
                                own.extend(float(t) for t in toks)
                            except ValueError:
                                in_own = False
                        if len(own) >= rules.size * rules.size:
                            break
                if white_win is not None and own:
                    self.last_white_win = white_win
                    self.last_ownership = own
                    return True
                return False
            except Exception:
                self.failed = True
                return False

    def stop(self):
        with self.lock:
            if self.proc is not None:
                try:
                    self.proc.stdin.write("quit\n")
                    self.proc.stdin.flush()
                except Exception:
                    pass
                try:
                    self.proc.terminate()
                except Exception:
                    pass
                self.proc = None


# ---------------------------------------------------------------------------
# 网络层：JSON 行协议
# ---------------------------------------------------------------------------


class NetEndpoint(threading.Thread):
    """一条已建立连接的双向消息端点。on_recv 在网络线程回调。"""

    def __init__(self, sock: socket.socket, on_recv, on_close):
        super().__init__(daemon=True)
        self.sock = sock
        self.on_recv = on_recv
        self.on_close = on_close
        self.alive = True
        self.wlock = threading.Lock()
        self._last_recv = time.time()

    def send(self, obj: dict):
        if not self.alive:
            return
        try:
            data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
            with self.wlock:
                self.sock.sendall(data)
        except OSError:
            self._close()

    def run(self):
        buf = b""
        try:
            while self.alive:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                self._last_recv = time.time()
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        self.on_recv(json.loads(line.decode("utf-8")))
                    except (ValueError, UnicodeDecodeError):
                        pass
        except OSError:
            pass
        finally:
            self._close()

    def _close(self):
        if not self.alive:
            return
        self.alive = False
        try:
            self.sock.close()
        except OSError:
            pass
        self.on_close()

    def close(self):
        self._close()


def lan_ips() -> list:
    """本机局域网 IPv4 列表（尽力而为）。"""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass
    if not ips:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
        except OSError:
            pass
        finally:
            s.close()
    return sorted(ips)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------


class App:
    BOARD_CANVAS = 640
    MARGIN = 36.0

    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("1060x760")
        self.rules = GoRules(19)
        self.my_color: Optional[str] = None
        self.my_name = ""
        self.peer_name = ""
        self.endpoint: Optional[NetEndpoint] = None
        self.server_sock: Optional[socket.socket] = None
        self.is_host = False
        self.passcode = ""
        self.msg_q: "queue.Queue[tuple]" = queue.Queue()
        self.kata = KataPanel()
        self.ai_on = tk.BooleanVar(value=False)
        self.ai_failed_notified = False
        self.undo_pending = False
        self.dead_stones: set = set()       # 终局死子标注
        self.sgf_re = ""                    # 判定结果（SGF RE 值）
        self.first_passer: str | None = None  # 终局先停手一方（"B"/"W"）
        self.board_size_var = tk.StringVar(value="19")
        self.komi_var = tk.StringVar(value="7.5")
        self._photo = None
        self._build_entry()
        self._poll_job = self.root.after(80, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------- 入口界面 ----------------
    def _build_entry(self):
        self.entry_frame = ttk.Frame(self.root, padding=16)
        self.entry_frame.pack(fill="both", expand=True)
        ttk.Label(self.entry_frame, text=APP_TITLE, font=("", 16, "bold")).pack(pady=8)
        row = ttk.Frame(self.entry_frame)
        row.pack(pady=4, anchor="w")
        ttk.Label(row, text="昵称:").pack(side="left")
        self.name_var = tk.StringVar(value="玩家")
        ttk.Entry(row, textvariable=self.name_var, width=14).pack(side="left", padx=6)

        self.mode_var = tk.StringVar(value="host")
        self.detail = ttk.Frame(self.entry_frame)
        self.detail.pack(pady=6, anchor="w")
        self._build_host_detail()

        ttk.Radiobutton(self.entry_frame, text="创建对局（我是主机）",
                        variable=self.mode_var, value="host",
                        command=self._switch_mode).pack(anchor="w", pady=2)
        ttk.Radiobutton(self.entry_frame, text="加入对局（输入主机 IP）",
                        variable=self.mode_var, value="join",
                        command=self._switch_mode).pack(anchor="w", pady=2)

        opt = ttk.Frame(self.entry_frame)
        opt.pack(pady=8, anchor="w")
        ttk.Label(opt, text="棋盘:").pack(side="left")
        for n in ("9", "13", "19"):
            ttk.Radiobutton(opt, text=n + " 路", variable=self.board_size_var,
                            value=n).pack(side="left", padx=3)
        ttk.Label(opt, text="    贴目:").pack(side="left")
        ttk.Entry(opt, textvariable=self.komi_var, width=5).pack(side="left")
        ttk.Label(opt, text="    口令(可选 4 位数字):").pack(side="left")
        self.pass_var = tk.StringVar()
        ttk.Entry(opt, textvariable=self.pass_var, width=6).pack(side="left")

        ttk.Button(self.entry_frame, text="开 始", command=self.on_start).pack(pady=10)
        self.entry_status = ttk.Label(self.entry_frame, text="", foreground="#a00")
        self.entry_status.pack(pady=4)
        ttk.Label(self.entry_frame, foreground="#666",
                  text="同一局域网直接连；跨公网需主机端做端口映射（默认端口 %d）或使用内网穿透工具。"
                  % DEFAULT_PORT).pack(pady=6, anchor="w")

    def _build_host_detail(self):
        for w in self.detail.winfo_children():
            w.destroy()
        ips = lan_ips()
        text = "本机 IP: " + (" / ".join(ips) if ips else "（检测失败，请用 ipconfig 查看）")
        text += "\n端口: %d（把 IP 和端口告诉对方即可）" % DEFAULT_PORT
        ttk.Label(self.detail, text=text, foreground="#060").pack(anchor="w")
        self.host_ip_var = tk.StringVar(value=ips[0] if ips else "")

    def _build_join_detail(self):
        for w in self.detail.winfo_children():
            w.destroy()
        row = ttk.Frame(self.detail)
        row.pack(anchor="w")
        ttk.Label(row, text="主机 IP:").pack(side="left")
        self.host_ip_var = tk.StringVar(value="")
        ttk.Entry(row, textvariable=self.host_ip_var, width=18).pack(side="left", padx=4)
        ttk.Label(row, text="端口:").pack(side="left")
        self.join_port_var = tk.StringVar(value=str(DEFAULT_PORT))
        ttk.Entry(row, textvariable=self.join_port_var, width=7).pack(side="left")

    def _switch_mode(self):
        if self.mode_var.get() == "host":
            self._build_host_detail()
        else:
            self._build_join_detail()

    # ---------------- 启动连接 ----------------
    def on_start(self):
        self.my_name = self.name_var.get().strip() or "玩家"
        self.passcode = self.pass_var.get().strip()
        try:
            size = int(self.board_size_var.get())
            komi = float(self.komi_var.get())
        except ValueError:
            self.entry_status.config(text="棋盘/贴目格式不正确")
            return
        if self.mode_var.get() == "host":
            self._start_host(size, komi)
        else:
            self._start_join(size, komi)

    def _start_host(self, size, komi):
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("", DEFAULT_PORT))
            srv.listen(1)
        except OSError as e:
            self.entry_status.config(text="监听失败: %s" % e)
            return
        self.server_sock = srv
        self.is_host = True
        self.pending_setup = (size, komi)
        self.entry_status.config(
            text="等待对方加入…（IP: %s  端口 %d）" % (self.host_ip_var.get(), DEFAULT_PORT),
            foreground="#080")

        def accept_job():
            try:
                conn, addr = srv.accept()
            except OSError:
                return
            self.msg_q.put(("connected", conn, addr))

        threading.Thread(target=accept_job, daemon=True).start()

    def _start_join(self, size, komi):
        host = self.host_ip_var.get().strip()
        try:
            port = int(self.join_port_var.get())
        except ValueError:
            self.entry_status.config(text="端口不正确")
            return
        if not host:
            self.entry_status.config(text="请输入主机 IP")
            return
        self.pending_setup = (size, komi)
        self.entry_status.config(text="连接中…", foreground="#666")

        def connect_job():
            try:
                s = socket.create_connection((host, port), timeout=10)
                self.msg_q.put(("connected", s, (host, port)))
            except OSError as e:
                self.msg_q.put(("connect_fail", str(e)))

        threading.Thread(target=connect_job, daemon=True).start()

    # ---------------- 对局界面 ----------------
    def _enter_game(self, my_color: str, size: int, komi: float):
        self.entry_frame.destroy()
        self.my_color = my_color
        self.rules = GoRules(size, komi)

        main = ttk.Frame(self.root, padding=8)
        main.pack(fill="both", expand=True)
        left = ttk.Frame(main)
        left.pack(side="left", fill="y", padx=8)
        right = ttk.Frame(main)
        right.pack(side="right", fill="both", expand=True)

        self.status_var = tk.StringVar(value=self._status_text())
        ttk.Label(right, textvariable=self.status_var, font=("", 12)).pack(pady=4)

        self.board_cv = tk.Canvas(right, width=self.BOARD_CANVAS,
                                  height=self.BOARD_CANVAS, bg="#e8c58a",
                                  highlightthickness=0)
        self.board_cv.pack(padx=8, pady=8)
        self.board_cv.bind("<Button-1>", self.on_board_click)

        ttk.Label(left, text="操作", font=("", 11, "bold")).pack(anchor="w", pady=4)
        for text, cmd in (("虚着 (Pass)", self.on_pass),
                          ("悔棋请求", self.on_undo_req),
                          ("认输", self.on_resign),
                          ("保存 SGF", self.on_save_sgf),
                          ("在 Lizzieyzy 打开复盘", self.on_open_lizzie)):
            ttk.Button(left, text=text, command=cmd).pack(fill="x", pady=2)
        ttk.Checkbutton(left, text="AI 胜率/归属面板",
                        variable=self.ai_on, command=self.on_toggle_ai).pack(anchor="w", pady=6)
        self.ai_var = tk.StringVar(value="（AI 面板未开启）")
        ttk.Label(left, textvariable=self.ai_var, foreground="#136").pack(anchor="w")

        ttk.Label(left, text="聊天", font=("", 11, "bold")).pack(anchor="w", pady=(10, 2))
        self.chat_text = tk.Text(left, height=10, width=34, state="disabled")
        self.chat_text.pack()
        chat_row = ttk.Frame(left)
        chat_row.pack(fill="x", pady=2)
        self.chat_var = tk.StringVar()
        ent = ttk.Entry(chat_row, textvariable=self.chat_var)
        ent.pack(side="left", fill="x", expand=True)
        ent.bind("<Return>", self.on_chat_enter)
        ttk.Button(chat_row, text="发送", command=self.send_chat).pack(side="left", padx=2)

        self._draw_board()
        self._append_chat("已连接：%s\n你执%s。" % (self.peer_name, "黑" if my_color == "B" else "白"))

    def _status_text(self) -> str:
        r = self.rules
        turn = r.turn()
        who = "黑" if turn == "B" else "白"
        mine = "你执黑" if self.my_color == "B" else "你执白"
        waiting = "（等待对方）" if turn != self.my_color and not r.ended else (
            "（轮到你）" if turn == self.my_color else "")
        extra = "  %s" % r.ended if r.ended else ""
        return "%s · 第 %d 手 · 轮到%s%s · 提子 黑%d/白%d%s" % (
            mine, r.move_count(), who, waiting,
            r.captures["B"], r.captures["W"], extra)

    # ---------------- 棋盘绘制 ----------------
    def _step(self):
        n = self.rules.size
        return (self.BOARD_CANVAS - 2 * self.MARGIN) / (n - 1)

    def _pos(self, i):
        return self.MARGIN + i * self._step()

    def _draw_board(self):
        cv = self.board_cv
        cv.delete("all")
        r = self.rules
        n = r.size
        step = self._step()
        cv.create_rectangle(4, 4, self.BOARD_CANVAS - 4, self.BOARD_CANVAS - 4,
                            fill="#e3b566", outline="#7a5a28", width=2)
        for k in range(n):
            cv.create_line(self._pos(k), self._pos(0), self._pos(k),
                           self._pos(n - 1), fill="#5c4018")
            cv.create_line(self._pos(0), self._pos(k), self._pos(n - 1),
                           self._pos(k), fill="#5c4018")
        stars = {19: [(3, 3), (9, 9), (15, 15), (3, 15), (15, 3)],
                 13: [(3, 3), (9, 9), (3, 9), (9, 3)],
                 9: [(2, 2), (6, 6), (2, 6), (6, 2)]}
        rs = max(2, step * 0.09)
        for sx, sy in stars.get(n, []):
            cv.create_oval(self._pos(sx) - rs, self._pos(sy) - rs,
                           self._pos(sx) + rs, self._pos(sy) + rs,
                           fill="#5c4018", outline="#5c4018")
        # AI 归属热图（底层）
        if self.ai_on.get() and self.kata.last_ownership:
            own = self.kata.last_ownership
            cell = step * 0.9
            for j in range(n):
                for i in range(n):
                    v = own[j * n + i]
                    if v > 0.4:
                        cv.create_rectangle(self._pos(i) - cell / 2, self._pos(j) - cell / 2,
                                            self._pos(i) + cell / 2, self._pos(j) + cell / 2,
                                            fill="#3a6cff", stipple="gray50", outline="")
                    elif v < -0.4:
                        cv.create_rectangle(self._pos(i) - cell / 2, self._pos(j) - cell / 2,
                                            self._pos(i) + cell / 2, self._pos(j) + cell / 2,
                                            fill="#d0342c", stipple="gray50", outline="")
        rad = step * 0.46
        for y in range(n):
            for x in range(n):
                p = r.get(x, y)
                if not p:
                    continue
                px, py = self._pos(x), self._pos(y)
                fill = "#141414" if p == "B" else "#f7f7f2"
                outline = "#eee" if p == "B" else "#333"
                cv.create_oval(px - rad, py - rad, px + rad, py + rad,
                               fill=fill, outline=outline)
        # 最后一手标记
        if r.history:
            lx, ly = r.history[-1][1], r.history[-1][2]
            if lx >= 0:
                mark = "#fff" if r.history[-1][0] == "B" else "#111"
                m = rad * 0.4
                cv.create_oval(self._pos(lx) - m, self._pos(ly) - m,
                               self._pos(lx) + m, self._pos(ly) + m,
                               fill=mark, outline=mark)
        # 终局死子标记（红叉）
        for (dx, dy) in self.dead_stones:
            if r.in_bounds(dx, dy):
                px, py = self._pos(dx), self._pos(dy)
                cv.create_line(px - rad * 0.6, py - rad * 0.6,
                               px + rad * 0.6, py + rad * 0.6,
                               fill="#e02020", width=2)
                cv.create_line(px - rad * 0.6, py + rad * 0.6,
                               px + rad * 0.6, py - rad * 0.6,
                               fill="#e02020", width=2)

    # ---------------- 对局交互 ----------------
    def on_board_click(self, event):
        if self.endpoint is None or not self.endpoint.alive:
            return
        r = self.rules
        if r.ended:
            # 终局后点击棋子 = 标注/取消死子，随后重算 2027 判定
            step = self._step()
            i = int(round((event.x - self.MARGIN) / step))
            j = int(round((event.y - self.MARGIN) / step))
            if r.in_bounds(i, j) and r.get(i, j):
                if (i, j) in self.dead_stones:
                    self.dead_stones.discard((i, j))
                else:
                    self.dead_stones.add((i, j))
                self._refresh_judge()
                self._draw_board()
            return
        if r.turn() != self.my_color:
            self._append_chat("【系统】还没轮到你。\n")
            return
        step = self._step()
        i = int(round((event.x - self.MARGIN) / step))
        j = int(round((event.y - self.MARGIN) / step))
        if not r.in_bounds(i, j):
            return
        if abs(event.x - self._pos(i)) > step * 0.5 or abs(event.y - self._pos(j)) > step * 0.5:
            return
        try:
            r.play(r.turn(), i, j)
        except RuleError as e:
            self._append_chat("【系统】%s\n" % e)
            return
        self.endpoint.send({"t": "move", "seq": r.move_count(), "x": i, "y": j,
                            "color": "W" if r.history[-1][0] == "W" else "B"})
        self._after_move()

    def on_pass(self):
        r = self.rules
        if r.ended or r.turn() != self.my_color or self.endpoint is None:
            return
        r.pass_move(r.turn())
        self.endpoint.send({"t": "move", "seq": r.move_count(), "x": -1, "y": -1,
                            "color": "W" if r.history[-1][0] == "W" else "B"})
        self._append_chat("【系统】你虚着。\n")
        self._after_move()
        if r.ended:
            self._finalize_judge()
            self._game_over()

    def on_undo_req(self):
        if self.endpoint is None or self.undo_pending or not self.rules.history:
            return
        self.undo_pending = True
        self.endpoint.send({"t": "undo_req"})
        self._append_chat("【系统】已发送悔棋请求，等待对方…\n")

    def on_resign(self):
        if self.endpoint is None or self.rules.ended:
            return
        if not messagebox.askyesno(APP_TITLE, "确定认输？"):
            return
        self.rules.resign(self.my_color)
        self.endpoint.send({"t": "resign"})
        self._game_over()

    def on_chat_enter(self, _e):
        self.send_chat()

    def send_chat(self):
        text = self.chat_var.get().strip()
        if not text or self.endpoint is None:
            return
        self.endpoint.send({"t": "chat", "text": text[:200]})
        self._append_chat("%s: %s\n" % (self.my_name, text))
        self.chat_var.set("")

    def _append_chat(self, text):
        self.chat_text.config(state="normal")
        self.chat_text.insert("end", text)
        self.chat_text.see("end")
        self.chat_text.config(state="disabled")

    def _after_move(self):
        self._draw_board()
        self.status_var.set(self._status_text())
        if self.ai_on.get():
            self._kick_ai_update()

    def _kick_ai_update(self):
        if not self.kata.start():
            self._notify_ai_fail()
            return

        def job():
            ok = self.kata.update(self.rules)
            self.msg_q.put(("ai", ok))

        threading.Thread(target=job, daemon=True).start()

    def _notify_ai_fail(self):
        if self.ai_failed_notified:
            return
        self.ai_failed_notified = True
        self.ai_var.set("（AI 面板不可用：引擎启动失败，对弈不受影响）")

    def on_toggle_ai(self):
        if self.ai_on.get():
            if self.kata.start():
                self.ai_var.set("（AI 面板已开启，对方落子后更新）")
                self._kick_ai_update()
            else:
                self._notify_ai_fail()
        else:
            self.ai_var.set("（AI 面板未开启）")
            self.kata.last_ownership = None
            self._draw_board()

    # ---------------- SGF / Lizzieyzy ----------------
    def on_save_sgf(self):
        os.makedirs(GAMES_DIR, exist_ok=True)
        result = self.sgf_re or self.rules.ended or ""
        gc = ""
        if self.first_passer:
            gc = ("2027 中国规则信息：先停手=%s；平局规程判定=%s"
                  % ("黑" if self.first_passer == "B" else "白",
                     self.rules.ended or "未判定"))
        path = os.path.join(
            GAMES_DIR, time.strftime("%Y%m%d_%H%M%S") + ".sgf")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(self.rules.to_sgf(
                black_name=self.peer_name if self.my_color == "W" else self.my_name,
                white_name=self.my_name if self.my_color == "W" else self.peer_name,
                result=result, game_comment=gc))
        self._append_chat("【系统】棋谱已保存：%s\n" % path)
        self.last_sgf = path
        messagebox.showinfo(APP_TITLE, "已保存：\n%s" % path)
        return path

    def on_open_lizzie(self):
        path = getattr(self, "last_sgf", None) or self.on_save_sgf()
        try:
            subprocess.Popen([JAVAW, "-jar", JAR_NAME, os.path.abspath(path)],
                             cwd=ROOT_DIR, creationflags=DETACHED_PROCESS)
        except OSError as e:
            messagebox.showerror(APP_TITLE, "启动 Lizzieyzy 失败：%s" % e)

    # ---------------- 消息处理 ----------------
    def _poll(self):
        try:
            while True:
                msg = self.msg_q.get_nowait()
                self._handle(msg)
        except queue.Empty:
            pass
        self._poll_job = self.root.after(80, self._poll)

    def _handle(self, msg):
        kind = msg[0]
        if kind == "connected":
            conn, addr = msg[1], msg[2]
            self.endpoint = NetEndpoint(conn, self._on_net_msg, self._on_net_close)
            self.endpoint.start()
            if self.is_host:
                size, komi = self.pending_setup
                self.endpoint.send({"t": "hello", "name": self.my_name,
                                    "ver": PROTOCOL_VERSION, "pass": self.passcode})
                self._host_await_hello = True
                self._host_setup = (size, komi)
            else:
                self.endpoint.send({"t": "hello", "name": self.my_name,
                                    "ver": PROTOCOL_VERSION, "pass": self.passcode})
        elif kind == "connect_fail":
            self.entry_status.config(text="连接失败: %s" % msg[1], foreground="#a00")
        elif kind == "net":
            self._dispatch(msg[1])
        elif kind == "closed":
            self._append_chat("【系统】对方已断开连接。棋盘保留，可保存棋谱。\n")
            if self.status_var:
                self.status_var.set(self._status_text() + " · 已断开")
        elif kind == "ai":
            if msg[1]:
                ww = self.kata.last_white_win
                own = self.kata.last_ownership or []
                black_wr = (1 - ww) * 100 if ww is not None else 0
                score = -sum(own) if own else 0  # ownership 为白方视角
                self.ai_var.set("黑方胜率 %.1f%%   目数差(黑-白)约 %+.1f" % (black_wr, score))
                self._draw_board()
            elif self.kata.failed:
                self._notify_ai_fail()

    def _on_net_msg(self, obj):
        self.msg_q.put(("net", obj))

    def _on_net_close(self):
        self.msg_q.put(("closed",))

    def _dispatch(self, obj):
        t = obj.get("t")
        if t == "hello":
            self._on_hello(obj)
        elif t == "start":
            self.peer_name = obj.get("name", "对方")
            self._enter_game(obj["color"], obj["size"], obj["komi"])
        elif t == "move":
            self._on_move(obj)
        elif t == "undo_req":
            if messagebox.askyesno(APP_TITLE, "对方请求悔棋，同意吗？"):
                self.endpoint.send({"t": "undo_ack"})
                self._apply_undo()
                self._append_chat("【系统】你同意了悔棋。\n")
            else:
                self.endpoint.send({"t": "undo_rej"})
        elif t == "undo_ack":
            self.undo_pending = False
            self._apply_undo()
            self._append_chat("【系统】对方同意悔棋。\n")
        elif t == "undo_rej":
            self.undo_pending = False
            self._append_chat("【系统】对方拒绝了悔棋。\n")
        elif t == "resign":
            winner = "黑" if self.my_color == "B" else "白"
            self.rules.ended = "对方认输，%s中盘胜" % winner
            self._after_move()
            self._game_over()
        elif t == "chat":
            self._append_chat("%s: %s\n" % (self.peer_name or "对方", obj.get("text", "")))
        elif t == "error":
            self._append_chat("【系统】对方报告错误：%s\n" % obj.get("text"))
            self.endpoint.close()

    def _on_hello(self, obj):
        if obj.get("ver") != PROTOCOL_VERSION:
            self.endpoint.send({"t": "error", "text": "版本不一致"})
            return
        if self.passcode and obj.get("pass", "") != self.passcode:
            self.endpoint.send({"t": "error", "text": "口令不正确"})
            return
        self.peer_name = str(obj.get("name", "对方"))[:24]
        if self.is_host:
            size, komi = self._host_setup
            self.endpoint.send({"t": "start", "color": "W", "size": size,
                                "komi": komi, "name": self.my_name})
            self._enter_game("B", size, komi)

    def _on_move(self, obj):
        r = self.rules
        try:
            if int(obj.get("seq", -1)) != r.move_count() + 1:
                raise RuleError("手数不同步（本地 %d 期望下一手 %d）"
                                % (r.move_count(), r.move_count() + 1))
            color = obj.get("color")
            x, y = int(obj.get("x", -2)), int(obj.get("y", -2))
            if x < 0:
                r.pass_move(color)
            else:
                r.play(color, x, y)
        except RuleError as e:
            self._append_chat("【系统】同步冲突：%s\n" % e)
            self.endpoint.send({"t": "error", "text": str(e)})
            return
        self._after_move()
        if r.ended:
            self._finalize_judge()
            self._game_over()

    def _apply_undo(self):
        try:
            self.rules.undo()
        except RuleError:
            return
        self._after_move()

    def _game_over(self):
        self._after_move()
        if self.first_passer:
            self.status_var.set(self._status_text() + " · 先停手: %s"
                                % ("黑" if self.first_passer == "B" else "白"))
        self._append_chat("【系统】%s 可保存棋谱或复盘。\n" % self.rules.ended)
        messagebox.showinfo(APP_TITLE, self.rules.ended)

    def _finalize_judge(self):
        """双虚着收束后按 2027 中国规则数子判定（含死子标注）。"""
        res = self.rules.judge_chinese(self.dead_stones)
        self.rules.ended = res["text"]
        self.sgf_re = res["sgf_re"]
        self.first_passer = res["first_passer"]
        fp_text = ("先停手：%s" % ("黑" if res["first_passer"] == "B"
                                    else "白")) if res["first_passer"] \
            else "谱中无先停手信息"
        self._append_chat("【系统】2027 中国规则判定：黑 %.1f 子 / 白 %.1f 子，%s · %s"
                          "（可点击棋盘上的棋子标注死子后重算）\n"
                          % (res["score_b"], res["score_w"], res["text"], fp_text))
        if res["result"] == "draw":
            if messagebox.askyesno(APP_TITLE, "对局为和棋。按 2027 规程方案一判白方胜？"):
                self.rules.ended = "和棋 → 按规程判白方胜"
                self.sgf_re = "W+"
            elif messagebox.askyesno(APP_TITLE,
                    "或按方案二判先停手一方胜？（是=先停手方胜，否=保持平局）"):
                fp = res["first_passer"]
                if fp:
                    self.rules.ended = "和棋 → 先停手一方（%s）胜" % (
                        "黑" if fp == "B" else "白")
                    self.sgf_re = fp + "+"

    def _refresh_judge(self):
        """死子标注变化后重算判定并更新状态。"""
        res = self.rules.judge_chinese(self.dead_stones)
        self.rules.ended = res["text"]
        self.sgf_re = res["sgf_re"]
        self.first_passer = res["first_passer"]
        self.status_var.set(self._status_text())
        self._append_chat("【系统】重算判定：%s（死子 %d 颗）\n"
                          % (res["text"], len(self.dead_stones)))

    def on_close(self):
        try:
            if self._poll_job is not None:
                self.root.after_cancel(self._poll_job)
                self._poll_job = None
        except Exception:
            pass
        try:
            if self.endpoint is not None:
                self.endpoint.send({"t": "bye"})
                self.endpoint.close()
            if self.server_sock is not None:
                self.server_sock.close()
        finally:
            self.kata.stop()
            self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
