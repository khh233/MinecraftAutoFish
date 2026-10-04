"""用 Nuitka + Zig 把 autofish 打包成单文件 exe。

用法:
    build.bat                                    双击即可
    .venv\\Scripts\\python.exe packaging\\build.py  等价命令
    可选: --console（保留黑窗，排查用）  --jobs=N（限并发，内存紧张时用）
          --onefile（单文件；本机 zstd 压缩会 OOM，见第 8 条，别默认用）

产物: build\\autofish.exe（图标来自 icon.png，经 packaging/make_icon.py 转 ico）

打包内容: 只跟随 `packaging/entry.py` 的 import 图 —— 也就是 mcfish 包 + 它的
依赖。tools/ 下的测试、诊断脚本不会被带进去（构建完会再扫一遍确认）。

以下 7 条都是这台机器上实测踩出来的，动这个脚本前先读完：

1. **%LOCALAPPDATA% 对 python（及其全部子进程）只读**，连 %TEMP% 都写不进去；
   而 Nuitka / Zig / pip 的默认缓存全在那儿。不重定向就是「编译到一半
   PermissionError」。所以 NUITKA_CACHE_DIR / ZIG_*_CACHE_DIR / TMP / TEMP
   一律改到工程内的 .nuitka-cache/。注意限制**按进程继承**，python 拉起的
   zig.exe 同样写不了 AppData。

2. **Zig 不走下载**：Nuitka `--zig` 时先 `getExecutablePath("zig")` 查 PATH，
   查不到才去私有 pip 空间下载 347MB 的 ziglang。把 AppData 缓存里现成那份挂到
   PATH 前面即可零下载（它只被读取，不写入）。

3. **shell 环境被裁剪**：缺 `PROCESSOR_ARCHITECTURE`（SCons 硬取 → 编译第一个
   文件时 KeyError）、`ComSpec`、`PATHEXT` 等，从注册表按 Windows 自己的规则
   补齐（HKLM + HKCU，展开 %VAR%）。**注意变量名大小写不敏感** —— 注册表里的
   `Path` 会和已有的 `PATH` 撞成重复键，必须按小写合并。

4. `--update-check=never` 必加：更新检查会去写 %LOCALAPPDATA%，卡死。

5. **数据文件三件套**（都是运行时才去读、Nuitka 静态分析看不到的）：
   - `mcfish/splash.ogg`：必须落在发行目录的 `mcfish/` 下，才和包内路径推导对上
   - soundcard 的 `mediafoundation.py.h`：运行时 `open()` 它来解析设备信息
   - `_soundfile_data/`：soundfile 的 libsndfile_x64.dll 在这目录里，要连目录一起带

6. **图标要七档尺寸**（16/24/32/48/64/128/256），像素图用 NEAREST 缩放。

7. `stdin=DEVNULL`：Nuitka 偶尔会交互式提问（要不要下载工具链），无人应答会挂住。

8. **默认 --standalone（文件夹），不要 --onefile**。onefile 的最后一步要把整个
   dist（190MB+）用 zstd 压成一个包，本机实测直接：
       zstd.ZstdError: zstd compress error: Allocation error : not enough memory
   652 个 C 文件全编过、链接也成了，卡在最后压缩，白等十几分钟。而且单文件每次
   启动都要把上百 MB 解压到 %TEMP%。程序本来就在 exe 旁读写 calib\\、logs\\，
   文件夹形态更自然。真要单文件就 --onefile --no-compress（不压缩就不吃 zstd
   的连续内存），代价是体积大一圈、启动解压更慢。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = sysconfig.get_paths()["purelib"]
CACHE_DIR = os.path.join(ROOT, ".nuitka-cache")
OUT_DIR = os.path.join(ROOT, "build")
EXE_NAME = "autofish.exe"
DIST_NAME = "autofish.dist"
ENTRY = os.path.join(ROOT, "packaging", "entry.py")
ICON_PNG = os.path.join(ROOT, "icon.png")
ICON_ICO = os.path.join(OUT_DIR, "autofish.ico")


# ---------------------------------------------------------------- 图标
def _ensure_icon() -> str:
    """生成/复用 icon.ico，返回路径。"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from make_icon import build_ico

    need = (not os.path.exists(ICON_ICO)
            or os.path.getmtime(ICON_ICO) < os.path.getmtime(ICON_PNG))
    if need:
        print("[*] 由 icon.png 生成多尺寸图标 ...")
        build_ico(ICON_PNG, ICON_ICO)
    print(f"[*] 图标: {ICON_ICO}")
    return ICON_ICO


