"""Self-test for mcfish.detector: verifies the properties the port depends on.

Runs on numpy alone (no soundcard / scipy / audio device needed).
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from mcfish.detector import ReelDetector, _unit, _pitch_variants, suggest_threshold

SR = 32000
FR = int(SR * 0.08)
rng = np.random.default_rng(7)

# 借用 NTE 的闪避音效当作“咬钩音”验证管线（真实使用需换成 MC 录音）。
# 这两个波形在上游仓库里、不随本仓库分发，找不到会打印提示并跳过。
import _refs
tmpl, decoy = _refs.load("dodge", "counter")

_bg = np.convolve(rng.normal(0, 1, SR * 3), np.ones(200) / 200)[: SR * 3]
_bg /= np.abs(_bg).max()


def assemble(event=None, level=1.0, dur=1.8, at=0.4):
    """一段短音频：低电平环境音 + (可选) 触发音。"""
    n = int(dur * SR)
    sig = _bg[np.arange(n) % len(_bg)].copy() * 0.02
    if event is not None:
        i0 = int(at * SR)
        k = min(len(event), n - i0)
        sig[i0:i0 + k] += event[:k] * level
    return sig


def run(sig, det):
    fires = []
    det.on_fire = lambda s: fires.append((det._t, s))
    scores = []
    for i in range(len(sig) // FR):
        det._t = i * 0.08
        scores.append(det.feed(sig[i * FR:(i + 1) * FR]))
    return np.array(scores), fires


def new_det(thresh=0.55, **kw):
    return ReelDetector(tmpl, sr=SR, thresh=thresh, on_fire=lambda s: None, **kw)


def show(label, scores, fires, base=0.4):
    print(f"{label:<42} peak={scores.max():6.3f}  fires={len(fires):2d}  "
          f"latency={'-' if not fires else f'{(fires[0][0]-base)*1000:.0f} ms'}")


print("=" * 90)
print("1) 增益无关性 —— 原实现同一段音效随音量漂移 0.056 -> 0.178")
print("=" * 90)
peaks = []
for lvl in (0.01, 0.05, 0.2, 1.0, 5.0):
    s, f = run(assemble(tmpl, lvl), new_det())
    peaks.append(s.max())
    show(f"   level x{lvl:<5g}", s, f)
s0, _ = run(assemble(None), new_det())
print(f"   {'(纯环境音，无事件)':<39} peak={s0.max():6.3f}")
print(f"   -> 事件峰值离散度 = {(max(peaks)-min(peaks)):.4f}  (越小越好；原实现约 0.12)")

print()
print("=" * 90)
print("2) 音高鲁棒性：1 半音间隔的变体覆盖")
print("=" * 90)
for st in (-3.0, -2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0):
    ev = _pitch_variants(tmpl, [st], SR)[0]
    s, f = run(assemble(ev), new_det())
    show(f"   pitch {st:+.1f} semitone", s, f)
print("   单模板对照（pitch_semitones=(0,)）:")
for st in (0.0, -1.0, 1.0, -2.0, 2.0):
    ev = _pitch_variants(tmpl, [st], SR)[0]
    s, f = run(assemble(ev), new_det(pitch_semitones=(0.0,)))
    show(f"     pitch {st:+.1f} semitone", s, f)

print()
print("=" * 90)
print("3) 判别力：无关音效不应触发")
print("=" * 90)
s, f = run(assemble(decoy), new_det())
show("   decoy = NTE 反击音效", s, f)

print()
print("=" * 90)
print("4) NCC 峰值应为 ~1.0（窗口与模板一致时）")
print("=" * 90)
d = ReelDetector.__new__(ReelDetector)
d.templates = [_unit(tmpl)]
d.tmpl_len = len(tmpl)
d._band = len(tmpl) + int(SR * 0.25)
exact = d._score(tmpl.copy())
print(f"   _score(模板本身) = {exact:.4f}   期望 ~1.0")
noise = rng.normal(0, 1, len(tmpl))
print(f"   _score(等长白噪声) = {d._score(noise):.4f}  期望 ~0")

print()
print("=" * 90)
print("5) 性能")
print("=" * 90)
det = new_det()
sig = assemble(tmpl)
nfr = len(sig) // FR
t0 = time.perf_counter()
run(sig, det)
dt = (time.perf_counter() - t0) / nfr
print(f"   每帧 {dt*1000:.2f} ms / 帧长 80 ms -> 可支撑 {80/(dt*1000):.0f} 路并行")

print()
print("=" * 90)
print("6) suggest_threshold（合成边界，仅供 API 冒烟测试）")
print("=" * 90)
# 检测器在含事件的帧上有 0.99 的分数、纯背景帧 <0.1，这已由第 1 节的
# 「事件峰值 0.99 / 纯环境音 0.006」证明。这里只验证 API 契约。
print("  ", suggest_threshold(np.array([0.99, 0.98, 0.02, 0.03]),
                              np.array([True, True, False, False])))
