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
"""mcfish - Minecraft 音频触发收杆模块。

从 NTESoundTrigger 的 Watcher 移植，保持相同的匹配数学
（模板 RMS 归一化 + 滑动归一化互相关取最大值），但修掉了三个问题：

1. 原实现把 score 直接除以 len(seg)，于是分数随「触发音相对环境音的响度」
   线性漂移（实测同一段音效：x0.1 gain -> 0.056，x1.0 -> 0.178，阈值 0.13）。
   本模块输出真正的归一化互相关，落在 [-1, 1]，与音量无关。
2. 原实现每次 feed 都用 np.concatenate 重建整个窗口。本模块用环形缓冲，
   并用 FFT + O(N) 滑窗范数递推代替 Python 级滑窗，每帧从数十毫秒降到数毫秒。
3. 原实现只支持单模板；本模块支持一组音高变体，取跨模板最大值。

⚠️ 重要限制（实测数据见 NOTES.md）：
   本模块的 NCC 对音高极其敏感 —— 在自带的高频音效上，偏 0.25 半音就掉到 0.08。
   Minecraft 的咬钩音效带随机音高，因此**直接套用本模块很可能大面积漏检**。
   音高变体只能在音高恰好落在变体上时命中（实测偏差 0 -> 1.000，±0.25 -> 0.08），
   并不能形成有效容差带。

   真正可用的方向（已实测对比，见 NOTES.md）是换用音高鲁棒的特征
   （log-mel 谱图：±2 半音仍有 0.89 分），但那会牺牲判别力
   （对无关的反击音效也给出 0.98 分），需要配合时间结构/delta 特征。
   结论：**必须先用真实 MC 音频标定，再定检测器**，不要直接依赖本文件上线。
"""
from __future__ import annotations

import numpy as np


# --------------------------------------------------------------------------- #
# 音频前端
# --------------------------------------------------------------------------- #
class HighPass:
    """流式高通滤波器（去环境低频轰鸣）。scipy 不可用时退化为差分高通。"""

    def __init__(self, sr: int, cutoff: float = 200.0, order: int = 4):
        self.sr, self.cutoff, self.order = sr, cutoff, order
        self._zi = None
        self._b = self._a = None
        try:
            from scipy.signal import butter, lfilter, lfilter_zi
            self._butter, self._lfilter, self._lfilter_zi = butter, lfilter, lfilter_zi
            self._b, self._a = butter(order, cutoff, btype="highpass", output="ba", fs=sr)
        except Exception:                       # pragma: no cover - scipy optional
            self._b = self._a = None

    def process(self, frame: np.ndarray) -> np.ndarray:
        if self._b is not None:
            if self._zi is None:
                # 从零状态起步。用 lfilter_zi(frame[0]) 会在第一个采样是设备
                # 毛刺时注入一个很大的衰减瞬态，而那个瞬态又长又低频 ——
                # 正好是 NCC 最容易误报的东西（实测首帧附近分数会飙到 2）。
                self._zi = np.zeros(max(len(self._a), len(self._b)) - 1)
            out, self._zi = self._lfilter(self._b, self._a, frame, zi=self._zi)
            return out
        # 一阶差分高通：y[n] = x[n] - x[n-1] + a*y[n-1]
        a = float(np.exp(-2 * np.pi * self.cutoff / self.sr))
        out = np.empty_like(frame, dtype=np.float64)
        prev_x = 0.0 if self._zi is None else self._zi
        prev_y = 0.0
        for i, x in enumerate(frame):
            prev_y = x - prev_x + a * prev_y
            out[i] = prev_y
            prev_x = x
        self._zi = prev_x
        return out


# --------------------------------------------------------------------------- #
# 检测器
# --------------------------------------------------------------------------- #
def _unit(v: np.ndarray) -> np.ndarray:
    """去均值 + 单位范数。**去均值不是可选项**，它决定 NCC 的上界。

    分母用的是滑窗的**方差**（已去均值），分子是滑窗与参考的内积。只有参考也
    严格零均值时内积才等于协方差，Cauchy-Schwarz 才给出 <=1 的上界。
    而重采样出来的音高变体会带明显的直流偏置（实测 splash 的变体 sum(t) 能到
    0.19，因为低频占主导、序列被截断）。不去均值的话，只要某个窗的局部均值不为
    零，就会多出 `局部均值 × sum(t)` 这一项，分数可以冲到 2~19 —— 实测出现过。
    """
    v = v - v.mean()
    return v / (np.linalg.norm(v) + 1e-12)


