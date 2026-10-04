"""GUI 真机冒烟：建窗口 -> 用界面参数真跑一小段（dry-run，不点鼠标）-> 收尾。

验证「界面 -> 后台线程 -> autofish 流程 -> 回到界面」这条链在真实环境下是通的，
包括自校准、自检、give-up 收尾。不会点鼠标（dry-run），只会播一次音效。
"""
import os
import sys
import time

# 从本文件位置推导项目根，不写死绝对路径 —— 整个文件夹可以随便挪位置
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from mcfish import gui as g

root = tk.Tk()
root.withdraw()
dialogs = []
g.messagebox.showerror = lambda *a, **k: dialogs.append(("error", a))
g.messagebox.showinfo = lambda *a, **k: dialogs.append(("info", a))

settings_backup = None
if os.path.exists(g.SETTINGS):
    settings_backup = open(g.SETTINGS, encoding="utf-8").read()

app = g.App(root)
root.update()

# 用界面自己的参数，跑一次很短的 dry-run 会话：钓 1 次、单次只等 4s、失败 1 次就停
app.sound_var.set(os.path.join("mcfish", "splash.ogg"))
app.win_var.set("Minecraft")
app.feature_var.set("mel")
app.thresh_var.set("0.80")
app.count_var.set("1")
app.delay_var.set("0.5")
app.wait_var.set("4")
app.giveup_var.set("1")
app.verify_var.set(True)          # 走自检（会播一遍音效）
app.dry_var.set(True)             # 不点鼠标
t0 = time.time()

app.on_start()
print("已点「开始钓鱼」，等待会话自己结束…", flush=True)
while app.worker and app.worker.is_alive():
    root.update()
    app._drain()
    time.sleep(0.05)
    if time.time() - t0 > 180:
        print("[!] 超时，强制停止", flush=True)
        app.on_stop()
        break
root.update()
app._drain()
root.update()

log = app.log_box.get("1.0", "end")
print("=" * 70)
print(log.strip())
print("=" * 70)

checks = [
    ("自校准跑过", "自校准" in log and "模板" in log),
    ("自检报了两种分数", "峰值 raw=" in log and "mel=" in log),
    ("进了钓鱼循环（有抛竿）", "抛竿" in log),
    ("dry-run 确实没点鼠标", "dry-run" in log),
    ("会话自己收尾（有「结束」）", "结束：" in log),
    ("没有弹错误框", not dialogs),
    ("按钮状态已复位", str(app.start_btn["state"]) == "normal"
     and str(app.stop_btn["state"]) == "disabled"),
]
ok = True
for name, cond in checks:
    print(f"  [{'ok' if cond else 'FAIL'}] {name}")
    ok = ok and cond

app.on_close()
if settings_backup is not None:
    open(g.SETTINGS, "w", encoding="utf-8").write(settings_backup)
elif os.path.exists(g.SETTINGS):
    os.remove(g.SETTINGS)
print("\n总体:", "通过" if ok else "未通过")
raise SystemExit(0 if ok else 1)
