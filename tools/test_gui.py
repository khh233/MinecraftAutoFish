"""GUI 自测：不需要人点，验证界面能建起来、参数校验和设置持久化都对。

不启动真正的钓鱼会话（那会点鼠标/播声音），只测界面逻辑这一层。
需要能开窗口的环境（Windows 桌面）；没有桌面会 skip。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  [{'ok' if cond else 'FAIL'}] {name}: {detail}", flush=True)


try:
    import tkinter as tk
except Exception as e:
    print(f"[skip] 没有 tkinter: {e}")
    raise SystemExit(0)

from mcfish import gui as g

try:
    root = tk.Tk()
except Exception as e:
    print(f"[skip] 开不了窗口（没有桌面？）: {e}")
    raise SystemExit(0)

root.withdraw()                       # 别弹到用户脸上
dialogs = []
g.messagebox.showerror = lambda *a, **k: dialogs.append(("error", a))
g.messagebox.showinfo = lambda *a, **k: dialogs.append(("info", a))

# 测试会写设置文件 —— 先把用户原来的备份起来并移走，结束时还原。
# 必须移走：不然「默认值」的断言会被上次跑剩下的设置影响。
_backup = None
if os.path.exists(g.SETTINGS):
    with open(g.SETTINGS, encoding="utf-8") as f:
        _backup = f.read()
    os.remove(g.SETTINGS)


def _restore_settings():
    try:
        if _backup is not None:
            with open(g.SETTINGS, "w", encoding="utf-8") as f:
                f.write(_backup)
        elif os.path.exists(g.SETTINGS):
            os.remove(g.SETTINGS)
    except Exception:
        pass


app = g.App(root)
root.update()

print("=" * 70)
print("1) 界面与默认值")
print("=" * 70)
check("窗口标题是 autofish", root.title().strip() == "autofish", root.title())
check("默认音效文件", app.sound_var.get().endswith("splash.ogg"), app.sound_var.get())
check("默认特征 mel", app.feature_var.get() == "mel", app.feature_var.get())
check("mel 默认阈值来自 autofish",
      float(app.thresh_var.get()) == g.af.DEFAULT_THRESH["mel"], app.thresh_var.get())
check("音频下拉有默认项", len(app.choices) >= 2, [c[0] for c in app.choices])

print("\n切到 raw 时阈值应跟着变:")
app.feature_var.set("raw")
app._on_feature()
check("raw 阈值自动切换",
      float(app.thresh_var.get()) == g.af.DEFAULT_THRESH["raw"], app.thresh_var.get())
app.feature_var.set("mel")
app._on_feature()

print()
print("=" * 70)
print("2) 参数校验")
print("=" * 70)
dialogs.clear()
app.sound_var.set("mcfish/splash.ogg")
cfg = app._read_cfg()
check("正常参数能通过", isinstance(cfg, dict) and cfg["feature"] == "mel",
      str({k: cfg[k] for k in ("feature", "count", "thresh")} if cfg else None))
check("音频来源翻译成 system", cfg and cfg["audio"] == g.AUDIO_SYS, cfg and cfg["audio"])
check("没弹错误框", not dialogs, dialogs)

dialogs.clear()
app.thresh_var.set("不是数字")
check("阈值填错 -> 拒绝", app._read_cfg() is None and dialogs, dialogs[:1])

dialogs.clear()
app.thresh_var.set("0.8")
app.count_var.set("abc")
check("次数填错 -> 拒绝", app._read_cfg() is None and dialogs, dialogs[:1])

dialogs.clear()
app.count_var.set("50")
app.sound_var.set("不存在的文件.ogg")
check("音效文件不存在 -> 拒绝", app._read_cfg() is None and dialogs, dialogs[:1])

dialogs.clear()
app.sound_var.set("mcfish/splash.ogg")
app.audio_var.set("这个标签不在列表里")
check("音频来源标签非法 -> 拒绝", app._read_cfg() is None and dialogs, dialogs[:1])
app.audio_var.set(app.choices[0][0])

print()
print("=" * 70)
print("3) 设置持久化（记住上次填的）")
print("=" * 70)
app.win_var.set("Minecraft 测试")
app.count_var.set("7")
app.dry_var.set(True)
cfg = app._read_cfg()
app._save_settings(cfg)
check("设置文件写出来了", os.path.exists(g.SETTINGS), g.SETTINGS)

app.win_var.set("被改掉了")
app.count_var.set("999")
app.dry_var.set(False)
app._load_settings()
check("重新载入能恢复窗口名", app.win_var.get() == "Minecraft 测试", app.win_var.get())
check("重新载入能恢复次数", app.count_var.get() == "7", app.count_var.get())
check("重新载入能恢复试跑勾选", app.dry_var.get() is True, app.dry_var.get())

print()
print("=" * 70)
print("4) 后台线程 <-> 界面 的消息通道")
print("=" * 70)
from mcfish import autofish as af
app.log("来自后台线程的一行")
app._status("tick", {"last": 0.83, "max": 0.91, "done": 3, "frames": 1234})
app._drain()          # 先把日志和状态处理掉（done 会把状态栏复位，所以分开验）
root.update()
txt = app.log_box.get("1.0", "end")
check("日志进了文本框", "来自后台线程的一行" in txt, txt.strip().splitlines()[-1:])
check("tick 更新状态栏",
      "0.83" in app.status.get() and "1234" in app.status.get(), app.status.get())
hit_before = app.status.get()
app.q.put(("done", None))
app._drain()
root.update()
check("done 后开始按钮恢复", str(app.start_btn["state"]) == "normal",
      str(app.start_btn["state"]))
check("done 后停止按钮禁用", str(app.stop_btn["state"]) == "disabled",
      str(app.stop_btn["state"]))
check("done 后状态栏复位（原来的 {} -> {}）".format(hit_before, app.status.get()),
      app.status.get() == "空闲", app.status.get())

print()
print("=" * 70)
print("5) 点击方式：前台 / 后台")
print("=" * 70)
check("默认前台模式", app.bg_var.get() is False, app.bg_var.get())
check("前台时「切到前台」可勾选", str(app.activate_chk["state"]) == "normal",
      str(app.activate_chk["state"]))

app.bg_var.set(True)
app._on_mode()
check("后台时「切到前台」被禁用", str(app.activate_chk["state"]) == "disabled",
      str(app.activate_chk["state"]))

app.dry_var.set(False)                  # 真点鼠标的后台模式必须指定窗口
dialogs.clear()
app.win_var.set("")
cfg = app._read_cfg()
check("后台模式（真点击）没填窗口 -> 拒绝",
      cfg is None and dialogs, dialogs[:1] or "居然通过了")

app.dry_var.set(True)                   # 试跑不点鼠标，所以不需要窗口
dialogs.clear()
cfg = app._read_cfg()
check("后台 + 试跑，没窗口也允许（反正不点）",
      cfg is not None and not dialogs, cfg and cfg.get("background"))
app.dry_var.set(False)

dialogs.clear()
app.win_var.set("Minecraft")
cfg = app._read_cfg()
check("后台模式带窗口 -> 通过且 flag 传下去",
      cfg is not None and cfg["background"] is True,
      cfg and cfg.get("background"))

app.win_var.set("Minecraft")
cfg = app._read_cfg()
app._save_settings(cfg)
app.bg_var.set(False)
app._load_settings()
check("后台模式被记住", app.bg_var.get() is True, app.bg_var.get())
app.bg_var.set(False)
app._on_mode()

print()
print("=" * 70)
print("6) 窗口列表（真实枚举，只读）")
print("=" * 70)
wins = af.list_windows()
check("能枚举到窗口", len(wins) > 0, f"{len(wins)} 个")
minecraft = [w for w in wins if "minecraft" in w[3].lower()]
print(f"    其中 Minecraft 相关: {[w[3][:44] for w in minecraft]}")

print()
print("=" * 70)
print("7) 「启动前自检」勾选真的生效（monkeypatch 音频层，不发声）")
print("=" * 70)
calls = []


def fake_prepare(*a, **k):
    calls.append(("prepare", a, k))
    return [1.0, 2.0], True


def fake_verify(*a, **k):
    calls.append(("verify", a, k))
    return {"err": None, "frames": 10, "peak": {"raw": 1.0, "mel": 1.0}}


def fake_run(*a, **k):
    calls.append(("run", a, k))
    return {"done": 0, "timeouts": 0, "error": None}


_orig = (af.prepare_template, af.verify_audio_path, af.run_fishing_session,
         af.describe_audio_source, af.resolve_window, g.template_path)
af.prepare_template = fake_prepare
af.verify_audio_path = fake_verify
af.run_fishing_session = fake_run
af.describe_audio_source = lambda src, log: (None, False, True)
af.resolve_window = lambda kw: (None, None)


def run_work(verify, tmpl_exists):
    """跑一遍 _work，返回其中对音频层的调用记录。"""
    calls.clear()
    if tmpl_exists:
        g.template_path = lambda name: os.path.join(ROOT, "calib",
                                                    "template_splash.npy")
    else:
        g.template_path = lambda name: os.path.join(ROOT, "calib",
                                                    "_no_such_test.npy")
    cfg = app._read_cfg()
    cfg["window"] = ""            # 不解析真窗口，免得找不到就提前返回
    cfg["verify"] = verify
    app._work(cfg)
    return list(calls)


c = run_work(verify=False, tmpl_exists=True)
prep = [x for x in c if x[0] == "prepare"]
vers = [x for x in c if x[0] == "verify"]
check("关掉自检：不再播音效自检（没调 verify_audio_path）", len(vers) == 0,
      f"verify 被调 {len(vers)} 次")
check("关掉自检：复用已校准的模板（不重新自校准）",
      bool(prep) and prep[0][1] and str(prep[0][1][1]).endswith("template_splash.npy"),
      prep[0][1][1] if prep and prep[0][1] else None)

c = run_work(verify=False, tmpl_exists=False)
prep = [x for x in c if x[0] == "prepare"]
vers = [x for x in c if x[0] == "verify"]
check("关掉自检且没模板：退回解码文件模板，且不写回模板文件",
      bool(prep) and prep[0][2].get("autocal") is False
      and prep[0][2].get("save") is False, prep[0][2] if prep else None)
check("关掉自检且没模板：依然不播音效", len(vers) == 0, f"verify 被调 {len(vers)} 次")

c = run_work(verify=True, tmpl_exists=True)
prep = [x for x in c if x[0] == "prepare"]
vers = [x for x in c if x[0] == "verify"]
check("勾上自检：做自校准 + 自检一遍",
      bool(prep) and prep[0][2].get("autocal") is True and len(vers) == 1,
      f"autocal={prep[0][2].get('autocal') if prep else None}, verify={len(vers)}")

(af.prepare_template, af.verify_audio_path, af.run_fishing_session,
 af.describe_audio_source, af.resolve_window, g.template_path) = _orig

app.on_close()
_restore_settings()
ok = all(c for _, c in results)
print()
print("=" * 70)
for n, c in results:
    print(f"  [{'ok' if c else 'FAIL'}] {n}")
print("\n总体:", "通过" if ok else "未通过")
raise SystemExit(0 if ok else 1)
