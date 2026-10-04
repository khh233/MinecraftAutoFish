"""Verify the NCC contract: bounded to [-1,1], 0 in silence, 1 on the template."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from mcfish.detector import ReelDetector

FL = 2560
rng = np.random.default_rng(0)
import _refs
(short,) = _refs.load("dodge")     # 上游参考波形，未随仓库分发；缺则跳过

results = []


def check(name, cond, detail):
    results.append((name, cond))
    print(f"  [{'ok' if cond else 'FAIL'}] {name}: {detail}")


print("=" * 70)
print("A) 超长模板 2.165s（splash.ogg 量级）")
print("=" * 70)
T = int(32000 * 2.165)
long_t = rng.normal(0, 1, T)
d = ReelDetector(long_t, sr=32000, thresh=0.6, on_fire=lambda s: None)
sil = []
for _ in range(int(3.0 * 32000 / FL)):
    d.feed(np.zeros(FL))
    sil.append(d.last_score)
mx = 0.0
for i in range(0, len(long_t), FL):
    d.feed(long_t[i:i + FL])
    mx = max(mx, d.last_score)
check("静音期分数为 0", max(sil) == 0.0, f"max={max(sil):.6f}")
check("整段模板分数 ~1.0", 0.9 < mx <= 1.0001, f"peak={mx:.4f}")

print()
print("=" * 70)
print("B) 正常长度模板 0.34s（原项目闪避音）")
print("=" * 70)
d2 = ReelDetector(short, sr=32000, thresh=0.6, on_fire=lambda s: None)
for _ in range(int(3.0 * 32000 / FL)):
    d2.feed(np.zeros(FL))
mx2 = 0.0
for i in range(0, len(short), FL):
    d2.feed(short[i:i + FL])
    mx2 = max(mx2, d2.last_score)
check("整段模板分数 ~1.0", 0.9 < mx2 <= 1.0001, f"peak={mx2:.4f}")

print()
print("=" * 70)
print("C) 白噪声不应给高分")
print("=" * 70)
d3 = ReelDetector(short, sr=32000, thresh=0.6, on_fire=lambda s: None)
noise_max = 0.0
for _ in range(200):
    d3.feed(rng.normal(0, 0.05, FL))
    noise_max = max(noise_max, d3.last_score)
check("白噪声分数低", noise_max < 0.35, f"max={noise_max:.4f}")

print()
print("=" * 70)
print("D) 分数上界：任意输入都不得超过 1.0")
print("=" * 70)
d4 = ReelDetector(short, sr=32000, thresh=0.6, on_fire=lambda s: None)
worst = 0.0
for k in range(150):
    kind = k % 3
    if kind == 0:
        fr = rng.normal(0, 1.0, FL)
    elif kind == 1:
        fr = np.zeros(FL)
    else:
        fr = short[(k * FL) % max(1, len(short) - FL):][:FL] * 500
    d4.feed(fr)
    worst = max(worst, d4.last_score)
check("分数上界 <= 1.0", worst <= 1.0001, f"max={worst:.4f}")

print()
print("=" * 70)
print("E) 低频占主导的模板：回归 —— 真实录音下曾冲到 1.96 / 2.98 / 19.2")
print("=" * 70)
# 真实的 splash 回环录音就是这个形状：能量集中在低频，音高变体被重采样+截断后
# 带着明显的直流偏置。若 _unit 不去均值，分子里会多出「窗局部均值 x sum(t)」
# 这一项，而分母只有窗方差，分数就能无限超过 1 —— 阈值 0.6 直接失去意义。
tt = np.arange(int(0.35 * 32000)) / 32000.0
lf_tmpl = np.sin(2 * np.pi * 90 * tt) * 0.8 + 0.15      # 低频 + 直流偏置
d5 = ReelDetector(lf_tmpl, sr=32000, thresh=0.6, on_fire=lambda s: None)
sums = [round(float(t.sum()), 4) for t in d5.templates]
check("音高变体已零均值", max(abs(s) for s in sums) < 1e-9, f"sum(t)={sums}")
rec_like = np.concatenate([np.zeros(4000), lf_tmpl * 3, np.zeros(8000), lf_tmpl * 3])
worst5 = 0.0
for i in range(len(rec_like) // FL):
    d5.feed(rec_like[i * FL:(i + 1) * FL])
    worst5 = max(worst5, d5.last_score)
check("低频模板下上界仍 <= 1.0", worst5 <= 1.0001, f"max={worst5:.4f}")

print()
print("=" * 70)
ok = all(c for _, c in results)
for n, c in results:
    print(f"  [{'ok' if c else 'FAIL'}] {n}")
print("\n总体:", "通过" if ok else "未通过")
raise SystemExit(0 if ok else 1)
