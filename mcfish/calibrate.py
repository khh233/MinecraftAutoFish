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
"""用真实录音标定收杆检测器：给出分数分布、建议阈值，并判定该用哪种特征。

用法：
    python -m mcfish.calibrate --in calib/session1

读 record.py 产出的 <in>.npy / <in>.json，然后：
  1. 在每个标记附近找出真实起振点
  2. 取第 1 个事件当模板，其余事件当正样本（跨事件复用同一模板）
  3. 在两个尺度上算分数：
       raw  —— 波形 NCC（NTESoundTrigger 原方案 + 音高变体 + 增益无关）
       mel  —— log-mel 余弦（音高鲁棒）
  4. 用 suggest_threshold 的逻辑给出建议阈值，并报告分离度

关键判据是「事件间分数」：如果同一个模板在别的咬钩事件上只有 0.1 分，
说明该音的播放音高/时长在游戏里是变化的，必须换 mel 特征。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

from mcfish.detector import _unit, _pitch_variants, suggest_threshold
from mcfish.features import LogMel, logmel_best_shift

FRAME_S = 0.08
WIN_S = 0.6                 # 检测缓冲区长度
PITCH = (-2.0, -1.0, 0.0, 1.0, 2.0)


def load(inp: str):
    audio = np.load(inp + ".npy").astype(np.float64)
    with open(inp + ".json", encoding="utf-8") as f:
        meta = json.load(f)
    return audio, meta


def find_onset(audio: np.ndarray, t: float, sr: int, span: float = 0.5) -> float:
    """在 t 附近 ±span 内找能量最集中的起点（用短时 RMS 包络）。"""
    lo = max(0, int((t - span) * sr))
    hi = min(len(audio), int((t + span) * sr))
    if hi - lo < sr // 20:
        return t
    seg = audio[lo:hi]
    hop = max(1, int(sr * 0.01))
    n = max(1, (len(seg) - hop) // hop)
    env = np.array([np.sqrt(np.mean(seg[i * hop:(i + 1) * hop] ** 2) + 1e-12)
                    for i in range(n)])
    k = int(np.argmax(env))
    # 往前退到能量明显上升之前
    thr = env.max() * 0.25
    j = k
    while j > 0 and env[j] > thr:
        j -= 1
    return lo / sr + j * hop / sr


def extract_window(audio: np.ndarray, t: float, sr: int, pre: float, length: float):
    """取以 t 为事件起点、前面留 pre 秒背景的窗口。"""
    a = int(round((t - pre) * sr))
    b = a + int(round(length * sr))
    if a < 0 or b > len(audio):
        return None
    return audio[a:b].copy()


def raw_scores(buf: np.ndarray, templates, band: int) -> float:
    """复刻检测器的 NCC：搜 len(buf)-len(t)+1 个 lag，取跨模板最大。"""
    n = len(buf)
    csum = np.concatenate(([0.0], np.cumsum(buf)))
    csum2 = np.concatenate(([0.0], np.cumsum(buf * buf)))
    best = -1.0
    for t in templates:
        T = len(t)
        if n < T:
            continue
        nfft = 1 << (n + T - 1).bit_length()
        corr = np.fft.irfft(np.fft.rfft(buf, nfft) * np.fft.rfft(t, nfft).conj(),
                            nfft)[: n - T + 1]
        s1 = csum[T:] - csum[:-T]
        s2 = csum2[T:] - csum2[:-T]
        var = s2 - s1 * s1 / T
        np.maximum(var, 1e-12, out=var)
        ncc = corr / (np.sqrt(var) * np.linalg.norm(t) + 1e-12)
        best = max(best, float(ncc.max()))
    return best


def stats(name: str, pos: np.ndarray, neg: np.ndarray):
    pmin, pmax = float(pos.min()), float(pos.max())
    nmin, nmax = float(neg.min()) if len(neg) else 0.0, float(neg.max()) if len(neg) else 0.0
    gap = pmin - nmax
    print(f"\n  [{name}] 正样本 {len(pos)} 个 / 负样本 {len(neg)} 个")
    print(f"    正样本 min/mean/max = {pmin:.3f} / {pos.mean():.3f} / {pmax:.3f}")
    print(f"    负样本 min/mean/max = {nmin:.3f} / {(neg.mean() if len(neg) else 0):.3f} / {nmax:.3f}")
    print(f"    分离度 (正 min - 负 max) = {gap:+.3f}  "
          f"{'-> 可分' if gap > 0.1 else '-> 分不开' if gap <= 0 else '-> 勉强'}")
    return gap


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="收杆检测器标定")
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--pre", type=float, default=0.15, help="事件前保留的背景时长 (s)")
    ap.add_argument("--no-delta", action="store_true", help="log-mel 不用 delta 特征")
    args = ap.parse_args(argv)

    audio, meta = load(args.inp)
    sr = int(meta["sr"])
    markers = list(meta["markers"])
    print("=" * 78)
    print(f"录音 {len(audio)/sr:.2f} s @ {sr} Hz   标记 {len(markers)} 个   HP={meta.get('hp')}")
    print("=" * 78)
    if len(markers) < 3:
        print("[!] 标记太少（<3），先多录几次咬钩")
        return 1

    # 1) 精确定位每个事件
    onsets = [find_onset(audio, t, sr) for t in markers]
    print("\n标记 -> 定位事件起点:")
    for m, o in zip(markers, onsets):
        print(f"   {m:7.3f} -> {o:7.3f}  (偏移 {o-m:+.3f} s)")

    # 2) 用第 1 个事件当模板（取 onset 起 0.35 s，太长的模板会拉高延迟）
    T_LEN = 0.35
    tmpl = extract_window(audio, onsets[0], sr, pre=0.0, length=T_LEN)
    if tmpl is None:
        print("[!] 无法从第 1 个事件取模板")
        return 1
    tmpl = tmpl - tmpl.mean()
    print(f"\n模板: 来自事件 1, {len(tmpl)} 采样 ({len(tmpl)/sr:.3f} s), "
          f"RMS={np.sqrt(np.mean(tmpl**2)):.5f}")

    # 3) 正样本：每个标记一个缓冲窗口，事件落在 pre 处
    pos_bufs, pos_idx = [], []
    for i, o in enumerate(onsets):
        w = extract_window(audio, o, sr, pre=args.pre, length=WIN_S)
        if w is not None:
            pos_bufs.append(w)
            pos_idx.append(i)
    # 4) 负样本：所有标记区间之外随机取同样长度的窗口
    guard = WIN_S + T_LEN
    forbid = np.zeros(len(audio), dtype=bool)
    for o in onsets:
        a = max(0, int((o - guard) * sr))
        b = min(len(audio), int((o + guard) * sr))
        forbid[a:b] = True
    n_pos_len = int(WIN_S * sr)
    cand = np.where(~forbid)[0]
    neg_bufs = []
    if len(cand) > n_pos_len:
        rng = np.random.default_rng(0)
        starts = cand[: len(cand) - n_pos_len]
        pick = rng.choice(starts, size=min(40, len(starts)), replace=False)
        for s in pick:
            neg_bufs.append(audio[s:s + n_pos_len].copy())
    print(f"正样本窗口 {len(pos_bufs)} 个, 负样本窗口 {len(neg_bufs)} 个")

    # 5) 两种特征的分数
    variants = [_unit(t) for t in _pitch_variants(tmpl, PITCH, sr)]
    raw_pos = np.array([raw_scores(b, variants, 0) for b in pos_bufs])
    raw_neg = np.array([raw_scores(b, variants, 0) for b in neg_bufs]) if neg_bufs else np.zeros(0)

    mfb = LogMel(sr=sr)
    use_delta = not args.no_delta
    mel_pos = np.array([logmel_best_shift(b, tmpl, mfb, use_delta=use_delta) for b in pos_bufs])
    mel_neg = (np.array([logmel_best_shift(b, tmpl, mfb, use_delta=use_delta) for b in neg_bufs])
               if neg_bufs else np.zeros(0))

    print("\n" + "=" * 78)
    print("逐事件分数（关键看这一列：同一模板在别的咬钩上还剩多少）")
    print("=" * 78)
    print(f"{'事件':>4}{'标记t':>9}{'定位t':>9}{'raw NCC':>10}{'log-mel':>10}")
    for k, (b, i) in enumerate(zip(pos_bufs, pos_idx)):
        print(f"{i+1:>4}{markers[i]:>9.3f}{onsets[i]:>9.3f}{raw_pos[k]:>10.3f}{mel_pos[k]:>10.3f}")

    print("\n" + "=" * 78)
    print("特征对比")
    print("=" * 78)
    gap_raw = stats("raw 波形 NCC", raw_pos, raw_neg)
    gap_mel = stats("log-mel" + ("+delta" if use_delta else ""), mel_pos, mel_neg)

    # 抛竿音交叉检查：模板对「抛竿」事件的分数。
    # 若两者都高，说明这个音在抛竿和咬钩时都会响，不能用来触发收杆。
    cast_markers = list(meta.get("cast_markers") or [])
    if cast_markers:
        cast_scores_raw, cast_scores_mel = [], []
        for t in cast_markers:
            w = extract_window(audio, t, sr, pre=args.pre, length=WIN_S)
            if w is None:
                continue
            cast_scores_raw.append(raw_scores(w, variants, 0))
            cast_scores_mel.append(logmel_best_shift(w, tmpl, mfb, use_delta=use_delta))
        if cast_scores_raw:
            cr = np.array(cast_scores_raw)
            cm = np.array(cast_scores_mel)
            print(f"\n  [抛竿音交叉检查] {len(cr)} 个抛竿事件")
            print(f"    raw     : {np.round(cr, 3).tolist()}")
            print(f"    log-mel : {np.round(cm, 3).tolist()}")
            print(f"    咬钩模板对抛竿音 raw 中位 {np.median(cr):.3f} / mel 中位 {np.median(cm):.3f}")
            if np.median(cr) > 0.5 or np.median(cm) > 0.9:
                print("    [!] 抛竿音与咬钩音得分接近 —— 说明该音效在抛竿时也会响，")
                print("        用它触发收杆会在抛竿瞬间就收线。请换咬钩专属音效。")
            else:
                print("    [ok] 抛竿音得分明显更低，可用作咬钩触发。")

    print("\n" + "=" * 78)
    print("建议阈值")
    print("=" * 78)
    for name, pos, neg, gap in (("raw", raw_pos, raw_neg, gap_raw),
                                ("mel", mel_pos, mel_neg, gap_mel)):
        sg = suggest_threshold(np.concatenate([pos, neg]),
                              np.concatenate([np.ones(len(pos), bool), np.zeros(len(neg), bool)]))
        print(f"  {name}: {sg}")

    print("\n" + "=" * 78)
    print("结论")
    print("=" * 78)
    if gap_raw > 0.15:
        print("  raw NCC 可用：该音效在游戏里的播放音高是稳定的。")
        print("  -> 用 mcfish.detector.ReelDetector（raw），延迟与开销最低。")
    elif gap_mel > 0.15:
        print("  raw NCC 不可用（音高有变化），但 log-mel 可分。")
        print("  -> 需要把检测器换成 log-mel 特征。")
    else:
        print("  两种特征都分不开。可能原因：")
        print("   - 录音里咬钩事件太弱 / 标记位置偏了")
        print("   - 水声/环境音与咬钩音在特征上太接近")
        print("   - 模板长度不合适（试试 --pre / T_LEN）")
        print("  建议先跑 record.py --probe 确认回环信号质量。")

    # 保存模板与阈值
    out_dir = os.path.dirname(os.path.abspath(args.inp)) or "."
    tpath = os.path.join(out_dir, "template.npy")
    np.save(tpath, tmpl.astype(np.float32))
    print(f"\n已保存模板 {tpath} ({len(tmpl)} 采样, {len(tmpl)/sr:.3f} s)")
    print(f"标定摘要写入 {args.inp}.calib.json")
    with open(args.inp + ".calib.json", "w", encoding="utf-8") as f:
        json.dump({"sr": sr, "template": tpath, "template_len": len(tmpl),
                   "gap_raw": gap_raw, "gap_mel": gap_mel,
                   "raw_pos": raw_pos.tolist(), "raw_neg": raw_neg.tolist(),
                   "mel_pos": mel_pos.tolist(), "mel_neg": mel_neg.tolist()},
                  f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
