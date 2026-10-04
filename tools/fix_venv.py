"""修复搬动/拷贝之后失效的 .venv。

**为什么需要**：Python 的 venv 在 `pyvenv.cfg` 里记了「建它用的解释器在哪」
（比如 `home = D:\\Python\\python312`）。整个 autofish 文件夹拷到别的电脑、
或那个 Python 被卸载/移动之后，`.venv\\Scripts\\python.exe` 就会报
「无法找到 python」—— 但 `.venv` 里的包（numpy/scipy/soundcard…）都还在，
其实只需要把 `home` 改到新机器上的解释器即可，不用重装。

用法：
    1) 先双击 **run.bat** 选[1]（它会自动做这件事）
    2) 或者手动： .venv\\Scripts\\python.exe tools\\fix_venv.py
       若报「解释器也坏了」，就用系统的 py -3.12 跑：
          py -3.12 tools\\fix_venv.py

它只改 pyvenv.cfg 的 home/executable 两行，不动任何包。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(ROOT, ".venv")
CFG = os.path.join(VENV, "pyvenv.cfg")
VPY = os.path.join(VENV, "Scripts", "python.exe")

# 依次尝试的解释器来源
CANDIDATES = [
    ["py", "-3.12"], ["py", "-3.11"], ["py", "-3.13"],
    ["py", "-3.10"], ["py", "-3"], ["python"],
]


def venv_python_works() -> bool:
    if not os.path.exists(VPY):
        return False
    try:
        r = subprocess.run([VPY, "-c", "import sys;print(sys.version)"],
                           capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def find_base() -> tuple[str, str] | None:
    """找一个可用的系统解释器，返回 (可执行路径, 解释器所在目录)。

    注意第二个返回值是 dirname(executable)，**不是**它的父目录 ——
    pyvenv.cfg 的 home 要的是解释器自己所在的目录
    (比如 D:\\Python\\python312，而不是 D:\\Python)。
    少剥一层会让 venv 报 No Python at '"...\\python.exe'。
    """
    for cmd in CANDIDATES:
        exe = cmd[0]
        if shutil.which(exe) is None:
            continue
        args = cmd[1:] + ["-c",
                          "import sys,os;print(sys.executable);print(os.path.dirname(sys.executable))"]
        try:
            r = subprocess.run([exe] + args, capture_output=True, text=True, timeout=40)
        except Exception:
            continue
        if r.returncode != 0:
            continue
        lines = [x.strip() for x in r.stdout.strip().splitlines() if x.strip()]
        if len(lines) >= 2 and os.path.isdir(lines[1]):
            return lines[0], lines[1]
    return None


def main() -> int:
    print("=" * 70)
    print(" 修复 autofish 的虚拟环境")
    print("=" * 70)

    if not os.path.isdir(VENV):
        print(f"[!] 没找到 {VENV}")
        print("    直接双击 run.bat 选 [1] 建一个全新的即可。")
        return 1

    if venv_python_works():
        print("[ok] .venv 目前是好的，不需要修。")
        try:
            r = subprocess.run([VPY, "-c",
                                "import numpy,scipy,soundcard,pydirectinput;"
                                "print('     numpy',numpy.__version__,'scipy',scipy.__version__)"],
                               capture_output=True, text=True, timeout=60)
            print(r.stdout.strip() or "     [warn] 依赖不全，建议 run.bat 选 [1] 重装")
            if r.returncode != 0:
                print("     缺依赖。跑 run.bat 的 [1] 补装。")
        except Exception as e:
            print(f"     [warn] 检查依赖时出错: {e}")
        return 0

    print("[!] .venv 里的 python 用不了了（多半是文件夹被搬走/拷贝，")
    print("    而 pyvenv.cfg 记的还是原来那台机器的解释器路径）。")
    print()

    base = find_base()
    if not base:
        print("[!] 没找到可用的系统 Python。")
        print("    请安装 Python 3.12（勾选 Add to PATH），然后重跑本脚本，")
        print("    或者直接删掉 .venv 文件夹再双击 run.bat 选 [1]。")
        return 1

    exe, home = base
    print(f"  找到解释器: {exe}")
    print(f"  它的目录  : {home}")
    print()

    # 改 pyvenv.cfg 的 home / executable
    try:
        raw = open(CFG, "r", encoding="utf-8").read()
    except FileNotFoundError:
        print(f"[!] 没有 {CFG} —— 这个 .venv 缺文件，建议直接删掉重建。")
        return 1

    out = []
    for line in raw.splitlines():
        if line.startswith("home ="):
            line = f"home = {home}"
        elif line.startswith("executable ="):
            line = f"executable = {exe}"
        out.append(line)
    new = "\n".join(out) + "\n"
    open(CFG, "w", encoding="utf-8", newline="\n").write(new)
    print(f"  已改写 {os.path.relpath(CFG, ROOT)} 的 home / executable")
    print()

    if venv_python_works():
        print("[ok] .venv 修好了。")
        r = subprocess.run([VPY, "-c",
                            "import numpy,scipy,soundcard,pydirectinput;"
                            "print('     numpy',numpy.__version__,'scipy',scipy.__version__)"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            print(r.stdout.strip())
            print()
            print("     依赖齐全，可以直接用了。")
            return 0
        print("     但依赖不全，跑run.bat 选 [1] 补装。")
        return 1

    print("[!] 改完还是不行。这个 .venv 里的包可能损坏了。")
    print("    最省事的做法：删掉 .venv 文件夹，双击 run.bat 选 [1] 重建。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
