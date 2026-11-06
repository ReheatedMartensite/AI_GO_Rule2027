# -*- coding: utf-8 -*-
"""实时盯盘 - Lizzieyzy/KataGo 便携版的窗口实时盯盘工具.

选择一个包含围棋棋盘的窗口后，工具每隔几秒截取该窗口画面，
自动识别其中的棋盘（复用 screenshot2board 识别核心），
增量推演出着法（复用 netplay 规则引擎），在自己界面上实时摆盘；
可选开启 KataGo 实时胜率/归属分析（loadsgf 方案，随棋局进展动态更新）。

仅支持 Windows。依赖：numpy / opencv-python / Pillow / tkinter / sgfmill。
"""

from __future__ import annotations

import base64
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import traceback

import numpy as np

import tkinter as tk
from tkinter import messagebox, ttk

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(THIS_DIR)
for _p in (os.path.join(ROOT_DIR, "screenshot2board"),
           os.path.join(ROOT_DIR, "netplay")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from recognizer import recognize_auto          # noqa: E402  识别核心
from netplay import GoRules, RuleError         # noqa: E402  规则引擎

APP_TITLE = "实时盯盘 - Lizzieyzy 工具箱"
APP_NAME = "实时盯盘"            # 过滤本工具自己的窗口时使用
SGF_LETTERS = "abcdefghijklmnopqrs"
DEFAULT_KOMI = 7.5
THUMB_MAX = 240                  # 缩略图最长边（像素）

GAMES_DIR = os.path.join(THIS_DIR, "games")
TMP_DIR = os.path.join(THIS_DIR, "tmp")
JAVAW = os.path.join(ROOT_DIR, "jre", "java17", "bin", "javaw.exe")
JAR_NAME = "lizzie-yzy2.5.3-shaded.jar"
KATAGO_EXE = os.path.join(ROOT_DIR, "engine_18b", "katago.exe")
KATAGO_MODEL = os.path.join(
    ROOT_DIR, "engine_18b", "kata1-b18c384nbt-s9996604416-d4316597426.bin.gz")
KATAGO_CFG = os.path.join(ROOT_DIR, "engine_18b", "estimate.cfg")
DETACHED_PROCESS = 0x00000008

IS_WINDOWS = (os.name == "nt")
# 后台子进程（wmic/netstat/powershell）不弹命令行黑框
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def _console_log(text: str):
    """控制台输出（强制 ASCII，避免编码问题）。"""
    try:
        print(text.encode("ascii", "replace").decode("ascii"), flush=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# A. Win32 窗口枚举与捕获（ctypes，仅 Windows）
# ---------------------------------------------------------------------------

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _kernel32.OpenProcess.restype = ctypes.c_void_p
    _kernel32.OpenProcess.argtypes = [ctypes.c_uint32, wintypes.BOOL,
                                      wintypes.DWORD]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD)]
    _kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_uint16),
            ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
            ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_uint32),
            ("biClrImportant", ctypes.c_uint32)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                    ("bmiColors", ctypes.c_uint32 * 3)]

    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    _user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_uint]
    _PW_RENDERFULLCONTENT = 0x00000002  # Win8.1+，可截取硬件加速内容

    # 保证 GetWindowRect / ImageGrab 使用同一（物理像素）坐标系
    try:
        _user32.SetProcessDPIAware()
    except Exception:
        pass


def _window_process(pid: int) -> str:
    """进程可执行文件名（如 Weixin.exe）；失败返回空串。"""
    if not IS_WINDOWS or not pid:
        return ""
    try:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False,
                                  int(pid))
        if not h:
            return ""
        try:
            size = ctypes.c_uint32(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if _kernel32.QueryFullProcessImageNameW(h, 0, buf,
                                                    ctypes.byref(size)):
                full = buf.value.replace("/", "\\")
                return full.rsplit("\\", 1)[-1]
        finally:
            _kernel32.CloseHandle(h)
    except Exception:
        pass
    return ""


def _is_cloaked(hwnd) -> bool:
    """DWM cloaked 窗口（如 UWP 挂靠窗口）：IsWindowVisible 为真但实际不显示。"""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        cloaked = ctypes.c_uint32(0)
        # DWMWA_CLOAKED = 14
        if _dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked),
                                         ctypes.sizeof(cloaked)) == 0:
            return cloaked.value != 0
    except Exception:
        pass
    return False


def _window_pid(hwnd) -> int:
    try:
        pid = ctypes.c_uint32(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def _window_title(hwnd) -> str:
    try:
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value.strip()
    except Exception:
        return ""


def window_label(hwnd, title="") -> str:
    """窗口列表显示标签：标题 + 进程名；无标题窗口也能被识别。"""
    try:
        proc = _window_process(_window_pid(hwnd))
    except Exception:
        proc = ""
    if not title:
        title = _window_title(hwnd)
    if not title:
        title = "（无标题）"
    if proc:
        return "%s [%s]" % (title, proc)
    return title


def resolve_window(hwnd) -> dict | None:
    """解析任意 hwnd 的信息（供点选模式使用）；无效返回 None。"""
    if not window_alive(hwnd):
        return None
    rect = RECT()
    try:
        if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
    except Exception:
        return None
    w = int(rect.right - rect.left)
    h = int(rect.bottom - rect.top)
    title = _window_title(hwnd)
    if APP_NAME and APP_NAME in title:
        return None  # 点到了本工具自己
    pid = _window_pid(hwnd)
    return {"hwnd": int(hwnd), "title": title,
            "label": window_label(hwnd, title),
            "process": _window_process(pid), "pid": pid,
            "rect": (int(rect.left), int(rect.top), w, h)}


def window_from_point(x: int, y: int) -> int:
    """屏幕坐标处的窗口句柄（可能是子窗口，如微信小程序内容区）。"""
    if not IS_WINDOWS:
        return 0
    try:
        pt = wintypes.POINT(int(x), int(y))
        return int(_user32.WindowFromPoint(pt))
    except Exception:
        return 0


def window_root(hwnd) -> int:
    """给定窗口的顶层祖先（GA_ROOT）。"""
    if not IS_WINDOWS or not hwnd:
        return 0
    try:
        return int(_user32.GetAncestor(hwnd, 2))  # GA_ROOT
    except Exception:
        return 0


def list_windows() -> list:
    """枚举可见顶层窗口（含无标题窗口，剔除 DWM cloaked 隐形窗口）。

    返回 [{"hwnd","title","label","process","pid","rect":(l,t,w,h)}]；
    过滤：宽或高 <300、标题含本程序名"实时盯盘"的窗口。
    非 Windows 返回 []。
    """
    if not IS_WINDOWS:
        return []
    result = []

    def _cb(hwnd, lparam):
        try:
            if not _user32.IsWindowVisible(hwnd):
                return True
            if _is_cloaked(hwnd):
                return True
            title = _window_title(hwnd)
            if APP_NAME in title:  # 排除本工具自己的窗口
                return True
            rect = RECT()
            if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            w = int(rect.right - rect.left)
            h = int(rect.bottom - rect.top)
            if w < 300 or h < 300:
                return True
            pid = _window_pid(hwnd)
            proc = _window_process(pid)
            if not title:
                title = ""
            label = ("%s [%s]" % (title, proc)) if proc else (
                title or "（无标题）")
            result.append({"hwnd": int(hwnd), "title": title, "label": label,
                           "process": proc, "pid": pid,
                           "rect": (int(rect.left), int(rect.top), w, h)})
        except Exception:
            pass
        return True

    try:
        _user32.EnumWindows(_WNDENUMPROC(_cb), 0)
    except Exception:
        pass
    return result


def window_alive(hwnd) -> bool:
    """窗口句柄是否仍然有效（未销毁）。"""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        return bool(_user32.IsWindow(hwnd))
    except Exception:
        return False


def window_minimized(hwnd) -> bool:
    """窗口是否处于最小化（Iconic）状态。"""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        return bool(_user32.IsIconic(hwnd))
    except Exception:
        return False


def restore_window(hwnd) -> bool:
    """还原最小化的窗口（SW_RESTORE）。ok-script 对最小化窗口即如此处理。

    返回是否执行了还原操作。
    """
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            return True
    except Exception:
        pass
    return False


def capture_window(hwnd):
    """截取窗口画面，返回 BGR ndarray 或 None。

    优先 PrintWindow（PW_RENDERFULLCONTENT，被遮挡也能截）；
    PrintWindow 返回 0 或全黑帧时回退 PIL.ImageGrab.grab（要求窗口在屏幕上可见）。
    窗口可能被移动/缩放：每次调用都重新 GetWindowRect。
    """
    if not IS_WINDOWS or not hwnd:
        return None
    rect = RECT()
    try:
        if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
    except Exception:
        return None
    w = int(rect.right - rect.left)
    h = int(rect.bottom - rect.top)
    # 最小化等异常状态会返回标题栏残帧（实测 28x160 之类），直接视为无效
    if w <= 0 or h <= 0 or w < 300 or h < 300:
        return None

    frame = None
    try:
        hdc = _user32.GetWindowDC(hwnd)
        if hdc:
            memdc = _gdi32.CreateCompatibleDC(hdc)
            bmp = _gdi32.CreateCompatibleBitmap(hdc, w, h)
            if memdc and bmp:
                old = _gdi32.SelectObject(memdc, bmp)
                ok = _user32.PrintWindow(hwnd, memdc, _PW_RENDERFULLCONTENT)
                _gdi32.SelectObject(memdc, old)
                if ok:
                    bmi = BITMAPINFO()
                    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                    bmi.bmiHeader.biWidth = w
                    bmi.bmiHeader.biHeight = -h  # 负值 = 自顶向下行序
                    bmi.bmiHeader.biPlanes = 1
                    bmi.bmiHeader.biBitCount = 32
                    bmi.bmiHeader.biCompression = 0  # BI_RGB
                    buf = ctypes.create_string_buffer(w * h * 4)
                    copied = _gdi32.GetDIBits(memdc, bmp, 0, h, buf,
                                              ctypes.byref(bmi), 0)
                    if copied:
                        arr = np.frombuffer(buf, dtype=np.uint8)
                        if arr.size >= w * h * 4:
                            arr = arr[:w * h * 4].reshape(h, w, 4)
                            frame = np.ascontiguousarray(arr[:, :, :3])  # BGRA->BGR
            if bmp:
                _gdi32.DeleteObject(bmp)
            if memdc:
                _gdi32.DeleteDC(memdc)
            _user32.ReleaseDC(hwnd, hdc)
    except Exception:
        frame = None

    if frame is not None and frame.any():
        return frame

    # 回退：屏幕截取（窗口需在屏幕上可见，且不能被其他窗口遮挡关键区域）
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom),
                             all_screens=True)
        rgb = np.asarray(img)  # RGB
        if rgb.size and rgb.ndim == 3 and rgb.shape[0] > 0 and rgb.shape[1] > 0:
            return np.ascontiguousarray(rgb[:, :, ::-1])  # RGB -> BGR
    except Exception:
        pass
    return frame


