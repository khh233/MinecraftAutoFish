# autofish —— Minecraft 音频触发钓鱼
# Copyright (C) 2026 欧金金 · 以 GNU GPL v3 发布（无担保），全文见 LICENSE。
"""音高鲁棒的特征。

实测（见 NOTES.md）：在高频瞬态音效上，裸波形互相关偏 0.25 半音就从
1.000 掉到 0.08，而 log-mel 谱图在 ±2 半音仍有 0.89。
所以一旦确认 MC 的触发音带随机音高，就必须用这里而不是纯 NCC。

代价是判别力下降（对无关音效也可能给高分），所以同时提供 delta 特征
保留时间结构，并对相似音效做区分。
"""
from __future__ import annotations

import numpy as np

__all__ = ["MelFilterBank", "to_logmel", "mel_cosine", "band_fingerprint",
           "logmel_best_shift", "mel_bank_best"]


class MelFilterBank:
    """三角 mel 滤波器组，纯 numpy。"""

    def __init__(self, sr: int = 32000, n_mels: int = 40, n_fft: int = 1024,
                 fmin: float = 100.0, fmax: float = 14000.0, win: int = 1024,
                 hop: int = 256):
        self.sr, self.n_mels, self.n_fft = sr, n_mels, n_fft
        self.win, self.hop = win, hop

        def hz2mel(f):
            return 2595.0 * np.log10(1.0 + f / 700.0)

        def mel2hz(m):
            return 700.0 * (10.0 ** (m / 2595.0) - 1.0)

        pts = mel2hz(np.linspace(hz2mel(fmin), hz2mel(fmax), n_mels + 2))
        freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
        self.fb = np.zeros((n_mels, len(freqs)))
        for i in range(n_mels):
            lo, ct, hi = pts[i], pts[i + 1], pts[i + 2]
            self.fb[i] = np.clip(np.minimum((freqs - lo) / max(ct - lo, 1e-9),
                                            (hi - freqs) / max(hi - ct, 1e-9)), 0, None)
        self._window = np.hanning(win)

    def frames(self, x: np.ndarray) -> np.ndarray:
        if len(x) < self.win:
            x = np.pad(x, (0, self.win - len(x)))
        n = 1 + (len(x) - self.win) // self.hop
        idx = np.arange(self.win)[None, :] + self.hop * np.arange(n)[:, None]
        return x[idx] * self._window


class LogMel:
    """MelFilterBank 的便捷包装；可直接传给 to_logmel / logmel_best_shift。"""

    def __init__(self, **kw):
        self.mfb = MelFilterBank(**kw)
        self.fb = self.mfb.fb
        self.win = self.mfb.win
        self.hop = self.mfb.hop
        self.sr = self.mfb.sr

    def frames(self, x: np.ndarray) -> np.ndarray:
        return self.mfb.frames(x)

    def __call__(self, x: np.ndarray) -> np.ndarray:
        return to_logmel(x, self)


def to_logmel(x: np.ndarray, mfb: MelFilterBank, floor_db: float = 40.0) -> np.ndarray:
    """波形 -> log-mel，形状 (帧数, mel 带数)。

    `floor_db`：每帧把低于「本帧最强带 floor_db 分贝」的带压到该下限。这一步不是
    可选的美化，是**必须的**：没有它，低能量带里全是环境噪声，而 log 压缩会把这
    种「微小值」放大成主成分，余弦相似度就被噪声拖垮 —— 实测同一段音效，干净时
    mel=1.000，只叠了 -23 dB 的白噪声就掉到 0.616，而同一份数据 raw NCC 还有
    0.997。压掉 40 dB 以下的带之后，噪声不再主导，分数回到 0.95+。
    """
    frames = mfb.frames(np.asarray(x, dtype=np.float64))
    spec = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    lm = np.log(spec @ mfb.fb.T + 1e-8)
    if floor_db and floor_db > 0:
        lm = np.maximum(lm, lm.max(axis=1, keepdims=True) - floor_db * np.log(10.0) / 10.0)
    return lm


def _norm(v: np.ndarray) -> np.ndarray:
    v = v - v.mean()
    return v / (np.linalg.norm(v) + 1e-12)


