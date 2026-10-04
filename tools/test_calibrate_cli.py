"""End-to-end: write a synthetic session, then run the real calibrate CLI on it.

Also verifies the cast-vs-bite cross-check distinguishes:
  session X: bite sound differs from cast sound  -> should report [ok]
  session Y: same sound on cast and bite         -> should warn
"""
import sys, os, json, subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from mcfish.detector import _pitch_variants

PY = sys.executable
SR = 32000
import _refs
bite, cast = _refs.load("dodge", "counter")   # 分别充当咬钩音 / 抛竿音
rng = np.random.default_rng(21)

_bg = np.convolve(rng.normal(0, 1, SR * 4), np.ones(300) / 300)[: SR * 4]
_bg /= np.abs(_bg).max()

# 写进 tools/_tmp_calib/（已被 .gitignore 忽略）。
# **不要改用 tempfile.mkdtemp()**：本机 %LOCALAPPDATA%\Temp 对 python 进程不可写，
# 而 os.access() 偏偏返回 True，于是 CPython 的 _get_default_tempdir() 试写失败后
# 一路回退到候选列表末尾的 os.getcwd() —— 临时目录会静默落在当前工作目录里。
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_calib")


def make(name, bite_sound, cast_sound, n=8, dur=60.0, level=1.0):
    N = int(dur * SR)
    sig = _bg[np.arange(N) % len(_bg)].copy() * 0.02
    marks, casts = [], []
    slots = np.linspace(4.0, dur - 4.0, n * 2)
    for i in range(n):
        tc = float(slots[2 * i])
        tb = float(slots[2 * i + 1])
        ev = cast_sound * level
        i0 = int(tc * SR); k = min(len(ev), N - i0); sig[i0:i0 + k] += ev[:k]
        casts.append(round(tc, 3))
        ev = bite_sound * level
        i0 = int(tb * SR); k = min(len(ev), N - i0); sig[i0:i0 + k] += ev[:k]
        marks.append(round(tb, 3))
    os.makedirs(OUT, exist_ok=True)
    np.save(os.path.join(OUT, name + ".npy"), sig.astype(np.float32))
    with open(os.path.join(OUT, name + ".json"), "w", encoding="utf-8") as f:
        json.dump({"sr": SR, "hp": 0, "markers": marks, "cast_markers": casts},
                  f, ensure_ascii=False)
    return os.path.join(OUT, name)


def run(path):
    r = subprocess.run([PY, "-m", "mcfish.calibrate", "--in", path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return r


pX = make("sessX", bite, cast)          # 咬钩/抛竿 不同音
pY = make("sessY", bite, bite)          # 咬钩/抛竿 同音

print("=" * 78)
print("会话 X：咬钩音 != 抛竿音")
print("=" * 78)
rx = run(pX)
print(rx.stdout[-2600:] if rx.stdout else "")
if rx.returncode != 0:
    print("STDERR:", rx.stderr[-1500:])

print("=" * 78)
print("会话 Y：咬钩音 == 抛竿音（应给出警告）")
print("=" * 78)
ry = run(pY)
out = ry.stdout or ""
# 只打印交叉检查段和结论
for key in ("抛竿音交叉检查", "判定", "结论", "[!]", "[ok]"):
    for line in out.splitlines():
        if key in line:
            print("   ", line.strip())

print()
print("=" * 78)
print("验收")
print("=" * 78)
ok = True
if rx.returncode != 0 or "咬钩模板对抛竿音" not in (rx.stdout or ""):
    print("  [FAIL] 会话 X 未产出抛竿交叉检查"); ok = False
elif "[ok] 抛竿音得分明显更低" not in rx.stdout:
    print("  [FAIL] 会话 X 应报告抛竿音明显更低"); ok = False
else:
    print("  [ok] 会话 X：识别出抛竿音与咬钩音不同，可作触发")

if ry.returncode != 0 or "该音效在抛竿时也会响" not in (ry.stdout or ""):
    print("  [FAIL] 会话 Y 应警告该音在抛竿时也响"); ok = False
else:
    print("  [ok] 会话 Y：识别出该音在抛竿时也响，会误触发")

print("\n总体:", "通过" if ok else "未通过")
raise SystemExit(0 if ok else 1)