# ---------------------------------------------------------------------------
# B. 棋盘增量追踪
# ---------------------------------------------------------------------------


class BoardTracker:
    """基于观测棋盘的增量追踪器。观测到的棋盘（recognizer 输出）是唯一真值。

    update(board) 返回 dict：
      mode    : "init" / "same" / "move" / "undo" / "resync"
      added   : move 模式下新子的 (x, y)，其余为 None
      last    : 追踪历史中的最后一手 (color, x, y)（可为 None）
      changed : 棋盘内容是否发生变化（same 为 False，其余 True）
      n/moves/tracked/resyncs/turn : 棋盘路数 / 历史手数 / 累计追踪手数 /
                 重新同步次数 / 当前轮次
    """

    def __init__(self, size: int = 19, komi: float = DEFAULT_KOMI):
        self.size = int(size)
        self.komi = float(komi)
        self.rules = GoRules(self.size, self.komi)
        self.resyncs = 0
        self.tracked_moves = 0
        self.last_mode = ""
        self._obs: np.ndarray | None = None
        self._has_obs = False

    # -- 内部 ---------------------------------------------------------------
    def _stones(self, board: np.ndarray):
        black, white = [], []
        n = board.shape[0]
        for y in range(n):
            for x in range(n):
                v = int(board[y, x])
                if v == 1:
                    black.append((x, y))
                elif v == 2:
                    white.append((x, y))
        return black, white

    def _setup(self, board: np.ndarray):
        """按观测盘面直接摆子同步。先行方按黑白子数猜：黑多则白先，否则黑先。"""
        black, white = self._stones(board)
        first = "W" if len(black) > len(white) else "B"
        self.rules.setup_from_stones(black, white, first=first)

    def _matches(self, board: np.ndarray) -> bool:
        """规则引擎当前盘面是否与观测盘面逐点一致。"""
        n = self.size
        if self.rules.size != n:
            return False
        for y in range(n):
            for x in range(n):
                p = self.rules.get(x, y)
                v = int(board[y, x])
                if v == 1 and p != "B":
                    return False
                if v == 2 and p != "W":
                    return False
                if v == 0 and p is not None:
                    return False
        return True

    def _last(self):
        if self.rules.history:
            c, x, y = self.rules.history[-1]
            return (c, x, y) if x >= 0 else None
        return None

    def _result(self, mode: str, changed: bool, added=None) -> dict:
        return {
            "mode": mode, "added": added, "last": self._last(),
            "changed": changed, "n": self.size,
            "moves": self.rules.move_count(), "tracked": self.tracked_moves,
            "resyncs": self.resyncs, "turn": self.rules.turn(),
        }

    # -- 主入口 ---------------------------------------------------------------
    def update(self, board) -> dict:
        board = np.asarray(board)
        n = int(board.shape[0])

        # 首帧 / 尺寸变化：摆盘同步
        if (not self._has_obs) or n != self.size:
            self.size = n
            self.rules = GoRules(n, self.komi)
            self._setup(board)
            self._obs = board.copy()
            self._has_obs = True
            self.last_mode = "init"
            return self._result("init", True)

        # 与上帧一致：最常见，直接跳过
        if np.array_equal(board, self._obs):
            return self._result("same", False)

        prev = self._obs
        new_pts, removed_pts, recolored = [], [], []
        for y in range(n):
            for x in range(n):
                a, b = int(prev[y, x]), int(board[y, x])
                if a == b:
                    continue
                if a == 0 and b != 0:
                    new_pts.append((x, y))
                elif a != 0 and b == 0:
                    removed_pts.append((x, y))
                else:
                    recolored.append((x, y))

        # 恰好 1 个空点变为新子（允许同时有提子消失点）：尝试按落子推演
        if len(new_pts) == 1 and not recolored:
            x, y = new_pts[0]
            color = "B" if int(board[y, x]) == 1 else "W"
            played = False
            try:
                self.rules.play(color, x, y)
                played = True
            except RuleError:
                played = False
            if played and self._matches(board):
                self.tracked_moves += 1
                self._obs = board.copy()
                self.last_mode = "move"
                return self._result("move", True, added=(x, y))
            if played:
                try:
                    self.rules.undo()
                except RuleError:
                    pass

        # 无新子、有消失点，且最后一手落点在其中：尝试撤销
        if not new_pts and not recolored and removed_pts:
            if self.rules.history:
                lx, ly = self.rules.history[-1][1], self.rules.history[-1][2]
                if (lx, ly) in removed_pts:
                    try:
                        self.rules.undo()
                    except RuleError:
                        pass
                    else:
                        if self._matches(board):
                            self._obs = board.copy()
                            self.last_mode = "undo"
                            return self._result("undo", True)

        # 其余（多点同变、验证失败、轮次错乱……）：重新同步
        self._setup(board)
        self.resyncs += 1
        self._obs = board.copy()
        self.last_mode = "resync"
        return self._result("resync", True)


# ---------------------------------------------------------------------------
# C. SGF 生成
# ---------------------------------------------------------------------------


