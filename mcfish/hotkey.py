# autofish —— Minecraft 音频触发钓鱼
# Copyright (C) 2026 欧金金 · 以 GNU GPL v3 发布（无担保），全文见 LICENSE。
"""全局热键：不依赖窗口焦点就能触发。

**为什么需要**：前台钓鱼时游戏窗口在前台，界面上的快捷键根本收不到；
后台模式虽然不抢焦点，但界面可能被盖住。所以要用 Win32 的
`RegisterHotKey` 注册**系统级**热键 —— 它在任何前台程序下都能响应。

**为什么不用键盘钩子**：`SetWindowsHookEx(WH_KEYBOARD_LL)` 能实现更灵活的组合键，
但它需要消息循环、并且部分安全软件会拦它，还容易被游戏反作弊误判。
`RegisterHotKey` 是官方推荐的做法，稳定且开销小。代价是热键组合必须全局唯一 ——
如果被别的程序占了，注册会失败（`ERROR_HOTKEY_ALREADY_REGISTERED`），
所以这里会**优雅降级**：占不到就退回窗口内快捷键，不影响主功能。

需要 ctypes（标准库，不用装包）。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

# 修饰键：MOD_ALT=1  MOD_CONTROL=2  MOD_SHIFT=4  MOD_WIN=8
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

MOD_NOREPEAT = 0x4000          # 按住不放只触发一次

WM_HOTKEY = 0x0312
GWLP_WNDPROC = -4

_hotkey_ready = False
_user32 = ctypes.WinDLL("user32", use_last_error=True) if hasattr(ctypes, "WinDLL") else None


def _err_name(code: int) -> str:
    try:
        return ctypes.FormatError(code)
    except Exception:
        return f"错误 {code}"


# ctypes 的窗口过程签名。
# **LRESULT 必须用 c_ssize_t**（64 位下是 longlong）而不是 c_long，否则大返回值被截断；
# WPARAM/LPARAM 用 wintypes 里的原生类型，别用 c_long ——那样转发时会报
# `OverflowError: int too long to convert`，而且这个异常发生在窗口过程里，
# 症状是「消息莫名其妙消失」，比直接崩溃更难查。
_WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

# 活着的子类化信息： hwnd -> (WNDPROC 对象, 原窗口过程, 回调表)
#  必须持有 WNDPROC 对象的引用，否则它被 GC 之后 Windows 还留着那个函数指针，
#  一按热键就跳进已释放的内存 —— 典型结果是随机崩溃。
_subclassed: dict = {}


def install(win_id: int, on_start, on_stop,
            start_spec=(MOD_CONTROL, ord("D")),
            stop_spec=(MOD_CONTROL, ord("F"))) -> dict:
    """在窗口 win_id 上注册两个全局热键，并接管它的窗口过程。

    返回 dict: ok / partial / failed / ok_actions
    """
    global _hotkey_ready
    result = {"ok": False, "partial": False, "failed": {}, "ok_actions": []}
    if _user32 is None:
        result["failed"]["all"] = "这个平台不是 Windows"
        return result
    if _hotkey_ready:
        result["ok"] = True
        return result

    _user32.RegisterHotKey.restype = wt.BOOL
    _user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
    _user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_void_p]
    _user32.CallWindowProcW.restype = ctypes.c_ssize_t
    _user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wt.HWND, wt.UINT,
                                        wt.WPARAM, wt.LPARAM]

    table: dict = {}
    for action, spec in (("start", start_spec), ("stop", stop_spec)):
        mods, key = spec
        hkid = 1 if action == "start" else 2
        if _user32.RegisterHotKey(wt.HWND(win_id), hkid, mods | MOD_NOREPEAT, key):
            table[hkid] = on_start if action == "start" else on_stop
            result["ok_actions"].append((hkid, table[hkid]))
        else:
            result["failed"][action] = _err_name(ctypes.get_last_error())

    _hotkey_ready = bool(result["ok_actions"])
    result["ok"] = _hotkey_ready
    result["partial"] = bool(result["ok_actions"]) and bool(result["failed"])
    if not _hotkey_ready:
        return result

    # --- 接管窗口过程，让 WM_HOTKEY 跟着 Tk 自己的消息循环走 ---
    # 另一个线程跑 GetMessage 是**不行**的（已实测）：消息队列是线程内共享的，
    # Tk 的 mainloop 和那个线程会互相抢消息，结果就是「注册成功但按了没反应」。
    old = _user32.SetWindowLongPtrW(wt.HWND(win_id), ctypes.c_int(GWLP_WNDPROC), 0)
    if not old:
        result["failed"]["subclass"] = "无法接管窗口过程"
        result["ok"] = result["partial"] = False
        return result

    def make(old_proc, tbl):
        def proc(hwnd, msg, wparam, lparam):
            if msg == WM_HOTKEY:
                fn = tbl.get(int(wparam))
                if fn is not None:
                    try:
                        fn()
                    except Exception:
                        # 热键回调抛异常绝不能影响窗口过程
                        pass
                    return 0                    # 已处理，不传给 Tk
            # 其余消息原样交回 Tk 自己的窗口过程
            return _user32.CallWindowProcW(
                ctypes.cast(_WNDPROC(old_proc), ctypes.c_void_p).value,
                wt.HWND(hwnd), ctypes.c_uint(msg),
                wt.WPARAM(wparam), wt.LPARAM(lparam))
        return proc

    # 先建好并**留住引用**（否则会被 GC，Windows 那边就成野指针了），
    # 再把它装到窗口上 —— 顺序不能反。
    proc_obj = _WNDPROC(make(old, table))
    _subclassed[win_id] = (proc_obj, old, table)
    ctypes.set_last_error(0)
    prev = _user32.SetWindowLongPtrW(
        wt.HWND(win_id), ctypes.c_int(GWLP_WNDPROC),
        ctypes.cast(proc_obj, ctypes.c_void_p))
    if not prev:
        # 装不上就退回去，千万别让热键处于「注册了但没人收」的状态
        _subclassed.pop(win_id, None)
        result["failed"]["subclass"] = _err_name(ctypes.get_last_error())
        result["ok"] = result["partial"] = False
        for hkid in list(table):
            _user32.UnregisterHotKey(wt.HWND(win_id), hkid)
        result["ok_actions"] = []
        _hotkey_ready = False
    return result
    return result


def unregister_all(win_id: int | None = None) -> None:
    """注销热键，并把窗口过程换回原来的那个。"""
    global _hotkey_ready
    if win_id is not None and _user32 is not None:
        for hkid in (1, 2):
            _user32.UnregisterHotKey(wt.HWND(win_id), hkid)
        entry = _subclassed.pop(win_id, None)
        if entry:
            _proc_obj, old, _t = entry
            _user32.SetWindowLongPtrW(wt.HWND(win_id), ctypes.c_int(GWLP_WNDPROC),
                                      ctypes.c_void_p(old))
    _hotkey_ready = False


def format_spec(spec) -> str:
    """把 (mods, key) 变成人看的文字，比如 (MOD_CONTROL, ord('D')) -> Ctrl+D。"""
    mods, key = spec
    parts = []
    if mods & MOD_CONTROL:
        parts.append("Ctrl")
    if mods & MOD_ALT:
        parts.append("Alt")
    if mods & MOD_SHIFT:
        parts.append("Shift")
    if mods & MOD_WIN:
        parts.append("Win")
    if key == 32:
        parts.append("Space")
    elif 0x70 <= key <= 0x87:
        parts.append(f"F{key - 0x6F}")
    else:
        parts.append(chr(key).upper())
    return "+".join(parts)