# ------------------------------------------------------- 环境（缓存/工具链）
def _windows_system_env() -> dict:
    """从注册表读标准 Windows 环境变量（补 shell 被裁掉的那些）。"""
    import winreg

    out: dict[str, str] = {}
    sources = (
        (winreg.HKEY_LOCAL_MACHINE,
         r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, r"Environment"),
    )
    for hive, path in sources:
        try:
            key = winreg.OpenKey(hive, path)
        except OSError:
            continue
        try:
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                i += 1
                if isinstance(value, str) and value:
                    out[name] = value
        finally:
            key.Close()
    return out


def _find_cached_zig() -> str | None:
    """在已知位置找现成的 zig.exe（Nuitka 私有 pip 空间里那份）。"""
    cands = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        base = os.path.join(local, "Nuitka", "Nuitka", "Cache", "downloads", "pip")
        if os.path.isdir(base):
            for name in sorted(os.listdir(base)):
                cands.append(os.path.join(base, name, "Lib", "site-packages",
                                          "ziglang", "zig.exe"))
    which = shutil.which("zig")
    if which:
        cands.append(which)
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def _build_env() -> dict:
    env = dict(os.environ)

    # 1) 补齐标准变量（已有的不动；按小写判重，避开 Path/PATH 撞键）
    have = {k.lower() for k in env}
    for name, value in _windows_system_env().items():
        if name.lower() in have:
            continue
        env[name] = value
    for name in list(env):                      # REG_EXPAND_SZ 里的 %VAR% 要展开
        v = env[name]
        if isinstance(v, str) and "%" in v:
            env[name] = os.path.expandvars(v)

    # 2) 所有缓存落进工程内（见文件头第 1 条）
    zig_global = os.path.join(CACHE_DIR, "zig", "global")
    zig_local = os.path.join(CACHE_DIR, "zig", "local")
    tmp = os.path.join(CACHE_DIR, "tmp")
    env["NUITKA_CACHE_DIR"] = CACHE_DIR
    env["ZIG_GLOBAL_CACHE_DIR"] = zig_global
    env["ZIG_LOCAL_CACHE_DIR"] = zig_local
    env["TMP"] = env["TEMP"] = tmp
    env["PIP_NO_CACHE_DIR"] = "1"
    for d in (tmp, zig_global, zig_local):
        os.makedirs(d, exist_ok=True)

    # 3) 复用现成的 Zig（见文件头第 2 条）
    zig = _find_cached_zig()
    if zig:
        print(f"[*] 复用已缓存的 Zig: {zig}")
        env["PATH"] = os.path.dirname(zig) + os.pathsep + env.get("PATH", "")
    else:
        print("[*] PATH 上没有 zig，交给 Nuitka 自己下载（需要联网）")

    print(f"[*] 构建缓存: {CACHE_DIR}")
    return env


def _env_get(env: dict, name: str) -> str | None:
    """大小写不敏感地取环境变量。

    Windows 变量名大小写不敏感，但 python 的 os.environ 在 Windows 上其实是
    大小写不敏感字典 —— 而**传给子进程的 dict 不是**。shell 里给的是
    `SYSTEMROOT`，按 `SystemRoot` 去取就会扑空（曾经因此误报预检失败）。
    """
    if name in env:
        return env[name]
    lower = name.lower()
    for k, v in env.items():
        if k.lower() == lower:
            return v
    return None


def _preflight(env: dict) -> bool:
    """几秒钟确认构建环境是活的，免得白等半小时才发现挂在权限/环境上。"""
    ok = True
    checks = [
        ("缓存目录可写", os.path.join(CACHE_DIR, "module-cache")),
        ("Zig 缓存可写", env["ZIG_LOCAL_CACHE_DIR"]),
        ("临时目录可写", env["TMP"]),
    ]
    for label, d in checks:
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, "_probe.tmp")
            with open(probe, "w") as f:
                f.write("x")
            os.remove(probe)
            print(f"  [ok] {label}: {d}")
        except Exception as e:
            print(f"  [FAIL] {label}: {d} -> {type(e).__name__}: {e}")
            ok = False

    for var in ("PROCESSOR_ARCHITECTURE", "ComSpec", "PATHEXT", "SystemRoot"):
        val = _env_get(env, var)
        print(f"  [{'ok' if val else 'FAIL'}] {var} = {val}")
        ok = ok and bool(val)

    pylib = sysconfig.get_paths()["purelib"]
    for pkg in ("nuitka", "ordered_set"):
        exists = os.path.isdir(os.path.join(pylib, pkg))
        print(f"  [{'ok' if exists else 'FAIL'}] {pkg} 已安装")
        ok = ok and exists
    try:
        import zstandard  # noqa: F401
        print("  [ok] zstandard 已安装")
    except Exception as e:
        print(f"  [warn] zstandard 不可用（将无法压缩 onefile）: {e}")

    return ok


