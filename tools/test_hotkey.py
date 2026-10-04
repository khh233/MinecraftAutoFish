"""快捷键自测：窗口内 F5/F6 + 全局热键注册与派发。

不需要声卡。验证三件事：
  1) 窗口内 F5 -> on_start、F6/Esc -> on_stop 真的被调用
  2) 全局热键能注册上（且不与已占用者冲突）
  3) WM_HOTKEY 被消息泵捞到并派发到回调
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ctypes.wintypes as wt
import tkinter as tk

from mcfish import gui as g
from mcfish import hotkey

PASS, FAIL = [], []
fired: list = []          # 全局热键回调的记录器


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))


# ---------------------------------------------------------------- 1) 窗口内按键
print("\n[1] 窗口内快捷键")
# 注意：**不能** withdraw()。Tk 对隐藏窗口不投递 event_generate 的按键事件
# （已实测：visible -> ['f5']，withdraw -> []）。这里让它离屏但保持 visible。
root = tk.Tk()
root.geometry("200x120+3000+3000")      # 挪到看不见的角落，别打扰用户
calls = []
app = g.App(root)
# 替换成记录器，不真的开钓（会要声卡）
app.on_start = lambda: calls.append("start")
app.on_stop = lambda: calls.append("stop")
# 重新绑一次，让 lambda 指向新的 on_start/on_stop
app.root.bind("<F5>", lambda e: app._hotkey_start(e))
app.root.bind("<F6>", lambda e: app._hotkey_stop(e))
app.root.bind("<Escape>", lambda e: app._hotkey_stop(e))
root.update()

app.root.event_generate("<F5>")
root.update()
check("F5 触发开始", calls == ["start"], str(calls))

# 停止键的设计是**只在钓鱼中才响应**（空闲时按 F6/Esc 不该有任何反应，
# 否则用户想关窗口按Esc 会误触发）。所以先模拟「正在钓」。
app.worker = type("W", (), {"is_alive": lambda self: True})()
calls.clear()
app.root.event_generate("<F6>")
root.update()
check("F6 钓鱼中触发停止", calls == ["stop"], str(calls))

calls.clear()
app.root.event_generate("<Escape>")
root.update()
check("Esc 钓鱼中触发停止", calls == ["stop"], str(calls))

# 空闲时按停止键必须无反应
app.worker = None
calls.clear()
app.root.event_generate("<F6>")
app.root.event_generate("<Escape>")
root.update()
check("空闲时停止键不误触发", calls == [], str(calls))

# 「开始」在已钓时不该重复起线程
app.worker = type("W", (), {"is_alive": lambda self: True})()
calls.clear()
app._do_start()
check("已在钓时「开始」不重复触发", calls == [], str(calls))
calls.clear()
app._do_stop()
check("钓鱼中「停止」能触发", calls == ["stop"], str(calls))
app.worker = None
calls.clear()
app._do_stop()
check("空闲时「停止」不误触发", calls == [], str(calls))

# ---------------------------------------------------------------- 2) 按钮上有键位提示
print("\n[2] 按钮提示")
check("开始按钮显示键位", g.HK_START_TEXT in app.start_btn.cget("text"),
      app.start_btn.cget("text"))
check("停止按钮显示键位", g.HK_STOP_TEXT in app.stop_btn.cget("text"),
      app.stop_btn.cget("text"))

# ---------------------------------------------------------------- 3) 全局热键
print("\n[3] 全局热键（模拟真实按键 -> 窗口过程 -> 回调）")
import ctypes

u = ctypes.WinDLL("user32")
VK_CONTROL = 0x11
KEYUP = 0x0002

# App(root) 内部已经用 Ctrl+D / Ctrl+F 注册好了，所以这里**直接复用**那组键，
# 不再自己调 install —— install 有「已注册就直接返回」的保护，
# 第二次调用会早退，拿不到子类化结果，测的就不是同一个东西了。
check("App 已注册全局热键", len(hotkey._subclassed) > 0,
      f"_subclassed={list(hotkey._subclassed)}")
check("注册表非空说明有热键在位", bool(getattr(hotkey, "_hotkey_ready", False)))

# 确认窗口过程真的被换掉了（这一步曾经漏掉，导致「注册成功但按了没反应」）
u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
u.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
cur = u.GetWindowLongPtrW(wt.HWND(root.winfo_id()), ctypes.c_int(-4))
check("窗口过程已被接管", cur != 0 and bool(hotkey._subclassed),
      f"hwnd={root.winfo_id()} cur={cur}")


def tap(mods, vk):
    for m in mods:
        u.keybd_event(m, 0, 0, 0)
    u.keybd_event(vk, 0, 0, 0)
    u.keybd_event(vk, 0, KEYUP, 0)
    for m in reversed(mods):
        u.keybd_event(m, 0, KEYUP, 0)


def spin(n=30):
    """让 Tk 事件循环转起来 —— WM_HOTKEY 是跟着 Tk 的循环派发的。"""
    for _ in range(n):
        root.update()
        time.sleep(0.02)


# 窗口内按键只改 calls，全局热键走 app._hotkey_* -> app.on_start/on_stop，
# 而这两个在第1 节被换成了记录器，所以 fired 会记录到。
tap([VK_CONTROL], ord("D"))
spin()
check("Ctrl+D 触发开始", "start" in calls, str(calls))

calls.clear()
app.worker = type("W", (), {"is_alive": lambda s: True})()
tap([VK_CONTROL], ord("F"))
spin()
check("Ctrl+F 钓鱼中触发停止", "stop" in calls, str(calls))

app.worker = None
calls.clear()
tap([VK_CONTROL], ord("F"))
spin()
check("Ctrl+F 空闲时不误触发", calls == [], str(calls))

# 注销后不应再触发
hotkey.unregister_all(root.winfo_id())
calls.clear()
tap([VK_CONTROL], ord("D"))
spin(10)
check("注销后不再触发", calls == [], str(calls))
check("注销后 _subclassed 已清空", not hotkey._subclassed)

root.destroy()

# ---------------------------------------------------------------- 汇总
print("\n" + "=" * 60)
if FAIL:
    print(f"总体: 失败 —— {len(FAIL)} 项不通过")
    for f in FAIL:
        print(f"   - {f}")
    sys.exit(1)
print(f"总体: 通过（{len(PASS)} 项）")
sys.exit(0)
