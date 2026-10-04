# autofish —— Minecraft 音频触发钓鱼

> **许可：GNU GPL v3.0** —— 含 NTESoundTrigger 的移植代码，属衍生作品，详见文末[「许可证」](#许可证)。

右键抛竿 → 听系统音频 → 等到咬钩音 → 右键收杆 → 循环。

**这个文件夹是自包含的**：代码、依赖（`.venv`）、音效、模板、日志全在里面，
不引用上级目录的任何东西。整个文件夹可以直接拷到别的电脑/别的盘符上双击运行。

> 从 Git 仓库克隆的话，`.venv/`、`build/`、`calib/`、`logs/` 都**不在**仓库里
> （见 `.gitignore`），先按文末「环境重建」建一次环境再跑。

---

## 快速开始

| 双击这个 | 作用 |
|---|---|
| **`gui.bat`** | **推荐日常使用** —— 图形界面，填参数点「开始钓鱼」 |
| `run.bat` | 命令行菜单（建环境 / 自检 / 标定 / 排查，全套工具） |
| `gui_admin.bat` | 以管理员身份开界面（后台模式被权限拦住时用，见下） |

### 快捷键

| 键 | 作用 | 生效范围 |
|---|---|---|
| **`Ctrl+D`** | 开始钓鱼 | **全局** —— 游戏在前台也能按 |
| **`Ctrl+F`** | 停止 | **全局** —— 同上 |
| `F5` | 开始钓鱼 | 仅 autofish 界面有焦点时 |
| `F6` / `Esc` | 停止 | 同上（只在**钓鱼中**才响应） |

全局热键走 Win32 `RegisterHotKey`，所以**钓鱼时切到游戏窗口、按Ctrl+D/Ctrl+F
一样能控制** —— 前台模式尤其需要（不然你得切回界面才能停）。
被别的程序占用时会自动降级并在日志区提示，此时仍可用 F5/F6。

命令行等价写法（在 `autofish/` 下）：

```bash
.venv\Scripts\python.exe -m mcfish.gui                # 图形界面
.venv\Scripts\python.exe -m mcfish.autofish --help        # 命令行帮助
.venv\Scripts\python.exe -m mcfish.record --probe         # 回环探针
.venv\Scripts\python.exe -m mcfish.calibrate --in calib\s1   # 标定分析
```

> 注意 Python 版本：**用 3.12 / 3.11**。3.13+ 没有 scipy 预编译包，
> 会退化成源码编译（需要 Fortran 编译器，基本必失败）。
> `run.bat` 会按 3.12 → 3.11 → 3.13 → 3.10 → 3 的顺序自动挑。

---

## 目录结构

```
autofish/                    <- 本文件夹，自包含
├─ gui.bat / run.bat / gui_admin.bat   入口（GBK+CRLF 编码，勿改成 UTF-8）
├─ build.bat                 一键打包（Nuitka + Zig，产物在 build\autofish.dist\）
├─ icon.png                  图标源图（exe 图标由它生成）
├─ mcfish/                   程序代码包 —— **打包进 exe 的只有这里**
│  ├─ paths.py               所有路径的唯一来源（钉在 autofish/ 内）
│  ├─ hotkey.py              全局热键：RegisterHotKey + 接管窗口过程
│  ├─ features.py            log-mel 音高鲁棒特征
│  ├─ detector.py            环形缓冲 + FFT 精确 NCC + 音高变体/音高库
│  ├─ autofish.py            主流程：抛竿 -> 听音 -> 收杆
│  ├─ record.py              回环录音 + 打标记
│  ├─ calibrate.py           标定分析、建议阈值
│  ├─ gui.py                 tkinter 图形界面
│  └─ splash.ogg             MC 咬钩音效（模板来源）
├─ packaging/                打包相关 —— **不进 exe**
│  ├─ entry.py               exe 入口（等价 python -m mcfish.gui，含崩溃弹窗/日志）
│  ├─ build.py               Nuitka + Zig 打包脚本（含预检、缓存重定向）
│  └─ make_icon.py           icon.png -> build\autofish.ico（七档尺寸）
├─ tools/                    开发脚本 —— **不进 exe**
│  ├─ test_*.py             各模块回归测试（run.bat 菜单里有入口）
│  ├─ _refs.py              定位上游参考波形；缺数据就让相关测试体面跳过
│  ├─ make_bat.py           .txt -> GBK+CRLF .bat 转换与校验
│  ├─ bat_enc.py            .bat 编码互转（GBK <-> UTF-8）
│  └─ fix_venv.py           换机器后修 .venv 的解释器指向
├─ calib/                    运行时数据（**不入库**）
│  ├─ gui_settings.json      界面参数（会自动记住）
│  ├─ template_splash.npy    自校准产出的模板
│  └─ gui_error.log          界面异常日志（出错时看这里）
├─ logs/                     运行日志（**不入库**）
├─ build/                    打包产物 autofish.dist\（**不入库**）
├─ .venv/                    独立虚拟环境（**不入库**，见「环境重建」）
├─ LICENSE                   GNU GPL v3 全文
├─ NOTES.md                  选型与实测记录（detector.py 多处引用）
├─ requirements.txt
└─ README.md                 本文件
```

**谁进 exe、谁不进**：打包只跟随 `packaging/entry.py` 的 import 图 —— 也就是
`mcfish/` 包和它的三方依赖。`tools/` 和 `packaging/` 自己都不会被带进包里
（构建完 `build.py` 还会再扫一遍发行目录确认）。

**所有路径都由 `mcfish/paths.py` 统一定义**，与「从哪个目录启动」无关 ——
所以从任何位置调`python -m mcfish.autofish` 都能把模板正确写进 `calib/`。

打包成 exe 之后这条规则**依然成立**，只是 `ROOT` 的含义变了一点点：
源码运行时它是 `autofish/`，打包后它是 **exe 所在目录** —— 所以配置、日志
都落在 `autofish.dist\` 里，跟 exe 待在一起。（注意不能用 `__file__` 推导：
打包后它指向 Nuitka 的内部解包路径，往里写东西进程一退就没了。）

---

## 第一次使用建议顺序

1. **双击 `gui.bat`** —— 若提示没环境，先跑 `run.bat` 选 `[1]`。
2. **先自检音频通路**（`run.bat` → `[2]`）：它会播一遍 `splash.ogg`、同时录回环，
   报告 raw / mel 匹配分。**分数上得去（≥0.6）才说明整条音频链是通的**。
3. **先试跑**（`run.bat` → `[4]`，或界面勾「试跑」）：不点鼠标，只看实时分数，
   用它来定阈值最直接。
4. **正式开钓**：界面填好窗口和次数 → 开始。

---

## 关键参数

| 参数 | 说明 |
|---|---|
| `--window <关键字>` | 目标窗口标题里的关键字（用 `run.bat` → `[9]` 可列出） |
| `--count N` | 钓多少次（`0` = 一直钓到 Ctrl+C）。数的是**成功收杆**次数 |
| `--feature mel\|raw` | `mel`（默认，扛音高）/ `raw`（音高稳定时更准） |
| `--thresh X` | 匹配阈值。默认 `raw=0.60`、`mel=0.80` |
| `--background` | 后台模式：不抢焦点，钓鱼时还能用电脑（但游戏不一定认） |
| `--dry-run` | 只看分数不点鼠标 —— **第一次务必用它** |
| `--cast-delay` / `--max-wait` / `--give-up` | 抛收后等待 / 单次等咬钩上限 / 连续失败上限 |

---

## 已知要点（踩过的坑）

1. **模板必须来自回环录制**。直接解码 `splash.ogg` 当模板，真实回环上只有
   `raw≈0.26`（设备混音/重采样会改波形，而波形互相关对此极敏感）。
   所以默认会**自校准**：播一遍 → 录回环 → 从录音截起振后 0.35s 当模板。
   `--no-autocal` 可关，但那样基本认不出来。

   界面上的「启动前自检音频通路」勾选**同时管这两件事**（自校准 + 自检，都会播音效）：
   取消勾选就不再发声，此时会自动**复用上次校准存下的** `calib/template_*.npy`；
   若还没有校准过，才退回解码文件模板（并在日志里提醒识别率会很低）。
   注意复用只在 `calib/` 里已有模板时精度才不受影响 —— 所以**换设备/改音频路由后
   要重新勾一次自检**。

   **自校准失败时不会写回模板文件**（只有成功才落盘）：否则就用那个 `raw≈0.26` 的
   兜底模板把上次校准出的好模板覆盖掉了，下次「不勾自检」还会照用不误。

2. **音高随机，raw 必漏**。MC 的水花音每次播放音高都不同（引擎 pitch 参数，
   约 ±1.6 半音）。裸波形互相关对音高是「δ 函数」——音高差半个半音就掉到噪声。
   所以默认用 `mel` + 音高库（12 份参考，任意音高都在 0.89 以上）。

3. **背景模式的 UIPI 限制**。`PostMessage` 发鼠标消息时，
   **发送方权限必须 ≥ 目标方**。所以：
   - 脚本被降权（如从某些终端启动）→ 从资源管理器双击 `gui.bat`
   - 游戏是管理员启动的 → 用 `gui_admin.bat` / `run.bat` 的 `[A]`
   - 不想提权 → 用前台模式（代价：钓鱼时不能用电脑）

   先用 `run.bat` → `[B]` 往游戏里投**一次**右键，验证游戏认不认。

4. **单机存档失焦会暂停**，所以后台模式基本只适合多人/局域网联机。

5. **只能采设备级回环**，做不到「只采某个窗口的声音」。`--window` 的作用是
   确认是哪个进程 + 把窗口切到前台（点击必须打到它身上）。
   想减少干扰：把游戏输出改到独立设备，然后用
   `--audio "loopback:<设备名>"` 只采它。

6. **bat 文件必须是 GBK + CRLF**。cmd.exe 逐字节解析批处理，UTF-8 的中文会让它
   把文案当命令执行，带 BOM 更会直接把 BOM 当第一条命令。
   改完用 `python tools/make_bat.py run.bat run.bat` 转回来并校验。

7. **全局热键不能靠另起线程跑 `GetMessage`**。消息队列是**线程内共享**的，
   另开的线程会和 Tk 的 `mainloop` 互相抢消息 —— 症状是
   `RegisterHotKey` 明明返回成功、按键也真的按了，但回调一次都不触发。
   正确做法是**接管窗口过程**（`SetWindowLongPtrW` + `GWLP_WNDPROC`），
   让 `WM_HOTKEY` 跟着 Tk 自己的循环走。两个配套的坑：
   - `LRESULT` 必须用 `ctypes.c_ssize_t`，`WPARAM/LPARAM` 用 `wintypes` 原生类型。
     用 `c_long` 转发时会抛 `OverflowError`，而这异常发生在窗口过程里，
     症状是「消息莫名消失」，比直接崩溃更难查。
   - 建好的 `WNDPROC` 对象**必须留住引用**（存在模块级 dict 里），
     否则被 GC 之后 Windows 那边就是野指针，一按热键就随机崩溃。

---

## 打包成 exe（Nuitka + Zig，文件夹模式）

双击 `build.bat`，或等价地：

```bash
.venv\Scripts\python.exe packaging\build.py
```

产物是 **`build\autofish.dist\`**（整个文件夹约 200MB），双击里面的
`autofish.exe` 即可运行 —— 无控制台黑窗，图标取自 `icon.png`。

- **只打包程序代码**：入口是 `packaging/entry.py`，它只 `import mcfish`，
  所以 `tools/`（测试/诊断）与 `packaging/` 自己都不会进包；构建完脚本还会再
  扫一遍发行目录确认。
- **C 编译器用 Zig**（`--zig`）：不用装几个 GB 的 Visual Studio。Zig 优先复用
  Nuitka 私有空间里现成那份，不额外下载 347MB。
- **图标取自 `icon.png`**：由 `packaging/make_icon.py` 生成
  16/24/32/48/64/128/256 七档 `build\autofish.ico`（像素图用最近邻缩放，
  平滑插值会把它糊掉）。这个 ico 同时用于 exe 文件图标、窗口与任务栏
  （`gui._apply_window_icon` 运行时会再从 exe 旁读 `icon.ico` / `icon.png`）。
- 构建前先跑几秒**预检**（缓存可写 / Zig 可用 / 环境变量齐全 / 依赖在位），
  免得白等十几分钟才发现挂在环境上。

| 参数 | 作用 |
|---|---|
| （无） | 默认：**独立文件夹** `build\autofish.dist\`，启动快、配置落在 exe 旁 |
| `--console` | 保留黑窗口。排查「双击没反应」时用 |
| `--jobs=N` | 限并发编译。scipy 的 `stats._continuous_distns`、`integrate._lebedev` 是自动生成的巨无霸 C 文件（24MB / 12MB），满并发下 Zig 的 LLVM 后端直接 `out of memory`；`--jobs=1` 最稳（实测 652 个目标文件全过） |
| `--onefile` | 单文件 exe。**本机会失败**：最后一步 zstd 压缩整个包时 `Allocation error : not enough memory`；加 `--no-compress` 能压过去，但启动要解压、体积也大 |

**为什么默认文件夹而不是单文件**：onefile 的收尾要用 zstd 把整个发行目录压成一个
包，本机实测在最后一步 OOM（编译全过、链接也成，白等十几分钟）；而且单文件每次
启动都要把上百 MB 解压到 `%TEMP%`。程序本来就要在 exe 旁读写 `calib\`、`logs\`，
文件夹形态更自然。

**拷到别的电脑**：整个 `autofish.dist\` 文件夹一起拷走即可，自带 Python 和全部依赖
（别只拷 exe —— 它旁边的 dll、`numpy`、`scipy`、`tcl\tk` 都得跟着）。
第一次运行会在 **exe 所在目录**建 `calib\`（设置、标定模板）和 `logs\`，
所以别把它放进 `C:\Program Files\` 这类需要管理员才能写的位置。

**后台模式需要管理员权限**（UIPI 限制，见上面第 3 条）：右键 exe →
「以管理员身份运行」。不想提权就用前台模式。

### 这台机器上特有的坑（都封在 `packaging/build.py` 里）

- **编译内存**：`--jobs=1` 串行编译才能过 scipy 那两个巨型生成文件；满并发必 OOM。
- **打包收尾内存**：默认不用 onefile，正是为了绕开 zstd 压缩的 OOM（见上）。
- **缓存全部重定向到工程内**（`.nuitka-cache\`）：本机 `%LOCALAPPDATA%` 对
  python 及其**全部子进程**只读（连 `%TEMP%` 都写不进去），而 Nuitka / Zig /
  pip 的默认缓存全在那儿 —— 不重定向就是「编译到一半 PermissionError」。
- **补全标准 Windows 环境变量**：本机 shell 环境被裁剪过，缺
  `PROCESSOR_ARCHITECTURE`（SCons 硬取 → 编译第一个文件就 KeyError）、
  `ComSpec`、`PATHEXT` 等，脚本从注册表按 Windows 自己的规则补齐。注意变量名
  大小写不敏感 —— 注册表的 `Path` 会和已有的 `PATH` 撞成重复键，得按小写合并。
- **数据文件三件套**（运行时才读、静态分析看不到，**落点必须和代码算出来的路径一致**）：
  - `mcfish/splash.ogg` → 落在发行目录的 `mcfish/` 下；
  - soundcard 的三个 CFFI 头 `*.py.h` → 必须落在发行目录的 **`soundcard/` 子目录**下，
    不能铺在 dist 根目录。原因：`soundcard/mediafoundation.py` 里是
    `open(os.path.join(os.path.split(__file__)[0], 'mediafoundation.py.h'))`，
    冻结后 `__file__` 解析成 `<dist>\soundcard\mediafoundation.py`，所以头文件得在它隔壁。
    放错层的症状是：能起窗口，但**「拿不到默认播放设备」→ 自校准一帧都采不到 →
    音频源下拉框只剩「整机回环 / 麦克风」两项**（`gui.device_choices()` 把异常吞了）；
  - soundfile 的 `libsndfile_x64.dll` → 在 `_soundfile_data/` 里，要连目录一起带。
- **`--update-check=never`**：更新检查会去写 `%LOCALAPPDATA%`，卡死。

`.nuitka-cache\` 只是缓存（编译一次能涨到 1GB 上下），嫌大可以整个删掉，
代价是下次打包重新编译十几分钟。

---

## 自测（改完代码跑一下）

```bash
.venv\Scripts\python.exe tools\test_detector.py           # 检测器
.venv\Scripts\python.exe tools\test_ncc_bounds.py# NCC 边界
.venv\Scripts\python.exe tools\test_calibrate_cli.py     # 标定流程
.venv\Scripts\python.exe tools\test_gui.py                # 界面
.venv\Scripts\python.exe tools\test_hotkey.py             # 快捷键（含模拟真实按键）
.venv\Scripts\python.exe tools\test_background_click.py   # 后台点击
```

或者直接 `run.bat` → `[T]` 一次跑完。这些**不需要声卡**。
`tools/test_autofish_loopback.py` 是真回环 + 真播放的端到端测试（会出声）。
`tools/test_gui_live.py` 是界面真机冒烟（走完整界面链路，dry-run 不点鼠标）。

其中 `test_detector` / `test_ncc_bounds` / `test_calibrate` / `test_calibrate_cli` /
`test_autofish_min` 拿 NTESoundTrigger 的两个音效当「咬钩音 / 抛竿音」样本。那份素材
**不随本仓库分发**，`tools/_refs.py` 会按 `NTES_ROOT` 环境变量 → 仓库同级目录 →
`tools/_ref_data/` 的顺序去找，找不到就打印一行 `[skip]` 并以 0 退出（不算失败）。
想跑全这几条：

```bash
git clone -b NTE-test https://github.com/Wattls/NTESoundTrigger.git
set NTES_ROOT=%CD%\NTESoundTrigger-NTE-test
```

打完包想确认产物没问题，跑验收（图标逐像素比对 + 运行时资源落点 + 拷到干净目录实跑 +
检查配置目录落点）：

```bash
.venv\Scripts\python.exe packaging\verify.py
```

---

## 环境重建（万一`.venv` 坏了）

```bash
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

或者删掉 `.venv`，双击 `run.bat` 选 `[1]`。

### 换电脑/ 换盘符后打不开？

**表现**：双击 `gui.bat` 或 `run.bat` 报 `No Python at '"...python.exe'`。
**原因**：venv 在 `pyvenv.cfg` 里记了「建它用的解释器在哪」（比如
`home = D:\Python\python312`）。文件夹拷到别的电脑、或者那个 Python
被移动/卸载之后，这个路径就失效了。

**解决办法**（不用重装依赖，几秒钟）：

```bash
py -3.12 tools\fix_venv.py
```

它只把 `home` 改到新机器上的解释器位置。包里numpy / scipy / soundcard
都还在，所以改完就能直接用。

或者直接双击 `run.bat` 选 `[1]` —— 它已经会自动跑一遍这个修复。
若目标机器**压根没装 Python**，那还是要先装 3.12，然后让`run.bat` 的 `[1]`
重建（这种情况无法复用旧 venv）。

---

## 许可证

本项目以 **GNU General Public License v3.0** 发布，全文见 [`LICENSE`](LICENSE)。

需要特别说明：`mcfish/detector.py` 是从 **NTESoundTrigger** 的 `Watcher` **移植**而来
（保留了它「模板归一化 + 滑窗归一化互相关取最大值」的匹配数学，并修掉了原实现的三个
问题 —— 见该文件头部注释）。因此本仓库属于 GPLv3 的**衍生作品**，整体必须继续以
GPLv3 发布，不能改成其他许可或闭源。

想复用本仓库的代码，请一并遵守 GPLv3，主要是这几条：

- **分发**（发群/发论坛/放网盘，或做成产品给别人用）时，必须同时提供**完整对应源码**，
  含能重建同一份二进制的构建脚本（本仓库的 `packaging/` + `build.bat` 就是）。
- 保留版权声明与 GPLv3 全文，并**显著标注你的修改**。
- **不得附加额外限制** —— 「仅供学习」「禁止商用」这类字样会让授权自动失效。
- 无任何担保。

只在自己机器上用、不对外分发，则不受这些义务约束。

### 完整源码

对应所发布二进制的完整源码就是**本仓库本身**：

```
https://github.com/khh233/MinecraftAutoFish
```

若你拿到的是别人转发的 `autofish.dist` 压缩包，源码请到上面的地址取；按 GPLv3 §6，
转发给你的人也应当向你提供源码。

## 致谢

- [Wattls/NTESoundTrigger](https://github.com/Wattls/NTESoundTrigger) —— 音频触发
  架构与匹配数学移植自它（GPL-3.0）。
- [ImLaoBJie/ZZZSoundTrigger](https://github.com/ImLaoBJie/ZZZSoundTrigger) ——
  NTESoundTrigger 又衍生自它，是最早的「按音效波形触发操作」实现。

## 第三方组件

| 组件 | 许可 | 说明 |
|---|---|---|
| numpy / scipy / soundcard / soundfile | BSD | 无 copyleft 义务 |
| pydirectinput | MIT | |
| **libsndfile**（`_soundfile_data/libsndfile_x64.dll`） | **LGPL-2.1** | 独立 DLL + 动态加载，未静态链接；再分发请保留其版权与许可声明 |
| Python 运行时（`python312.dll` 等） | PSF License | 随 exe 分发 |

`mcfish/splash.ogg` 是 Minecraft 的游戏音效，仅作模板来源；其版权归 Mojang 所有，
不在本项目授权范围内。