# ---------------------------------------------------------------- 主流程
def _nuiska_cmd(icon: str, console: bool, compress: bool,
                jobs: int | None = None, onefile: bool = False) -> list[str]:
    cmd = [
        sys.executable, "-m", "nuitka",
        # 默认 --standalone（自包含目录）。**不要默认 --onefile**：这台机器上
        # onefile 的最后一步 zstd 压缩会 OOM（zstd.ZstdError: not enough memory），
        # 而且单文件每次启动都要把上百 MB 解压到 %TEMP%。详见文件头第 8 条。
        "--standalone",
        "--zig",                        # 用 Zig 当 C 编译器（免装 Visual Studio）
        "--assume-yes-for-downloads",
        "--update-check=never",         # 见文件头第 4 条
        "--enable-plugin=tk-inter",     # tkinter 的 tcl/tk 运行库
        f"--windows-console-mode={'force' if console else 'disable'}",
        "--company-name=autofish",
        "--product-name=autofish",
        "--file-version=1.0.0.0",
        "--product-version=1.0.0.0",
        "--file-description=autofish - Minecraft 音频触发收杆",
        f"--output-dir={OUT_DIR}",
        f"--output-filename={EXE_NAME}",
        f"--windows-icon-from-ico={icon}",
        # 数据文件三件套（见文件头第 5 条）
        "--include-data-file=mcfish/splash.ogg=mcfish/splash.ogg",
        # 运行时给窗口/任务栏设图标用（gui._apply_window_icon 会去 exe 旁读它们）
        f"--include-data-file={ICON_PNG}=icon.png",
        f"--include-data-file={icon}=icon.ico",
    ]
    # 并发编译的进程数。scipy 的 integrate._lebedev 是自动生成的巨无霸文件
    # （几十万个静态数组元素），8 路并发时 Zig 的 LLVM 后端会 OOM：
    #   LLVM ERROR: out of memory / Allocation failed
    # 限到 2 路就稳了（编译慢一点，但内存峰值降下来）。
    if jobs:
        cmd.append(f"--jobs={jobs}")
    # 目标必须带上 `soundcard/` 前缀！soundcard/mediafoundation.py 运行时是
    #   `open(os.path.join(os.path.split(__file__)[0], 'mediafoundation.py.h'))`
    # 去隔壁找头文件；冻结后 __file__ 解析成 `<dist>\soundcard\mediafoundation.py`，
    # 所以文件必须落在 `<dist>\soundcard\` 下。之前只写文件名 → 铺在 dist 根，
    # 结果 soundcard 初始化失败：拿不到播放设备 / 自校准一帧都采不到。
    for name in ("mediafoundation.py.h", "coreaudio.py.h", "pulseaudio.py.h"):
        p = os.path.join(SITE, "soundcard", name)
        if os.path.exists(p):
            cmd.append(f"--include-data-file={p}=soundcard/{name}")
    snd = os.path.join(SITE, "_soundfile_data")
    if os.path.isdir(snd):
        cmd.append(f"--include-data-dir={snd}=_soundfile_data")

    # 不带测试/诊断脚本：入口只 import mcfish，这里再显式挡一层
    cmd += ["--nofollow-import-to=tools",
            "--nofollow-import-to=packaging"]

    if onefile:
        cmd.append("--onefile")
        if not compress:
            cmd.append("--onefile-no-compression")
    cmd.append(ENTRY)
    return cmd


def _dir_size(path: str) -> int:
    total = 0
    for r, _d, files in os.walk(path):
        for n in files:
            try:
                total += os.path.getsize(os.path.join(r, n))
            except OSError:
                pass
    return total


