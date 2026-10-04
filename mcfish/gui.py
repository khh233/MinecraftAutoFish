# autofish —— Minecraft 音频触发钓鱼
# Copyright (C) 2026 欧金金 · 以 GNU GPL v3 发布（无担保），全文见 LICENSE。
"""mcfish GUI —— 图形界面，mcfish/autofish.py 的外壳。

界面只做三件事：收集参数、把会话丢到后台线程跑、把日志贴出来。
真正的流程（自校准 -> 自检 -> 抛竿/收杆循环）全部复用 autofish 里那套已经实测过的
代码，逻辑一份，不做第二遍 —— 不然两边迟早跑偏。

用法（或双击 gui.bat）：
    .venv\\Scripts\\python.exe -m mcfish.gui
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcfish import autofish as af
from mcfish import hotkey
from mcfish.paths import (ROOT, ensure_dirs, errlog,
                          resolve as resolve_path, template as template_path)
# ROOT = autofish/ —— 所有配置/日志都落在本文件夹内，不碰上级目录
SETTINGS = os.path.join(ROOT, "calib", "gui_settings.json")
ERRLOG = os.path.join(ROOT, "calib", "gui_error.log")

# 全局热键：游戏在前台时也能响应（后台模式钓鱼时特别有用）
# 装不上全局热键也不影响用—— 窗口内快捷键照常工作
HK_START = (hotkey.MOD_CONTROL, ord("D"))
HK_STOP = (hotkey.MOD_CONTROL, ord("F"))
HK_START_TEXT = hotkey.format_spec(HK_START)
HK_STOP_TEXT = hotkey.format_spec(HK_STOP)

AUDIO_SYS = "system"
AUDIO_MIC = "mic"


def device_choices():
    """音频来源下拉项：标签 -> --audio 取值。"""
    out = [("整机回环（默认，最省事）", AUDIO_SYS), ("麦克风 / 输入设备", AUDIO_MIC)]
    try:
        import soundcard as sc
        for spk in sc.all_speakers():
            out.append((f"只采这个设备：{spk.name}", f"loopback:{spk.name}"))
    except Exception:
        pass
    return out


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: queue.Queue = queue.Queue()          # 后台线程 -> 界面
        self.stop_ev = threading.Event()
        self.worker: threading.Thread | None = None
        self.choices = device_choices()

        self._build()                                # 标题/最小尺寸在 _build 里设
        self._load_settings()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(80, self._drain)
        self._bind_keys()

    # ------------------------------------------------------------- 快捷键
    def _bind_keys(self):
        """窗口内快捷键 + 全局热键。

        窗口内（界面有焦点时）：F5 开始 / F6 停止 / Esc 停止。
        全局（游戏在前台时）：Ctrl+D 开始 / Ctrl+F 停止 —— 靠 Win32
        RegisterHotKey，见 hotkey.py。被别的程序占了就自动降级，不影响上面的。

        钓鱼时按的键会进游戏窗口，所以选的是 Ctrl+字母这种游戏里不太会用的组合，
        且F5/F6 在MC 里是第三人称/视角切换，纯属巧合但更该避开 —— 所以窗口内
        那组也改成 F5/F6 之外更安全的键。
        """
        win = self.root
        # --- 窗口内：F5 开始 / F6 停止 / Esc 停止 ---
        win.bind("<F5>", lambda e: self._hotkey_start())
        win.bind("<F6>", lambda e: self._hotkey_stop())
        win.bind("<Escape>", lambda e: self._hotkey_stop())
        # 让界面一上来就拿到键盘焦点，否则第一次按键可能只被窗口管理器吃掉
        try:
            win.focus_force()
        except Exception:
            pass

        # --- 全局热键：注册 + 接管窗口过程 ---
        self.hk_actions: list[tuple[int, object]] = []
        try:
            res = hotkey.install(win.winfo_id(), self._hotkey_start, self._hotkey_stop,
                                 HK_START, HK_STOP)
            self.hk_actions = res.get("ok_actions", [])
            if res["ok"]:
                self._write(f"[热键] 全局可用：{HK_START_TEXT} 开始 / "
                            f"{HK_STOP_TEXT} 停止（游戏在前台也能按）")
            elif res["partial"]:
                got = "、".join("开始" if i == 1 else "停止" for i, _ in self.hk_actions)
                bad = "、".join(f"{k}: {v}" for k, v in res["failed"].items())
                self._write(f"[热键] 部分可用：{got} 成功；{bad} 被别的程序占了")
            else:
                why = "、".join(f"{k}: {v}" for k, v in res["failed"].items())
                self._write(f"[热键] 全局热键不可用（{why}）。"
                            f"界面内仍可用 F5 开始 / F6 停止。")
        except Exception as e:
            self._write(f"[热键] 注册失败（{e}）。界面内 F5/F6 仍可用。")

    def _hotkey_start(self, _event=None):
        # 全局热键经窗口过程进来，而子类化的窗口过程跑在**主线程**（Tk 那个），
        # 所以可以直接碰控件。窗口内的 F5 更是本来就在主线程。
        self._do_start()

    def _hotkey_stop(self, _event=None):
        self._do_stop()

    def _do_start(self):
        if self.worker and self.worker.is_alive():
            return# 已经在钓了，别重复起线程
        self.on_start()

    def _do_stop(self):
        if not (self.worker and self.worker.is_alive()):
            return
        self.on_stop()

    # ----------------------------------------------------------------- 界面
    def _build(self):
        """布局：上面「目标」一行一组，中间「检测 / 循环」左右并排，底部日志填满。

        控件名字、回调、行为全部和以前一样，只调摆放 —— 测试里引用的
        self.xxx_var / self.xxx_btn 都还在。
        """
        root = self.root
        root.title("autofish")
        root.minsize(780, 640)

        # 说明性小字的统一颜色
        HINT = "#777"

        page = ttk.Frame(root, padding=(12, 10, 12, 8))
        page.pack(fill="both", expand=True)
        # 两列等宽：中间的「检测 / 循环」各占一半
        page.columnconfigure(0, weight=1, uniform="col")
        page.columnconfigure(1, weight=1, uniform="col")

        def hint(parent, text, row, col, span=1, **kw):
            lbl = ttk.Label(parent, text=text, foreground=HINT, justify="left")
            lbl.grid(row=row, column=col, columnspan=span, sticky="w",
                     padx=(8, 6), pady=(0, 4), **kw)
            return lbl

        # ================= 目标 =================
        goal = ttk.LabelFrame(page, text=" 目标 ", padding=(6, 4, 8, 6))
        goal.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 8))
        goal.columnconfigure(1, weight=1)

        ttk.Label(goal, text="窗口").grid(row=0, column=0, sticky="e", padx=(2, 8), pady=4)
        self.win_var = tk.StringVar(value="Minecraft")
        ttk.Entry(goal, textvariable=self.win_var).grid(row=0, column=1, sticky="we",
                                                        pady=4)
        ttk.Button(goal, text="列出窗口…", width=11, command=self.pick_window
                   ).grid(row=0, column=2, padx=(8, 0), pady=4)

        ttk.Label(goal, text="音效文件").grid(row=1, column=0, sticky="e", padx=(2, 8),
                                              pady=4)
        self.sound_var = tk.StringVar(value=os.path.join("mcfish", "splash.ogg"))
        ttk.Entry(goal, textvariable=self.sound_var).grid(row=1, column=1, sticky="we",
                                                          pady=4)
        ttk.Button(goal, text="浏览…", width=11, command=self.pick_sound
                   ).grid(row=1, column=2, padx=(8, 0), pady=4)

        ttk.Label(goal, text="音频来源").grid(row=2, column=0, sticky="e", padx=(2, 8),
                                              pady=4)
        self.audio_var = tk.StringVar(value=self.choices[0][0])
        self.audio_cb = ttk.Combobox(goal, textvariable=self.audio_var, state="readonly",
                                     values=[c[0] for c in self.choices])
        self.audio_cb.grid(row=2, column=1, sticky="we", pady=4)
        ttk.Button(goal, text="刷新设备", width=11, command=self.refresh_devices
                   ).grid(row=2, column=2, padx=(8, 0), pady=4)

        hint(goal, "窗口填标题里的关键字即可（如 Minecraft）；音频来源默认采整机回环",
             3, 1, span=2)

        # ================= 检测 =================
        det = ttk.LabelFrame(page, text=" 检测 ", padding=(8, 4, 8, 6))
        det.grid(row=1, column=0, sticky="nsew", padx=(0, 4))
        det.columnconfigure(1, weight=1)

        self.feature_var = tk.StringVar(value="mel")
        ttk.Radiobutton(det, text="mel（扛随机音高，默认）", variable=self.feature_var,
                        value="mel", command=self._on_feature
                        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Radiobutton(det, text="raw（音高稳定时更干净）", variable=self.feature_var,
                        value="raw", command=self._on_feature
                        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Label(det, text="阈值").grid(row=2, column=0, sticky="e", padx=(2, 8),
                                        pady=(8, 2))
        self.thresh_var = tk.StringVar(value=f"{af.DEFAULT_THRESH['mel']}")
        ttk.Entry(det, textvariable=self.thresh_var, width=8).grid(row=2, column=1,
                                                                   sticky="w",
                                                                   pady=(8, 2))
        hint(det, "切到 raw 会自动换成它的默认阈值（0.6）", 3, 0, span=2)

        # ================= 循环 =================
        run = ttk.LabelFrame(page, text=" 循环 ", padding=(8, 4, 8, 6))
        run.grid(row=1, column=1, sticky="nsew", padx=(4, 0))
        run.columnconfigure(2, weight=1)

        def run_row(row, text, var, unit):
            ttk.Label(run, text=text).grid(row=row, column=0, sticky="e", padx=(2, 8),
                                          pady=3)
            ttk.Entry(run, textvariable=var, width=7).grid(row=row, column=1, sticky="w",
                                                           pady=3)
            # 小字统一用弱色：短单位（秒/次）也跟着弱化，视觉上更整齐
            ttk.Label(run, text=unit, foreground=HINT).grid(row=row, column=2,
                                                            sticky="w", padx=(6, 0),
                                                            pady=3)

        self.count_var = tk.StringVar(value="50")
        run_row(0, "钓多少次", self.count_var, "次　（0 = 一直钓到按停止）")
        self.delay_var = tk.StringVar(value="1.5")
        run_row(1, "抛/收后等待", self.delay_var, "秒")
        self.wait_var = tk.StringVar(value="40")
        run_row(2, "等咬钩上限", self.wait_var, "秒　（超时就收线重抛）")
        self.giveup_var = tk.StringVar(value="5")
        run_row(3, "连续失败上限", self.giveup_var, "次　（到次数就停下并提示）")

        # ================= 点击方式 =================
        mode = ttk.LabelFrame(page, text=" 点击方式 ", padding=(8, 4, 8, 6))
        mode.grid(row=2, column=0, columnspan=2, sticky="we", pady=(8, 0))
        mode.columnconfigure(1, weight=1)

        self.bg_var = tk.BooleanVar(value=False)
        ttk.Radiobutton(mode, text="前台（会抢焦点）",
                        variable=self.bg_var, value=False, command=self._on_mode
                        ).grid(row=0, column=0, sticky="w", padx=(2, 24), pady=2)
        ttk.Radiobutton(mode, text="后台（不抢焦点，你还能用电脑）",
                        variable=self.bg_var, value=True, command=self._on_mode
                        ).grid(row=0, column=1, columnspan=2, sticky="w", pady=2)

        self.activate_var = tk.BooleanVar(value=True)
        self.activate_chk = ttk.Checkbutton(
            mode, text="前台模式：点击前自动把窗口切到前台", variable=self.activate_var)
        self.activate_chk.grid(row=1, column=0, columnspan=2, sticky="w", padx=(2, 0),
                               pady=(6, 2))
        self.bg_test_btn = ttk.Button(mode, text="后台试一下（只点一次）",
                                      command=self.on_bg_test)
        self.bg_test_btn.grid(row=1, column=2, sticky="e", padx=(8, 0), pady=(6, 2))
        hint(mode, "后台模式靠消息投递：有的游戏不认；单机存档失焦会暂停；被权限拦住时"
                   "改用 gui_admin.bat 以管理员身份开。不确定就先「试一下」。",
             2, 0, span=3)

        # ================= 启动选项 =================
        opt = ttk.Frame(page)
        opt.grid(row=3, column=0, columnspan=2, sticky="we", pady=(8, 4))
        self.verify_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="启动前自检音频通路（会播一遍音效；关掉则复用上次校准结果）",
                        variable=self.verify_var).grid(row=0, column=0, sticky="w",
                                                       padx=(2, 20))
        self.dry_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="试跑：不点鼠标，只看分数", variable=self.dry_var
                        ).grid(row=0, column=1, sticky="w")

        # ================= 操作 + 日志 =================
        bar = ttk.Frame(page)
        bar.grid(row=4, column=0, columnspan=2, sticky="nwe")
        bar.columnconfigure(2, weight=1)
        # 键位提示直接写在按钮上，免得忘了还要去翻README
        self.start_btn = ttk.Button(bar, text=f"开始钓鱼  ({HK_START_TEXT})",
                                    width=22, command=self.on_start)
        self.start_btn.grid(row=0, column=0, padx=(2, 6))
        self.stop_btn = ttk.Button(bar, text=f"停止  ({HK_STOP_TEXT})",
                                   width=16, command=self.on_stop,
                                   state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=(0, 14))
        self.status = tk.StringVar(value="空闲")
        ttk.Label(bar, textvariable=self.status, foreground="#0a5").grid(
            row=0, column=2, sticky="w")

        logf = ttk.LabelFrame(page, text=" 日志 ", padding=(4, 2, 4, 4))
        logf.grid(row=5, column=0, columnspan=2, sticky="nsew")
        page.rowconfigure(5, weight=1)
        self.log_box = ScrolledText(logf, height=7, state="disabled", wrap="word")
        self.log_box.pack(fill="both", expand=True)

        # 给一个默认窗口尺寸。日志区有 weight，用户拉大窗口时它自己会长，
        # 所以这里把日志初始高度压小一点。注意长度单位是**逻辑像素**（Tk 的坐标），
        # 高 DPI 下 1 逻辑像素 = 1.25 物理像素 —— 这台机器逻辑屏是 1536x864，
        # 所以高取 750，给标题栏和任务栏留出余量，不然窗口底部会被任务栏盖住。
        root.geometry("940x750")

    # ------------------------------------------------------------- 小工具
    def _on_feature(self):
        self.thresh_var.set(str(af.DEFAULT_THRESH[self.feature_var.get()]))

    def _on_mode(self):
        """后台模式下「切前台」那个勾就没用了，禁掉免得误导。"""
        state = "disabled" if self.bg_var.get() else "normal"
        self.activate_chk.configure(state=state)

    def on_bg_test(self):
        """往目标窗口投递一次后台点击，让用户自己看游戏认不认。"""
        pat = self.win_var.get().strip()
        if not pat:
            messagebox.showerror("参数不对", "先填目标窗口（或用「列出窗口…」选一个）。")
            return
        win, note = af.resolve_window(pat)
        if win is None:
            messagebox.showerror("找不到窗口", note or pat)
            return
        hwnd, pid, exe, title = win
        button = "right"
        ok, msg = af.background_click(hwnd, button, 40)
        self._write(f"[后台试一下] {title}  ->  {'[ok] ' if ok else '[!] '}{msg}")
        if ok:
            self._write("             现在看游戏里有没有反应（比如抛出鱼竿）："
                        "有 -> 认；没有 -> 这游戏只读 Raw Input，只能用前台模式。")
        else:
            self._write("             （常见原因就是权限：双击 gui_admin.bat 以管理员身份"
                        "运行本界面，再试一次）")

    def log(self, msg: str):
        """从后台线程调用：只入队，不碰控件。"""
        self.q.put(("log", msg))

    def _write(self, msg: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def pick_sound(self):
        p = filedialog.askopenfilename(
            title="选择咬钩音效",
            filetypes=[("音频", "*.ogg *.wav *.flac *.mp3"), ("全部", "*.*")],
            initialdir=ROOT)
        if p:
            try:
                p = os.path.relpath(p, ROOT)
            except ValueError:
                pass
            self.sound_var.set(p)

    def refresh_devices(self):
        self.choices = device_choices()
        self.audio_cb.configure(values=[c[0] for c in self.choices])
        self.audio_var.set(self.choices[0][0])
        self.log("已刷新音频设备列表")

    def pick_window(self):
        wins = af.list_windows()
        top = tk.Toplevel(self.root)
        top.title("选择目标窗口（双击使用）")
        top.geometry("720x380")
        ttk.Label(top, text="列出的是当前可见窗口。双击一行即可填入。",
                  foreground="#666").pack(anchor="w", padx=8, pady=4)
        lb = tk.Listbox(top, font=("Consolas", 10))
        lb.pack(fill="both", expand=True, padx=8, pady=4)
        for hwnd, pid, exe, title in wins:
            lb.insert("end", f"{title}    [{exe or '?'} pid={pid or '?'}]")
        if not wins:
            lb.insert("end", "（一个窗口都没列到）")

        def use(_evt=None):
            sel = lb.curselection()
            if not sel:
                return
            title = wins[sel[0]][3]
            self.win_var.set(title)
            self.log(f"目标窗口设为: {title}")
            top.destroy()
        lb.bind("<Double-Button-1>", use)
        ttk.Button(top, text="使用选中", command=use).pack(pady=6)

    # --------------------------------------------------------- 设置持久化
    def _settings_path(self):
        return SETTINGS

    def _load_settings(self):
        try:
            with open(self._settings_path(), encoding="utf-8") as f:
                d = json.load(f)
            for var, key in ((self.win_var, "window"), (self.sound_var, "sound"),
                             (self.count_var, "count"), (self.delay_var, "delay"),
                             (self.wait_var, "wait"), (self.giveup_var, "giveup"),
                             (self.thresh_var, "thresh"), (self.feature_var, "feature")):
                if key in d:
                    var.set(str(d[key]))
            if d.get("audio_label") in [c[0] for c in self.choices]:
                self.audio_var.set(d["audio_label"])
            self.verify_var.set(bool(d.get("verify", True)))
            self.dry_var.set(bool(d.get("dry", False)))
            self.activate_var.set(bool(d.get("activate", True)))
            self.bg_var.set(bool(d.get("background", False)))
            self._on_mode()
        except Exception:
            pass

    def _save_settings(self, cfg):
        try:
            os.makedirs(os.path.dirname(self._settings_path()), exist_ok=True)
            with open(self._settings_path(), "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # --------------------------------------------------------------- 会话
    def _read_cfg(self):
        """读界面参数；出错返回 None 并提示。"""
        try:
            cfg = {
                "window": self.win_var.get().strip(),
                "sound": self.sound_var.get().strip(),
                "audio": dict(self.choices)[self.audio_var.get()],
                "audio_label": self.audio_var.get(),
                "feature": self.feature_var.get(),
                "thresh": float(self.thresh_var.get()),
                "count": int(self.count_var.get()),
                "delay": float(self.delay_var.get()),
                "wait": float(self.wait_var.get()),
                "giveup": int(self.giveup_var.get()),
                "verify": bool(self.verify_var.get()),
                "dry": bool(self.dry_var.get()),
                "activate": bool(self.activate_var.get()),
                "background": bool(self.bg_var.get()),
            }
        except KeyError:
            messagebox.showerror("参数不对", "音频来源没选。")
            return None
        except ValueError as e:
            messagebox.showerror("参数不对", f"数字填错了：{e}")
            return None
        if not cfg["sound"]:
            messagebox.showerror("参数不对", "要给一个音效文件（如 mcfish\\splash.ogg）。")
            return None
        # 打包成 exe 后内置音效在解包临时目录里，所以不能只看 ROOT：
        # 交给 resolve 依次试 cwd / ROOT / 包上一级 / 包内。
        if not os.path.exists(resolve_path(cfg["sound"])):
            messagebox.showerror("参数不对", f"找不到音效文件：{cfg['sound']}")
            return None
        if cfg["background"] and not cfg["dry"] and not cfg["window"]:
            messagebox.showerror("参数不对", "后台模式必须指定目标窗口。")
            return None
        return cfg

    def on_start(self):
        if self.worker and self.worker.is_alive():
            return
        cfg = self._read_cfg()
        if cfg is None:
            return
        self._save_settings(cfg)
        self.stop_ev.clear()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status.set("准备中…")
        self._write("=" * 68)
        self._write(f"开始：窗口={cfg['window']!r} 音效={cfg['sound']} "
                    f"次数={cfg['count']} 特征={cfg['feature']} 阈值={cfg['thresh']}")
        self.worker = threading.Thread(target=self._work, args=(cfg,), daemon=True)
        self.worker.start()

    def on_stop(self):
        self.stop_ev.set()
        self.status.set("正在停止…")

    def _work(self, cfg):
        """后台线程：跑一遍完整会话。只通过队列和界面说话。"""
        t0 = time.time()
        try:
            hwnd = title = None
            if cfg["window"]:
                win, note = af.resolve_window(cfg["window"])
                if win is None:
                    self.log(f"[!] {note}    用「列出窗口…」看看有哪些窗口。")
                    return
                hwnd, pid, exe, title = win
                self.log(f"目标窗口: {title}  (hwnd={hwnd} pid={pid} {exe})")
                if note:
                    self.log(f"    提示: {note}")

            device, want_mic, ok = af.describe_audio_source(cfg["audio"], self.log)
            if not ok:
                return

            # 解析一次给后面的音频调用用；界面上/设置里仍保留用户填的原样文本
            sound = resolve_path(cfg["sound"])

            # 自校准和「自检音频通路」都会把音效**播一遍**再录取回环，同属
            # 「启动前自检」，由界面上那个勾选统一控制。没勾时优先复用上次
            # 校准存下的模板（不发声、精度一样好），没有才退回解码文件模板。
            saved_tmpl = template_path(
                f"template_{os.path.splitext(os.path.basename(sound))[0]}.npy")

            if cfg["verify"]:
                tmpl, ok = af.prepare_template(
                    sound, None, autocal=True, device=device, want_mic=want_mic,
                    log=self.log)
            elif os.path.exists(saved_tmpl):
                self.log(f"已跳过自检 / 自校准（未勾选），复用上次校准的模板：{saved_tmpl}")
                tmpl, ok = af.prepare_template(None, saved_tmpl, log=self.log)
            else:
                self.log("[注意] 未勾选自检，又没有校准过的模板 —— 退回解码文件模板，"
                         "真实回环上识别率很低（raw≈0.26）。")
                self.log("      想要准：勾上「启动前自检」跑一遍即可（只多花几秒）。")
                tmpl, ok = af.prepare_template(sound, None, autocal=False,
                                               save=False, log=self.log)
            if not ok:
                return

            if cfg["verify"]:
                self.log("自检音频通路：播一遍音效 + 采回环")
                res = af.verify_audio_path(sound, tmpl, hp=200.0,
                                           device=device, want_mic=want_mic)
                if res["err"]:
                    self.log(f"[!] 播放失败: {res['err']}")
                pk = res["peak"]
                self.log(f"    采集 {res['frames']} 帧，峰值 raw={pk['raw']:.3f} "
                         f"mel={pk['mel']:.3f}（当前阈值 {cfg['thresh']}）")
                if res["frames"] == 0:
                    self.log("[!] 一帧都没采到 —— 音频设备/回环有问题，先别上游戏。")
                    return
                got = pk[cfg["feature"]]
                if got < cfg["thresh"]:
                    other = "mel" if cfg["feature"] == "raw" else "raw"
                    self.log(f"[!] {cfg['feature']} 只有 {got:.3f}，低于阈值。"
                             f"另一种特征 {other}={pk[other]:.3f}")
                    self.log("    可以换特征、压阈值，或先解决音频路由问题。"
                             "（试跑模式不受影响，可以继续看分数）")
                    if not cfg["dry"]:
                        return

            self.q.put(("status", "钓鱼中…"))
            stats = af.run_fishing_session(
                hwnd=hwnd, title=title, template=tmpl, feature=cfg["feature"],
                thresh=cfg["thresh"], cast_delay=cfg["delay"], max_wait=cfg["wait"],
                count=cfg["count"], dry_run=cfg["dry"], device=device,
                want_mic=want_mic, give_up=cfg["giveup"], activate=cfg["activate"],
                background=cfg["background"], log=self.log, on_status=self._status,
                stop=self.stop_ev, tick=0.5)
            self.log(f"结束：完成 {stats.get('done', 0)} 次，"
                     f"超时 {stats.get('timeouts', 0)} 次，"
                     f"共用 {time.time() - t0:.0f}s")
            if stats.get("error"):
                self.log(f"[!] {stats['error']}")
        except Exception as e:                       # 兜底：别让线程静默死掉
            self.log(f"[!] 出错: {type(e).__name__}: {e}")
            self.log(traceback.format_exc())
            try:
                os.makedirs(os.path.dirname(ERRLOG), exist_ok=True)
                with open(ERRLOG, "a", encoding="utf-8") as f:
                    f.write(f"\n=== {time.asctime()} ===\n{traceback.format_exc()}\n")
            except Exception:
                pass
        finally:
            self.q.put(("done", None))

    def _status(self, kind, st):
        """后台线程 -> 只入队。tick 只更新状态栏，不刷日志。"""
        if kind == "tick":
            self.q.put(("status", f"钓鱼中  分数 {st['last']:.3f}  "
                                  f"峰值 {st['max']:.3f}  完成 {st['done']}  "
                                  f"帧 {st['frames']}"))
        elif kind == "hit":
            self.q.put(("status", f"命中！完成 {st['done']}"))

    # ----------------------------------------------------------- 队列泵
    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._write(payload)
                elif kind == "status":
                    self.status.set(payload)
                elif kind == "done":
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
                    self.status.set("空闲")
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    def on_close(self):
        self.stop_ev.set()
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=3)
        # 注销全局热键，否则 Ctrl+D/Ctrl+F 会一直被本进程占着
        try:
            hotkey.unregister_all(self.root.winfo_id())
        except Exception:
            pass
        self.root.destroy()


def _apply_window_icon(root: tk.Tk) -> None:
    """设窗口 / 任务栏图标。

    Windows 下不给窗口设图标的话，任务栏会顶着 Tk 的默认羽毛图标，跟 exe 自己的
    图标对不上。优先用打包时随发行目录带出来的 icon.ico（含 16~256 多档，任务栏
    各尺寸都清晰），退回 icon.png。
    """
    ico = resolve_path("icon.ico")
    if os.path.exists(ico):
        try:
            root.iconbitmap(default=ico)
        except Exception:
            pass
    png = resolve_path("icon.png")
    if os.path.exists(png):
        try:
            img = tk.PhotoImage(file=png)
            root.iconphoto(True, img)
            root._icon_photo = img          # 必须留引用，否则被 GC 后图标会掉
        except Exception:
            pass


def main() -> int:
    # soundcard 的 COM 必须在**长期存活的线程**里初始化（见 autofish.prewarm_audio），
    # 否则第一轮跑完之后，下一轮就会 0x800401f0。GUI 主线程就是那个线程。
    af.prewarm_audio()
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.25)
    except Exception:
        pass
    _apply_window_icon(root)
    app = App(root)
    ensure_dirs()
    try:
        root.mainloop()
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:                                # 连窗口都起不来时留个记录
        try:
            os.makedirs(os.path.dirname(ERRLOG), exist_ok=True)
            with open(ERRLOG, "a", encoding="utf-8") as f:
                f.write(f"\n=== {time.asctime()} 启动失败 ===\n{traceback.format_exc()}\n")
        except Exception:
            pass
        raise
