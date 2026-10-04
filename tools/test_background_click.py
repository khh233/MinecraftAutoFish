"""后台点击自测：真的把消息投递给一个窗口，验证它收到了什么。

做法：开一个自己的 tkinter 窗口并把它的 WndProc 换成我们的回调（subclass），
然后用 mcfish.autofish.background_click 投递点击，检查收到的
WM_MOUSEMOVE / WM_RBUTTONDOWN / WM_RBUTTONUP（wParam 的 MK_ 位、lParam 的坐标）。

这样能在**不碰游戏**的前提下证明「消息投递」这一段是真的通的；至于具体某个游戏
认不认这种投递，只能进游戏里试（`--background-test` 或 GUI 的「后台试一下」）。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

from mcfish import autofish as af

# 也可以只当「靶子窗口」用：开一个窗口挂着，让你在另一个终端跑
#   python -m mcfish.autofish --window "mcfish bgtest target" --background-test
# 这样能在不碰游戏的前提下验证命令行这条链。
_ap = argparse.ArgumentParser(description="后台点击自测 / 靶子窗口")
_ap.add_argument("--hold", type=float, default=0,
                 help="只开一个标题为 'mcfish bgtest target' 的窗口，保持这么多秒")
_ap.add_argument("--title", default="mcfish bgtest target")
_args = _ap.parse_args()

if _args.hold > 0:
    _root = tk.Tk()
    _root.title(_args.title)
    _root.geometry("360x220")
    tk.Label(_root, text=f"靶子窗口：{_args.title}\n保持 {_args.hold:g} 秒",
             justify="center").pack(expand=True)
    _end = time.time() + _args.hold
    while time.time() < _end:
        _root.update()
        time.sleep(0.02)
    _root.destroy()
    print(f"靶子窗口已关闭（标题 {_args.title!r}）")
    raise SystemExit(0)

user32 = ctypes.WinDLL("user32", use_last_error=True)
GWL_WNDPROC = -4
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, ctypes.c_uint,
                             ctypes.c_size_t, ctypes.c_ssize_t)

if hasattr(user32, "SetWindowLongPtrW"):
    _set_wndproc = user32.SetWindowLongPtrW
    _set_wndproc.restype = ctypes.c_void_p
    _set_wndproc.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
else:                                   # pragma: no cover - 32 位 Python
    _set_wndproc = user32.SetWindowLongW
    _set_wndproc.restype = ctypes.c_long
    _set_wndproc.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]

user32.CallWindowProcW.restype = LRESULT
user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, ctypes.c_uint,
                                   ctypes.c_size_t, ctypes.c_ssize_t]

WATCH = {af.WM_MOUSEMOVE: "WM_MOUSEMOVE", af.WM_RBUTTONDOWN: "WM_RBUTTONDOWN",
         af.WM_RBUTTONUP: "WM_RBUTTONUP", af.WM_LBUTTONDOWN: "WM_LBUTTONDOWN",
         af.WM_LBUTTONUP: "WM_LBUTTONUP"}

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  [{'ok' if cond else 'FAIL'}] {name}: {detail}", flush=True)


seen = []
keep_alive = []

root = tk.Tk()
root.title("mcfish background-click self test")
root.geometry("360x220")
root.update()
hwnd = int(root.winfo_id())

old_proc = _set_wndproc(ctypes.c_void_p(hwnd), GWL_WNDPROC, None)


def _proc(h, msg, wparam, lparam):
    if msg in WATCH:
        seen.append((WATCH[msg], int(wparam), int(lparam)))
    return user32.CallWindowProcW(ctypes.c_void_p(old_proc), h, msg, wparam, lparam)


proc = WNDPROC(_proc)
keep_alive.append(proc)                 # 千万别让回调被 GC 掉，否则窗口崩
_set_wndproc(ctypes.c_void_p(hwnd), GWL_WNDPROC, ctypes.cast(proc, ctypes.c_void_p))
root.update()


def pump(seconds=0.6):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.01)


print("=" * 70)
print("1) 客户区坐标")
print("=" * 70)
cx, cy = af.client_center(hwnd)
check("客户区中心点合理", cx > 0 and cy > 0, f"({cx},{cy})")

print()
print("=" * 70)
print("2) 投递右键：窗口应该收到 MOVE + RDOWN + RUP")
print("=" * 70)
seen.clear()
ok, msg = af.background_click(hwnd, "right", hold_ms=20)
pump()
check("投递返回成功", ok, msg)
names = [s[0] for s in seen]
check("收到了 WM_MOUSEMOVE", "WM_MOUSEMOVE" in names, str(names))
check("收到了 WM_RBUTTONDOWN", "WM_RBUTTONDOWN" in names, str(names))
check("收到了 WM_RBUTTONUP", "WM_RBUTTONUP" in names, str(names))
down = next((s for s in seen if s[0] == "WM_RBUTTONDOWN"), None)
if down:
    check("按下时 wParam 带 MK_RBUTTON", down[1] & af.MK_RBUTTON == af.MK_RBUTTON,
          hex(down[1]))
    lx, ly = down[2] & 0xFFFF, (down[2] >> 16) & 0xFFFF
    check("lParam 是客户区中心", (lx, ly) == (cx, cy), f"({lx},{ly}) vs ({cx},{cy})")
order = [n for n in names if n != "WM_MOUSEMOVE"]
check("顺序是先按下后抬起", order[:2] == ["WM_RBUTTONDOWN", "WM_RBUTTONUP"], str(order))

print()
print("=" * 70)
print("3) 投递左键")
print("=" * 70)
seen.clear()
ok, msg = af.background_click(hwnd, "left", hold_ms=20)
pump()
names = [s[0] for s in seen]
check("收到了 WM_LBUTTONDOWN/UP",
      "WM_LBUTTONDOWN" in names and "WM_LBUTTONUP" in names, str(names))

print()
print("=" * 70)
print("4) 不抢焦点：投递前后前台窗口没变")
print("=" * 70)
fg_before = int(user32.GetForegroundWindow() or 0)
seen.clear()
af.background_click(hwnd, "right", hold_ms=20)
pump()
fg_after = int(user32.GetForegroundWindow() or 0)
check("前台窗口没有被抢走", fg_before == fg_after, f"{fg_before} -> {fg_after}")
check("目标窗口不是前台（本来就没激活它）",
      af.foreground_is(hwnd) is False or fg_before != hwnd,
      f"foreground_is={af.foreground_is(hwnd)}")
if seen:
    check("后台点击照常送达", any(s[0] == "WM_RBUTTONDOWN" for s in seen), str(seen))

print()
print("=" * 70)
print("5) 窗口没了应该报错而不是静默")
print("=" * 70)
root2 = tk.Tk()
root2.withdraw()
hwnd2 = int(root2.winfo_id())
root2.destroy()
time.sleep(0.2)
ok, msg = af.background_click(hwnd2, "right")
check("死窗口返回失败并给说明", (not ok) and msg, msg)

print()
print("=" * 70)
print("6) 没窗口时的行为")
print("=" * 70)
click, err = af.make_clicker(background=True, hwnd=None)
check("后台点击器缺窗口 -> 报错", click is None and err, err)

print()
print("=" * 70)
print("7) 后台模式真跑一遍：合成 2 次咬钩 -> 窗口应收到 4 次右键（2 抛 2 收）")
print("=" * 70)
import threading

import numpy as np

from mcfish.detector import make_template_from_audio

tmpl, _info = make_template_from_audio(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "mcfish", "splash.ogg"), sr=af.SR, length=0.35)
FL = int(af.SR * af.FRAME_S)
feed_stop = threading.Event()


def synth_feed(on_frame):
    """每若干帧塞进一个完整模板 —— 模拟「咬钩音」周期性出现。"""
    def run():
        period = (len(tmpl) // FL) + 10
        n = pos = 0
        while not feed_stop.is_set():
            fr = np.zeros(FL)
            if pos < len(tmpl):
                m = min(FL, len(tmpl) - pos)
                fr[:m] = tmpl[pos:pos + m] * 900
                pos += m
            on_frame(fr)
            n += 1
            if n % period == 0:
                pos = 0
            time.sleep(0.001)
    threading.Thread(target=run, daemon=True).start()


seen.clear()
fg_before = int(user32.GetForegroundWindow() or 0)
stats = af.run_fishing_session(
    hwnd=hwnd, title="mcfish bgtest target", template=tmpl, feature="raw",
    thresh=0.6, cooldown=0.3, cast_delay=0.05, max_wait=4.0, count=2,
    background=True, feed_frames=synth_feed, log=lambda m: None,
    stop=feed_stop, tick=0)
feed_stop.set()
pump(0.3)
downs = [s for s in seen if s[0] == "WM_RBUTTONDOWN"]
lifts = [s for s in seen if s[0] == "WM_RBUTTONUP"]
fg_after = int(user32.GetForegroundWindow() or 0)

check("钓满 2 次", stats.get("done") == 2,
      f"done={stats.get('done')} timeouts={stats.get('timeouts')}")
check("窗口收到 4 次右键按下（2 抛 + 2 收）", len(downs) == 4, f"{len(downs)} 次")
check("按下/抬起成对", len(lifts) == 4, f"{len(lifts)} 次")
check("全程没抢焦点", fg_before == fg_after, f"{fg_before} -> {fg_after}")
check("没有报错", not stats.get("error"), stats.get("error"))

# 收尾：把 WndProc 还回去，再关窗口
try:
    _set_wndproc(ctypes.c_void_p(hwnd), GWL_WNDPROC, ctypes.c_void_p(old_proc))
except Exception:
    pass
root.destroy()

ok = all(c for _, c in results)
print()
print("=" * 70)
for n, c in results:
    print(f"  [{'ok' if c else 'FAIL'}] {n}")
print("\n总体:", "通过" if ok else "未通过")
raise SystemExit(0 if ok else 1)
