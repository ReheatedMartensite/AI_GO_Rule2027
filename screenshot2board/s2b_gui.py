"""截图摆棋 - KataGo 便携版 (tkinter GUI).

Workflow:
  选择图片 / 粘贴截图  ->  自动识别  ->  棋盘预览上点交点纠错
  ->  (可选) 四角校准兜底  ->  保存 SGF / 在 Lizzieyzy 中打开

识别在工作线程中执行，主界面不会卡死；异常一律弹窗，不静默崩溃。
"""

from __future__ import annotations

import base64
import queue
import threading
import traceback

import cv2
import numpy as np
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import s2b_core as core
import recognizer

try:  # Pillow is optional (clipboard support)
    from PIL import ImageGrab
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

TITLE = "截图摆棋 - Lizzieyzy 工具箱"
BOARD_CANVAS = 480          # board preview canvas size (square)
SRC_CANVAS_W, SRC_CANVAS_H = 560, 480
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}

WOOD = "#dcb35c"
LINE = "#5b4626"


def star_points(n: int):
    """Star (hoshi) points as (col, row) index pairs."""
    if n == 19:
        e = [3, 9, 15]
        return [(a, b) for a in e for b in e]
    if n == 13:
        return [(3, 3), (9, 3), (3, 9), (9, 9), (6, 6)]
    if n == 9:
        return [(2, 2), (6, 2), (2, 6), (6, 6), (4, 4)]
    if n >= 7 and n % 2 == 1:
        return [(n // 2, n // 2)]
    return []


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(TITLE)
        root.minsize(1080, 640)

        # ----- state -----------------------------------------------------
        self.src_image: np.ndarray | None = None   # BGR
        self.src_stem = ""
        self.rec: recognizer.Recognition | None = None
        self.edited = False
        self.pick_mode = False          # clicking the 4 corner intersections
        self.corner_pts: list[tuple[float, float]] = []
        self.busy = False
        self.q: "queue.Queue[tuple]" = queue.Queue()
        self._src_photo = None
        self._src_view = None           # (scale, ox, oy, dw, dh)
        self._last_sgf: str | None = None
        self._worker: threading.Thread | None = None

        self._build_widgets()
        self._set_status("请选择图片文件，或直接粘贴剪贴板截图（Ctrl+V 位置的按钮）。")
        root.after(80, self._poll_queue)

    # ------------------------------------------------------------------
    # widget construction
    # ------------------------------------------------------------------
    def _build_widgets(self):
        pad = {"padx": 6, "pady": 4}

        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=8, pady=(8, 0))

        self.btn_open = ttk.Button(top, text="选择图片…", command=self.on_pick_file)
        self.btn_open.pack(side="left", **pad)

        self.btn_paste = ttk.Button(
            top, text=("粘贴截图" if HAVE_PIL else "粘贴截图（缺 PIL，不可用）"),
            command=self.on_paste, state=(tk.NORMAL if HAVE_PIL else tk.DISABLED))
        self.btn_paste.pack(side="left", **pad)

        self.btn_redo = ttk.Button(top, text="重新识别", command=self.on_recompute,
                                   state=tk.DISABLED)
        self.btn_redo.pack(side="left", **pad)

        self.btn_corners = ttk.Button(top, text="四角校准识别", command=self.on_corner_mode,
                                      state=tk.DISABLED)
        self.btn_corners.pack(side="left", **pad)

        row2 = ttk.Frame(self.root)
        row2.pack(fill="x", padx=8)

        ttk.Label(row2, text="棋盘尺寸:").pack(side="left", padx=(6, 2))
        self.size_var = tk.StringVar(value="自动")
        self.cb_size = ttk.Combobox(
            row2, textvariable=self.size_var, width=5, state="readonly",
            values=["自动", "19", "13", "9"])
        self.cb_size.pack(side="left", padx=(0, 12))

        ttk.Label(row2, text="贴目:").pack(side="left", padx=(6, 2))
        self.komi_var = tk.StringVar(value="7.5")
        ttk.Entry(row2, textvariable=self.komi_var, width=6).pack(side="left",
                                                                  padx=(0, 12))

        ttk.Label(row2, text="下一手:").pack(side="left", padx=(6, 2))
        self.player_var = tk.StringVar(value="黑 (B)")
        ttk.Combobox(row2, textvariable=self.player_var, width=6, state="readonly",
                     values=["黑 (B)", "白 (W)"]).pack(side="left", padx=(0, 12))

        ttk.Label(row2, text="规则:").pack(side="left", padx=(6, 2))
        self.rules_var = tk.StringVar(value="Chinese")
        ttk.Combobox(row2, textvariable=self.rules_var, width=8, state="readonly",
                     values=["Chinese", "Japanese", "Korean", "AGA"]).pack(
            side="left", padx=(0, 12))

        # ----- centre: board preview + source preview --------------------
        mid = ttk.Frame(self.root)
        mid.pack(fill="both", expand=True, padx=8, pady=6)

        left = ttk.LabelFrame(mid, text="识别结果（点击交点：空→黑→白→空）")
        left.pack(side="left", fill="y", padx=(0, 6))
        self.board_cv = tk.Canvas(left, width=BOARD_CANVAS, height=BOARD_CANVAS,
                                  bg="#f0f0f0", highlightthickness=0)
        self.board_cv.pack(padx=8, pady=8)
        self.board_cv.bind("<Button-1>", self.on_board_click)
        self.info_var = tk.StringVar(value="黑子: 0  白子: 0  尺寸: -  方式: -")
        ttk.Label(left, textvariable=self.info_var).pack(pady=(0, 8))

        right = ttk.LabelFrame(
            mid, text="原图预览（四角校准时在此依序点击：左上→右上→右下→左下）")
        right.pack(side="left", fill="both", expand=True)
        self.src_cv = tk.Canvas(right, width=SRC_CANVAS_W, height=SRC_CANVAS_H,
                                bg="#202020", highlightthickness=0)
        self.src_cv.pack(padx=8, pady=8, fill="both", expand=True)
        self.src_cv.bind("<Button-1>", self.on_src_click)
        self.src_cv.bind("<Button-3>", self.on_src_undo_corner)

        # ----- bottom: actions + status ----------------------------------
        bottom = ttk.Frame(self.root)
        bottom.pack(fill="x", padx=8, pady=(0, 4))
        self.btn_save = ttk.Button(bottom, text="保存 SGF", command=self.on_save,
                                   state=tk.DISABLED)
        self.btn_save.pack(side="left", padx=6)
        self.btn_lizzie = ttk.Button(bottom, text="在 Lizzieyzy 中打开",
                                     command=self.on_open_lizzie, state=tk.DISABLED)
        self.btn_lizzie.pack(side="left", padx=6)

        self.status_var = tk.StringVar(value="")
        ttk.Label(self.root, textvariable=self.status_var, relief="sunken",
                  anchor="w").pack(fill="x", side="bottom")

        self._draw_empty_board_hint()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _set_status(self, text: str):
        self.status_var.set(text)

    def _set_busy(self, busy: bool):
        self.busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        for b in (self.btn_open, self.btn_redo, self.btn_corners,
                  self.btn_save, self.btn_lizzie):
            b.config(state=state)
        if not busy and not HAVE_PIL:
            self.btn_paste.config(state=tk.DISABLED)
        elif not busy:
            self.btn_paste.config(state=tk.NORMAL)
        if busy:
            self._set_status("正在识别，请稍候…")

    def _selected_n(self):
        txt = self.size_var.get()
        return None if txt == "自动" else int(txt)

    # ------------------------------------------------------------------
    # input actions
    # ------------------------------------------------------------------
    def on_pick_file(self):
        path = filedialog.askopenfilename(
            title="选择棋盘截图",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff"),
                       ("所有文件", "*.*")])
        if not path:
            return
        try:
            image = core.load_image(path)
        except Exception as exc:
            messagebox.showerror(TITLE, "无法读取图片：\n%s" % exc)
            return
        from pathlib import Path
        self._set_source(image, Path(path).stem)
        self.start_auto_recognition()

    def on_paste(self):
        if not HAVE_PIL:
            messagebox.showinfo(TITLE, "未安装 Pillow，剪贴板功能不可用。\n"
                                "请使用「选择图片…」按钮。")
            return
        try:
            grabbed = ImageGrab.grabclipboard()
        except Exception as exc:
            messagebox.showerror(TITLE, "读取剪贴板失败：\n%s" % exc)
            return
        if grabbed is None:
            messagebox.showinfo(TITLE, "剪贴板中没有图片。")
            return
        if isinstance(grabbed, (list, tuple)):
            imgs = [p for p in grabbed
                    if str(p).lower().endswith(tuple(IMAGE_EXTS))]
            if not imgs:
                messagebox.showinfo(TITLE,
                                    "剪贴板中的文件不是图片：\n%s" % "\n".join(
                                        map(str, grabbed[:5])))
                return
            try:
                image = core.load_image(imgs[0])
            except Exception as exc:
                messagebox.showerror(TITLE, "无法读取剪贴板图片：\n%s" % exc)
                return
            from pathlib import Path
            self._set_source(image, Path(imgs[0]).stem)
        else:  # PIL.Image
            arr = np.array(grabbed.convert("RGB"))
            image = arr[:, :, ::-1].copy()  # RGB -> BGR
            self._set_source(image, "clipboard")
        self.start_auto_recognition()

    def on_recompute(self):
        if self.src_image is None:
            messagebox.showinfo(TITLE, "还没有载入图片。")
            return
        self.start_auto_recognition()

    def _set_source(self, image: np.ndarray, stem: str):
        self.src_image = image
        self.src_stem = stem or "board"
        self.rec = None
        self.edited = False
        self.corner_pts = []
        self.pick_mode = False
        self.btn_redo.config(state=tk.NORMAL)
        self.btn_corners.config(state=tk.NORMAL)
        self.btn_save.config(state=tk.DISABLED)
        self.btn_lizzie.config(state=tk.DISABLED)
        self._render_source()
        self._draw_empty_board_hint()
        self.info_var.set("黑子: 0  白子: 0  尺寸: -  方式: -")

    # ------------------------------------------------------------------
    # recognition (worker thread)
    # ------------------------------------------------------------------
    def start_auto_recognition(self):
        if self.busy or self.src_image is None:
            return
        self._set_busy(True)
        image = self.src_image
        n = self._selected_n()
        self.corner_pts = []
        self.pick_mode = False

        def job():
            try:
                rec, err = core.recognize(image, n)
                self.q.put(("rec", rec, err))
            except Exception as exc:
                self.q.put(("error", traceback.format_exc(), str(exc)))

        self._worker = threading.Thread(target=job, daemon=True)
        self._worker.start()

    def start_corner_recognition(self, pts):
        if self.busy or self.src_image is None:
            return
        self._set_busy(True)
        image = self.src_image
        n = self._selected_n() or 19  # corner mode needs an explicit size
        corners = [list(map(float, p)) for p in pts]

        def job():
            try:
                rec = recognizer.recognize_from_corners(image, corners, n)
                self.q.put(("rec", rec, ""))
            except Exception as exc:
                self.q.put(("error", traceback.format_exc(), str(exc)))

        self._worker = threading.Thread(target=job, daemon=True)
        self._worker.start()

    def _poll_queue(self):
        try:
            while True:
                msg = self.q.get_nowait()
                self._handle_msg(msg)
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    def _handle_msg(self, msg):
        kind = msg[0]
        if kind == "rec":
            _, rec, err = msg
            self._set_busy(False)
            if rec is None:
                self._set_status("自动识别失败：%s" % (err or "未知错误"))
                self.pick_mode = True
                self.corner_pts = []
                self._render_source()
                messagebox.showwarning(
                    TITLE,
                    "自动识别失败：%s\n\n"
                    "请在右侧原图上依次点击四个角部交点\n"
                    "（左上 → 右上 → 右下 → 左下）进行四角校准识别。\n"
                    "若尺寸选择为「自动」，将按 19 路处理。" % (err or "未知错误"))
                return
            self.rec = rec
            self.edited = False
            self.pick_mode = False
            self.corner_pts = []
            method = {"stones": "石子阵格", "lines": "网格线",
                      "corners": "四角校准"}.get(rec.method, rec.method)
            self._method_label = method
            self.btn_save.config(state=tk.NORMAL)
            self.btn_lizzie.config(state=tk.NORMAL)
            self._render_source()
            self._draw_board()
            self._set_status("识别完成（%s）。可点击左侧交点纠错，然后保存。"
                             % method)
        elif kind == "error":
            _, tb_text, short = msg
            self._set_busy(False)
            messagebox.showerror(TITLE, "识别过程发生异常：\n%s" % short)
            self._set_status("识别异常：%s" % short)

    # ------------------------------------------------------------------
    # source preview / corner picking
    # ------------------------------------------------------------------
    def _render_source(self):
        cv_ = self.src_cv
        cv_.delete("all")
        self._src_photo = None
        if self.src_image is None:
            cv_.create_text(SRC_CANVAS_W // 2, SRC_CANVAS_H // 2,
                            text="（尚未载入图片）", fill="#aaaaaa", font=("", 12))
            return
        cw = cv_.winfo_width()
        ch = cv_.winfo_height()
        if cw < 50:  # canvas not laid out yet (first render)
            cw = SRC_CANVAS_W
        if ch < 50:
            ch = SRC_CANVAS_H
        ih, iw = self.src_image.shape[:2]
        scale = min(cw / iw, ch / ih)
        dw, dh = max(1, int(iw * scale)), max(1, int(ih * scale))
        ox, oy = (cw - dw) // 2, (ch - dh) // 2
        disp = cv2.resize(self.src_image, (dw, dh), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".png", disp)
        if ok:
            self._src_photo = tk.PhotoImage(
                data=base64.b64encode(buf.tobytes()).decode("ascii"))
            cv_.create_image(ox, oy, anchor="nw", image=self._src_photo)
        self._src_view = (scale, ox, oy, dw, dh)
        self._draw_corner_marks()

    def _draw_corner_marks(self):
        if not self._src_view or self.src_cv is None:
            return
        scale, ox, oy, dw, dh = self._src_view
        for i, (px, py) in enumerate(self.corner_pts):
            x, y = ox + px * scale, oy + py * scale
            self.src_cv.create_oval(x - 8, y - 8, x + 8, y + 8,
                                    outline="#ffe100", width=3)
            self.src_cv.create_text(x + 14, y - 14, text=str(i + 1),
                                    fill="#ffe100", font=("", 11, "bold"))
        if self.pick_mode and len(self.corner_pts) < 4:
            self.src_cv.create_text(
                (SRC_CANVAS_W // 2, SRC_CANVAS_H - 16),
                text="四角校准：请点击第 %d/4 个角部交点（右键撤销上一点）"
                     % (len(self.corner_pts) + 1),
                fill="#ffe100", font=("", 10))
        # overlay recognized corners after success
        if (not self.pick_mode and self.rec is not None
                and self.rec.corners is not None):
            for (px, py) in np.asarray(self.rec.corners, dtype=float).reshape(-1, 2):
                x, y = ox + px * scale, oy + py * scale
                self.src_cv.create_rectangle(x - 6, y - 6, x + 6, y + 6,
                                             outline="#00e05a", width=3)

    def on_corner_mode(self):
        if self.src_image is None:
            messagebox.showinfo(TITLE, "请先选择图片或粘贴截图。")
            return
        self.pick_mode = True
        self.corner_pts = []
        self._render_source()
        n = self._selected_n() or 19
        self._set_status("四角校准：在原图上依次点击 左上→右上→右下→左下 "
                         "四个角部交点（按 %d 路识别，右键撤销）。" % n)

    def on_src_click(self, event):
        if not self.pick_mode or self._src_view is None:
            return
        scale, ox, oy, dw, dh = self._src_view
        if self.src_image is None:
            return
        ih, iw = self.src_image.shape[:2]
        px = (event.x - ox) / scale
        py = (event.y - oy) / scale
        if not (0 <= px < iw and 0 <= py < ih):
            return
        self.corner_pts.append((px, py))
        self._draw_corner_marks()
        if len(self.corner_pts) >= 4:
            self.start_corner_recognition(self.corner_pts)

    def on_src_undo_corner(self, event):
        if self.pick_mode and self.corner_pts:
            self.corner_pts.pop()
            self._render_source()

    # ------------------------------------------------------------------
    # board preview
    # ------------------------------------------------------------------
    def _draw_empty_board_hint(self):
        cv_ = self.board_cv
        cv_.delete("all")
        cv_.create_text(BOARD_CANVAS // 2, BOARD_CANVAS // 2,
                        text="（识别结果预览）", fill="#909090", font=("", 12))

    def _draw_board(self):
        cv_ = self.board_cv
        cv_.delete("all")
        if self.rec is None:
            self._draw_empty_board_hint()
            return
        n = self.rec.n
        m = 34.0
        span = BOARD_CANVAS - 2 * m
        step = span / (n - 1)

        def pos(idx):
            return m + idx * step

        cv_.create_rectangle(4, 4, BOARD_CANVAS - 4, BOARD_CANVAS - 4,
                             fill=WOOD, outline=LINE, width=2)
        for k in range(n):
            cv_.create_line(pos(k), pos(0), pos(k), pos(n - 1), fill=LINE)
            cv_.create_line(pos(0), pos(k), pos(n - 1), pos(k), fill=LINE)
        r_star = max(2, step * 0.09)
        for (c, r) in star_points(n):
            cv_.create_oval(pos(c) - r_star, pos(r) - r_star,
                            pos(c) + r_star, pos(r) + r_star,
                            fill=LINE, outline=LINE)
        rad = step * 0.46
        board = self.rec.board
        for j in range(n):
            for i in range(n):
                v = int(board[j, i])
                if not v:
                    continue
                x, y = pos(i), pos(j)
                if v == 1:
                    cv_.create_oval(x - rad, y - rad, x + rad, y + rad,
                                    fill="#101010", outline="#eeeeee")
                else:
                    cv_.create_oval(x - rad, y - rad, x + rad, y + rad,
                                    fill="#f7f7f2", outline="#333333")
        self._update_info()

    def _update_info(self):
        if self.rec is None:
            self.info_var.set("黑子: 0  白子: 0  尺寸: -  方式: -")
            return
        b, w = recognizer.board_counts(self.rec.board)
        label = getattr(self, "_method_label", self.rec.method)
        if self.edited:
            label += "，已手动修改"
        self.info_var.set("黑子: %d  白子: %d  尺寸: %d  方式: %s"
                          % (b, w, self.rec.n, label))

    def on_board_click(self, event):
        if self.rec is None or self.busy:
            return
        n = self.rec.n
        m = 34.0
        span = BOARD_CANVAS - 2 * m
        step = span / (n - 1)
        i = int(round((event.x - m) / step))
        j = int(round((event.y - m) / step))
        if not (0 <= i < n and 0 <= j < n):
            return
        if abs(event.x - (m + i * step)) > step * 0.5:
            return
        if abs(event.y - (m + j * step)) > step * 0.5:
            return
        cur = int(self.rec.board[j, i])
        self.rec.board[j, i] = (cur + 1) % 3   # 0 -> 1 -> 2 -> 0
        self.edited = True
        self._draw_board()

    # ------------------------------------------------------------------
    # saving / opening
    # ------------------------------------------------------------------
    def _sgf_params(self):
        try:
            komi = float(self.komi_var.get())
        except ValueError:
            raise ValueError("贴目必须是数字（例如 7.5）。")
        player = "B" if self.player_var.get().startswith("黑") else "W"
        rules = self.rules_var.get() or "Chinese"
        return komi, player, rules

    def _do_save(self) -> str:
        if self.rec is None:
            raise ValueError("还没有识别结果。")
        komi, player, rules = self._sgf_params()
        out = core.default_out_path(self.src_stem or "board")
        core.save_sgf(self.rec, out, komi=komi, next_player=player, rules=rules)
        self._last_sgf = str(out)
        return str(out)

    def on_save(self):
        try:
            path = self._do_save()
        except Exception as exc:
            messagebox.showerror(TITLE, "保存失败：\n%s" % exc)
            return
        self._set_status("SGF 已保存：%s" % path)
        messagebox.showinfo(TITLE, "已保存：\n%s" % path)

    def on_open_lizzie(self):
        if self.rec is None:
            messagebox.showinfo(TITLE, "还没有识别结果。")
            return
        try:
            path = self._do_save()  # always save the latest board first
        except Exception as exc:
            messagebox.showerror(TITLE, "保存失败：\n%s" % exc)
            return
        try:
            launched = core.open_in_lizzie(path)
        except Exception as exc:
            messagebox.showerror(TITLE, "启动 Lizzieyzy 失败：\n%s" % exc)
            return
        if launched:
            self._set_status("已启动 Lizzieyzy 并打开：%s" % path)
        else:
            core.open_explorer(path)
            messagebox.showinfo(
                TITLE,
                "检测到 Lizzieyzy 已在运行，无法用命令行再打开一份棋谱。\n\n"
                "SGF 已保存到：\n%s\n\n已为你打开所在文件夹，"
                "请把 SGF 文件拖拽到 Lizzieyzy 窗口即可。" % path)
            self._set_status("Lizzieyzy 已在运行，请手动拖拽：%s" % path)


def _show_fatal_error(text: str):
    """Show a fatal-error popup even when tkinter itself is unusable."""
    print(text, file=__import__("sys").stderr)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, TITLE, 0x10)
    except Exception:
        pass


def main() -> int:
    try:
        root = tk.Tk()
    except Exception as exc:
        _show_fatal_error("无法创建窗口（显示环境异常）：\n%s\n%s"
                          % (exc, traceback.format_exc()))
        return 1

    def exc_handler(exc_type, exc, tb):
        messagebox.showerror(
            TITLE, "程序内部错误：\n%s"
            % "".join(traceback.format_exception(exc_type, exc, tb))[-1500:])

    root.report_callback_exception = exc_handler
    try:
        App(root)
    except Exception:
        _show_fatal_error("界面初始化失败：\n%s" % traceback.format_exc())
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
