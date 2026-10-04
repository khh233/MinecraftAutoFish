# autofish —— Minecraft 音频触发钓鱼
# Copyright (C) 2026 欧金金 · 以 GNU GPL v3 发布（无担保），全文见 LICENSE。
"""定位并加载 NTESoundTrigger 的参考波形。

有几条回归测试拿上游仓库的两个音效当「咬钩音 / 抛竿音」样本 —— 真实、带瞬态，
比合成信号更能暴露 NCC 的问题。这两个 .npy 属于 Wattls/NTESoundTrigger
（GPL-3.0），**不随本仓库分发**（游戏音效素材，避免再分发），所以这里按下面的
顺序去找；实在找不到就让测试体面地跳过，而不是抛一堆 FileNotFoundError。

查找顺序：
    1. 环境变量 ``NTES_ROOT``
    2. 仓库同级目录   ``<repo>/../NTESoundTrigger-NTE-test``
    3. 仓库内         ``tools/_ref_data/``（自己放一份进去也行）

需要时::

    git clone -b NTE-test https://github.com/Wattls/NTESoundTrigger.git
    set NTES_ROOT=<克隆出来的 NTESoundTrigger-NTE-test 目录>
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

FILES = {"dodge": "闪避波形_32000.npy", "counter": "承轨反击波形_32000.npy"}

HINT = (
    "[skip] 缺少 NTESoundTrigger 参考波形（上游素材，未随本仓库分发）\n"
    "       取一份：git clone -b NTE-test "
    "https://github.com/Wattls/NTESoundTrigger.git\n"
    "       然后二选一：\n"
    "         set NTES_ROOT=<...>/NTESoundTrigger-NTE-test\n"
    "         或把 闪避波形_32000.npy 与 承轨反击波形_32000.npy 放进 "
    + os.path.join(HERE, "_ref_data") + "\n"
)


def _dir_for(kind: str) -> str | None:
    """返回含该波形的目录；找不到返回 None。"""
    for d in (os.environ.get("NTES_ROOT"),
              os.path.join(REPO, os.pardir, "NTESoundTrigger-NTE-test"),
              os.path.join(HERE, "_ref_data")):
        if d and os.path.isfile(os.path.join(d, FILES[kind])):
            return os.path.abspath(d)
    return None


def load(*kinds: str) -> list:
    """加载参考波形；``kinds`` 取 ``"dodge"`` / ``"counter"``，默认只取 dodge。

    缺数据时打印提示并 ``SystemExit(0)`` —— 算跳过，不算失败。
    """
    import numpy as np

    kinds = kinds or ("dodge",)
    out = []
    for k in kinds:
        d = _dir_for(k)
        if d is None:
            print(HINT)
            raise SystemExit(0)
        out.append(np.load(os.path.join(d, FILES[k])))
    return out