def tracker_sgf(tracker: BoardTracker, black: str = "黑方",
                white: str = "白方") -> str:
    """把追踪器当前状态导出为 SGF。

    - 无重新同步且有落子历史：正常逐手谱（rules.to_sgf）；
    - 否则：摆子谱（AB/AW 直接摆当前盘面，行 a = 顶行）。
    """
    rules = tracker.rules
    if tracker.resyncs == 0 and rules.history:
        return rules.to_sgf(black, white)
    n = tracker.size
    ab, aw = [], []
    for y in range(n):
        for x in range(n):
            p = rules.get(x, y)
            if p == "B":
                ab.append(SGF_LETTERS[x] + SGF_LETTERS[y])
            elif p == "W":
                aw.append(SGF_LETTERS[x] + SGF_LETTERS[y])
    parts = ["(;GM[1]FF[4]CA[UTF-8]SZ[%d]KM[%g]" % (n, tracker.komi)]
    if ab:
        parts.append("AB" + "".join("[%s]" % s for s in ab))
    if aw:
        parts.append("AW" + "".join("[%s]" % s for s in aw))
    parts.append(")")
    return "".join(parts)


def _coord_str(x: int, y: int, n: int) -> str:
    """展示用坐标（Q16 风格：列字母 + 自下而上的行号）。"""
    try:
        return "%s%d" % (SGF_LETTERS[x].upper(), n - y)
    except Exception:
        return "(%d,%d)" % (x, y)


# ---------------------------------------------------------------------------
# D. KataGo 分析引擎（loadsgf 方案）
# ---------------------------------------------------------------------------


