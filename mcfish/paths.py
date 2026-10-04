# autofish —— Minecraft 音频触发钓鱼
# Copyright (C) 2026 欧金金 · 以 GNU GPL v3 发布（无担保），全文见 LICENSE。
"""autofish 的路径中枢 —— 全部基于本文件夹，**不引用上级目录的任何东西**。

为什么需要这个模块：原先各处写的是相对路径（`calib/template_x.npy`），
它依赖「启动时的当前工作目录」。双击 bat 恰好会 cd 到项目根所以能用，
但只要从别的目录用绝对路径调`python -m mcfish.autofish`，模板就会存到
奇怪的地方。这里把所有目录钉死成绝对路径，整套代码就与 cwd 无关了。

    ROOT   autofish/            —— 可写目录（设置、模板、日志都落这儿）
    CALIB  autofish/calib/
    LOGS   autofish/logs/
    PKG    autofish/mcfish/     —— 随包走的只读数据（splash.ogg）

**打包成 exe 后有一处关键区别**：`__file__` 指向 Nuitka 的临时解包目录
（`%TEMP%\\onefile_xxx\\`），进程一退整个目录就没了。所以那种情况下：

    ROOT   改成 exe 所在目录  -> 配置/模板/日志留在用户身边，换位置也还在
    PKG    仍是解包目录       -> 内置的 splash.ogg 从这儿读（只读，不用留）

两个概念分清了，exe 才能既「单文件」又能保存设置。
"""
from __future__ import annotations

import os
import sys


def _is_compiled() -> bool:
    """现在跑的是编译出来的 exe，还是源码？

    常见的 `"__compiled__" in globals()` 探测法在这里**不灵**：Nuitka 只给
    「非包内模块」预置这个全局名（源码注释原文 "Pre-seed __compiled__ for
    non-packages"），而本模块在 `mcfish` 包里， globals() 里根本没有它 ——
    误判成源码运行，ROOT 就会指到解包临时目录，用户的设置写完即逝。

    Nuitka 自己的判法（PythonVersions.py）是看 `__main__` 模块有没有
    `__compiled__`：主模块（打包入口，非包内）一定有。这里照抄，外加
    onefile 引导程序设置的环境变量做双保险。
    """
    main = sys.modules.get("__main__")
    if hasattr(main, "__compiled__"):
        return True
    return "__compiled__" in globals() or bool(
        os.environ.get("NUITKA_ONEFILE_DIRECTORY"))


def _exe_dir() -> str | None:
    """运行在编译出来的 exe 里时返回 exe 所在目录，否则 None。

    用 exe 的路径而不是 `os.getcwd()`：从快捷方式启动时 cwd 可能是别处。

    这里要特别提防一件事：**onefile 模式会把整个发行版解压到一个临时目录**
    （`NUITKA_ONEFILE_DIRECTORY` 指向的地方），进程一退就被删掉。要是把 ROOT
    指到那儿，用户的设置、标定模板、日志全都会「写完就没」，还会以为程序在
    丢数据。

    onefile 有两层进程：外层引导程序（用户双击的那个 exe）解压后 spawn 内层
    二进制，并且**把内层的 argv[0] 改写成了临时 exe 的路径**（OnefileBootstrap.c
    第 1469 行）。所以 argv[0]/sys.executable 在内层进程里都不可信，真正可靠
    的是引导程序留下来的 `NUITKA_ORIGINAL_ARGV0`（= 用户双击的原始 exe 路径）。
    即便如此仍然做三重保险：凡是落在解包目录里的候选一律跳过。
    """
    if not _is_compiled() and not getattr(sys, "frozen", False):
        return None

    temp_dir = os.path.normcase(os.environ.get("NUITKA_ONEFILE_DIRECTORY") or "")

    candidates = []
    original = os.environ.get("NUITKA_ORIGINAL_ARGV0")   # 外层原始 exe，最可靠
    if original:
        candidates.append(original)
    if sys.argv:
        candidates.append(sys.argv[0])
    candidates.append(getattr(sys, "executable", ""))

    for cand in candidates:
        cand = str(cand or "").strip().strip('"')
        if not cand.lower().endswith(".exe"):
            continue
        d = os.path.dirname(os.path.abspath(cand))
        if temp_dir and os.path.normcase(d) == temp_dir:
            continue                                    # 解包目录，用完即删
        if "\\onefile_" in os.path.normcase(d + os.sep):
            continue                                    # 兜底：识别临时目录名字
        return d
    return os.getcwd()


# mcfish/paths.py -> mcfish/
PKG = os.path.dirname(os.path.abspath(__file__))
# 包目录的上一级：源码运行时 = autofish/；打包后 = 解包临时目录
BASE = os.path.dirname(PKG)

_EXEDIR = _exe_dir()
IS_FROZEN = _EXEDIR is not None
ROOT = _EXEDIR or BASE

CALIB = os.path.join(ROOT, "calib")
LOGS = os.path.join(ROOT, "logs")

# 随包走的音效（与本文件同目录），14 处代码/文档都按 mcfish/splash.ogg 引用它
SOUND = os.path.join(PKG, "splash.ogg")

_SETTINGS = os.path.join(CALIB, "gui_settings.json")
_ERRLOG = os.path.join(CALIB, "gui_error.log")


def settings() -> str:
    return _SETTINGS


def errlog() -> str:
    return _ERRLOG


def ensure_dirs() -> None:
    """把运行时需要写的目录建出来。缺目录会让第一次运行时静默失败。"""
    for d in (CALIB, LOGS):
        os.makedirs(d, exist_ok=True)


def template(name: str = "template_splash.npy") -> str:
    """按名字取calib/ 下的模板路径（不保证存在）。"""
    return os.path.join(CALIB, name)


def resolve(p: str) -> str:
    """把设置里/命令行给的路径解析成实际存在的绝对路径。

    依次尝试：绝对路径 -> 相对 cwd -> 相对 ROOT(exe 目录) -> 相对 BASE(包上一级)
    -> 相对 PKG(包内)。

    为什么要试这么多：打包后内置的 splash.ogg 躺在解包临时目录里
    （`BASE/mcfish/splash.ogg`），此时 ROOT 是 exe 目录，两边不一定在一起。
    只按 cwd 或只按 ROOT 找都会落空，表现为「明明带了音效却说找不到文件」。

    全都找不到时按 ROOT 拼一个路径返回，交给上层去报错 —— 保持错误信息里
    有个完整路径，比返回原样更容易查。
    """
    if not p:
        return p
    p = os.path.expanduser(str(p).strip().strip('"'))
    if os.path.isabs(p):
        return os.path.normpath(p)
    for base in (os.getcwd(), ROOT, BASE, PKG):
        cand = os.path.normpath(os.path.join(base, p))
        if os.path.exists(cand):
            return cand
    return os.path.normpath(os.path.join(ROOT, p))


def rel(p: str) -> str:
    """把绝对路径显示成相对 ROOT 的短路径，给日志/界面看用。"""
    try:
        r = os.path.relpath(p, ROOT)
    except ValueError:                # 跨盘符时 relpath 会抛
        return p
    return p if r.startswith("..") else r
