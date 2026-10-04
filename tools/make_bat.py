"""Convert a UTF-8 .txt source into a GBK + CRLF + BOM-less .bat, with checks.

cmd.exe requires CRLF and mis-parses UTF-8 batch files, and a leading UTF-8 BOM
becomes a bogus first command. Chinese Windows uses codepage 936, so GBK is the
safe encoding.

usage: python tools/make_bat.py <src.txt> <dst.bat> [--check-only]
       python tools/make_bat.py run.bat run.bat--check-only
"""
import sys, os, re

# 不写死绝对路径 —— 默认在本文件夹内自转自校
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.path.join(ROOT, "run.bat")

args = [a for a in sys.argv[1:] if not a.startswith("--")]
check_only = "--check-only" in sys.argv
if len(args) >= 2:
    SRC, DST = args[0], args[1]
elif len(args) == 1:
    SRC = DST = args[0]
else:
    SRC = DST = DEFAULT

raw = open(SRC, "rb").read()
bom = raw[:3] == b"\xef\xbb\xbf"
try:
    text = raw.decode("utf-8-sig")      # utf-8-sig 自动剥离 BOM
    src_enc = "UTF-8"
except UnicodeDecodeError:
    # 源已经是 GBK 的 .bat（比如已经发布出去的那份，只想 --check-only 校验结构）
    text = raw.decode("gbk")
    src_enc = "GBK"
print(f"源: {SRC}")
print(f"  {len(raw)} 字节, 编码={src_enc}, BOM={bom}")
lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
print(f"  {len(lines)} 行")

# --- 结构检查：跨行 if/for 块的括号嵌套 ---
depth, problems = 0, []
for i, ln in enumerate(lines, 1):
    s = ln.strip()
    if not s or s.startswith("rem") or s.startswith("::") or s.startswith("echo"):
        continue
    d0 = depth
    depth += s.count("(") - s.count(")")
    if depth < 0:
        problems.append((i, s, d0))
        depth = 0
print(f"  [{'!' if depth else 'ok'}] 括号最终嵌套深度 = {depth}")
for i, s, d0 in problems:
    print(f"      [!] 行{i} 深度变负(前 {d0}): {s!r}")

# --- 乱码特征 ---
sus = ["鍒", "閽", "鏍", "缂", "璺", "鎵", "璇", "鏈", "绉", "澹", "铏", "锛",
       "鐢", "涓", "鍚", "鏂", "妯", "鏉", "锟"]
hits = [(i, s, ln) for i, ln in enumerate(lines, 1) for s in sus if s in ln]
print(f"  [{'!' if hits else 'ok'}] 乱码残留 = {len(hits)}")
for i, s, ln in hits[:10]:
    print(f"      [!] 行{i} 含'{s}': {ln.strip()[:70]}")

glued = [ln for ln in lines if re.search(r"echo\.\S", ln)]
print(f"  [{'!' if glued else 'ok'}] echo 粘连 = {len(glued)}")

# --- goto / 标签 交叉检查：goto 到不存在的标签会静默跳到文件末尾，很难查 ---
labels = {m.group(1).lower() for m in
          (re.match(r"\s*:([A-Za-z_]\w*)", ln) for ln in lines) if m}
gotos = []
for ln in lines:
    s = ln.strip()
    if s.lower().startswith("rem") or s.startswith("::"):
        continue
    gotos.extend(m.group(1).lower()
                 for m in re.finditer(r"goto\s+:?([A-Za-z_]\w*)", s, re.I))
missing = sorted({g for g in gotos if g not in labels and g != "eof"})
print(f"  [{'!' if missing else 'ok'}] goto 目标都存在"
      f"（goto {len(gotos)} 处 / 标签 {len(labels)} 个）"
      + (f" —— 找不到: {missing}" if missing else ""))
unused = sorted(labels - set(gotos))
if unused:
    print(f"      提示: 没被 goto 的标签: {unused}（可能是顺序执行到的入口，正常）")

if check_only:
    sys.exit(1 if (depth or problems or hits or glued or missing) else 0)

# --- 转成 GBK + CRLF ---
out = "\r\n".join(lines)
if out.endswith("\r\n"):
    out = out[:-2]
try:
    data = out.encode("gbk")
except UnicodeEncodeError as e:
    print(f"[!] 无法用 GBK 编码: {e}")
    bad = set()
    for ch in set(text):
        try:
            ch.encode("gbk")
        except UnicodeEncodeError:
            bad.add(ch)
    print("    问题字符:", " ".join(sorted(bad)))
    sys.exit(1)

assert not data.startswith(b"\xef\xbb\xbf"), "不应有 BOM"
crlf = data.count(b"\r\n")
lf_only = data.count(b"\n") - crlf
open(DST, "wb").write(data)
print(f"写出: {DST}")
print(f"  {len(data)} 字节, CRLF={crlf}, 单独 LF={lf_only} "
      f"{'(ok)' if lf_only == 0 else '(!! 有裸 LF)'}")
print(f"  首 24 字节: {data[:24]!r}")
