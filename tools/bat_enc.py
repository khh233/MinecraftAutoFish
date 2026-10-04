"""Convert a GBK .bat back to UTF-8 (BOM-less, CRLF) so editors/tools can read it,
or convert UTF-8 -> GBK when --to-gbk is given.

usage: python tools/bat_enc.py <file> [--to-gbk]
"""
import sys

p = sys.argv[1]
to_gbk = "--to-gbk" in sys.argv
raw = open(p, "rb").read()

if to_gbk:
    text = raw.decode("utf-8-sig")
    out = text.replace("\r\n", "\n").replace("\r", "\n")
    data = out.replace("\n", "\r\n").encode("gbk")
    open(p, "wb").write(data)
    print(f"{p}: UTF-8 -> GBK, {len(data)} 字节, CRLF={data.count(bytes([13,10]))}")
else:
    text = raw.decode("gbk")
    out = text.replace("\r\n", "\n").replace("\r", "\n")
    data = out.replace("\n", "\r\n").encode("utf-8")
    open(p, "wb").write(data)
    print(f"{p}: GBK -> UTF-8, {len(data)} 字节, CRLF={data.count(bytes([13,10]))}")
