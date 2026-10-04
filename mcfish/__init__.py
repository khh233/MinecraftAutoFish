# autofish —— Minecraft 音频触发钓鱼
# Copyright (C) 2026 欧金金 · 以 GNU GPL v3 发布（无担保），全文见 LICENSE。
"""mcfish —— Minecraft 音频触发钓鱼。

模块划分：
    paths      所有路径（钉在 autofish/ 内，与当前工作目录无关）
    features   log-mel 音高鲁棒特征
    detector   环形缓冲 + FFT 精确 NCC + 音高变体/音高库
    autofish   钓鱼主流程（抛竿 -> 听音 -> 收杆）
    record     回环录音 + 打标记
    calibrate  标定分析、出建议阈值
    gui        tkinter 图形界面（autofish 的外壳）
"""
__all__ = ["paths", "features", "detector", "autofish",
           "record", "calibrate", "gui"]
__version__ = "1.0.0"