def band_fingerprint(x: np.ndarray, mfb: MelFilterBank) -> np.ndarray:
    """对时间取平均的 log-mel 指纹（纯音色，不看时间结构）。"""
    return to_logmel(x, mfb).mean(axis=0)


def mel_cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(_norm(a.ravel()) @ _norm(b.ravel()))


def logmel_best_shift(seg: np.ndarray, ref: np.ndarray, mfb: MelFilterBank,
                      use_delta: bool = False, n_delta: int = 1) -> float:
    """在 seg 上滑窗，取与 ref 的 log-mel 余弦相似度最大值。

    use_delta=True 时拼上时间差分，保留时间结构，可提升对相似音效的判别力。
    """
    if len(seg) < len(ref):
        seg = np.pad(seg, (0, len(ref) - len(seg)))
    E = to_logmel(seg, mfb)
    R = to_logmel(ref, mfb)
    if use_delta:
        for _ in range(n_delta):
            E = np.concatenate([E, np.diff(E, axis=0, prepend=E[:1])], axis=1)
            R = np.concatenate([R, np.diff(R, axis=0, prepend=R[:1])], axis=1)
    L = R.shape[0]
    if E.shape[0] < L:
        return 0.0
    Rn = _norm(R.ravel())
    return max(float(_norm(E[k:k + L].ravel()) @ Rn) for k in range(E.shape[0] - L + 1))


def _with_delta(E: np.ndarray, n_delta: int) -> np.ndarray:
    for _ in range(n_delta):
        E = np.concatenate([E, np.diff(E, axis=0, prepend=E[:1])], axis=1)
    return E


def mel_bank_best(seg: np.ndarray, refs, mfb: MelFilterBank,
                  use_delta: bool = True, n_delta: int = 1) -> float:
    """对「一组参考波形」取滑窗 log-mel 余弦最大值（分数裁到 >=0）。

    与 logmel_best_shift 的唯一区别是 seg 的 log-mel **只算一次**，再对所有参考
    做匹配（滑窗归一化用累积和的 O(T) 递推，参考侧预先单位化）。
    正因为参考侧几乎不要钱，才放得起十几份「音高变体」—— 这是对付 Minecraft
    随机音高的关键（见 detector.pitch_bank）。

    分数 = <窗, 参考> / (||窗-窗均值|| · ||参考-参考均值||)，与音量和整体增益无关。
    """
    refs = [np.asarray(r, dtype=np.float64) for r in refs]
    if not refs or len(seg) == 0:
        return 0.0
    E = _with_delta(to_logmel(seg, mfb), n_delta) if use_delta else to_logmel(seg, mfb)
    T, F = E.shape
    # 每帧的元素和 / 平方和 -> 滑窗方差与滑窗均值的 O(T) 递推
    cs = np.concatenate(([0.0], np.cumsum(E.sum(axis=1))))
    cs2 = np.concatenate(([0.0], np.cumsum((E * E).sum(axis=1))))

    best = 0.0
    for r in refs:
        R = to_logmel(r, mfb) if len(r) >= mfb.win else None
        if R is None:
            continue
        if use_delta:
            R = _with_delta(R, n_delta)
        L = R.shape[0]
        if T < L:
            continue
        Rc = R - R.mean()
        rn = float(np.linalg.norm(Rc))
        if rn < 1e-12:
            continue
        Rc = Rc / rn

        # 滑窗点积：对每个 feature 列做 1D 相关再求和
        nfft = 1 << (T + L - 1).bit_length()
        corr = np.fft.irfft(np.fft.rfft(E, nfft, axis=0)
                            * np.conj(np.fft.rfft(Rc, nfft, axis=0)), nfft, axis=0)
        corr = corr[: T - L + 1].sum(axis=1)

        s1 = cs[L:] - cs[:-L]
        s2 = cs2[L:] - cs2[:-L]
        var = np.maximum(s2 - s1 * s1 / (L * F), 0.0)
        active = var > 1e-12                    # 静音窗给 0，不放大数值噪声
        if not np.any(active):
            continue
        cos = np.where(active, corr / np.sqrt(np.maximum(var, 1e-12)), 0.0)
        m = float(cos.max())
        if m > best:
            best = m
    return best
