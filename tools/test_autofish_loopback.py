"""端到端：真实回环 + 真实播放，跑一遍完整的「抛竿 -> 等咬钩音 -> 收杆」。

这是最接近实机的验证 —— 不碰游戏，但除了游戏本身之外全都是真的：
  * 模板来自真回环录制（autocal）
  * 音频来自真回环采集（AudioSource）
  * 咬钩音是**真的播出来**的，而且和 MC 一样**每次随机音高**
  * 检测、循环、超时恢复都走生产代码

因为要真播声音，跑之前请确保没有别的程序在放音频（会被当成环境音混进来）。

用法：
    python tools/test_autofish_loopback.py
    python tools/test_autofish_loopback.py --count 4 --pitches 0.5,0.7,1.0
    python tools/test_autofish_loopback.py --skip          # 没声卡/不想出声时跳过
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mcfish.autofish import (AudioSource, DEFAULT_THRESH, FRAME_S, SR, autocal_template,
                             build_detector, play_once, run_session)
from mcfish.detector import load_audio_file, pitch_shift

SOUND = os.path.join(ROOT, "mcfish", "splash.ogg")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="真实回环端到端测试")
    ap.add_argument("--sound", default=SOUND)
    ap.add_argument("--count", type=int, default=4, help="钓几次")
    ap.add_argument("--pitches", default="0.5,0.63,0.79,1.0",
                    help="每次咬钩用的音高倍率（模拟 MC 的随机 pitch）")
    ap.add_argument("--feature", default="mel", choices=["raw", "mel"])
    ap.add_argument("--thresh", type=float, default=None)
    ap.add_argument("--gap", type=float, default=1.2, help="每次咬钩之间的间隔")
    ap.add_argument("--skip", action="store_true", help="直接跳过（返回 0）")
    args = ap.parse_args(argv)

    if args.skip:
        print("已跳过（--skip）")
        return 0

    print("=" * 78)
    print("端到端：真实回环 + 真实播放（每次随机音高）")
    print("=" * 78)

    # ---- 1) 自校准模板 ----
    print("\n[1] 自校准：播一遍 splash.ogg，录回环，用录音当模板")
    tmpl, msg, perr = autocal_template(args.sound, sr=SR, hp=200.0, length=0.35)
    if tmpl is None:
        print(f"    [!] 失败: {msg}")
        if perr:
            print(f"        播放报错: {perr}")
        print("    没有可用声卡就没法跑这个测试 —— 用 --skip 跳过。")
        return 1
    print(f"    [ok] {msg}")

    raw, file_sr = load_audio_file(args.sound, None)      # 原始采样率，播放用
    thresh = args.thresh if args.thresh is not None else DEFAULT_THRESH[args.feature]
    print(f"    模板 {len(tmpl)} 采样  feature={args.feature} 阈值={thresh}")

    # ---- 2) 真实采集 + 假鱼 ----
    src = AudioSource(sr=SR, frame_s=FRAME_S, hp=200.0)
    stop = threading.Event()
    pitches = [float(x) for x in args.pitches.split(",")]
    played = []

    def fish():
        """每隔一段时间播一次咬钩音，音高依次取 --pitches（= MC 的随机 pitch）。"""
        i = 0
        # 咬钩的节奏要比主循环「抛竿+等」的一轮更长，否则会在收杆冷却期响，
        # 被当作旧事件丢掉，测试就变成在考验时序而不是检测本身
        period = args.gap + 1.6
        time.sleep(2.0)                       # 先让第一根竿抛出去
        while not stop.is_set() and i < args.count * 3:
            r = pitches[i % len(pitches)]
            try:
                play_once(pitch_shift(raw, r), SR, file_sr, gain=0.7)
                played.append(r)
            except Exception as e:
                print(f"    [!] 播放失败: {e}")
                break
            i += 1
            if stop.wait(period):
                break

    def feed_frames(on_frame):
        src.start(on_frame)

    events = []

    def on_status(kind, st):
        if kind == "tick":
            return
        events.append(kind)
        print(f"    [{kind}] done={st['done']} last={st['last']:.3f}")

    print(f"\n[2] 开跑：钓 {args.count} 次，每次咬钩音高 "
          f"{['%.2f' % p for p in pitches]}（每次间隔 {args.gap}s）")
    th = threading.Thread(target=fish, daemon=True)
    th.start()
    t0 = time.perf_counter()
    stats = run_session(tmpl, feed_frames, lambda: None, count=args.count,
                        thresh=thresh, cooldown=0.4, cast_delay=args.gap,
                        max_wait=15.0, feature=args.feature,
                        on_status=on_status, stop=stop, tick=0)
    stop.set()
    src.stop()
    el = time.perf_counter() - t0

    # ---- 3) 验收 ----
    print("\n" + "=" * 78)
    print("验收")
    print("=" * 78)
    print(f"  完成 {stats['done']}/{args.count}   超时 {stats['timeouts']}   "
          f"帧 {stats['frames']}   实际播放 {len(played)} 次   用时 {el:.1f}s")
    checks = []
    checks.append(("采到了音频帧", stats["frames"] > 0))
    checks.append((f"钓满 {args.count} 次", stats["done"] == args.count))
    checks.append(("没有超时（说明每次随机音高都认出来了）", stats["timeouts"] == 0))
    for name, ok in checks:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}")
    allok = all(ok for _, ok in checks)
    print("\n总体:", "通过" if allok else "未通过")
    return 0 if allok else 1


if __name__ == "__main__":
    raise SystemExit(main())