def _pitch_variants(tmpl: np.ndarray, semitones, sr: int) -> list[np.ndarray]:
    """按半音生成音高变体（同时缩放时长，模拟游戏音效随机化）。"""
    out = []
    for st in semitones:
        r = 2.0 ** (st / 12.0)
        n_out = max(8, int(round(len(tmpl) / r)))
        n_fft = 1 << max(1, (len(tmpl) - 1)).bit_length()
        X = np.fft.rfft(tmpl, n_fft)
        idx = np.arange(n_fft // 2 + 1)
        re = np.interp(idx, idx / r, X.real, left=0, right=0)
        im = np.interp(idx, idx / r, X.imag, left=0, right=0)
        out.append(np.fft.irfft(re + 1j * im, n_fft)[:n_out])
    return out


class _StreamBase:
    """环形缓冲 + 布防/冷却/回调 —— ReelDetector 与 MelDetector 的公共部分。

    两个检测器只有 `_score` 不同，缓冲和「一次事件只报一次」的逻辑完全一样，
    抽在这里，免得两份实现各自漂移。
    """

    def _setup_stream(self, sr, frame, window, thresh, cooldown, on_fire, need):
        self.sr = int(sr)
        self.frame_len = int(round(sr * frame))
        # 窗口必须装得下整个模板，否则 _score 永远返回 0 —— 静默失效。
        # 按模板长度自动放大窗口（splash.ogg 长达 2.165 s，极易踩到这个坑）。
        self.win_len = max(self.frame_len * 4, int(round(sr * window)), int(need))
        self.thresh = float(thresh)
        self.cooldown = float(cooldown)
        self.on_fire = on_fire

        self._buf = np.zeros(self.win_len, dtype=np.float64)
        self._pos = 0
        self._filled = 0

        # 只在最近 need 个采样里搜索：足够容纳一个完整模板，
        # 又能限制“陈旧匹配”把几百毫秒前的旧声音反复当新事件报出来。
        self._band = int(need)
        self._cooldown_left = 0.0
        self.last_score = 0.0
        self.armed = True

    # -- 环形缓冲读写 ------------------------------------------------------ #
    def _push(self, frame: np.ndarray) -> np.ndarray:
        n = len(frame)
        size = self.win_len
        if n >= size:
            self._buf[:] = frame[-size:]
            self._pos = 0
            self._filled = size
        else:
            end = self._pos + n
            if end <= size:
                self._buf[self._pos:end] = frame
            else:
                p1 = size - self._pos
                self._buf[self._pos:] = frame[:p1]
                self._buf[: end - size] = frame[p1:]
            self._pos = (self._pos + n) % size
            self._filled = min(self._filled + n, size)

        if self._filled < size:
            return self._buf[: self._filled]
        if self._pos == 0:
            return self._buf
        return np.concatenate((self._buf[self._pos:], self._buf[: self._pos]))

    # -- 主入口 ------------------------------------------------------------ #
    def feed(self, frame: np.ndarray, now: float | None = None) -> float:
        """喂一帧音频；返回本帧 score。命中时触发 on_fire(score)。"""
        frame = np.asarray(frame, dtype=np.float64)
        seg = self._push(frame)
        score = self._score(seg)
        self.last_score = score

        if self._cooldown_left > 0:
            self._cooldown_left -= len(frame) / self.sr

        if score >= self.thresh and self.armed:
            self._cooldown_left = self.cooldown
            self.armed = False
            if self.on_fire:
                self.on_fire(score)
        elif score < self.thresh * 0.6:
            # 声音消失后才重新布防，避免一次咬钩触发多次
            self.armed = True
        return score

    def _score(self, seg: np.ndarray) -> float:
        raise NotImplementedError


class ReelDetector(_StreamBase):
    """在滚动音频缓冲上做归一化匹配，命中即回调。

    score 是归一化互相关（0..1）：1.0 = 与模板完全一致，约 0.0 = 无关音频。
    """

    def __init__(self, template: np.ndarray, sr: int = 32000, frame: float = 0.08,
                 window: float = 0.6, thresh: float = 0.55,
                 pitch_semitones=(-2.0, -1.0, 0.0, 1.0, 2.0), cooldown: float = 0.35,
                 on_fire=None, slack: float = 0.12, name: str = "reel"):
        self.name = name
        tmpl = np.asarray(template, dtype=np.float64)
        self.templates = [_unit(t) for t in _pitch_variants(tmpl, pitch_semitones, sr)]
        self.tmpl_len = max(len(t) for t in self.templates)
        need = self.tmpl_len + int(round(sr * slack))
        self._setup_stream(sr, frame, window, thresh, cooldown, on_fire, need)

    # -- 匹配 -------------------------------------------------------------- #
    def _score(self, seg: np.ndarray) -> float:
        """归一化互相关 (NCC) 峰值：max over lags and over pitch variants.

        分数 = <window, template> / (||window|| * ||template||)，落在 [-1, 1]，
        与整体音量无关 —— 这正是原实现缺的那一环。

        实现要点（每一处都踩过坑）：
        - 每个音高变体长度不同，必须**按各自的长度**做相关和范数归一化。
          之前用最长变体的长度去套所有变体，导致真正匹配时分数只有 0.06。
        - 窗口范数用累积和 O(N) 递推；如果拿 1e-12 兜底去做除法，静音窗口会把
          数值噪声放大成几百的假高分（实测出现过 197），所以显式屏蔽静音窗口。
        """
        T = self.tmpl_len
        if len(seg) < T:
            return 0.0
        region = seg[max(0, len(seg) - self._band):]
        n = len(region)
        if n < T:
            return 0.0

        mu = float(region.mean())
        r = region - mu
        rnorm = float(np.linalg.norm(r))
        if rnorm < 1e-12:
            return 0.0
        u = r / rnorm                      # 单位化 -> 相关值天然在 [-1,1]

        nfft = 1 << (n + T - 1).bit_length()
        U = np.fft.rfft(u, nfft)
        cs = np.concatenate(([0.0], np.cumsum(u)))
        cs2 = np.concatenate(([0.0], np.cumsum(u * u)))

        best = -1.0
        for t in self.templates:
            Tt = len(t)
            if n < Tt:
                continue
            tn = float(np.linalg.norm(t))
            if tn < 1e-12:
                continue

            corr = np.fft.irfft(U * np.fft.rfft(t, nfft).conj(), nfft)[: n - Tt + 1]

            # 窗口方差，必须用**窗口自己的均值**（用全局 mu 是错的）
            s1 = cs[Tt:] - cs[:-Tt]
            s2 = cs2[Tt:] - cs2[:-Tt]
            var_w = s2 - s1 * s1 / Tt
            active = var_w > 1e-12
            if not np.any(active):
                continue
            denom = np.sqrt(np.maximum(var_w, 0.0)) * tn
            # 只对能量足够的窗口计分，静音窗口给 0
            ncc = np.where(active, corr / np.maximum(denom, 1e-12), 0.0)
            m = float(ncc.max())
            if m > best:
                best = m
        return best


class MelDetector(_StreamBase):
    """log-mel + delta 余弦匹配，带**音高库** —— 这是应对随机音高的那一环。

    为什么必须有音高库（实测数据见 NOTES.md）：

    - 裸波形 NCC 对音高是 δ 函数：模板完全一致时 1.000，移调 3 个半音就只剩 0.22。
      Minecraft 的咬钩水花每次播放都随机音高（`PlaySound(..., 0.4F/(r*0.4F+0.8F))`
      这类写法），所以 raw 在游戏里会大面积漏检。
    - log-mel 本身对音高宽容（±2 半音仍有 0.89），但单份参考在偏得多时也会掉到 0.86，
      离背景分（实测 0.77 上下）太近。
    - 所以按引擎的 pitch 倍率搭一组参考（默认 0.33~1.0 共 12 份，1.7 半音一档，
      配合 mel 自带的 ±2 半音容差正好无缝），取跨参考最大值 → 任意音高都在 0.95+，
      于是阈值可以提到 0.92 附近，和背景拉开安全距离。

    代价：判别力不如 raw（对无关的宽带噪声也会给高分），阈值必须标定。所以
    `calibrate.py` 会同时给出两种特征的分离度，照着选。
    """

    def __init__(self, template: np.ndarray, sr: int = 32000, frame: float = 0.08,
                 window: float = 0.6, thresh: float = 0.92, cooldown: float = 0.35,
                 on_fire=None, n_mels: int = 40, use_delta: bool = True,
                 pitch_range=(0.33, 1.0), n_pitch: int = 12, slack: float = 0.15,
                 name: str = "reel-mel"):
        from mcfish.features import LogMel, mel_bank_best
        self.name = name
        self._bank_best = mel_bank_best
        self.use_delta = bool(use_delta)
        tmpl = np.asarray(template, dtype=np.float64)
        self.refs = pitch_bank(tmpl, sr, *pitch_range, n=n_pitch)
        self.mfb = LogMel(sr=int(sr), n_mels=n_mels)
        # 窗口要装得下最长的那个参考（低音高变体被拉长了）
        need = max(len(r) for r in self.refs) + int(round(sr * slack))
        self._setup_stream(sr, frame, window, thresh, cooldown, on_fire, need)

    @property
    def n_refs(self):
        return len(self.refs)

    def _score(self, seg: np.ndarray) -> float:
        if len(seg) < min(len(r) for r in self.refs):
            return 0.0
        region = seg[max(0, len(seg) - self._band):]
        if len(region) < min(len(r) for r in self.refs):
            return 0.0
        r = region - region.mean()
        # 静音窗口的 log-mel 全是 log(1e-8)，余弦会给假高分，显式屏蔽
        if float(np.linalg.norm(r)) < 1e-12:
            return 0.0
        return float(self._bank_best(r, self.refs, self.mfb, use_delta=self.use_delta))


# --------------------------------------------------------------------------- #
# 从音频文件直接做模板（splash.ogg 这条路）
# --------------------------------------------------------------------------- #
def pitch_bank(tmpl: np.ndarray, sr: int, lo: float = 0.33, hi: float = 1.0,
               n: int = 12) -> list[np.ndarray]:
    """音高库：把模板按引擎 pitch 参数可能的**倍率**重采样成一组参考。

    `PlaySound` 的 pitch=r 就是把素材按 r 倍速播放（时长变 1/r、频率 ×r），
    所以这里按倍率（不是半音）搭网格。默认 0.33~1.0、12 档 ≈ 1.7 半音一档 ——
    配合 log-mel 自带的 ±2 半音容差，任意音高都落得进去。
    只用 raw NCC 时别用这个（δ 宽度意味着要上百份才盖得住）。
    """
    tmpl = np.asarray(tmpl, dtype=np.float64)
    if n <= 1 or hi <= lo:
        return [tmpl]
    factors = np.exp(np.linspace(np.log(lo), np.log(hi), int(n)))
    semis = [12.0 * float(np.log2(f)) for f in factors]
    return _pitch_variants(tmpl, semis, sr)


def pitch_shift(x: np.ndarray, factor: float) -> np.ndarray:
    """模拟引擎以 pitch=factor 播放：按 factor 倍速重采样（频率 ×factor、时长 ÷factor）。

    用来做「音高变化」实验（见 NOTES.md）。优先走多相滤波
    （resample_poly），拿不到 scipy 时退化为线性插值。
    """
    x = np.asarray(x, dtype=np.float64)
    if abs(factor - 1.0) < 1e-9:
        return x
    try:
        from fractions import Fraction
        from scipy.signal import resample_poly
        num, den = Fraction(1.0 / factor).limit_denominator(1000).as_integer_ratio()
        return resample_poly(x, num, den)
    except Exception:                                    # pragma: no cover
        n_out = max(8, int(round(len(x) / factor)))
        return np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x)


