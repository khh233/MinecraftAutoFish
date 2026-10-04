# ---------------------------------------------------------------------------
# autofish —— Minecraft 音频触发钓鱼（GPL-3.0）
# Copyright (C) 2026 欧金金
#
# 本文件含 NTESoundTrigger 的移植/改写代码，来源：
#   https://github.com/Wattls/NTESoundTrigger   Copyright (C) 2026 Wattls (GPL-3.0)
#   上游又衍生自 https://github.com/ImLaoBJie/ZZZSoundTrigger
# 已修改（2026-10-04）：改动内容与原因见 README「许可证」一节。
#
# 本文件以 GNU GPL v3 发布，无任何担保，全文见仓库根的 LICENSE。
# ---------------------------------------------------------------------------
"""autofish - Minecraft 音频触发钓鱼。

流程（就是这条）：
    选定窗口 -> 右键抛竿 -> 听系统音频 -> 等到咬钩音 -> 右键收杆
    -> 等 --cast-delay -> 重复，共 --count 次

音效来源（二选一）：
    --sound mcfish/splash.ogg    直接把音频文件当模板，**不用录音标定**
    --template calib/template_splash.npy   用标定产出的模板（更准，但要先录一遍）

音频采集：
    --audio system                     采默认播放设备回环（默认，最省事）
    --audio "loopback:耳机 (Realtek(R) Audio)"   只采指定设备
    --audio mic                        采麦克风/输入设备

先用 --verify 确认音频通路，再上游戏：它会把这个音效播一遍、同时采集回环、
直接告诉你匹配分 —— 分数上得去才说明整条链（播放->系统混音->回环->匹配）是通的。

例子：
    python mcfish/autofish.py --list-windows
    python mcfish/autofish.py --sound mcfish/splash.ogg --window Minecraft --verify
    python mcfish/autofish.py --sound mcfish/splash.ogg --window Minecraft --count 50
    python mcfish/autofish.py --sound mcfish/splash.ogg --window Minecraft --count 0 --dry-run
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcfish.detector import (HighPass, MelDetector, ReelDetector, find_onset,
                             load_audio_file, make_template_from_audio)
from mcfish.paths import (CALIB, ensure_dirs, resolve as resolve_path,
                          template as template_path)

SR = 32000
FRAME_S = 0.08

# raw 与 mel 的可用阈值区间完全不同，默认值必须分开：
#   raw 落在 [0,1]，命中 ~0.99、纯噪声 ~0.05 —— 音高一致时极干净，但音高一变就废
#   mel 命中 0.87~0.99（音高 0.5~1.0×）、纯噪声 ≤0.30 —— 扛音高，判别力弱
# 所以 mel 取 0.80：在所有实测命中之上、噪声底之上。游戏里嫌误报就往上抬、
# 嫌漏就往下压，用 --dry-run 看实际分数最直接。
DEFAULT_THRESH = {"raw": 0.60, "mel": 0.80}


def quiet_soundcard():
    """压掉 soundcard 的 data discontinuity 警告。

    它是「这块采样不连续」的提示，回环采集下很常见（音频引擎自身的抖动），
    对我们没有可操作意义，但会刷满屏幕，把真正有用的输出挤掉。
    """
    try:
        import warnings
        from soundcard.mediafoundation import SoundcardRuntimeWarning
        warnings.filterwarnings("ignore", category=SoundcardRuntimeWarning)
    except Exception:
        pass


class NotFocused(RuntimeError):
    """目标窗口不在前台 —— 此时点击会打到别的程序上，必须停下来。"""


class ClickFailed(RuntimeError):
    """点击发不出去（窗口没了 / 后台投递失败）—— 会话应该停下。"""


# --------------------------------------------------------------------------- #
# 窗口（纯 ctypes，不依赖 pygetwindow）
# --------------------------------------------------------------------------- #
_is_win = os.name == "nt"
_user32 = ctypes.WinDLL("user32", use_last_error=True) if _is_win else None
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if _is_win else None

SW_RESTORE = 9
GA_ROOT = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WNDENUMPROC = (ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
                if _is_win else object)


def window_pid(hwnd) -> int | None:
    pid = ctypes.c_ulong()
    _user32.GetWindowThreadProcessId(ctypes.c_void_p(int(hwnd)), ctypes.byref(pid))
    return int(pid.value) or None


def window_exe(pid) -> str:
    """进程可执行文件名（javaw.exe / Minecraft.Windows.exe …）。"""
    if not pid or _kernel32 is None:
        return ""
    h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return ""
    try:
        size = ctypes.c_ulong(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        _kernel32.CloseHandle(h)


def list_windows():
    """返回 [(hwnd, pid, exe, title)]：有标题的可见顶层窗口。"""
    if _user32 is None:
        return []
    out = []

    def cb(hwnd, _lp):
        if not _user32.IsWindowVisible(hwnd):
            return True
        n = _user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        _user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value.strip()
        if title:
            out.append((int(hwnd), window_pid(hwnd), window_exe(window_pid(hwnd)), title))
        return True

    _user32.EnumWindows(_WNDENUMPROC(cb), 0)
    seen, uniq = set(), []
    for hwnd, pid, exe, title in out:
        if title in seen:
            continue
        seen.add(title)
        uniq.append((hwnd, pid, exe, title))
    return uniq


def resolve_window(pattern: str):
    """按标题子串找窗口。命中多个时优先 java/Minecraft 进程，并提示收窄。"""
    hits = [w for w in list_windows() if pattern.lower() in w[3].lower()]
    if not hits:
        return None, f"没有标题包含 '{pattern}' 的窗口"
    if len(hits) > 1:
        prefer = [w for w in hits if ("java" in w[2].lower() or "minecraft" in w[2].lower())]
        hits = prefer or hits
    return hits[0], (f"命中 {len(hits)} 个，用第一个；要收窄就换个更完整的标题"
                     if len(hits) > 1 else None)


def hwnd_by_value(text: str):
    """--hwnd 0x1a2b3c / 1715004"""
    hwnd = int(text, 0)
    title = ctypes.create_unicode_buffer(512)
    _user32.GetWindowTextW(ctypes.c_void_p(hwnd), title, 512)
    pid = window_pid(hwnd)
    return (hwnd, pid, window_exe(pid), title.value or "(无标题)")


def _root(hwnd) -> int:
    """顶层窗口句柄（对话框等子窗口归到它的根窗口上）。"""
    if not hwnd:
        return 0
    return int(_user32.GetAncestor(ctypes.c_void_p(int(hwnd)), GA_ROOT) or int(hwnd))


def foreground_is(hwnd) -> bool:
    """目标窗口（或它的根窗口）是否正处于前台。"""
    if _user32 is None or not hwnd:
        return True
    fg = _user32.GetForegroundWindow()
    if not fg:
        return False
    return _root(fg) == _root(hwnd)


def activate(hwnd) -> bool:
    """把窗口拉到前台。

    SetForegroundWindow 有一条「本进程必须收到过最后一次输入」的限制，
    直接调一般会失败；AttachThreadInput 把当前线程挂到前台线程和目标的输入
    队列上，就能绕过它。这是标准做法。
    """
    if _user32 is None or not hwnd:
        return True
    hwnd = int(hwnd)
    if not _user32.IsWindow(ctypes.c_void_p(hwnd)):
        return False
    if _user32.IsIconic(ctypes.c_void_p(hwnd)):
        _user32.ShowWindow(ctypes.c_void_p(hwnd), SW_RESTORE)

    cur_tid = _kernel32.GetCurrentThreadId()
    fg = _user32.GetForegroundWindow()
    tids = []
    for w in (fg, hwnd):
        if not w:
            continue
        tid = _user32.GetWindowThreadProcessId(ctypes.c_void_p(int(w)), None)
        if tid and tid != cur_tid and tid not in tids:
            tids.append(tid)

    attached = [t for t in tids if _user32.AttachThreadInput(cur_tid, t, True)]
    try:
        _user32.BringWindowToTop(ctypes.c_void_p(hwnd))
        _user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
        _user32.SetFocus(ctypes.c_void_p(hwnd))
    finally:
        for t in attached:
            _user32.AttachThreadInput(cur_tid, t, False)
    time.sleep(0.12)
    return foreground_is(hwnd)


class WindowGuard:
    """每次点击前确认目标窗口还在前台。

    不然后台跑着、窗口被别的程序盖住时，这些右键会原样打到前台那个程序上 ——
    在桌面上就是一堆随机的右键菜单，在别的游戏里就更糟。
    """

    def __init__(self, hwnd, title="", auto_activate=True):
        self.hwnd = hwnd
        self.title = title
        self.auto_activate = auto_activate
        self.reactivated = 0

    def ensure(self) -> bool:
        if self.hwnd is None:
            return True                      # 没指定窗口 -> 不做限制
        if foreground_is(self.hwnd):
            return True
        if not self.auto_activate:
            return False
        for _ in range(3):
            activate(self.hwnd)
            if foreground_is(self.hwnd):
                self.reactivated += 1
                return True
        return False


# --------------------------------------------------------------------------- #
# 音频采集
# --------------------------------------------------------------------------- #
def list_devices():
    import soundcard as sc
    print("播放设备（可回环采集）:")
    for s in sc.all_speakers():
        print(f"  - {s.name}")
    print("\n输入设备 / 回环端点:")
    for m in sc.all_microphones(include_loopback=True):
        print(f"  - {m.name}")


class AudioSource:
    """后台回环采集 -> 按固定帧长把单声道帧推给检测器。"""

    def __init__(self, sr=SR, frame_s=FRAME_S, device=None, hp=200.0, want_mic=False):
        self.sr = sr
        self.frame_len = int(sr * frame_s)
        self.device = device
        self.want_mic = want_mic
        self.hp = HighPass(sr, hp) if hp and hp > 0 else None
        self._stop = threading.Event()
        self._thread = None
        self._err = None
        self._last_rms = 0.0
        self.frames_seen = 0
        self.peak = 0.0

    def _open(self):
        import soundcard as sc
        if self.want_mic:
            return sc.default_microphone()
        if self.device:
            # 先按设备名精确找，再退回模糊找，最后列出可用的
            try:
                mic = sc.get_microphone(id=self.device, include_loopback=True)
            except Exception:
                mic = None
            if mic is None:
                for cand in sc.all_microphones(include_loopback=True):
                    if self.device.lower() in str(cand.name).lower():
                        mic = cand
                        break
            if mic is None:
                names = "\n".join(f"      - {m.name}" for m in
                                  sc.all_microphones(include_loopback=True))
                raise RuntimeError(f"找不到设备 '{self.device}'。可用设备:\n{names}")
            return mic
        spk = sc.default_speaker()
        return sc.get_microphone(id=str(spk.name), include_loopback=True)

    def start(self, on_frame, warmup=0.3):
        # 在**调用线程**里把 soundcard 导进来（见 prewarm_audio 的说明）：
        # 若让采集线程去导入，那个线程一结束 MTA 就没了，后面全崩。
        prewarm_audio()

        def run():
            quiet_soundcard()
            com_init()
            while not self._stop.is_set():
                try:
                    mic = self._open()
                    with mic.recorder(samplerate=self.sr, channels=2) as rec:
                        carry = np.zeros(0, dtype=np.float64)
                        # 设备刚打开那几十毫秒是毛刺（实测能把 NCC 顶到 2），
                        # 直接丢掉，别让检测器看见
                        skip = int(warmup * self.sr)
                        while not self._stop.is_set():
                            data = rec.record(numframes=max(256, self.sr // 50))
                            mono = data.mean(axis=1) if data.ndim > 1 else data
                            if skip > 0:
                                skip -= len(mono)
                                continue
                            x = self.hp.process(mono) if self.hp else mono
                            self._last_rms = float(np.sqrt(np.mean(x ** 2)) + 1e-12)
                            self.peak = max(self.peak * 0.999, self._last_rms)
                            carry = np.concatenate([carry, x])
                            while len(carry) >= self.frame_len:
                                fr = carry[: self.frame_len]
                                carry = carry[self.frame_len:]
                                self.frames_seen += 1
                                on_frame(fr)
                except Exception as e:            # 设备抖动 -> 重连
                    if self._stop.is_set():
                        break
                    self._err = e
                    # 用 wait 而不是 sleep：否则 stop() 时线程还卡在睡，设备不释放，
                    # 下一个 AudioSource 就打不开回环（实测表现为「一帧都收不到」）
                    self._stop.wait(2)
        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=6)

    @property
    def rms(self):
        return self._last_rms

    @property
    def error(self):
        return self._err


# --------------------------------------------------------------------------- #
# 播放 / 自校准 / 音频通路自检
# --------------------------------------------------------------------------- #
def com_init():
    """在每个会碰 soundcard 的子线程里先初始化 COM。

    soundcard 只在**导入它的那个线程**里调 CoInitializeEx（模块级的
    `_com = _COMLibrary()`），而 COM 是按线程算的。所以自己起的线程里再用
    soundcard 就会炸 `0x800401f0 CO_E_NOTINITIALIZED` —— 实测「播放」线程和
    「采集」线程都踩过。这里按标准做法补上（重复调用返回 S_FALSE，无害）。
    """
    if os.name != "nt":
        return
    try:
        COINIT_MULTITHREADED = 0x0
        ctypes.windll.ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
    except Exception:
        pass


def prewarm_audio():
    """在**长期存活的那个线程**（一般是主线程）里导入 soundcard。

    这是踩过的最坑的一个坑。soundcard 在模块级做 `_com = _COMLibrary()`，也就是
    在「导入它的那个线程」里调 CoInitializeEx。如果第一次导入发生在用完就退出的
    子线程（比如采集线程），那个线程一结束 MTA 就跟着没了，之后**任何**线程里的
    COM 调用都会抛 `0x800401f0 CO_E_NOTINITIALIZED`。实测症状是：
    第一次采集成功 -> 子线程退出 -> 之后播放失败 + 采集一帧都收不到。

    所以：导入必须发生在一个活着的线程里；同时每个自己起的线程再显式 com_init()
    补一次（重复初始化返回 S_FALSE，无害）。
    """
    try:
        import soundcard  # noqa: F401  —— 只为让 _COMLibrary 在这一线程初始化
    except Exception:
        return False
    quiet_soundcard()
    com_init()
    return True


def play_once(audio, sr=SR, file_sr=None, device_name=None, gain=0.7, drain=0.2):
    """在**调用线程**里把 audio 播完，返回实际用到的采样率。

    优先按素材原始采样率播 —— 那才是游戏实际走的那条路（OS 混音器自己重采样），
    设备不接受再退回 sr。

    刻意不开子线程：soundcard 的 COM 是按线程算的（模块级
    `_com = _COMLibrary()` 只在导入它的那个线程里 CoInitializeEx），跨线程创建
    播放器实测会抛 `0x800401f0`。在调用线程里播就绕开了这个问题，而采集本来就
    在后台线程跑，主线程播两秒音效没有任何影响。

    soundcard 的 play() 把数据排进缓冲就返回，紧接着 __exit__ 就 Stop()，
    最后一段会被切掉；所以按 drain 等一下把尾巴放完。
    """
    import soundcard as sc
    prewarm_audio()
    a = np.asarray(audio, dtype=np.float64)
    a = a - a.mean()
    a = (a / max(1e-9, float(np.abs(a).max())) * gain).astype(np.float32)

    spk = sc.default_speaker()
    if device_name:
        for cand in sc.all_speakers():
            if device_name.lower() in str(cand.name).lower():
                spk = cand
                break
        else:
            raise RuntimeError(f"找不到播放设备 '{device_name}'")

    last = None
    for rate in (int(file_sr or sr), sr):
        try:
            with spk.player(samplerate=rate, channels=1) as p:
                p.play(a)
                time.sleep(drain)
            return rate
        except Exception as e:
            import traceback
            last = RuntimeError(f"播放失败（{rate} Hz）: {e}\n{traceback.format_exc()}")
            if rate == sr:
                break
    raise last


def autocal_template(sound_path, sr=SR, hp=200.0, length=0.35, device=None,
                     want_mic=False, play_device=None, gain=0.7, settle=0.4, pad=0.8):
    """播一遍音效 -> 录回环 -> 从**录音**里截模板。返回 (template|None, 说明, 播放错误)。

    这一步不是可选的优化，是必须的。实测（见 NOTES.md）：

        解码文件当模板        打真实回环 raw = 0.259
        同一段录音当自己的模板  打真实回环 raw = 1.000

    设备混音、重采样、以及 Realtek 那类「音效增强」都会改变波形，而波形互相关
    对这个级别的改变极其敏感。模板从录音里来，模板和实时信号就走同一条路，
    那点染色在两边同时出现、自动抵消。
    """
    if not os.path.exists(sound_path):
        return None, f"找不到 {sound_path}", None
    # 播放用**原始采样率**的素材（别拿重采样到 32k 的数组按 11025 播）
    play_audio, file_sr = load_audio_file(sound_path, None)
    frames = []
    src = AudioSource(sr=sr, frame_s=FRAME_S, device=device, hp=hp, want_mic=want_mic)
    src.start(lambda fr: frames.append(fr))
    time.sleep(settle)
    perr = None
    try:
        play_once(play_audio, sr, file_sr, play_device, gain)
    except Exception as e:
        perr = e
    time.sleep(pad)
    src.stop()

    if not frames:
        return None, "一帧都没采到（设备打不开 / 没有回环端点）", perr
    audio = np.concatenate(frames)
    peak = float(np.abs(audio).max())
    if peak < 1e-4:
        return None, (f"录音几乎是静音（peak={peak:.2e}）—— 播放没走到采集的那个设备，"
                      f"或者采集设备选错了"), perr
    o = find_onset(audio, sr)
    n = int(round(length * sr))
    seg = audio[o:o + n]
    if len(seg) < n:
        seg = np.pad(seg, (0, n - len(seg)))
    seg = seg - seg.mean()
    return seg, f"起振 {o / sr:.3f}s  peak {peak:.4f}", perr


def verify_audio_path(sound_path, template, hp, device=None, want_mic=False,
                      play_device=None, sr=SR, gain=0.7, settle=0.4, pad=0.8):
    """把音效播一遍，同时采回环，**两种特征都打分** —— 验证整条音频通路。

    这是「能不能用」的分界线：分数上得去，说明
    [播放 -> 系统混音 -> 回环采集 -> 模板匹配] 全通；上不去就是设备路由 /
    独占模式 / 采样率的问题，先在系统里解决，别急着上游戏。
    """
    raw, file_sr = load_audio_file(resolve_path(sound_path), None)  # 播放用原始采样率
    state = {"frames": 0}
    peaks = {"raw": 0.0, "mel": 0.0}
    # 阈值设成 1.1（不可能达到）—— 这一步只要峰值，不要触发
    dets = {f: build_detector(template, f, 1.1, 1.0, None) for f in ("raw", "mel")}
    src = AudioSource(sr=sr, frame_s=FRAME_S, device=device, hp=hp, want_mic=want_mic)

    def on_frame(fr):
        state["frames"] += 1
        for f, d in dets.items():
            s = d.feed(fr)
            if s > peaks[f]:
                peaks[f] = s

    src.start(on_frame)
    time.sleep(settle)
    err = played_sr = None
    try:
        played_sr = play_once(raw, sr, file_sr, play_device, gain)
    except Exception as e:
        err = e
    time.sleep(pad)
    src.stop()
    return {"peak": peaks, "frames": state["frames"], "err": err,
            "file_sr": file_sr, "played_sr": played_sr,
            "src_err": src.error}


def build_detector(template, feature, thresh, cooldown, on_fire):
    """按 --feature 造检测器。两个检测器的 feed/armed/last_score 接口一致。"""
    kw = dict(sr=SR, frame=FRAME_S, thresh=thresh, cooldown=cooldown, on_fire=on_fire)
    if feature == "mel":
        return MelDetector(template, **kw)
    return ReelDetector(template, **kw)


# --------------------------------------------------------------------------- #
# 鼠标
# --------------------------------------------------------------------------- #
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
MK_LBUTTON, MK_RBUTTON = 0x0001, 0x0002

if _user32 is not None:
    # 显式声明，别让 ctypes 按 c_int 猜：64 位下 WPARAM/LPARAM 是 8 字节
    _user32.PostMessageW.restype = ctypes.c_bool
    _user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                     ctypes.c_size_t, ctypes.c_ssize_t]


def _post_error_hint(err: int) -> str:
    """把 PostMessage 的错误码翻成人话。错误码 5 是最坑的那个。"""
    if err == 5:
        return ("投递被拒绝（错误码 5 = ERROR_ACCESS_DENIED）—— 这是 **UIPI**："
                "目标进程的完整性级别比我们高，Windows 内核会拦掉低级别进程发过去的"
                "鼠标/键盘输入消息（WM_NULL 之类不受限，所以窗口看着一切正常，"
                "很容易误判成「游戏不认」）。\n"
                "        两个办法：\n"
                "          ① 以管理员身份运行（推荐）：右键 gui.bat -> 以管理员身份运行\n"
                "             （管理员是 High，高于游戏的 Medium，就能发了）\n"
                "        ② 改用前台模式：去掉后台勾选/不加 --background\n"
                "        核对两边的权限级别：任务管理器 -> 详细信息 -> 看「管理员」列")
    if err == 1400:
        return "投递失败：窗口句柄已失效（ERROR_INVALID_WINDOW_HANDLE）"
    if err == 1448:
        return "投递失败：需要交互式窗口站（ERROR_REQUIRES_INTERACTIVE_WINDOWSTATION）"
    if err:
        return f"投递失败，Win32 错误码 {err}"
    return "投递失败（窗口可能真的没响应）"


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def client_center(hwnd):
    """客户区中心点。PostMessage 要的是**客户区**坐标，不是屏幕坐标。"""
    r = _RECT()
    if not _user32 or not _user32.GetClientRect(ctypes.c_void_p(int(hwnd)),
                                                ctypes.byref(r)):
        return 0, 0
    return (r.right - r.left) // 2, (r.bottom - r.top) // 2


def background_click(hwnd, button="right", hold_ms=40):
    """**后台点击**：把一次按键直接投递给窗口，不抢焦点、不动真实鼠标。

    和前台模式的区别（这是「后台模式」的全部意义）：
      前台 = pydirectinput（SendInput）-> 进系统输入队列 -> 落到**当前焦点窗口**。
             所以必须先把游戏切前台，而且钓鱼期间你不能用电脑。
      后台 = PostMessage(WM_*BUTTONDOWN/UP) 直接投给指定窗口 -> 不抢焦点、不动鼠标，
             游戏窗口可以盖住，你能同时干别的。

    投递成功 ≠ 游戏一定响应：有的游戏只读 Raw Input，完全不理会消息投递。
    所以务必先在游戏里确认一次（`--background-test` 或 GUI 的「后台试一下」）。
    Minecraft 是 GLFW，按钮走 WM_*BUTTONDOWN 的 WndProc，一般认；但**单机存档
    一失焦就暂停**（多人/局域网不会），所以后台模式基本只适用于多人游戏。

    返回 (是否投递成功, 说明)。
    """
    if _user32 is None:
        return False, "非 Windows，后台模式不可用"
    hwnd = int(hwnd)
    if not _user32.IsWindow(ctypes.c_void_p(hwnd)):
        return False, "窗口已不存在（游戏退出了？）"
    down, up, mk = ((WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON) if button == "right"
                    else (WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON))
    x, y = client_center(hwnd)
    lp = (y << 16) | (x & 0xFFFF)
    # 先送一个 WM_MOUSEMOVE：不少游戏靠它更新内部光标位置，否则会按上一次的位置算
    ctypes.set_last_error(0)
    _user32.PostMessageW(ctypes.c_void_p(hwnd), WM_MOUSEMOVE, 0, lp)
    ctypes.set_last_error(0)
    ok1 = _user32.PostMessageW(ctypes.c_void_p(hwnd), down, mk, lp)
    err = ctypes.get_last_error()
    time.sleep(max(0.005, hold_ms / 1000.0))
    ctypes.set_last_error(0)
    ok2 = _user32.PostMessageW(ctypes.c_void_p(hwnd), up, 0, lp)
    if not err:
        err = ctypes.get_last_error()
    if not (ok1 and ok2):
        return False, _post_error_hint(err)
    return True, f"已投递{button}键（客户区 {x},{y}，按住 {hold_ms}ms）"


def make_clicker(button="right", hold_ms=12, guard=None, hwnd=None, background=False,
                 bg_hold_ms=40):
    if background:
        if hwnd is None:
            return None, "后台模式需要指定目标窗口（--window 或 --hwnd）。"

        def click_bg():
            ok, msg = background_click(hwnd, button, bg_hold_ms)
            if not ok:
                raise ClickFailed(f"后台点击失败：{msg}")
        return click_bg, None

    try:
        import pydirectinput
    except Exception:
        return None, "pydirectinput 未安装（pip install pydirectinput）"
    pydirectinput.PAUSE = 0
    pydirectinput.FAILSAFE = False

    def click():
        if guard is not None and not guard.ensure():
            raise NotFocused(
                f"目标窗口不在前台，而 --no-activate 又禁止我自动切过去："
                f"{guard.title!r}。请手动点一下游戏窗口再重跑，"
                f"或者用后台模式（--background）。")
        pydirectinput.mouseDown(button=button)
        time.sleep(hold_ms / 1000.0)
        pydirectinput.mouseUp(button=button)
    return click, None


def _sleep(seconds: float, stop: threading.Event | None = None) -> bool:
    """可被 stop 打断的 sleep。返回 False 表示已被要求停止。

    直接 time.sleep() 会让 Ctrl+C 等满整个 --cast-delay，很难受。
    """
    end = time.time() + seconds
    while time.time() < end:
        if stop is not None and stop.is_set():
            return False
        time.sleep(min(0.05, max(0.001, end - time.time())))
    return not (stop is not None and stop.is_set())


# --------------------------------------------------------------------------- #
# 钓鱼主循环（与音频来源解耦，所以能用合成音频测）
# --------------------------------------------------------------------------- #
def run_session(template, feed_frames, click, *, count=10, thresh=None, cooldown=0.5,
                cast_delay=1.5, max_wait=40.0, no_cast=False, feature="raw",
                on_status=None, stop=None, tick=0.5, give_up=5):
    """抛竿 -> 等咬钩音 -> 收杆 -> 等 cast_delay -> 重复，共 count 次。

    count<=0 表示一直钓到 Ctrl+C。feed_frames(on_frame) 必须在后台开始投递
    32 kHz 单声道帧。返回 stats 字典。

    `count` 数的是**成功的收杆次数**，不是轮数 —— 漏一次不算数，会重抛再等，这才是
    钓鱼该有的行为。但连着 `give_up` 轮都听不到咬钩音就得停：那说明音频通路或阈值
    不对（比如设备采不到游戏声音），继续转下去只会一直空抛，还让人以为在工作。
    give_up=0 表示不放弃。
    """
    if thresh is None:
        thresh = DEFAULT_THRESH[feature]
    hit = threading.Event()
    stats = {"frames": 0, "last": 0.0, "max": 0.0, "done": 0, "timeouts": 0,
             "streak": 0, "error": None, "feature": feature, "thresh": thresh}
    det = build_detector(template, feature, thresh, cooldown, lambda s: hit.set())

    def on_frame(fr):
        det.feed(fr)
        stats["frames"] += 1
        stats["last"] = det.last_score
        if det.last_score > stats["max"]:
            stats["max"] = det.last_score

    feed_frames(on_frame)

    def stopped():
        return stop is not None and stop.is_set()

    def do_click(why) -> bool:
        """点一下并等 cast_delay。返回 False 表示该结束会话了。"""
        try:
            click()
        except (NotFocused, ClickFailed) as e:
            stats["error"] = str(e)
            if on_status:
                on_status("unfocused", stats)
            return False
        if on_status:
            on_status(why, stats)
        return _sleep(cast_delay, stop) if why != "recover" else True

    infinite = count is None or count <= 0
    while (infinite or stats["done"] < count) and not stopped():
        if not no_cast:
            if not do_click("cast"):
                break

        hit.clear()
        t0 = time.time()
        timed_out = False
        last_tick = t0
        while not hit.is_set():
            if stopped():
                break
            now = time.time()
            if now - t0 > max_wait:
                timed_out = True
                break
            if on_status and tick and now - last_tick >= tick:
                last_tick = now
                on_status("tick", stats)
            if not _sleep(0.02, stop):
                break

        if stopped():
            break
        if hit.is_set():
            if not do_click("hit"):
                break
            stats["done"] += 1
            stats["max"] = 0.0
            stats["streak"] = 0
        elif timed_out:
            # 超时说明线还在水里。**必须先收线**：不收的话下一轮的「抛竿」右键
            # 实际是在收线，状态就错位了 —— 之后每一轮都会白等一整个 max_wait。
            stats["timeouts"] += 1
            stats["streak"] += 1
            stats["max"] = 0.0
            if on_status:
                on_status("timeout", stats)
            if give_up and stats["streak"] >= give_up:
                stats["error"] = (
                    f"连着 {stats['streak']} 轮没听到咬钩音（每轮等 {max_wait:g}s），"
                    f"先停下 —— 这通常不是运气问题，而是音频通路或阈值不对。"
                    f"跑一下 --verify 看通路分，再用 --dry-run 看实际分数。")
                if on_status:
                    on_status("giveup", stats)
                break
            if not no_cast:
                try:
                    click()
                except (NotFocused, ClickFailed) as e:
                    stats["error"] = str(e)
                    if on_status:
                        on_status("unfocused", stats)
                    break
                if on_status:
                    on_status("recover", stats)
                if not _sleep(cast_delay, stop):
                    break
    return stats


# --------------------------------------------------------------------------- #
# 会话流程（CLI 与 GUI 共用同一套代码）
# --------------------------------------------------------------------------- #
def describe_audio_source(audio, log=print):
    """把 --audio 的说法翻成 (device, want_mic, ok)。"""
    if audio == "system":
        try:
            import soundcard as sc
            dev = str(sc.default_speaker().name)
        except Exception as e:
            log(f"[!] 拿不到默认播放设备: {e}")
            return None, False, False
        log(f"音频: 整机回环（{dev}）")
        return dev, False, True
    if audio == "mic":
        log("音频: 麦克风/输入设备")
        return None, True, True
    if audio == "process":
        log("[!] soundcard 做不了「进程级」回环（那需要另一套 WASAPI 接口），")
        log("    所以无法只采某一个窗口的声音。两种可行做法：")
        log("      1) 直接采整机（最省事，钓鱼时别放音乐就行）")
        log("      2) 把游戏输出改到独立设备，再只采它：")
        log("         设置 > 系统 > 声音 > 音量合成器 -> 游戏 -> 输出设备改为某虚拟声卡")
        log("         然后用 --audio \"loopback:<设备名>\"")
        return None, False, False
    if audio.startswith("loopback:"):
        dev = audio.split(":", 1)[1].strip()
        log(f"音频: 指定设备回环（{dev}）")
        return dev, False, True
    log(f"音频: 指定设备回环（{audio}）")
    return audio, False, True


def prepare_template(sound=None, template=None, *, sr=SR, hp=200.0, length=0.35,
                     autocal=True, device=None, want_mic=False, play_device=None,
                     save_to=None, save=True, log=print):
    """做出检测模板，返回 (template | None, ok)。

    `sound` 走**自校准**（播一遍 -> 录回环 -> 从录音里截模板），这是实测必需的一步：
    解码文件的模板打真实回环只有 raw≈0.259，同一段录音当模板是 1.000。
    `autocal=False` 会退回解码文件模板（只适合先看看分数）。
    `template` 直接给 .npy 时优先用它（可跳过自校准）。

    `save=False` 时不写回模板文件 —— 用于「关闭自校准又想避开解码文件模板」的场合，
    免得把上次校准存下的好模板覆盖掉。

    **自校准请求了但失败**时也不写回：那时手上只剩兜底的「解码文件」模板
    （真实回环上 raw≈0.26，游戏里基本认不出来），存下去只会把上次校准出的好模板
    覆盖掉，而且日志还会误报「下次可直接用模板文件跳过自校准」。
    """
    tmpl = None
    calibrated = True          # 手上的模板值不值得落盘（见上面 docstring）
    if sound:
        # 相对路径不能只看 cwd：打包成 exe 后 cwd 可能是任意位置，
        # 内置音效则在解包临时目录里。交给 paths.resolve 逐个位置试。
        sound = resolve_path(sound)
        if not os.path.exists(sound):
            log(f"[!] 找不到音效文件 {sound}")
            return None, False
        try:
            tmpl, info = make_template_from_audio(sound, sr=sr, length=length, hp=hp)
        except Exception as e:
            log(f"[!] 做模板失败: {e}")
            return None, False
        log(f"音效: {sound}")
        log(f"      {info['file_sr']} Hz / {info['src_dur']:.3f} s -> 截 "
            f"{info['len_s']:.3f} s（起振 {info['onset']:.3f} s），RMS {info['rms']:.4f}")

        if autocal:
            log("自校准：把这个音效播一遍、录回环，用**录音**当模板")
            log("      （现在就播，请确保此刻没有别的声音在响）")
            seg, msg, perr = autocal_template(sound, sr=sr, hp=hp, length=length,
                                             device=device, want_mic=want_mic,
                                             play_device=play_device)
            if seg is not None:
                tmpl = seg
                log(f"      [ok] {msg}  ->  模板 {len(seg)} 采样")
            else:
                calibrated = False
                log(f"      [!] 自校准失败：{msg}")
                if perr:
                    log(f"          播放报错: {perr}")
                log("          退回「解码文件」模板。实测它在真实回环上只能到 raw≈0.26，")
                log("          游戏里大概率认不出来 —— 先把音频通路修好再上。")
        else:
            log("      [注意] 已关闭自校准，用的是解码文件模板（真实回环上只有 raw≈0.26）")

        if not save:
            log("      （本次不写回模板文件）")
            return tmpl, True

        if not calibrated:
            # 别用兜底模板覆盖上次校准存下的好模板（也不该提示「下次可跳过自校准」）
            log("      （自校准没成功，不写回模板文件 —— 免得覆盖掉上次校准存下的好模板）")
            return tmpl, True

        out = save_to or template_path(f"template_{os.path.splitext(os.path.basename(sound))[0]}.npy")
        try:
            os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
            np.save(out, tmpl.astype(np.float32))
            log(f"      已存 {out}（下次可直接用「模板文件」跳过自校准）")
        except Exception as e:
            log(f"      [warn] 模板没存成: {e}")
        return tmpl, True

    if template:
        if not os.path.exists(template):
            log(f"[!] 找不到模板 {template}")
            return None, False
        tmpl = np.load(template).astype(np.float64)
        log(f"模板: {template}  {len(tmpl)} 采样 ({len(tmpl)/sr:.3f} s)")
        return tmpl, True

    log("[!] 需要「音效文件」或「模板文件」之一。")
    return None, False


def run_fishing_session(*, hwnd=None, title=None, template, feature="raw", thresh=None,
                        cooldown=0.5, cast_delay=1.5, max_wait=40.0, count=10,
                        no_cast=False, button="right", dry_run=False, device=None,
                        want_mic=False, hp=200.0, give_up=5, activate=True,
                        background=False, bg_hold_ms=40, feed_frames=None,
                        log=print, on_status=None, stop=None, tick=0.5):
    """点击器 + 采集 + 抛竿/收杆循环 + 收尾。返回 stats。CLI 与 GUI 共用。

    `background=True` 走后台模式：不抢焦点、直接 PostMessage 投递给窗口，
    你在钓鱼期间可以正常用电脑（游戏窗口被盖住也行）。代价是有的游戏不认消息
    投递，且单机存档失焦会暂停 —— 先用 `--background-test` 确认。
    `stop`（threading.Event）可以从别的线程 set —— 循环会在 ~20ms 内退出。
    `feed_frames` 不给时才开真实采集；测试可以塞合成音频进来，这样点击这段
    逻辑能在不碰声卡、不碰游戏的情况下端到端验一遍。
    """
    if thresh is None:
        thresh = DEFAULT_THRESH[feature]

    guard = None
    if dry_run:
        def click():
            pass
        log("dry-run: 不会真的点鼠标")
    else:
        if background:
            if hwnd is None:
                msg = "后台模式需要指定目标窗口（窗口关键字或句柄）。"
                log(f"[!] {msg}")
                return {"done": 0, "frames": 0, "error": msg}
            click, err = make_clicker(button, hwnd=hwnd, background=True,
                                      bg_hold_ms=bg_hold_ms)
            log(f"点击: 后台模式 —— 直接投递给 {title or hwnd!r}，**不抢焦点**，"
                f"游戏窗口可以盖住")
            log("      前提是游戏认这种投递；单机存档失焦会暂停（多人不会）。"
                "不确定就先点一次试。")
        else:
            if hwnd is not None:
                guard = WindowGuard(hwnd, title, auto_activate=activate)
            click, err = make_clicker(button, guard=guard)
            if not err:
                log(f"点击: 前台模式（鼠标{button}键）"
                    + (f"，目标窗口 {title!r}" if title else ""))
        if err:
            log(f"[!] {err}")
            return {"done": 0, "frames": 0, "error": err}

    src = None
    if feed_frames is None:
        src = AudioSource(sr=SR, frame_s=FRAME_S, device=device, hp=hp, want_mic=want_mic)

        def feed_frames(on_frame):
            src.start(on_frame)

    n_total = "∞" if count <= 0 else str(count)

    def status(kind, st):
        if kind == "cast":
            log(f"[{st['done'] + 1}/{n_total}] 抛竿")
        elif kind == "hit":
            log(f"    命中！分数 {st['last']:.3f} -> 收杆  (完成 {st['done']}/{n_total})")
        elif kind == "timeout":
            log(f"    等待超时 {max_wait:g}s，收线重抛（当前分数 {st['last']:.3f}）")
        elif kind == "recover":
            log("    已收线")
        elif kind == "giveup":
            log(f"[!] {st['error']}")
        elif kind == "unfocused":
            log(f"[!] {st['error']}")
        if on_status:
            on_status(kind, st)

    try:
        stats = run_session(template, feed_frames, click, count=count, thresh=thresh,
                            cooldown=cooldown, cast_delay=cast_delay, max_wait=max_wait,
                            no_cast=no_cast, feature=feature, on_status=status,
                            stop=stop, tick=tick, give_up=give_up)
    finally:
        if src is not None:
            src.stop()
    if guard and guard.reactivated:
        log(f"（过程中把目标窗口拉回前台 {guard.reactivated} 次 —— "
            f"最好别在钓鱼时用别的程序抢焦点）")
    return stats


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    try:
        # 只放宽错误处理，**不要**强行改成 UTF-8：Windows 控制台默认是 cp936，
        # 强行输出 UTF-8 会让中文全是乱码。跟着控制台的编码走才对。
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    quiet_soundcard()
    prewarm_audio()

    ap = argparse.ArgumentParser(
        description="MC 音频触发收杆：右键抛竿 -> 等咬钩音 -> 右键收杆 -> 循环")
    ap.add_argument("--list-windows", action="store_true", help="列出可见窗口后退出")
    ap.add_argument("--list-devices", action="store_true", help="列出音频设备后退出")
    ap.add_argument("--window", default=None, help="窗口标题关键字，如 Minecraft")
    ap.add_argument("--hwnd", default=None, help="直接给窗口句柄（0x… 或十进制）")
    ap.add_argument("--no-activate", action="store_true",
                    help="不自动把目标窗口切到前台；它不在前台时直接停下")
    ap.add_argument("--sound", default=None,
                    help="咬钩音效文件（splash.ogg 等），直接当模板，免标定")
    ap.add_argument("--no-autocal", action="store_true",
                    help="不做自校准（默认会把这个音效播一遍、录回环、用录音当模板；"
                         "实测这一步是必需的：解码文件的模板打真实回环只有 raw≈0.26）")
    ap.add_argument("--template", default=None, help="标定产出的模板 .npy")
    ap.add_argument("--save-template", default=None,
                    help="把 --sound 生成的模板存到哪（默认 calib/template_<名>.npy）")
    ap.add_argument("--template-len", type=float, default=0.35,
                    help="从音效里截多长做模板 (s)；太长会拉高检测延迟，默认 0.35")
    ap.add_argument("--feature", default="raw", choices=["raw", "mel"],
                    help="raw=波形互相关（默认，判别力好）| mel=log-mel（音高鲁棒但易误报）")
    ap.add_argument("--count", type=int, default=10, help="钓多少次（0=一直钓到 Ctrl+C）")
    ap.add_argument("--thresh", type=float, default=None,
                    help=f"匹配阈值；不填则按 feature 取默认（{DEFAULT_THRESH}）")
    ap.add_argument("--audio", default="system",
                    help="system（默认）| mic | loopback:<设备名> | <设备名>")
    ap.add_argument("--hp", type=float, default=200.0, help="高通截止 Hz，0=关闭")
    ap.add_argument("--cast-delay", type=float, default=1.5,
                    help="抛竿/收杆后等多少秒再进下一步")
    ap.add_argument("--max-wait", type=float, default=40.0,
                    help="单次等咬钩的上限秒数，超时就收线重抛")
    ap.add_argument("--cooldown", type=float, default=0.5, help="触发冷却秒数")
    ap.add_argument("--give-up", type=int, default=5,
                    help="连着多少轮听不到咬钩音就停下（0=永不放弃）")
    ap.add_argument("--no-cast", action="store_true", help="不自动抛竿，只收杆")
    ap.add_argument("--button", default="right", choices=["right", "left"])
    ap.add_argument("--background", action="store_true",
                    help="后台模式：直接 PostMessage 投递点击给窗口，不抢焦点、"
                         "不动真实鼠标（钓鱼时你还能用电脑）。有的游戏不认这种投递，"
                         "先用 --background-test 试一次")
    ap.add_argument("--bg-hold", type=float, default=40.0,
                    help="后台模式按住多少毫秒（默认 40，太短可能不被认）")
    ap.add_argument("--background-test", action="store_true",
                    help="只往目标窗口投递一次后台点击就退出，用来确认游戏认不认")
    ap.add_argument("--verify", action="store_true",
                    help="先自检音频通路（播一遍音效并打分），再进主流程")
    ap.add_argument("--verify-only", action="store_true", help="只自检音频通路就退出")
    ap.add_argument("--play-device", default=None, help="自检时往哪个设备播（默认设备）")
    ap.add_argument("--dry-run", action="store_true",
                    help="不点鼠标，只打印实时匹配分（先用它确认能听到/认得出）")
    args = ap.parse_args(argv)

    if args.list_windows:
        wins = list_windows()
        if not wins:
            print("[!] 一个窗口都没列到（非 Windows，或权限不足）。")
            return 1
        print(f"{'hwnd':>12}  {'pid':>7}  {'exe':<22} 标题")
        for hwnd, pid, exe, title in wins:
            print(f"{hwnd:>12}  {str(pid or '-'):>7}  {exe[:22]:<22} {title}")
        return 0

    if args.list_devices:
        try:
            list_devices()
        except Exception as e:
            print(f"[!] 无法枚举音频设备: {e}")
            return 1
        return 0

    # ---- 目标窗口 ----
    hwnd = title = None
    if args.hwnd:
        hwnd, pid, exe, title = hwnd_by_value(args.hwnd)
        print(f"目标窗口: {title}  (hwnd={hwnd} pid={pid} {exe})")
    elif args.window:
        win, note = resolve_window(args.window)
        if win is None:
            print(f"[!] {note}")
            print("    用 --list-windows 看有哪些窗口，或 --hwnd 直接指句柄。")
            return 1
        hwnd, pid, exe, title = win
        print(f"目标窗口: {title}  (hwnd={hwnd} pid={pid} {exe})")
        if note:
            print(f"    提示: {note}")

    # ---- 后台点击自测（只点一次就退出）----
    if args.background_test:
        if hwnd is None:
            print("[!] 需要 --window 或 --hwnd 指定要投递的目标窗口。")
            return 1
        print(f"\n后台模式自测：往 {title or hwnd!r} 投递一次{args.button}键点击")
        print("  （不会抢焦点、不会移动你的真实鼠标）")
        ok, msg = background_click(hwnd, args.button, args.bg_hold)
        print(f"  {'[ok]' if ok else '[!]'} {msg}")
        if ok:
            print("\n  现在看游戏里有没有反应（比如抛出鱼竿）：")
            print("    有反应 -> 游戏认后台投递，可以加 --background 正式用")
            print("    没反应 -> 这游戏只读 Raw Input，只能老老实实用前台模式")
        return 0 if ok else 2

    # ---- 音频来源 ----
    device, want_mic, ok = describe_audio_source(args.audio, print)
    if not ok:
        return 1

    # ---- 模板 ----
    if not args.sound and not args.template:
        print("[!] 需要 --sound <音效文件>（推荐，免标定）或 --template <模板.npy>。")
        print("    比如: --sound mcfish/splash.ogg")
        return 1
    if args.sound and args.template:
        print("[!] --sound 和 --template 同时给了，用 --sound 现做模板，忽略 --template。")
    tmpl, ok = prepare_template(args.sound, None if args.sound else args.template,
                                hp=args.hp, length=args.template_len,
                                autocal=not args.no_autocal, device=device,
                                want_mic=want_mic, play_device=args.play_device,
                                save_to=args.save_template, log=print)
    if not ok:
        return 1

    thresh = args.thresh if args.thresh is not None else DEFAULT_THRESH[args.feature]
    print(f"检测: feature={args.feature}  阈值={thresh}  冷却={args.cooldown}s"
          f"  高通={args.hp} Hz")
    if args.feature == "mel":
        print("      [注意] mel 的判别力弱于 raw，阈值要按实测定；")
        print("             钓鱼时环境里响的音效越杂，越要往上抬（见 NOTES.md 第九节）。")

    # ---- 音频通路自检 ----
    if args.verify or args.verify_only:
        if not args.sound:
            print("[!] --verify 需要 --sound（要播一个真实的音效文件）。")
            return 1
        print("\n" + "=" * 72)
        print("音频通路自检：播一遍音效 + 采回环 + **两种特征都打分**")
        print("=" * 72)
        res = verify_audio_path(args.sound, tmpl, args.hp,
                                device=device, want_mic=want_mic,
                                play_device=args.play_device)
        if res["err"]:
            print(f"[!] 播放失败: {res['err']}")
        if res["src_err"]:
            print(f"[!] 采集报错: {res['src_err']}")
        pk = res["peak"]
        print(f"采集帧数 {res['frames']}   峰值分数: raw={pk['raw']:.3f}  "
              f"mel={pk['mel']:.3f}")
        print(f"   (素材 {res['file_sr']} Hz -> 播放 {res['played_sr']} Hz)")
        best = max(pk, key=lambda k: pk[k])
        print(f"   当前 feature={args.feature}，阈值 {thresh}")
        if res["frames"] == 0:
            print("[!] 一帧都没采到 —— 设备打不开或没有回环端点。")
            if args.verify_only:
                return 1
        elif pk[args.feature] >= thresh:
            print(f"[ok] 音频通路可用：{args.feature} 峰值 {pk[args.feature]:.3f} "
                  f">= 阈值 {thresh}。")
            print("     游戏里如果认不出，多半是「游戏声音没从采集的那个设备出」。")
        else:
            print(f"[!] {args.feature} 峰值 {pk[args.feature]:.3f} 低于阈值 {thresh}"
                  f"（另一种特征 {best}={pk[best]:.3f}）—— 先别急着上游戏：")
            print("     1) 游戏/系统音量是否为 0；声音是不是从「默认播放设备」出来的")
            print("     2) 游戏是否用了独占模式/ASIO（回环会拿到静音）")
            print(f"     3) 若 {best} 明显更高，换 --feature {best}")
            print("     4) 也可直接调 --thresh（先用 --dry-run 看实际分数）")
            if args.verify_only:
                return 2
        print()
        if args.verify_only:
            return 0 if (res["frames"] and pk[args.feature] >= thresh) else 2

    # ---- 开钓 ----
    print("\n" + "=" * 72)
    print(f"开始：钓 {'∞' if args.count <= 0 else args.count} 次，"
          f"阈值 {thresh}，冷却 {args.cooldown}s，"
          f"点击={'后台（不抢焦点）' if args.background else '前台'}")
    print("Ctrl+C 停止")
    print("=" * 72)

    stop = threading.Event()
    try:
        stats = run_fishing_session(
            hwnd=hwnd, title=title, template=tmpl, feature=args.feature,
            thresh=thresh, cooldown=args.cooldown, cast_delay=args.cast_delay,
            max_wait=args.max_wait, count=args.count, no_cast=args.no_cast,
            button=args.button, dry_run=args.dry_run, device=device,
            want_mic=want_mic, hp=args.hp, give_up=args.give_up,
            activate=not args.no_activate, background=args.background,
            bg_hold_ms=args.bg_hold, log=print, stop=stop)
    except KeyboardInterrupt:
        stop.set()
        print("\n(已停止)")
        stats = {"frames": 0, "done": 0}

    frames = stats.get("frames", 0)
    print(f"\n完成 {stats.get('done', 0)} 次；共处理 {frames} 帧音频"
          f"（峰值分数 {stats.get('max', 0.0):.3f}）。")
    if stats.get("timeouts"):
        print(f"（其中 {stats['timeouts']} 次等待超时）")
    if stats.get("error"):
        print(f"[!] 提前结束: {stats['error']}")
    if frames == 0:
        print("[!] 一帧音频都没收到 —— 先跑 --verify 检查音频设备/回环。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
