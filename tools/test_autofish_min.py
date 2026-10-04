"""Minimal check that run_session drives cast -> detect -> reel -> repeat."""
import sys, os, threading, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from mcfish.autofish import run_session, FRAME_S, SR

import _refs
(tmpl,) = _refs.load("dodge")      # 上游参考波形，未随仓库分发；缺则跳过
FL = int(SR * FRAME_S)
print(f"frame={FL} samples, template={len(tmpl)}", flush=True)

stop = threading.Event()
casts, reels = [], []


def feed(on_frame):
    """Feed on this thread; inject one whole bite (spread across frames) per block."""
    def run():
        period = max(12, (len(tmpl) // FL) + 8)   # block length in frames
        n = 0
        pos = 0                                   # how much of the template is sent
        while not stop.is_set():
            fr = np.zeros(FL)
            if pos < len(tmpl):                   # still streaming the bite
                m = min(FL, len(tmpl) - pos)
                fr[:m] = tmpl[pos:pos + m] * 900
                pos += m
            on_frame(fr)
            n += 1
            if n % period == 0:
                pos = 0                           # next bite starts
            time.sleep(0.001)
    t = threading.Thread(target=run, daemon=True)
    t.start()


def on_status(kind, st):
    tag = "CAST" if kind == "cast" else ("REEL" if kind == "hit" else "TIMEOUT")
    print(f"  {tag} done={st['done']} last={st['last']:.3f} frames={st['frames']}",
          flush=True)
    (casts if kind == "cast" else reels if kind == "hit" else []).append(1)


t0 = time.perf_counter()
res = run_session(tmpl, feed, lambda: None, count=3, thresh=0.6, cooldown=0.2,
                  cast_delay=0.05, max_wait=3.0, on_status=on_status, stop=stop)
stop.set()
el = time.perf_counter() - t0
print(f"\n结果: done={res['done']}/3 timeouts={res['timeouts']} "
      f"frames={res['frames']} casts={len(casts)} reels={len(reels)} 用时 {el:.1f}s",
      flush=True)
ok = res["done"] == 3 and len(casts) == 3 and res["timeouts"] == 0
print("验收:", "通过" if ok else "未通过", flush=True)
raise SystemExit(0 if ok else 1)