def resample(x: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """线性相位重采样。scipy 可用时用 resample_poly，否则退化为 np.interp。"""
    x = np.asarray(x, dtype=np.float64)
    if int(src_sr) == int(dst_sr):
        return x
    try:
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(int(src_sr), int(dst_sr))
        return resample_poly(x, int(dst_sr) // g, int(src_sr) // g)
    except Exception:                                    # pragma: no cover
        n_out = int(round(len(x) * dst_sr / src_sr))
        return np.interp(np.arange(n_out) / dst_sr,
                         np.arange(len(x)) / src_sr, x)


def load_audio_file(path: str, sr: int | None = 32000):
    """解码任意 soundfile 支持的格式（ogg/wav/flac/…）成单声道 float64。

    返回 (mono, 文件原始采样率)。sr=None 表示**不重采样**，保持文件原始采样率 ——
    播放时必须用这个，否则会拿 32 kHz 的数组按 11 kHz 播，声音被拉长变调
    （splash.ogg 是 11025 Hz，会慢 2.9 倍）。需要 soundfile（含其自带 libsndfile）。
    """
    try:
        import soundfile as sf
    except Exception as e:                               # pragma: no cover
        raise RuntimeError("直接读音频文件需要 soundfile：pip install soundfile") from e
    try:
        data, file_sr = sf.read(path, dtype="float64", always_2d=True)
    except Exception as e:
        raise RuntimeError(f"soundfile 打不开 {path}: {e}\n"
                           "（若是不支持的格式，改用 record.py --template-from 录一遍）") from e
    mono = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
    if sr is None:
        return mono, int(file_sr)
    return resample(mono, file_sr, sr), int(file_sr)


def find_onset(x: np.ndarray, sr: int, hop_s: float = 0.01, thresh_ratio: float = 0.2) -> int:
    """用短时 RMS 包络找起振点：从峰值往前退到能量明显上升之前。"""
    hop = max(1, int(sr * hop_s))
    n = max(1, len(x) // hop)
    env = np.array([np.sqrt(np.mean(x[i * hop:(i + 1) * hop] ** 2) + 1e-12)
                    for i in range(n)])
    k = int(np.argmax(env))
    thr = env.max() * thresh_ratio
    j = k
    while j > 0 and env[j] > thr:
        j -= 1
    return j * hop


def make_template_from_audio(path: str, sr: int = 32000, length: float = 0.35,
                             hp: float = 200.0, order: int = 4):
    """把音频文件（splash.ogg）直接做成检测模板。返回 (template, info)。

    解码 -> 重采样到 sr -> 找起振点 -> 截 length 秒 -> 高通 -> 去均值。

    两个不能省的细节：

    1. **模板必须截断**。splash.ogg 有 2.165 s，而匹配峰值要等模板完整进入
       缓冲区才出现，用满全长会带来 ~2.2 s 延迟，Java 的收杆窗口只有 1~2 s。
       截起振后 0.2~0.35 s 既保住瞬态又压住延迟。
    2. **必须用和实时采集同一条因果滤波**（HighPass.process，而不是参考实现
       里模板用的 filtfilt 零相位滤波）。实时路径是逐帧因果 lfilter，模板走
       filtfilt 的话两者在瞬态处波形不一致，NCC 会白白丢分。
    """
    mono, file_sr = load_audio_file(path, sr)
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    if peak < 1e-5:
        raise RuntimeError(f"{path} 几乎是静音（peak={peak:.2e}），做不了模板")

    onset = find_onset(mono, sr)
    n = int(round(length * sr))
    seg = mono[onset:onset + n]
    if len(seg) < n:
        seg = np.pad(seg, (0, n - len(seg)))
    seg = seg.astype(np.float64)
    if hp and hp > 0:
        seg = HighPass(sr, hp, order).process(seg)
    seg = seg - seg.mean()

    info = {"path": path, "file_sr": file_sr, "src_dur": len(mono) / sr,
            "onset": onset / sr, "sr": sr, "len": len(seg), "len_s": len(seg) / sr,
            "peak": peak, "rms": float(np.sqrt(np.mean(seg ** 2))), "hp": hp}
    return seg, info


# --------------------------------------------------------------------------- #
# 标定辅助
# --------------------------------------------------------------------------- #
def suggest_threshold(scores: np.ndarray, events: np.ndarray) -> dict:
    """从标定录音里选阈值：低于所有事件外峰值，且留出安全余量。"""
    scores = np.asarray(scores, dtype=np.float64)
    events = np.asarray(events, dtype=bool)
    hit = scores[events]
    bg = scores[~events]
    if len(hit) == 0:
        return {"ok": False, "reason": "没有标定到任何事件"}
    hit_min = float(hit.min())
    bg_max = float(bg.max()) if len(bg) else 0.0
    if hit_min <= bg_max:
        return {"ok": False, "reason": f"事件与背景重叠 (hit_min={hit_min:.3f} <= bg_max={bg_max:.3f})",
                "hit_min": hit_min, "bg_max": bg_max}
    thresh = bg_max + 0.6 * (hit_min - bg_max)
    return {"ok": True, "thresh": round(thresh, 3),
            "hit_min": round(hit_min, 3), "hit_max": round(float(hit.max()), 3),
            "bg_max": round(bg_max, 3),
            "margin": round(hit_min - bg_max, 3)}
