"""打包验收：图标嵌对没有 + exe 能不能跑 + 配置目录落在 exe 旁。

用法:
    .venv\\Scripts\\python.exe packaging\\verify.py

三件事分别对应三个最容易悄悄坏掉的地方：
  1. 图标：从 exe 资源里把图标抠出来，和 icon.png 逐像素比。Nuitka 不报错但
     图标没进去 / 进错尺寸是很常见的。
  2. 启动：把发行目录拷到一处干净地方、**故意换一个 cwd** 启动，确认窗口能起来。
  3. 落点：确认 calib/、logs/ 建在 **exe 旁边**，而不是临时解包目录或 cwd ——
     这条专门验 paths.py 的 ROOT 推导（打包后 sys.argv[0] 指向内部解包路径，
     直接拿来当根的话配置就会「写完即逝」）。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
EXE_NAME = "autofish.exe"
DIST = os.path.join(BUILD, "autofish.dist")          # 默认产物：文件夹模式
EXE = os.path.join(DIST, EXE_NAME)
if not os.path.exists(EXE):                          # 兼容 --onefile 的单文件产物
    _flat = os.path.join(BUILD, EXE_NAME)
    if os.path.exists(_flat):
        EXE = _flat
ICON_PNG = os.path.join(ROOT, "icon.png")
CACHE = os.path.join(ROOT, ".nuitka-cache")
WORK = os.path.join(CACHE, "verify_run")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)   # ExtractIconEx 在这儿，不在 user32

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


# 64 位下句柄常超 int32，不给 argtypes 就会 OverflowError —— 显式声明
gdi32.GetObjectW.argtypes = [wt.HGDIOBJ, ctypes.c_int, ctypes.c_void_p]
gdi32.GetObjectW.restype = ctypes.c_int
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteObject.restype = wt.BOOL
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.GetIconInfo.argtypes = [wt.HICON, ctypes.POINTER(ICONINFO)]
user32.GetIconInfo.restype = wt.BOOL
user32.DestroyIcon.argtypes = [wt.HICON]
user32.DestroyIcon.restype = wt.BOOL
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostMessageW.restype = wt.BOOL
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
shell32.ExtractIconExW.argtypes = [wt.LPCWSTR, ctypes.c_int,
                                   ctypes.POINTER(wt.HICON),
                                   ctypes.POINTER(wt.HICON), wt.UINT]
shell32.ExtractIconExW.restype = wt.UINT


def _hicon_to_rgba(hicon: int):
    """取出 hicon 的彩色位图为 (w, h, bytes)，失败返回 None。"""
    from PIL import Image

    info = ICONINFO()
    if not user32.GetIconInfo(wt.HICON(hicon), ctypes.byref(info)):
        return None
    bm = info.hbmColor or info.hbmMask
    if not bm:
        return None

    class BITMAP(ctypes.Structure):
        _fields_ = [("bmType", wt.LONG), ("bmWidth", wt.LONG), ("bmHeight", wt.LONG),
                    ("bmWidthBytes", wt.LONG), ("bmPlanes", wt.WORD),
                    ("bmBitsPixel", wt.WORD), ("bmBits", ctypes.c_void_p)]

    bmp = BITMAP()
    if not gdi32.GetObjectW(bm, ctypes.sizeof(BITMAP), ctypes.byref(bmp)):
        return None
    w, h = bmp.bmWidth, bmp.bmHeight

    hdc = user32.GetDC(None)
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h              # 负数 = 自上而下
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0          # BI_RGB
    buf = ctypes.create_string_buffer(w * h * 4)
    got = gdi32.GetDIBits(hdc, bm, 0, h, buf, ctypes.byref(bi), 0)
    user32.ReleaseDC(None, hdc)
    if not got:
        return None
    return w, h, buf.raw


def verify_icon() -> bool:
    print("[1] 图标：从 exe 抠出来和 icon.png 比")
    if not os.path.exists(EXE):
        print(f"    [FAIL] 没有 {EXE}")
        return False

    large = wt.HICON()
    small = wt.HICON()
    n = shell32.ExtractIconExW(str(EXE), 0, ctypes.byref(large),
                               ctypes.byref(small), 1)
    print(f"    图标资源个数: {n}")
    hicon = large.value or small.value
    if not hicon:
        print("    [FAIL] 抠不到图标（资源没写进去？）")
        return False

    try:
        from PIL import Image
        got = _hicon_to_rgba(hicon)
        if got is None:
            print("    [FAIL] 位图读取失败")
            return False
        w, h, raw = got
        # GetDIBits 给的是 BGRA 字节序；直接按 RGBA 读会把红蓝通道读反，
        # 像素图（像 icon.png 这种）就会大面积对不上 —— 必须显式指定 rawmode。
        img = Image.frombytes("RGBA", (w, h), raw, "raw", "BGRA").convert("RGB")
        src = Image.open(ICON_PNG).convert("RGB").resize(
            (w, h), Image.Resampling.NEAREST)
        diff = 0
        worst = 0
        for (a, b) in zip(img.getdata(), src.getdata()):
            d = max(abs(a[i] - b[i]) for i in range(3))
            worst = max(worst, d)
            if d > 8:
                diff += 1
        total = w * h
        print(f"    抠出 {w}x{h}，与 icon.png(最近邻放大) 比较："
              f"差异像素 {diff}/{total}（{diff / total * 100:.1f}%），最大差 {worst}")
        ok = diff / total < 0.02
        print(f"    [{'ok' if ok else 'FAIL'}] 图标{'一致' if ok else '对不上'}")
        return ok
    finally:
        user32.DestroyIcon(wt.HICON(hicon))


def _window_titles() -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []

    def cb(hwnd, _lp):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value:
            out.append((int(hwnd), buf.value))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def verify_run() -> bool:
    print("[2] 启动 + [3] 配置目录落点")
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK)
    # 文件夹模式要**连整个 dist 一起拷**（exe 靠旁边的 dll / numpy / tcl 才起得来）
    if os.path.isdir(os.path.join(os.path.dirname(EXE), "mcfish")):
        app = os.path.join(WORK, "app")
        shutil.copytree(os.path.dirname(EXE), app)
        exe2 = os.path.join(app, os.path.basename(EXE))
    else:                                               # 单文件模式：拷一个 exe 就够
        exe2 = os.path.join(WORK, os.path.basename(EXE))
        shutil.copy2(EXE, exe2)
    elsewhere = os.path.join(WORK, "elsewhere")
    os.makedirs(elsewhere)

    # 本机 %TEMP% 对 python 只读；onefile 要往 %TEMP% 解包，所以指到工程内
    tmp = os.path.join(CACHE, "tmp")
    os.makedirs(tmp, exist_ok=True)
    env = dict(os.environ, TMP=tmp, TEMP=tmp)

    print(f"    exe: {exe2}")
    print(f"    cwd: {elsewhere}（故意的，和 exe 不同目录）")
    p = subprocess.Popen([exe2], cwd=elsewhere, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ok = True
    hwnd = 0
    deadline = time.time() + 40
    while time.time() < deadline:
        for h, t in _window_titles():
            if t.strip() == "autofish":
                hwnd = h
                break
        if hwnd:
            break
        if p.poll() is not None:
            print(f"    [FAIL] exe 提前退出，退出码 {p.returncode}")
            ok = False
            break
        time.sleep(1)

    if hwnd:
        print(f"    [ok] 窗口起来了（hwnd={hwnd}）")
    elif p.poll() is None:
        print("    [FAIL] 40 秒内没看到 autofish 窗口")
        ok = False

    # 目录落点：应该落在 **exe 所在目录**（文件夹模式下就是那份拷贝的 app\）
    app_dir = os.path.dirname(exe2)
    for name in ("calib", "logs"):
        beside = os.path.isdir(os.path.join(app_dir, name))
        in_cwd = os.path.isdir(os.path.join(elsewhere, name))
        print(f"    [{'ok' if beside else 'FAIL'}] {name}/ 建在 exe 旁")
        print(f"    [{'ok' if not in_cwd else 'FAIL'}] {name}/ 没跑进 cwd")
        ok = ok and beside and not in_cwd

    errlog = os.path.join(app_dir, "calib", "gui_error.log")
    if os.path.exists(errlog):
        print(f"    [FAIL] 起了报错日志 {errlog}：")
        print("      " + open(errlog, encoding="utf-8").read()[-500:])
        ok = False
    else:
        print("    [ok] 没有 gui_error.log")

    # 收尾：先礼貌关窗，还在就强杀
    try:
        if hwnd:
            user32.PostMessageW(wt.HWND(hwnd), 0x0010, 0, 0)   # WM_CLOSE
        for _ in range(10):
            if p.poll() is not None:
                break
            time.sleep(0.5)
        for h, t in _window_titles():
            if t.strip() == "autofish":
                pid = wt.DWORD()
                user32.GetWindowThreadProcessId(wt.HWND(h), ctypes.byref(pid))
                subprocess.run(["taskkill", "/F", "/PID", str(pid.value)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if p.poll() is None:
            p.kill()
    except Exception as e:
        print(f"    [warn] 收尾时出问题: {e}")
    print("    已关闭测试实例")
    return ok


def verify_assets() -> bool:
    """运行时才读的资源，必须落在**它自己算出来的那个路径**上。

    这类的坑：文件明明打进去了，但放错了层目录 —— Nuitka 不报错、构建「成功」、
    双击也能起窗口，只有真正用到时才发现。soundcard 就是这么翻的：
        soundcard/mediafoundation.py  里 `open(os.path.join(dirname(__file__), 'mediafoundation.py.h'))`
    冻结后 __file__ = <dist>\\soundcard\\mediafoundation.py，所以头文件必须落在
    <dist>\\soundcard\\ 下；打到 <dist>\\ 根目录上等于没打，症状是
    「拿不到默认播放设备 / 自校准一帧都采不到 / 设备列表只剩两项」。
    """
    print("[4] 运行时资源落点")
    base = os.path.dirname(EXE) if EXE.lower().endswith(".exe") else BUILD
    if not os.path.isdir(os.path.join(base, "mcfish")):
        base = BUILD                                    # onefile 单文件：跳过（资源在 exe 里）
        print("    [skip] 单文件产物，资源内嵌，跳过")
        return True

    ok = True

    # soundcard 的 CFFI 头：必须和 soundcard 包「同目录」
    need = ["mediafoundation.py.h"]
    for name in need:
        p = os.path.join(base, "soundcard", name)
        good = os.path.isfile(p)
        print(f"    [{'ok' if good else 'FAIL'}] soundcard/{name}")
        ok = ok and good
    stray = [n for n in ("mediafoundation.py.h", "coreaudio.py.h", "pulseaudio.py.h")
             if os.path.isfile(os.path.join(base, n))]
    if stray:
        print(f"    [FAIL] 这 {len(stray)} 个头文件被铺在 dist 根目录（层目录错了）: {stray}")
        ok = False

    # 随包走的只读数据 / soundfile 的 dll
    for rel in ("mcfish/splash.ogg", "_soundfile_data/libsndfile_x64.dll",
                "icon.png", "icon.ico"):
        p = os.path.join(base, *rel.split("/"))
        good = os.path.isfile(p)
        print(f"    [{'ok' if good else 'FAIL'}] {rel}")
        ok = ok and good

    print(f"    [{'ok' if ok else 'FAIL'}] 运行时资源{'齐全' if ok else '有缺失/错位'}")
    return ok


def main() -> int:
    print("=" * 66)
    print(" 打包验收")
    print("=" * 66)
    results = []
    if os.path.exists(EXE):
        print(f"产物: {EXE}  ({os.path.getsize(EXE) / 1024 / 1024:.1f} MB)\n")
    results.append(("图标", verify_icon()))
    print()
    results.append(("运行时资源", verify_assets()))
    print()
    results.append(("启动与落点", verify_run()))
    print()
    print("=" * 66)
    for name, ok in results:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}")
    ok = all(v for _, v in results)
    print("\n结论:", "通过" if ok else "未通过")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
