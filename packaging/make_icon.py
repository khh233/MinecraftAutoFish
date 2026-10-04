"""icon.png -> 多尺寸 icon.ico（供 Nuitka 注入 exe 图标）。

像素风的图**不能用平滑缩放**：一旦插值，16x16 那几档就会糊成一团。
所以全部用 NEAREST（最近邻），保持像素边界干净。

用法:
    .venv\\Scripts\\python.exe packaging\\make_icon.py [输出路径]
默认输出 build\\autofish.ico。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "icon.png")
DEFAULT_OUT = os.path.join(ROOT, "build", "autofish.ico")

# Windows 会在不同场合挑不同尺寸：托盘/标题栏 16、任务栏 32、Alt+Tab 48、
# 大图标视图 256。少一档 Windows 就自己拉伸，像素图会被拉花。
SIZES = (16, 24, 32, 48, 64, 128, 256)


def build_ico(src: str = SRC, out: str = DEFAULT_OUT) -> str:
    """把 png 转成多尺寸 ico，返回 ico 路径。"""
    from PIL import Image

    if not os.path.exists(src):
        raise FileNotFoundError(f"找不到图标源图: {src}")

    base = Image.open(src).convert("RGBA")
    frames = [base.resize((s, s), Image.Resampling.NEAREST) for s in SIZES]
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    frames[-1].save(out, format="ICO",
                    sizes=[(s, s) for s in SIZES],
                    append_images=frames[:-1])
    return out


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT
    try:
        p = build_ico(SRC, out)
    except Exception as e:
        print(f"[!] 生成图标失败: {type(e).__name__}: {e}")
        return 1
    size = os.path.getsize(p)
    from PIL import Image
    with Image.open(p) as im:
        got = sorted(im.info.get("sizes", set()))
    print(f"[ok] {p}  {size} 字节  含尺寸 {[s[0] for s in got]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
