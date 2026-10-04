# autofish —— Minecraft 音频触发钓鱼
# Copyright (C) 2026 欧金金 · 以 GNU GPL v3 发布（无担保），全文见 LICENSE。
"""采集游戏回环音频 + 标记咬钩时刻，用于标定。

用法：
    # 1) 先确认回环能不能采到游戏声音（10 秒，看电平条动不动）
    python -m mcfish.record --seconds 10 --probe

    # 2) 正式标定：每次咬钩/收杆时按一下回车
    python -m mcfish.record --seconds 90 --out calib/session1

    # 3) 已有模板时直接看实时分数
    python -m mcfish.record --seconds 30 --template calib/template_splash.npy

录完会写 <out>.npy（float32 单声道 32 kHz）和 <out>.json（标记）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcfish.paths import CALIB, ensure_dirs, template as template_path

SR = 32000
FRAME_S = 0.08


def _try_import_soundcard():
    try:
        import soundcard as sc
        return sc
    except Exception as e:
        print(f"[!] 无法导入 soundcard: {e}")
        print("    安装: pip install soundcard")
        print("    (需要 numpy; soundcard 是纯 Python wheel, 依赖 cffi)")
        return None


def open_loopback(sc, sr: int, channels: int = 2, block_ms: float = 20.0):
    """打开默认扬声器的回环采集。返回 (recorder, speaker_name)。"""
    spk = sc.default_speaker()
    mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
    rec = mic.recorder(samplerate=sr, channels=channels,
                       blocksize=int(sr * block_ms / 1000 * 4))
    return rec, str(spk.name)


def check_deps():
    ok = True
    for name in ("numpy", "soundcard", "scipy", "pydirectinput"):
        try:
            m = __import__(name)
            print(f"  [ok]   {name:<14} {getattr(m, '__version__', '')}")
        except Exception as e:
            kind = "必需" if name in ("numpy", "soundcard") else "可选"
            print(f"  [miss] {name:<14} ({kind}) {type(e).__name__}")
            if name in ("numpy", "soundcard"):
                ok = False
    return ok


def _read_keys() -> list[str]:
    """Windows 下非阻塞读取所有待处理按键。返回 ['enter'] / ['space'] 列表。"""
    try:
        import msvcrt
    except ImportError:
        return []
    out = []
    while msvcrt.kbhit():
        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            out.append("enter")
        elif ch == " ":
            out.append("space")
    return out


def _bar(level: float, width: int = 40, peak: float = 0.05) -> str:
    n = int(min(1.0, level / peak) * width)
    return "#" * n + "-" * (width - n)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="回环录音 + 咬钩标记")
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--out", default=os.path.join(CALIB, "session"))
    ap.add_argument("--sr", type=int, default=SR)
    ap.add_argument("--channels", type=int, default=2)
    ap.add_argument("--hp", type=float, default=200.0,
                    help="高通截止 Hz；MC 水花在低频, 默认 200（原项目是 1000）")
    ap.add_argument("--template", default=None,
                    help="给一个模板 .npy，实时显示匹配分数")
    ap.add_argument("--template-from", default=None,
                    help="一键做模板：播放这个音频文件（如 splash.ogg），"
                         "录下来自动截取最响的一段存成模板")
    ap.add_argument("--template-out", default=template_path(),
                    help="--template-from 的输出路径")
    ap.add_argument("--template-len", type=float, default=0.35,
                    help="模板截取长度秒数（默认 0.35；太长会拉高检测延迟）")
    ap.add_argument("--probe", action="store_true",
                    help="只验证回环是否有信号，不写标记流程")
    args = ap.parse_args(argv)

    print("=" * 72)
    print("依赖检查")
    print("=" * 72)
    deps_ok = check_deps()
    if not deps_ok:
        print("\n[!] 缺少必需依赖，先 pip install soundcard numpy")
        return 1

    sc = _try_import_soundcard()
    if sc is None:
        return 1

    from mcfish.detector import HighPass, ReelDetector

    hp = HighPass(args.sr, args.hp) if args.hp > 0 else None
    det = None
    if args.template:
        tmpl = np.load(args.template)
        det = ReelDetector(tmpl, sr=args.sr, thresh=0.5, on_fire=lambda s: None)
        print(f"\n已载入模板 {args.template} ({len(tmpl)/args.sr:.3f} s)")

    try:
        rec, spk_name = open_loopback(sc, args.sr, args.channels)
    except Exception as e:
        print(f"\n[!] 打开回环失败: {e}")
        print("    可能原因：没有任何播放设备 / 设备名不匹配 / 驱动不支持回环。")
        return 1

    print(f"\n默认播放设备: {spk_name}")
    print(f"采样率 {args.sr} Hz, {args.channels} ch, 增益 {'HP '+str(args.hp)+' Hz' if hp else '无'}")
    print("=" * 72)
    if args.template_from:
        print("★ 一键做模板")
        print(f"  请把 {args.template_from} 用播放器播一遍（声音从默认播放设备出）。")
        print(f"  正在录 {args.seconds:.0f} 秒，我会自动截出最响的那段当模板。")
    elif args.probe:
        print("探针模式：请让游戏播放任意声音（走路/挖方块）。看下面电平条是否动。")
    else:
        print("标定模式：")
        print("  每次【咬钩/水花】-> 按【回车】")
        print("  每次【抛竿入水】  -> 按【空格】  (用来确认 splash 响在哪一步)")
    print("Ctrl+C 提前结束。")
    print("=" * 72)

    markers: list[float] = []          # 咬钩（回车）
    cast_markers: list[float] = []     # 抛竿/入水（空格）
    chunks: list[np.ndarray] = []      # 已高通（标定用）
    raw_chunks: list[np.ndarray] = []  # 未高通（探针用）
    t0 = None
    frame_len = int(args.sr * FRAME_S)
    block = max(256, int(args.sr * 0.02))
    n_blocks = 0

    try:
        with rec:
            t_start = time.perf_counter()
            carry = np.zeros(0, dtype=np.float64)
            pk = 0.0
            while True:
                el = time.perf_counter() - t_start
                if el >= args.seconds:
                    break
                data = rec.record(numframes=block)
                mono = data.mean(axis=1) if data.ndim > 1 else data
                raw_chunks.append(mono.astype(np.float32))
                x = hp.process(mono) if hp else mono
                if hp:
                    chunks.append(x.astype(np.float32))

                # 攒够一帧就过检测器
                carry = np.concatenate([carry, x])
                while len(carry) >= frame_len:
                    fr = carry[:frame_len]
                    carry = carry[frame_len:]
                    if det is not None:
                        det.feed(fr)

                # 按键在「每个音频块」读一次（放在帧循环里会漏按键）
                for key in _read_keys():
                    if key == "enter":
                        markers.append(round(el, 3))
                    else:
                        cast_markers.append(round(el, 3))

                rms = float(np.sqrt(np.mean(x ** 2)) + 1e-12)
                pk = max(pk * 0.995, rms)
                msg = f"\r  t={el:6.1f}s  rms={rms:7.5f} [{_bar(rms)}]"
                if det is not None:
                    msg += f"  score={det.last_score:.3f}"
                msg += f"  咬钩={len(markers)} 抛竿={len(cast_markers)}"
                sys.stdout.write(msg + "   ")
                sys.stdout.flush()
    except KeyboardInterrupt:
        print("\n(提前结束)")
    finally:
        print()

    audio = (np.concatenate(raw_chunks) if raw_chunks else np.zeros(0, dtype=np.float32)) \
        if args.probe else (np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32))
    if audio.size == 0:
        print("[!] 没有采到任何音频")
        return 1

    # ---- 一键做模板：从录到的音频里截最响的一段 ----
    if args.template_from:
        peak = float(np.max(np.abs(audio)))
        rms = float(np.sqrt(np.mean(audio ** 2)))
        print("=" * 72)
        print(f"录制结果: {len(audio)/args.sr:.2f} s  峰值 {peak:.5f}  RMS {rms:.6f}")
        if peak < 1e-4:
            print("[!] 几乎是静音，没录到声音。")
            print("    1) 确认播放器真的出声了（系统音量、播放器音量）")
            print("    2) 确认声音从「默认播放设备」出")
            print("    3) 先跑 --probe 验证回环")
            return 2

        n = len(audio)
        wl = int(args.template_len * args.sr)
        # 用短时 RMS 找最响的起点
        hop = max(1, int(args.sr * 0.005))
        nf = max(1, (n - wl) // hop) if n > wl else 1
        best_i, best_e = 0, -1.0
        for i in range(nf):
            a = i * hop
            e = float(np.mean(audio[a:a + wl] ** 2))
            if e > best_e:
                best_e, best_i = e, a
        seg = audio[best_i:best_i + wl].astype(np.float64)
        seg = seg - seg.mean()

        out = args.template_out
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        np.save(out, seg)
        onset = best_i / args.sr
        print(f"模板: 起点 {onset:.3f}s，长度 {len(seg)/args.sr:.3f}s，"
              f"RMS {float(np.sqrt(np.mean(seg**2))):.5f}")
        print(f"已保存 -> {out}")
        print()
        print("下一步（验证模板能不能认出这个音）：")
        print(f"  python -m mcfish.record --seconds 20 --template {out}")
        print("  再放一遍 splash.ogg，看 score 能不能到 0.8+。")
        print()
        print("然后就能跑 autofish：")
        print(f"  python mcfish/autofish.py --window Minecraft --template {out} "
              f"--count 50 --dry-run")
        return 0

    # --probe 时只判断「有没有信号」，不做逐帧滤波（避免采样率假设）
    if args.probe:
        peak = float(np.max(np.abs(audio)))
        rms = float(np.sqrt(np.mean(audio ** 2)))
        print("=" * 72)
        print(f"探针结果: 时长 {len(audio)/args.sr:.2f} s 峰值 {peak:.5f} RMS {rms:.6f}")
        if peak < 1e-4:
            print("[!] 几乎是静音 —— 回环没采到游戏声音。")
            print("    排查: 1) 游戏音量/系统音量是否为 0")
            print("          2) 游戏是否用了独占模式 / ASIO 直通")
            print("          3) 声音是否从「默认播放设备」出（换设备要改默认）")
            print("          4) 用播放器放一首歌，边放边看电平条动不动")
            return 2
        print("[ok] 回环采集可用，有信号")
        if args.out:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
            np.save(args.out + ".npy", audio)
            print(f"已保存 {args.out}.npy（未经高通；如需 32k 单声道分析请用非 probe 模式）")
        return 0

    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(audio ** 2)))
    print("=" * 72)
    print(f"时长 {len(audio)/args.sr:.2f} s  峰值 {peak:.5f}  RMS {rms:.6f}  标记 {len(markers)} 个")
    if peak < 1e-4:
        print("[!] 几乎是静音 —— 回环没采到游戏声音。")
        print("    排查: 1) 游戏音量/系统音量是否为 0  2) 游戏是否用了独占模式/ASIO")
        print("          3) 声音是否从「默认播放设备」出  4) 换 --probe 边放音乐边看电平")
        return 2
    print("[ok] 有信号，回环采集可用")

    if len(markers) < 3:
        print(f"[!] 标记只有 {len(markers)} 个，标定至少需要 5 个咬钩事件")
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    np.save(args.out + ".npy", audio)
    with open(args.out + ".json", "w", encoding="utf-8") as f:
        json.dump({"sr": args.sr, "hp": args.hp, "markers": markers,
                   "cast_markers": cast_markers,
                   "peak": peak, "rms": rms}, f, ensure_ascii=False, indent=2)
    print(f"已写 {args.out}.npy / {args.out}.json")
    print(f"  咬钩标记 {len(markers)} 个，抛竿标记 {len(cast_markers)} 个")
    if cast_markers:
        print("  (有抛竿标记：calibrate 会额外报告「咬钩模板对抛竿音」的分数，")
        print("   用来确认 splash 到底响在咬钩还是抛竿)")
    print(f"下一步: python -m mcfish.calibrate --in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