class KataEngine:
    """KataGo GTP 引擎封装：clear_board + loadsgf + kata-raw-nn 0。

    每次分析把追踪器当前 SGF 写入临时文件后 loadsgf，
    解析 whiteWin 与 whiteOwnership。失败静默降级（failed=True）。
    readiness 用 ready_evt 线程事件：排水线程与命令线程并发读管道会死锁，
    必须等 ready_evt 之后才允许 analyze 独占读管道（与 netplay KataPanel 相同模式）。
    """

    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()
        self.failed = False
        self.ready_evt = threading.Event()

    def start(self) -> bool:
        if self.proc is not None or self.failed:
            return self.proc is not None
        if not os.path.exists(KATAGO_EXE):
            self.failed = True
            return False
        try:
            self.proc = subprocess.Popen(
                [KATAGO_EXE, "gtp", "-model", KATAGO_MODEL, "-config", KATAGO_CFG],
                cwd=ROOT_DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                bufsize=1,
                creationflags=DETACHED_PROCESS if os.name == "nt" else 0)
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

    def analyze(self, sgf_text: str):
        """分析一个 SGF 局面。阻塞调用，须在工作线程执行。

        返回 (white_win: float, ownership: list) 或 None。
        ownership 长度 size*size，自顶向下行序，正值偏白、负值偏黑。
        """
        with self.lock:
            if self.failed:
                return None
            if self.proc is None and not self.start():
                return None
            # 等引擎就绪，确保读管道的只有本方法（避免与排水线程竞争）
            if not self.ready_evt.wait(timeout=30):
                return None
            try:
                os.makedirs(TMP_DIR, exist_ok=True)
                sgf_path = os.path.join(TMP_DIR, "analysis.sgf")
                with open(sgf_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(sgf_text)
                sgf_path = sgf_path.replace("\\", "/")
                m = re.search(r"SZ\[(\d+)\]", sgf_text)
                size = int(m.group(1)) if m else 19

                self.proc.stdin.write("clear_board\n")
                self.proc.stdin.write("loadsgf %s\n" % sgf_path)
                self.proc.stdin.write("kata-raw-nn 0\n")
                self.proc.stdin.flush()

                white_win, own, in_own = None, [], False
                deadline = time.time() + 60
                while time.time() < deadline:
                    line = self.proc.stdout.readline()
                    if not line:
                        self.failed = True
                        return None
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
                        if len(toks) == size:
                            try:
                                own.extend(float(t) for t in toks)
                            except ValueError:
                                in_own = False
                        if len(own) >= size * size:
                            break
                if white_win is None or len(own) < size * size:
                    return None
                return white_win, own[:size * size]
            except Exception:
                self.failed = True
                return None

    def stop(self):
        acquired = self.lock.acquire(timeout=2.0)
        try:
            proc = self.proc
            self.proc = None
        finally:
            if acquired:
                self.lock.release()
        if proc is not None:
            try:
                proc.stdin.write("quit\n")
                proc.stdin.flush()
            except Exception:
                pass
            try:
                proc.terminate()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# D2. Lizzieyzy 棋盘同步桥（复用其内置 ReadBoard 协议）
# ---------------------------------------------------------------------------

LIZZIE_PORT_FILE = os.path.join(THIS_DIR, "tmp", "lizzie_port.txt")
LIZZIE_VERSION = "220501"          # >= Lizzieyzy 2.5.3 的 220430 即可
LIZZIE_PROTOCOL_VERSION = 220430


class LizzieSync:
    """把追踪到的局面实时推送到 Lizzieyzy 的内置棋盘同步通道。

    协议（Lizzieyzy analysis/ReadBoard.java，行协议 UTF-8）：
      子端先发 "ready"，Lizzieyzy 回 "version"，子端回 "version <220430+>"；
      "start W H"  重开/清盘；"re=x,x,..." 每行一排（0空 1黑 2白）共 H 行；
      "end" 触发 Lizzieyzy 侧增量摆盘；"sync"/"stopsync" 开关同步模式。
    端口来源：Lizzieyzy 菜单/Alt+O 启动棋盘同步后，其 TCP 回退路径会把端口
    交给 readboard\\readboard.bat（本包已替换为转发脚本，写入 tmp/lizzie_port.txt）。
    """

    def __init__(self):
        self.sock: socket.socket | None = None
        self.port: int | None = None
        self.last_size: int | None = None
        self.lock = threading.Lock()
        self.mode = "socket"                # "socket" | "clipboard"
        self._lizzie_hwnd_cache: int | None = None
        self._last_clip_push = 0.0
        self._lz_prev: list | None = None   # 我们认为 Lizzieyzy 持有的盘面
        self._lz_turn = "B"                 # 我们认为 Lizzieyzy 的当前轮次

    # -- 端口发现 -----------------------------------------------------------
    @staticmethod
    def find_port() -> int | None:
        try:
            if os.path.isfile(LIZZIE_PORT_FILE):
                age = time.time() - os.path.getmtime(LIZZIE_PORT_FILE)
                if age < 120:  # 2 分钟内的记录才可信（每次 Alt+O 都会重写）
                    with open(LIZZIE_PORT_FILE, "r", encoding="utf-8",
                              errors="replace") as f:
                        return int(f.read().strip())
        except (OSError, ValueError):
            pass
        return None

    def port_candidates(self) -> list:
        """候选端口：端口文件优先，其后扫描 Lizzieyzy javaw 监听端口。"""
        cands = []
        port = self.find_port()
        if port:
            cands.append(port)
        for p in self._scan_ports_only():
            if p not in cands:
                cands.append(p)
        return cands

    @staticmethod
    def _scan_ports_only() -> list:
        """扫描 Lizzieyzy javaw 进程的监听端口（不探测）。"""
        if not IS_WINDOWS:
            return []
        try:
            res = subprocess.run(
                ["wmic", "process", "where", "name='javaw.exe'", "get",
                 "processid,commandline"],
                capture_output=True, timeout=15,
                creationflags=CREATE_NO_WINDOW)
            pids = set()
            for ln in res.stdout.decode("utf-8", "replace").splitlines():
                low = ln.lower()
                if "lizzie" in low and ".jar" in low:
                    tail = ln.strip().split()[-1]
                    if tail.isdigit():
                        pids.add(tail)
            if not pids:
                return []
            res = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                 capture_output=True, timeout=15,
                                 creationflags=CREATE_NO_WINDOW)
            cands = []
            for ln in res.stdout.decode("utf-8", "replace").splitlines():
                parts = ln.split()
                if len(parts) >= 5 and parts[-1] in pids \
                        and parts[3] == "LISTENING":
                    addr = parts[1]
                    if addr.startswith("127.0.0.1:") or \
                            addr.startswith("0.0.0.0:"):
                        port = int(addr.rsplit(":", 1)[1])
                        if port not in cands:
                            cands.append(port)
            return cands
        except Exception:
            return []

    @staticmethod
    def _probe(port: int) -> bool:
        """连接探测：发 ready，若收到 "version" 请求即为 ReadBoard 端口。"""
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=1.5)
            s.settimeout(2.0)
            s.sendall(b"ready\n")
            buf = b""
            try:
                while b"\n" not in buf:
                    chunk = s.recv(256)
                    if not chunk:
                        break
                    buf += chunk
            except socket.timeout:
                pass
            s.close()
            return b"version" in buf
        except OSError:
            return False

    # -- 连接与发送 ----------------------------------------------------------
    def ensure_connected(self, status=None) -> bool:
        """未连接时按候选链尝试：端口文件 → 扫描（逐个连接+协议探测）。

        status: 回调 fn(str)，用于界面提示。
        """
        with self.lock:
            if self.sock is not None:
                return True
        tried = []
        for port in self.port_candidates():
            if port in tried:
                continue
            tried.append(port)
            try:
                s = socket.create_connection(("127.0.0.1", port), timeout=2)
                s.settimeout(3)
                s.sendall(b"ready\n")
                try:
                    s.recv(256)  # Lizzieyzy 回 "version"（读取即可）
                except socket.timeout:
                    pass
                s.sendall(("version %s\n" % LIZZIE_VERSION).encode("utf-8"))
                s.sendall(b"sync\n")   # 进入同步模式并启动 Lizzieyzy 分析
                with self.lock:
                    self.sock = s
                    self.port = port
                    self.last_size = None
                    self._lz_prev = None
                    self._lz_turn = "B"
                if status:
                    status("已连接（端口 %d），棋局变化将实时推送" % port)
                return True
            except OSError as e:
                if status:
                    status("端口 %d 连接失败：%s" % (port, e))
                try:
                    s.close()
                except Exception:
                    pass
                continue
        if status:
            if not tried:
                status("未发现同步通道——请先在 Lizzieyzy 中按 Alt+O"
                       "（或菜单 同步→棋盘同步工具(Alt+O)）启动棋盘同步")
            else:
                status("连接候选端口 %s 失败，请在 Lizzieyzy 重新按 Alt+O"
                       % tried)
        return False

    def send_position(self, rules: GoRules, mode: str = "move") -> bool:
        """把当前局面整帧推送（Lizzieyzy 侧自行增量摆盘）。

        轮次对齐：Lizzieyzy 每摆一子翻转换子方，且按行序摆放，因此每帧后
        其轮次是确定的（最后一颗被摆子的颜色取反）。与追踪器推断的轮次
        （交替落子原则）不符时发一次 "pass" 翻正，保证形势判断不踩轮次错。
        结构性变化（悔棋/重同步/换色）会让 Lizzieyzy 的增量比较失效，
        此时先发 "start" 清盘整帧重摆。
        """
        with self.lock:
            if self.sock is None:
                return False
            try:
                n = rules.size
                colors = []
                for y in range(n):
                    for x in range(n):
                        p = rules.board[y * n + x]
                        colors.append("0" if p is None else
                                      ("1" if p == "B" else "2"))
                structural = (mode in ("undo", "resync")
                              or self._lz_prev is None
                              or len(self._lz_prev) != n * n)
                if not structural and self._lz_prev:
                    for i in range(n * n):
                        a, b = self._lz_prev[i], colors[i]
                        if a != "0" and b != "0" and a != b:
                            structural = True   # 换色：清盘重摆最稳
                            break
                if self.last_size != n or structural:
                    self.sock.sendall(("start %d %d\n" % (n, n))
                                      .encode("utf-8"))
                    self._lz_prev = ["0"] * (n * n)
                    self._lz_turn = "B"
                    self.last_size = n
                # Lizzieyzy 将执行的摆子（行序），用于推断其轮次
                placements = []
                for i in range(n * n):
                    if self._lz_prev[i] == "0" and colors[i] != "0":
                        placements.append("B" if colors[i] == "1" else "W")
                for y in range(n):
                    row = ",".join(colors[y * n:(y + 1) * n])
                    self.sock.sendall(("re=%s\n" % row).encode("utf-8"))
                self.sock.sendall(b"end\n")
                self._lz_prev = colors
                if placements:
                    self._lz_turn = "W" if placements[-1] == "B" else "B"
                # 交替落子原则：轮次不符则一次 pass 翻正
                if self._lz_turn != rules.turn():
                    self.sock.sendall(b"pass\n")
                    self._lz_turn = rules.turn()
                return True
            except OSError:
                try:
                    self.sock.close()
                except OSError:
                    pass
                self.sock = None
                self.port = None
                self.last_size = None
                self._lz_prev = None
                return False

    def send_pass(self) -> bool:
        """翻转 Lizzieyzy 的落子方（仅供调试/验证轮次）。"""
        with self.lock:
            if self.sock is None:
                return False
            try:
                self.sock.sendall(b"pass\n")
                self._lz_turn = "W" if self._lz_turn == "B" else "B"
                return True
            except OSError:
                return False

    def disable(self):
        """发送 stopsync 并断开（同步开关关闭/退出时）。"""
        with self.lock:
            if self.sock is not None:
                try:
                    self.sock.sendall(b"stopsync\n")
                    self.sock.close()
                except OSError:
                    pass
                self.sock = None
            self.port = None
            self.last_size = None

    # -- 剪贴板通道（零设置；每次推送整谱，Lizzieyzy 端 Ctrl+V 导入） ----------
    @staticmethod
    def _set_clipboard_text(text: str) -> bool:
        if not IS_WINDOWS:
            return False
        # 先试 ctypes 直写，失败则回退 PowerShell 文件法（Unicode 可靠）
        try:
            CF_UNICODETEXT = 13
            GMEM_MOVEABLE = 0x0002
            k = _kernel32
            k.GlobalAlloc.restype = ctypes.c_void_p
            k.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
            k.GlobalLock.restype = ctypes.c_void_p
            k.GlobalLock.argtypes = [ctypes.c_void_p]
            k.GlobalUnlock.argtypes = [ctypes.c_void_p]
            k.GlobalFree.argtypes = [ctypes.c_void_p]
            _user32.SetClipboardData.restype = ctypes.c_void_p
            _user32.SetClipboardData.argtypes = [ctypes.c_uint,
                                                 ctypes.c_void_p]
            data = text.encode("utf-16-le") + b"\x00\x00"
            # 剪贴板常被其他程序短暂占用：重试数次（ctypes 直写不弹黑框，
            # 全部失败才走 PowerShell 回退）
            for _attempt in range(6):
                if not _user32.OpenClipboard(0):
                    time.sleep(0.05)
                    continue
                try:
                    _user32.EmptyClipboard()
                    h = k.GlobalAlloc(GMEM_MOVEABLE, len(data))
                    if h:
                        p = k.GlobalLock(h)
                        if p:
                            ctypes.memmove(p, data, len(data))
                            k.GlobalUnlock(h)
                            if _user32.SetClipboardData(CF_UNICODETEXT, h):
                                return True
                        if h:
                            k.GlobalFree(h)
                finally:
                    _user32.CloseClipboard()
                break
        except Exception:
            pass
        try:
            import tempfile
            fd, tmpf = tempfile.mkstemp(suffix=".sgf", dir=THIS_DIR)
            with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as f:
                f.write(text)
            try:
                res = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Set-Clipboard -Value ([System.IO.File]::ReadAllText("
                     "'%s', [System.Text.Encoding]::UTF8))" % tmpf],
                    capture_output=True, timeout=15,
                    creationflags=CREATE_NO_WINDOW)
                return res.returncode == 0
            finally:
                try:
                    os.remove(tmpf)
                except OSError:
                    pass
        except Exception:
            return False

    def _lizzie_hwnd(self) -> int | None:
        if self._lizzie_hwnd_cache and window_alive(self._lizzie_hwnd_cache):
            return self._lizzie_hwnd_cache
        for w in list_windows():
            t = w.get("title", "")
            if "KataGo-18b" in t or "KataGo-28b" in t or t == "Lizzie":
                self._lizzie_hwnd_cache = w["hwnd"]
                return self._lizzie_hwnd_cache
        return None

    @staticmethod
    def _post_ctrl_v(hwnd) -> None:
        """可靠送达 Ctrl+V：TOPMOST+AttachThreadInput 绕过前台锁 → 真实按键。

        （PostMessage 合成键盘消息在 AWT 下不可靠，故用真实输入。）
        副作用：同步时 Lizzieyzy 会被短暂置前。
        """
        VK_CONTROL, VK_V = 0x11, 0x56
        HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
        SWP_NOMOVE_NOSIZE = 0x0003
        try:
            _user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                 SWP_NOMOVE_NOSIZE)
            fg = _user32.GetForegroundWindow()
            fg_tid = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
            my_tid = _kernel32.GetCurrentThreadId()
            attached = False
            if fg_tid and fg_tid != my_tid:
                attached = bool(_user32.AttachThreadInput(my_tid, fg_tid, 1))
            _user32.SetForegroundWindow(hwnd)
            time.sleep(0.25)
            if _user32.GetForegroundWindow() != hwnd:
                _user32.SwitchToThisWindow(hwnd, True)
                time.sleep(0.2)
            _user32.keybd_event(VK_CONTROL, 0, 0, 0)
            _user32.keybd_event(VK_V, 0, 0, 0)
            _user32.keybd_event(VK_V, 0, 2, 0)
            _user32.keybd_event(VK_CONTROL, 0, 2, 0)
            time.sleep(0.15)
            if attached:
                _user32.AttachThreadInput(my_tid, fg_tid, 0)
            _user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                                 SWP_NOMOVE_NOSIZE)
        except Exception:
            pass

    def _synth_history_sgf(self, rules: GoRules, next_turn: str) -> str:
        """把摆子局面合成为合法交替棋谱（无 AB/AW）。

        黑白按当前轮次交替落子，某色用尽时以虚着换手；末手颜色使下一轮次
        恰为 next_turn——Lizzieyzy 不解析 PL，靠手数奇偶定轮次，此法可让
        回退/重同步后的轮次也精确正确。行棋次序经论证对合法局面永不自杀、
        永不误提（每组棋的最终气在终局图为空点，合成过程中不会被填）。
        """
        n = rules.size
        b, w = [], []
        for y in range(n):
            for x in range(n):
                p = rules.board[y * n + x]
                if p == "B":
                    b.append((x, y))
                elif p == "W":
                    w.append((x, y))
        seq = []
        cur = "B"                      # 导入后从空盘轮黑开始
        ib = iw = 0
        while ib < len(b) or iw < len(w):
            if cur == "B" and ib < len(b):
                x, y = b[ib]
                ib += 1
                seq.append(";B[%s%s]" % (SGF_LETTERS[x], SGF_LETTERS[y]))
                cur = "W"
            elif cur == "W" and iw < len(w):
                x, y = w[iw]
                iw += 1
                seq.append(";W[%s%s]" % (SGF_LETTERS[x], SGF_LETTERS[y]))
                cur = "B"
            else:
                seq.append(";%s[]" % cur)   # 虚着换手
                cur = "W" if cur == "B" else "B"
        # 尾部虚着对齐轮次（二态，至多一步）
        cur_turn = "W" if (seq and seq[-1][1] == "B") else "B"
        guard = 0
        while cur_turn != next_turn and guard < 2:
            seq.append(";%s[]" % cur_turn)
            cur_turn = "W" if cur_turn == "B" else "B"
            guard += 1
        head = ("(;GM[1]FF[4]CA[UTF-8]AP[BoardWatch:1.0]SZ[%d]KM[%g]"
                "RU[Chinese]" % (n, rules.komi))
        return head + "".join(seq) + ")"

    def push(self, sgf_text: str, rules: GoRules, mode: str = "move",
             status=None) -> bool:
        """推送当前局面：socket 已连走增量协议，否则走剪贴板+Ctrl+V。"""
        with self.lock:
            if self.sock is not None:
                return self.send_position(rules, mode)
        # 剪贴板通道：限频，避免 Lizzieyzy 端导入风暴
        now = time.time()
        if now - self._last_clip_push < 2.0:
            return True  # 跳过本帧（下一变化帧会再推）
        # 摆子谱（AB/AW）没有着法序且 Lizzieyzy 不解析 PL——轮次会卡死黑。
        # 改为合成合法交替棋谱（黑白交替+虚着补位，末手颜色由目标轮次决
        # 定），轮次由手数奇偶自然正确。
        if "AB[" in sgf_text or "AW[" in sgf_text:
            sgf_text = self._synth_history_sgf(rules, rules.turn())
        hwnd = self._lizzie_hwnd()
        if not hwnd:
            if status:
                status("未找到 Lizzieyzy 窗口，请先启动 Lizzieyzy")
            return False
        if not self._set_clipboard_text(sgf_text):
            if status:
                status("剪贴板写入失败")
            return False
        self._post_ctrl_v(hwnd)
        self._last_clip_push = now
        return True


