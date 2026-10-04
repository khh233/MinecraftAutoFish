"""End-to-end validation of record->calibrate logic using synthetic sessions.

We cannot record real audio here, so we synthesise two sessions whose ground truth
we control:
  A) bite sound played at a CONSTANT pitch  -> raw NCC should win
  B) bite sound played at RANDOM pitch      -> raw NCC should fail, log-mel should not
Then we run mcfish.calibrate's analysis functions on both and check the verdicts.
"""
import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from mcfish.detector import _pitch_variants
from mcfish.calibrate import (find_onset, extract_window, raw_scores, stats,
                              WIN_S, PITCH)
from mcfish.detector import _unit
from mcfish.features import LogMel, logmel_best_shift

SR = 32000
import _refs
(tmpl0,) = _refs.load("dodge")     # 上游参考波形，未随仓库分发；缺则跳过
rng = np.random.default_rng(11)

_bg = np.convolve(rng.normal(0, 1, SR * 4), np.ones(300) / 300)[: SR * 4]
_bg /= np.abs(_bg).max()


def build_session(pitches, dur=40.0, level=1.0, bg_level=0.02):
    n = int(dur * SR)
    sig = _bg[np.arange(n) % len(_bg)].copy() * bg_level
    markers = []
    for st in pitches:
        t = float(rng.uniform(4.0, dur - 3.0))
        ev = _pitch_variants(tmpl0, [st], SR)[0] * level
        i0 = int(t * SR)
        k = min(len(ev), n - i0)
        sig[i0:i0 + k] += ev[:k]
        markers.append(round(t, 3))
    return sig, sorted(markers)


def analyse(sig, markers, label):
    onsets = [find_onset(sig, t, SR) for t in markers]
    T_LEN = 0.35
    tmpl = extract_window(sig, onsets[0], SR, pre=0.0, length=T_LEN)
    tmpl = tmpl - tmpl.mean()

    pos = [extract_window(sig, o, SR, pre=0.15, length=WIN_S) for o in onsets]
    pos = [p for p in pos if p is not None]
    guard = WIN_S + T_LEN
    forbid = np.zeros(len(sig), dtype=bool)
    for o in onsets:
        forbid[max(0, int((o - guard) * SR)): int((o + guard) * SR)] = True
    L = int(WIN_S * SR)
    cand = np.where(~forbid)[0]
    neg = [sig[s:s + L].copy() for s in
           rng.choice(cand[:len(cand) - L], size=40, replace=False)] if len(cand) > L else []

    variants = [_unit(t) for t in _pitch_variants(tmpl, PITCH, SR)]
    raw_pos = np.array([raw_scores(b, variants, 0) for b in pos])
    raw_neg = np.array([raw_scores(b, variants, 0) for b in neg]) if neg else np.zeros(0)
    mfb = LogMel(sr=SR)
    mel_pos = np.array([logmel_best_shift(b, tmpl, mfb, use_delta=True) for b in pos])
    mel_neg = np.array([logmel_best_shift(b, tmpl, mfb, use_delta=True) for b in neg]) \
        if neg else np.zeros(0)

    gap_raw = float(raw_pos.min() - raw_neg.max())
    gap_mel = float(mel_pos.min() - mel_neg.max())
    print(f"\n--- {label} ---")
    print(f"  raw 逐事件: {np.round(raw_pos, 3).tolist()}")
    print(f"  mel 逐事件: {np.round(mel_pos, 3).tolist()}")
    print(f"  gap_raw={gap_raw:+.3f}   gap_mel={gap_mel:+.3f}")
    if gap_raw > 0.15:
        verdict = "raw 可用"
    elif gap_mel > 0.15:
        verdict = "改用 log-mel"
    else:
        verdict = "都分不开"
    print(f"  -> 判定: {verdict}")
    return gap_raw, gap_mel, verdict, raw_pos, mel_pos


print("=" * 78)
print("场景 A：咬钩音音高恒定（游戏不改 pitch）")
print("=" * 78)
sigA, mA = build_session([1.0] * 8)
gA = analyse(sigA, mA, "恒定音高")

print()
print("=" * 78)
print("场景 B：咬钩音音高随机（模拟 pitch 随机化）")
print("=" * 78)
sigB, mB = build_session(list(rng.uniform(0.8, 1.25, 8)))
gB = analyse(sigB, mB, "随机音高")

print()
print("=" * 78)
print("验收")
print("=" * 78)
ok = True
if not (gA[2] == "raw 可用"):
    print("  [FAIL] 恒定音高场景应判定 raw 可用"); ok = False
else:
    print("  [ok] 恒定音高 -> raw 可用")
if not (gB[2] == "改用 log-mel"):
    print(f"  [FAIL] 随机音高场景应判定改用 log-mel，实际 {gB[2]}"); ok = False
else:
    print("  [ok] 随机音高 -> 改用 log-mel")
print("\n总体:", "通过" if ok else "未通过")
raise SystemExit(0 if ok else 1)