def _check_no_tests() -> None:
    """确认打出来的发行目录里没有测试脚本。"""
    dist = os.path.join(OUT_DIR, DIST_NAME)
    if not os.path.isdir(dist):
        return
    bad = []
    for r, _d, files in os.walk(dist):
        for n in files:
            if n.startswith("test_") or n.startswith("diag_") or n == "analyze.py":
                bad.append(os.path.join(r, n))
    if bad:
        print(f"[!] 发行目录里混进了 {len(bad)} 个测试/诊断脚本：{bad[:5]}")
    else:
        print("[ok] 发行目录里没有测试/诊断脚本（符合预期）")


def build(console: bool = False, compress: bool = True,
          jobs: int | None = None, onefile: bool = False) -> int:
    if not os.path.exists(ENTRY):
        print(f"[!] 找不到打包入口 {ENTRY}")
        return 1

    print("=" * 66)
    print(" 预检：缓存可写 / Zig 可用 / 环境变量齐全 / 打包依赖在位")
    print("=" * 66)
    env = _build_env()
    if not _preflight(env):
        print("\n[!] 预检没过 —— 先修环境再打包，否则多半白等。")
        return 2

    icon = _ensure_icon()
    cmd = _nuiska_cmd(icon, console, compress, jobs, onefile)

    print("\n[*] Nuitka 命令：")
    print("    " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    print()

    t0 = time.time()
    # stdin 接 DEVNULL：后台跑没人能回答交互式提问，给 EOF 让它立刻报错而不是无声挂起
    proc = subprocess.run(cmd, cwd=ROOT, env=env, stdin=subprocess.DEVNULL)
    dt = time.time() - t0

    if proc.returncode != 0:
        print(f"\n[!] Nuitka 失败，退出码 {proc.returncode}（耗时 {dt / 60:.1f} 分钟）")
        return proc.returncode

    # Nuitka 按入口文件名给 dist 目录命名（entry.dist），换成项目名更直观
    if not onefile:
        raw = os.path.join(OUT_DIR, "entry.dist")
        dst = os.path.join(OUT_DIR, DIST_NAME)
        if os.path.isdir(raw) and os.path.normcase(raw) != os.path.normcase(dst):
            shutil.rmtree(dst, ignore_errors=True)
            try:
                os.rename(raw, dst)
            except OSError as e:
                print(f"[warn] dist 目录改名失败（不影响使用）: {e}")

    if onefile:
        exe = os.path.join(OUT_DIR, EXE_NAME)
    else:
        exe = ""
        dist = os.path.join(OUT_DIR, DIST_NAME)
        if os.path.isdir(dist):
            for n in sorted(os.listdir(dist)):      # exe 名以 --output-filename 为准
                if n.lower().endswith(".exe"):
                    exe = os.path.join(dist, n)
                    break
    if not exe or not os.path.exists(exe):
        print(f"\n[!] 编译成功但没找到可执行文件: {exe or OUT_DIR}")
        return 3

    print(f"\n[ok] 打包完成，耗时 {dt / 60:.1f} 分钟")
    if onefile:
        print(f"     产物: {exe}  ({os.path.getsize(exe) / 1024 / 1024:.1f} MB，单文件)")
    else:
        d = os.path.dirname(exe)
        print(f"     产物: {d}  ({_dir_size(d) / 1024 / 1024:.1f} MB)")
        print(f"     入口: {exe}   <- 双击它")
        print("     （整个文件夹一起拷走才能换电脑跑，别只拷 exe）")
    _check_no_tests()
    return 0


def main() -> int:
    args = sys.argv[1:]
    console = "--console" in args
    compress = "--no-compress" not in args
    onefile = "--onefile" in args
    jobs = None
    for a in args:                      # --jobs=N 限并发（内存不够时用，见 _nuiska_cmd）
        if a.startswith("--jobs="):
            try:
                jobs = int(a.split("=", 1)[1])
            except ValueError:
                print(f"[!] --jobs= 后面要跟数字：{a}")
                return 2
    return build(console=console, compress=compress, jobs=jobs, onefile=onefile)


if __name__ == "__main__":
    unset = os.environ.pop("PYTHONPATH", None)   # 环境注入的 PYTHONPATH 会干扰子进程
    if unset:
        print(f"[*] 已临时移除 PYTHONPATH={unset}")
    raise SystemExit(main())
