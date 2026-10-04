"""打包成 exe 时的入口（源码运行时不要用这个，直接跑 gui.bat）。

等价于 `python -m mcfish.gui`，多做两件事：启动失败时**弹窗 + 落日志** ——
exe 是 GUI 模式（没有黑窗口），崩了如果只 print 到 stdout，用户是看不到的。

注意：只有这个入口会 `import mcfish.gui`，Nuitka 顺着 import 图编译，
`tools/` 下的测试与诊断脚本不会被带进包里。
"""
from __future__ import annotations

import os
import sys


def _log_traceback() -> str:
    """把栈写进 exe 同目录的 gui_error.log，并返回日志路径。"""
    import time
    import traceback

    text = f"\n=== {time.asctime()} ===\n{traceback.format_exc()}\n"
    for d in (os.path.dirname(os.path.abspath(sys.argv[0] or "")), os.getcwd()):
        try:
            p = os.path.join(d, "gui_error.log")
            with open(p, "a", encoding="utf-8") as f:
                f.write(text)
            return p
        except Exception:
            continue
    return "(日志也没写出去)"


def _alert(msg: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, "autofish 启动失败", 0x10)
    except Exception:
        pass


def main() -> int:
    try:
        from mcfish import gui
    except Exception as e:
        import traceback
        detail = traceback.format_exc()
        where = _log_traceback()
        _alert(f"{type(e).__name__}: {e}\n\n详细日志：{where}")
        print(detail, file=sys.stderr)
        return 1

    try:
        gui.main()
    except SystemExit as e:
        return int(e.code or 0)
    except Exception as e:
        import traceback
        detail = traceback.format_exc()
        where = _log_traceback()
        _alert(f"{type(e).__name__}: {e}\n\n详细日志：{where}")
        print(detail, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