# ---------------------------------------------------------------------------
# E. tkinter GUI
# ---------------------------------------------------------------------------


class App:
    BOARD_CANVAS = 640
    MARGIN = 36.0

    MODE_NAMES = {"init": "初始化", "same": "无变化", "move": "落子",
                  "undo": "悔棋", "resync": "重新同步"}
    COLOR_NAMES = {"B": "黑", "W": "白"}

    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("1120x780")
        self.msg_q: "queue.Queue[tuple]" = queue.Queue()
        self.tracker = BoardTracker(19, DEFAULT_KOMI)
        self.engine = KataEngine()

        self.ai_on = tk.BooleanVar(value=False)
        self._ai_enabled = False            # 供工作线程读取的镜像（tk 变量非线程安全）
        self.ai_white_win = None
        self.ai_ownership = None
        self.ai_failed_notified = False

        self._windows: list = []
        self.watching = False
        self.watch_hwnd = None
        self.watch_title = ""
        self.stop_evt: threading.Event | None = None
        self._interval = 5.0
        self._w_rec_ok = None               # 工作线程内部：识别成败转换标记
        self._w_cap_ok = None               # 工作线程内部：截屏成败转换标记
        self.last_sgf_path = None
        self._thumb_photo = None
        self._restore_minimized = True      # 供工作线程读取的镜像
        self.lizzie = LizzieSync()
        self._lizzie_enabled = False
        self._lizzie_last_try = 0.0

        self._build_ui()
        # 本工具自身顶层窗口句柄（点选时用于排除）
        try:
            self._own_root = window_root(self.root.winfo_id())
        except Exception:
            self._own_root = 0
        self._poll_job = self.root.after(80, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------- 界面构建 ----------------
    def _build_ui(self):
        top = ttk.Frame(self.root, padding=6)
        top.pack(side="top", fill="x")
        ttk.Label(top, text="窗口:").pack(side="left")
        self.win_var = tk.StringVar(value="")
        self.win_combo = ttk.Combobox(top, textvariable=self.win_var,
                                      width=36, state="readonly")
        self.win_combo.pack(side="left", padx=4)
        ttk.Button(top, text="刷新窗口列表",
                   command=self.on_refresh_windows).pack(side="left", padx=2)
        ttk.Button(top, text="点选窗口",
                   command=self.on_pick_click).pack(side="left", padx=2)
        ttk.Label(top, text="间隔(秒):").pack(side="left", padx=(8, 0))
        self.interval_var = tk.StringVar(value="5")
        self.interval_spin = ttk.Spinbox(top, from_=2, to=600, width=4,
                                         textvariable=self.interval_var)
        self.interval_spin.pack(side="left", padx=2)
        self.btn_start = ttk.Button(top, text="开始盯盘", command=self.on_start_watch)
        self.btn_start.pack(side="left", padx=(10, 2))
        self.btn_stop = ttk.Button(top, text="停止", command=self.on_stop_watch,
                                   state="disabled")
        self.btn_stop.pack(side="left")
        # 第二行：功能开关（避免默认窗口宽度下溢出屏幕）
        row2 = ttk.Frame(top)
        row2.pack(fill="x", pady=(4, 0))
        ttk.Checkbutton(row2, text="AI 面板 (KataGo)", variable=self.ai_on,
                        command=self.on_toggle_ai).pack(side="left", padx=(2, 0))
        self.restore_on = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text="最小化时自动还原窗口",
                        variable=self.restore_on,
                        command=self._sync_restore_flag).pack(side="left", padx=(10, 0))
        self.lizzie_on = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="同步到 Lizzieyzy",
                        variable=self.lizzie_on,
                        command=self.on_toggle_lizzie).pack(side="left", padx=(10, 0))

        main = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        main.pack(fill="both", expand=True)
        left = ttk.Frame(main)
        left.pack(side="left", fill="y")
        right = ttk.Frame(main)
        right.pack(side="right", fill="both", expand=True, padx=(10, 0))

        self.board_cv = tk.Canvas(left, width=self.BOARD_CANVAS,
                                  height=self.BOARD_CANVAS, bg="#e8c58a",
                                  highlightthickness=0)
        self.board_cv.pack()

        ttk.Label(right, text="窗口画面", font=("", 10, "bold")).pack(anchor="w")
        self.thumb_cv = tk.Canvas(right, width=264, height=250, bg="#20242a",
                                  highlightthickness=0)
        self.thumb_cv.pack(pady=2)

        self.win_label_var = tk.StringVar(value="当前窗口: （未开始）")
        self.upd_var = tk.StringVar(value="最近更新: —")
        self.mode_var = tk.StringVar(value="模式: — · 手数 0 · 追踪 0 · 重同步 0")
        self.ai_var = tk.StringVar(value="AI: （未开启）")
        for var in (self.win_label_var, self.upd_var, self.mode_var):
            ttk.Label(right, textvariable=var).pack(anchor="w", pady=1)
        ttk.Label(right, textvariable=self.ai_var, foreground="#136").pack(
            anchor="w", pady=1)
        self.lizzie_var = tk.StringVar(value="Lizzieyzy: （未同步）")
        ttk.Label(right, textvariable=self.lizzie_var, foreground="#136").pack(
            anchor="w", pady=1)

        btn_row = ttk.Frame(right)
        btn_row.pack(anchor="w", pady=6)
        ttk.Button(btn_row, text="保存 SGF", command=self.on_save_sgf).pack(
            side="left", padx=2)
        ttk.Button(btn_row, text="在 Lizzieyzy 打开复盘",
                   command=self.on_open_lizzie).pack(side="left", padx=2)

        ttk.Label(right, text="日志", font=("", 10, "bold")).pack(
            anchor="w", pady=(4, 0))
        self.log_text = tk.Text(right, height=10, width=46, state="disabled",
                                font=("", 9))
        self.log_text.pack(fill="both", expand=True, pady=2)

        self.on_refresh_windows(first=True)
        self._draw_board()
        self._log("就绪。请选择包含棋盘的窗口，点击「开始盯盘」。")

    # ---------------- 棋盘绘制 ----------------
    def _pos(self, i: float) -> float:
        return self.MARGIN + i * self._step()

    def _step(self) -> float:
        n = max(2, self.tracker.size)
        return (self.BOARD_CANVAS - 2 * self.MARGIN) / (n - 1)

    def _draw_board(self):
        cv = self.board_cv
        if cv is None:
            return
        try:
            cv.delete("all")
        except Exception:
            return
        rules = self.tracker.rules
        n = self.tracker.size
        step = self._step()
        size = self.BOARD_CANVAS

        cv.create_rectangle(4, 4, size - 4, size - 4,
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

        # AI 归属热图（底层，>0.4 蓝=白地，<-0.4 红=黑地）
        own = self.ai_ownership
        if self._ai_enabled and own and len(own) >= n * n:
            cell = step * 0.9
            for j in range(n):
                for i in range(n):
                    v = own[j * n + i]
                    if v > 0.4:
                        cv.create_rectangle(
                            self._pos(i) - cell / 2, self._pos(j) - cell / 2,
                            self._pos(i) + cell / 2, self._pos(j) + cell / 2,
                            fill="#3a6cff", stipple="gray50", outline="")
                    elif v < -0.4:
                        cv.create_rectangle(
                            self._pos(i) - cell / 2, self._pos(j) - cell / 2,
                            self._pos(i) + cell / 2, self._pos(j) + cell / 2,
                            fill="#d0342c", stipple="gray50", outline="")

        rad = step * 0.46
        for y in range(n):
            for x in range(n):
                p = rules.get(x, y)
                if not p:
                    continue
                px, py = self._pos(x), self._pos(y)
                fill = "#141414" if p == "B" else "#f7f7f2"
                outline = "#eee" if p == "B" else "#333"
                cv.create_oval(px - rad, py - rad, px + rad, py + rad,
                               fill=fill, outline=outline)

        # 最后一手标记（重新同步后历史为空，自然无标记）
        if self.tracker.last_mode != "resync" and rules.history:
            lx, ly = rules.history[-1][1], rules.history[-1][2]
            if lx >= 0:
                mark = "#fff" if rules.history[-1][0] == "B" else "#111"
                m = rad * 0.4
                cv.create_oval(self._pos(lx) - m, self._pos(ly) - m,
                               self._pos(lx) + m, self._pos(ly) + m,
                               fill=mark, outline=mark)

    # ---------------- 窗口选择 / 盯盘控制 ----------------
    def on_refresh_windows(self, first: bool = False):
        try:
            wins = list_windows()
        except Exception:
            wins = []
        self._windows = wins
        values = [self._combo_label(w) for w in wins]
        self.win_combo["values"] = values
        if values:
            self.win_combo.current(0)
        else:
            self.win_var.set("")
        self._log("窗口列表已刷新，共 %d 个候选窗口（无标题窗口以进程名标注，"
                  "找不到目标可用「点选窗口」）。" % len(values))
        if first and not values:
            self._log("未发现候选窗口，请确认桌面会话可用后手动刷新。")

    @staticmethod
    def _combo_label(w) -> str:
        return "%s  (%dx%d)" % (w.get("label", w.get("title", ""))
                                [:56], w["rect"][2], w["rect"][3])

    def on_pick_click(self):
        """点选窗口：5 秒内单击目标窗口（可点微信小程序等枚举不到的窗口）。"""
        if self.watching:
            return
        self._log("点选窗口：请在 5 秒内单击目标窗口（直接点棋盘区域即可）。")
        self.win_label_var.set("当前窗口: 点选中…请在 5 秒内单击目标窗口")
        deadline = time.time() + 5.0
        own_warned = [False]

        def poll():
            if not self.root.winfo_exists():
                return
            if time.time() >= deadline:
                self.win_label_var.set("当前窗口: （点选超时，未捕获到点击）")
                self._log("点选超时。")
                return
            try:
                state = _user32.GetAsyncKeyState(0x01)  # VK_LBUTTON
            except Exception:
                state = 0
            if state & 0x8000:
                pt = wintypes.POINT()
                try:
                    _user32.GetCursorPos(ctypes.byref(pt))
                except Exception:
                    pt.x, pt.y = 0, 0
                picked = window_from_point(pt.x, pt.y)
                root_hwnd = window_root(picked)
                if self._own_root and root_hwnd == self._own_root:
                    if not own_warned[0]:
                        self._log("点到了本工具窗口，请去点击目标窗口。")
                        own_warned[0] = True
                    self.root.after(400, poll)
                    return
                self._add_or_select(window_root(picked) or picked)
                return
            self.root.after(50, poll)

        self.root.after(50, poll)

    def _add_or_select(self, hwnd):
        info = resolve_window(hwnd)
        if info is None:
            self._log("未能解析该窗口（可能点到了本工具或无效区域）。")
            self.win_label_var.set("当前窗口: （点选无效，请重试）")
            return
        for i, w in enumerate(self._windows):
            if w.get("hwnd") == info["hwnd"]:
                self.win_combo.current(i)
                self._log("已选中列表中的窗口：%s" % info["label"])
                self.win_label_var.set("当前窗口: %s" % info["label"][:48])
                return
        self._windows.append(info)
        values = list(self.win_combo["values"])
        values.append(self._combo_label(info))
        self.win_combo["values"] = values
        self.win_combo.current(len(values) - 1)
        self.win_label_var.set("当前窗口: %s" % info["label"][:48])
        self._log("已通过点选添加窗口：%s  %s" % (
            info["label"], info["rect"]))

    def on_start_watch(self):
        if self.watching:
            return
        idx = self.win_combo.current()
        if idx < 0 or idx >= len(self._windows):
            messagebox.showwarning(APP_TITLE, "请先选择要盯盘的窗口。")
            return
        try:
            interval = float(self.interval_var.get())
        except (TypeError, ValueError):
            interval = 5.0
        interval = max(2.0, min(600.0, interval))
        self._interval = interval
        win = self._windows[idx]
        self.watch_hwnd = win["hwnd"]
        self.watch_title = win["title"]
        self.tracker = BoardTracker(19, DEFAULT_KOMI)
        self.ai_white_win = None
        self.ai_ownership = None
        self._ai_enabled = bool(self.ai_on.get())
        self._w_rec_ok = None
        self._w_cap_ok = None
        self.stop_evt = threading.Event()
        self.watching = True
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        try:
            self.win_combo.config(state="disabled")
            self.interval_spin.config(state="disabled")
        except Exception:
            pass
        self.win_label_var.set("当前窗口: %s" % self.watch_title[:48])
        self.mode_var.set("模式: — · 手数 0 · 追踪 0 · 重同步 0")
        self._log("开始盯盘：%s（间隔 %.0f 秒）" % (self.watch_title, interval))
        threading.Thread(target=self._watch_worker, daemon=True).start()

    def on_stop_watch(self):
        if not self.watching:
            return
        self.watching = False
        if self.stop_evt is not None:
            self.stop_evt.set()
        if self._lizzie_enabled:
            self.lizzie.disable()
            self.lizzie_var.set("Lizzieyzy: 同步暂停（盯盘已停止）")
        self._reset_watch_ui()
        self._log("已停止盯盘。")

    def _reset_watch_ui(self):
        try:
            self.btn_start.config(state="normal")
            self.btn_stop.config(state="disabled")
            self.win_combo.config(state="readonly")
            self.interval_spin.config(state="normal")
        except Exception:
            pass
        self.win_label_var.set("当前窗口: （未开始）")

    def on_toggle_ai(self):
        self._ai_enabled = bool(self.ai_on.get())
        if self._ai_enabled:
            if self.engine.failed:
                self.ai_var.set("AI: 不可用（引擎启动失败，已静默降级）")
            else:
                self.ai_var.set("AI: 已开启，棋局变化后自动更新")
        else:
            self.ai_white_win = None
            self.ai_ownership = None
            self.ai_var.set("AI: （未开启）")
            self._draw_board()

    def _sync_restore_flag(self):
        self._restore_minimized = bool(self.restore_on.get())

    def on_toggle_lizzie(self):
        self._lizzie_enabled = bool(self.lizzie_on.get())
        if self._lizzie_enabled:
            self._lizzie_last_try = 0.0  # 立即允许尝试连接
            self.lizzie_var.set("Lizzieyzy: 正在寻找同步通道…")
            threading.Thread(target=self._lizzie_connect_job,
                             daemon=True).start()
        else:
            self.lizzie.disable()
            self.lizzie_var.set("Lizzieyzy: （未同步）")

    def _lizzie_connect_job(self):
        if self.lizzie.ensure_connected(
                status=lambda s: self.msg_q.put(("lizzie_status", s))):
            self.msg_q.put(("lizzie_started",))

    def _lizzie_rate_ok(self) -> bool:
        """连接尝试限速：失败后至少间隔 5 秒（扫描可能较慢）。"""
        now = time.time()
        if now - self._lizzie_last_try < 5.0:
            return False
        self._lizzie_last_try = now
        return True

    # ---------------- 盯盘工作线程 ----------------
    def _watch_worker(self):
        while self.stop_evt is not None and not self.stop_evt.is_set():
            t0 = time.time()
            try:
                self._watch_tick()
            except Exception as e:
                self.msg_q.put(("watch_error",
                                "%s: %s" % (type(e).__name__, e)))
            remain = self._interval - (time.time() - t0)
            if self.stop_evt.wait(max(0.3, remain)):
                break

    def _watch_tick(self):
        hwnd = self.watch_hwnd
        # 窗口被关闭（IsWindow=False 或枚举丢失）→ 自动停止
        if not window_alive(hwnd) or not any(
                w["hwnd"] == hwnd for w in list_windows()):
            self.msg_q.put(("window_gone",))
            self.stop_evt.set()
            return

        # 最小化的窗口截不到画面（只有标题栏残帧）；
        # 可按选项自动还原（ok-script 对最小化窗口的处理方式）
        if self._restore_minimized and window_minimized(hwnd):
            if restore_window(hwnd):
                self.msg_q.put(("watch_error",
                                "窗口已最小化，已自动还原以继续盯盘"))
                time.sleep(0.6)  # 等待窗口重绘

        frame = capture_window(hwnd)
        if frame is None or frame.size == 0:
            if self._w_cap_ok is not False:  # 只在状态转换时上报，避免刷屏
                self.msg_q.put(("capture_fail",))
            self._w_cap_ok = False
            return
        self._w_cap_ok = True

        # 缩略图（工作线程内编码）
        s = min(1.0, float(THUMB_MAX) / max(frame.shape[0], frame.shape[1]))
        ok_png, buf, corners_msg = False, None, None
        try:
            tw = max(1, int(round(frame.shape[1] * s)))
            th = max(1, int(round(frame.shape[0] * s)))
            thumb = cv2_resize(frame, (tw, th))
            ok_png, buf = thumb
        except Exception:
            ok_png, buf = False, None

        rec, err = recognize_auto(frame)
        if rec is not None and rec.corners is not None and ok_png:
            try:
                corners_msg = (np.asarray(rec.corners, dtype=float) * s).tolist()
            except Exception:
                corners_msg = None
        if ok_png and buf is not None:
            self.msg_q.put(("thumb", buf.tobytes(), corners_msg))

        if rec is None:
            if self._w_rec_ok is not False:
                self.msg_q.put(("rec_fail", err or "未找到棋盘"))
            self._w_rec_ok = False
            return
        if self._w_rec_ok is False:
            self.msg_q.put(("rec_ok",))
        self._w_rec_ok = True

        info = self.tracker.update(rec.board)
        if info.get("mode") == "same":
            return
        self.msg_q.put(("tick", info))
        # changed 才触发 AI 分析（同线程内阻塞调用，结果顺序有保证）
        if info.get("changed") and self._ai_enabled:
            sgf = tracker_sgf(self.tracker, black="黑方", white="白方")
            res = self.engine.analyze(sgf)
            if res is not None:
                self.msg_q.put(("ai", res[0], res[1]))
            elif self.engine.failed:
                self.msg_q.put(("ai_fail",))
        # 同步到 Lizzieyzy（棋面有变化才推帧）
        if info.get("changed") and self._lizzie_enabled:
            sgf_txt = tracker_sgf(self.tracker, black="黑方", white="白方")
            # 高级通道（增量协议）仅当用户在 Lizzieyzy 按 Alt+O 后存在端口
            if self.lizzie.sock is None and \
                    os.path.isfile(LIZZIE_PORT_FILE) and self._lizzie_rate_ok():
                self.lizzie.ensure_connected(
                    status=lambda s: self.msg_q.put(("lizzie_status", s)))
            if self.lizzie.push(sgf_txt, self.tracker.rules,
                                mode=info.get("mode", "move"),
                                status=lambda s: self.msg_q.put(
                                    ("lizzie_status", s))):
                self.msg_q.put(("lizzie_pushed",
                                self.tracker.rules.move_count()))

    # ---------------- 消息轮询与处理 ----------------
    def _poll(self):
        # 采样间隔实时可调（快棋/慢棋中途切换立即生效）
        try:
            v = float(self.interval_var.get())
            self._interval = max(2.0, min(600.0, v))
        except (TypeError, ValueError):
            pass
        try:
            while True:
                msg = self.msg_q.get_nowait()
                try:
                    self._handle(msg)
                except Exception:
                    traceback.print_exc()
        except queue.Empty:
            pass
        self._poll_job = self.root.after(80, self._poll)

    def _handle(self, msg):
        kind = msg[0]
        if kind == "thumb":
            self._handle_thumb(msg[1], msg[2])
        elif kind == "tick":
            self._handle_tick(msg[1])
        elif kind == "ai":
            self._handle_ai(msg[1], msg[2])
        elif kind == "ai_fail":
            if not self.ai_failed_notified:
                self.ai_failed_notified = True
                self.ai_var.set("AI: 不可用（引擎启动/分析失败，已静默降级）")
                self._log("KataGo 分析不可用（不影响盯盘）。")
        elif kind == "rec_fail":
            self._log("识别失败：%s（将自动重试）" % msg[1])
        elif kind == "rec_ok":
            self._log("棋盘识别恢复。")
        elif kind == "capture_fail":
            self._log("窗口截取失败（窗口可能已最小化或被系统保护）。")
        elif kind == "window_gone":
            self._log("被盯窗口已关闭或不可见，自动停止。")
            self.watching = False
            self._reset_watch_ui()
        elif kind == "watch_error":
            self._log("盯盘出错：%s" % msg[1])
        elif kind == "lizzie_status":
            self.lizzie_var.set("Lizzieyzy: %s" % msg[1][:60])
            self._log(msg[1])
        elif kind == "lizzie_started":
            self._log("同步通道就绪，勾选状态下棋局变化将自动推送。")
        elif kind == "lizzie_pushed":
            self.lizzie_var.set("Lizzieyzy: 已同步第 %d 手（实时分析中）"
                                % msg[1])
        elif kind == "lizzie_ok":
            self._log("已在 Lizzieyzy 打开复盘。")
        elif kind == "lizzie_fail":
            messagebox.showerror(APP_TITLE, "启动 Lizzieyzy 失败：%s" % msg[1])

    def _handle_thumb(self, png_bytes, corners):
        cv_ = self.thumb_cv
        if cv_ is None or not png_bytes:
            return
        try:
            self._thumb_photo = tk.PhotoImage(
                data=base64.b64encode(png_bytes).decode("ascii"))
        except Exception:
            return
        try:
            cv_.delete("all")
            iw = max(1, self._thumb_photo.width())
            ih = max(1, self._thumb_photo.height())
            cw = max(60, cv_.winfo_width())
            ch = max(60, cv_.winfo_height())
            ox = (cw - iw) / 2.0
            oy = (ch - ih) / 2.0
            cv_.create_image(ox, oy, anchor="nw", image=self._thumb_photo)
            if corners and len(corners) == 4:
                pts = []
                for px, py in corners:
                    pts.extend([ox + px, oy + py])
                if len(pts) == 8:
                    cv_.create_polygon(pts, fill="", outline="#00cc44", width=2)
        except Exception:
            pass

    def _handle_tick(self, info):
        mode = info.get("mode", "?")
        self._draw_board()
        self.upd_var.set("最近更新: " + time.strftime("%H:%M:%S"))
        self.mode_var.set("模式: %s · 手数 %d · 追踪 %d · 重同步 %d" % (
            self.MODE_NAMES.get(mode, mode), info.get("moves", 0),
            info.get("tracked", 0), info.get("resyncs", 0)))
        n = info.get("n", self.tracker.size)
        if mode == "move" and info.get("last"):
            c, x, y = info["last"]
            self._log("第 %d 手：%s %s" % (
                info.get("moves", 0), self.COLOR_NAMES.get(c, c),
                _coord_str(x, y, n)))
        elif mode == "undo":
            self._log("检测到撤销，回到第 %d 手。" % info.get("moves", 0))
        elif mode == "resync":
            self._log("盘面多点变化，重新同步（第 %d 次）。" % info.get("resyncs", 0))
        elif mode == "init":
            self._log("棋盘初始化（%d 路）。" % n)
        # 对局收束（谱中含双虚着）→ 显示 2027 中国规则近似判定
        if self.tracker.rules.ended and self.tracker.resyncs == 0 \
                and not getattr(self, "_judge_shown", False):
            self._judge_shown = True
            res = self.tracker.rules.judge_chinese()
            fp = res.get("first_passer")
            fp_text = ("先停手：%s" % ("黑" if fp == "B" else "白")) if fp \
                else "谱中无先停手信息"
            self.ai_var.set("2027 中国规则判定（按全部棋子为活子的近似）：\n"
                            "黑 %.1f 子 / 白 %.1f 子 → %s\n%s"
                            % (res["score_b"], res["score_w"], res["text"],
                               fp_text))
            self._log("2027 判定：%s · %s（SGF RE=%s）"
                      % (res["text"], fp_text, res["sgf_re"]))
        elif not self.tracker.rules.ended:
            self._judge_shown = False

    def _handle_ai(self, white_win, ownership):
        self.ai_white_win = white_win
        self.ai_ownership = ownership
        score = -sum(ownership) if ownership else 0.0
        self.ai_var.set("AI: 黑方胜率 %.1f%% · 目数差(黑-白) %+.1f" % (
            (1.0 - white_win) * 100.0, score))
        self._draw_board()

    # ---------------- 日志 ----------------
    def _log(self, text):
        _console_log(text)
        try:
            self.log_text.config(state="normal")
            self.log_text.insert("end", "[%s] %s\n" % (time.strftime("%H:%M:%S"),
                                                       text))
            if int(self.log_text.index("end-1c").split(".")[0]) > 400:
                self.log_text.delete("1.0", "150.0")
            self.log_text.see("end")
            self.log_text.config(state="disabled")
        except Exception:
            pass

    # ---------------- SGF / Lizzieyzy ----------------
    def _save_sgf(self):
        os.makedirs(GAMES_DIR, exist_ok=True)
        path = os.path.join(GAMES_DIR, time.strftime("%Y%m%d_%H%M%S") + ".sgf")
        text = tracker_sgf(self.tracker, black="黑方", white="白方")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        self.last_sgf_path = path
        self._log("棋谱已保存：%s" % path)
        return path

    def on_save_sgf(self):
        try:
            path = self._save_sgf()
        except Exception as e:
            messagebox.showerror(APP_TITLE, "保存棋谱失败：%s" % e)
            return
        messagebox.showinfo(APP_TITLE, "已保存：\n%s" % path)

    def on_open_lizzie(self):
        try:
            path = self._save_sgf()
        except Exception as e:
            messagebox.showerror(APP_TITLE, "保存棋谱失败：%s" % e)
            return

        def job():
            try:
                subprocess.Popen([JAVAW, "-jar", JAR_NAME, os.path.abspath(path)],
                                 cwd=ROOT_DIR, creationflags=DETACHED_PROCESS)
                self.msg_q.put(("lizzie_ok",))
            except OSError as e:
                self.msg_q.put(("lizzie_fail", str(e)))

        threading.Thread(target=job, daemon=True).start()

    # ---------------- 关闭 ----------------
    def on_close(self):
        try:
            if self._poll_job is not None:
                self.root.after_cancel(self._poll_job)
                self._poll_job = None
        except Exception:
            pass
        try:
            if self.stop_evt is not None:
                self.stop_evt.set()
        except Exception:
            pass
        try:
            self.lizzie.disable()
        except Exception:
            pass
        try:
            self.engine.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


def cv2_resize(frame, size):
    """缩略帧并编码为 PNG。独立小函数便于复用与测试。"""
    import cv2
    thumb = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
    return cv2.imencode(".png", thumb)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main() -> int:
    root = tk.Tk()
    try:
        App(root)
    except Exception:
        traceback.print_exc()
        try:
            messagebox.showerror(APP_TITLE, "界面初始化失败，详见控制台输出。")
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass
        return 1
    try:
        root.mainloop()
    except Exception:
        traceback.print_exc()
        try:
            messagebox.showerror(APP_TITLE, "界面发生错误，详见控制台输出。")
        except Exception:
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
