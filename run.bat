@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "VENV=%ROOT%\.venv"
set "VPY=%VENV%\Scripts\python.exe"
rem 不要设 PYTHONUTF8/PYTHONIOENCODING。控制台默认 cp936，而这个 bat 自己也是
rem GBK 编码 —— 强行让 python 输出 UTF-8 会让两边说不同的话，满屏乱码。
rem 让 python 跟着控制台编码走即可（autofish 里只放宽了 errors=replace）。
set "PIP_DISABLE_PIP_VERSION_CHECK=1"

title autofish - MC 音频收杆

rem ---------------- 探测 Python ----------------
rem 注意: 检测完必须立刻 goto，绝不能让执行「流过」:try_* 标签。
rem 若 %1 为空会执行成 `py -`，那会启动交互式解释器并永久等待 stdin。
:detect
set "BASEPY="
call :try_py 3.12
if defined BASEPY goto menu
call :try_py 3.11
if defined BASEPY goto menu
call :try_py 3.13
if defined BASEPY goto menu
call :try_py 3.10
if defined BASEPY goto menu
call :try_py 3
if defined BASEPY goto menu
call :try_py_plain
goto menu

:try_py
if defined BASEPY goto :eof
if "%~1"=="" goto :eof
py -%1 -c "import sys" >nul 2>&1
if not errorlevel 1 set "BASEPY=py -%1"
goto :eof

:try_py_plain
if defined BASEPY goto :eof
python -c "import sys" >nul 2>&1
if not errorlevel 1 set "BASEPY=python"
goto :eof

rem ---------------- 主菜单 ----------------
:menu
cls
echo ============================================================================
echo   autofish - Minecraft 音频触发收杆（独立版）
echo ============================================================================
echo   工作目录: %ROOT%
echo   （本文件夹自带代码与环境，不依赖上级目录）
if exist "%VPY%" (
  for /f "delims=" %%v in ('"%VPY%" -c "import sys;print(sys.version.split()[0])" 2^>nul') do set "PYVER=%%v"
  echo   虚拟环境: 已就绪  ^(Python !PYVER!^)
) else (
  echo   虚拟环境: 未创建  ^<-- 请先跑 [1]
)
echo ============================================================================
echo.
echo   [1] 创建虚拟环境 + 安装依赖   ^(只需一次；文件夹搬走后再跑一次也能修^)
echo.
echo   ---- 开钓 ----
echo   [2] 自检音频通路（播一遍 splash.ogg，报告 raw/mel 匹配分）  ^<- 先跑这个
echo   [3] 开始自动钓鱼（右键抛竿 -^> 听咬钩音 -^> 右键收杆，可设次数）
echo   [4] 试跑：不点鼠标，只看实时匹配分
echo.
echo   ---- 出问题时排查 ----
echo   [5] 回环探针（录 10 秒，只看有没有信号）
echo   [6] 标定录音（咬钩按回车 / 抛竿按空格）
echo   [7] 分析标定结果（分数分布 + 建议阈值 + 判定方案）
echo   [8] 实时分数（用已有模板）
echo   [9] 列出窗口 / 音频设备
echo   [B] 后台点击自测（往游戏窗口投一次，看它认不认）
echo   [A] 以管理员身份开图形界面（后台模式被权限拦住时用）
echo.
echo   ---- 其它 ----
echo   [G] 打开图形界面（图形化开始/停止，推荐）
echo快捷键: Ctrl+D 开始 / Ctrl+F 停止（全局，游戏在前台也能按）
echo   [T] 跑自测（不需要声卡，验证代码）
echo   [N] 打开分析报告 REPORT.md
echo   [0] 退出
echo.
set "CH="
set /p "CH=请选择: "
if "%CH%"=="1" goto setup
if "%CH%"=="2" goto verify
if "%CH%"=="3" goto autofish
if "%CH%"=="4" goto dryrun
if "%CH%"=="5" goto probe
if "%CH%"=="6" goto calib_rec
if "%CH%"=="7" goto calib_run
if "%CH%"=="8" goto live
if "%CH%"=="9" goto listall
if /i "%CH%"=="B" goto bgtest
if /i "%CH%"=="A" goto guiadmin
if /i "%CH%"=="G" goto gui
if /i "%CH%"=="T" goto selftest
if /i "%CH%"=="N" goto notes
if "%CH%"=="0" exit /b 0
goto menu

rem ---------------- 需要虚拟环境 ----------------
:need_venv
if exist "%VPY%" exit /b 0
echo.
echo [!] 虚拟环境还没建，请先跑 [1]。
echo.
pause
goto menu

rem ---------------- [1] 建环境 ----------------
:setup
echo.
echo ----------------------------------------------------------------------------
echo  查找 Python
echo ----------------------------------------------------------------------------
set "BASEPY="
rem 优先 3.12/3.11：3.13 起缺少 scipy 预编译包，会退化成源码编译
call :try_py 3.12
if not defined BASEPY call :try_py 3.11
if not defined BASEPY call :try_py 3.13
if not defined BASEPY call :try_py 3.10
if not defined BASEPY call :try_py 3
if not defined BASEPY call :try_py_plain
if not defined BASEPY (
  echo [!] 没找到可用的 Python。
  echo     请安装 Python 3.12 并勾选 "Add python.exe to PATH"。
  pause
  goto menu
)
for /f "delims=" %%v in ('%BASEPY% -c "import sys;print(sys.version.split()[0])"') do set "BV=%%v"
echo   使用: %BASEPY%   ^(Python %BV%^)

if not exist "%VENV%" (
  echo.
  echo   创建虚拟环境 .venv ...
  %BASEPY% -m venv "%VENV%"
  if errorlevel 1 (
    echo.
    echo   [注意] venv 内置的 pip 引导 ^(ensurepip^) 失败了，改用 --without-pip 重试。
    %BASEPY% -m venv --without-pip "%VENV%"
  )
)
if not exist "%VPY%" (
  echo.
  echo [!] 创建虚拟环境失败，.venv 里没有 python.exe。
  echo     手动做法:
  echo       py -3.12 -m venv .venv
  echo       .venv\Scripts\python.exe -m pip install numpy soundcard pydirectinput scipy
  pause
  goto menu
)
if not exist "%VENV%\Scripts\pip.exe" (
  echo   虚拟环境缺 pip，尝试用 ensurepip 补 ...
  "%VPY%" -m ensurepip --upgrade
  if errorlevel 1 (
    echo   [注意] ensurepip 仍失败。请手动跑:
    echo     %VPY% -m ensurepip --upgrade
    pause
    goto menu
  )
)

rem 整个文件夹被拷到别的电脑/别的盘时，.venv 里记的解释器路径会失效。
rem 先试着修一下（只改 pyvenv.cfg 的 home，不用重装依赖）。
"%VPY%" "%ROOT%\tools\fix_venv.py"
if errorlevel 1 (
  echo   [注意] venv 没能自动修好。若它已经坏了，最省事是删掉 .venv 文件夹
  echo          再重新跑这个菜单的 [1]。
)

echo.
echo   升级 pip ...
"%VPY%" -m pip install --upgrade pip >nul 2>&1

echo.
echo   安装必需依赖: numpy soundcard pydirectinput
"%VPY%" -m pip install numpy soundcard pydirectinput
if errorlevel 1 (
  echo.
  echo [!] 必需依赖安装失败，把上面的报错发给我。
  pause
  goto menu
)

echo.
echo   安装可选依赖: scipy
"%VPY%" -m pip install scipy
if errorlevel 1 (
  echo   [注意] scipy 装不上也不影响：高通滤波会自动降级成 numpy 实现。
)

echo.
echo   校验安装结果:
"%VPY%" -c "import numpy,soundcard;print('  numpy',numpy.__version__,' soundcard',soundcard.__version__)"
"%VPY%" -c "import scipy;print('  scipy',scipy.__version__)" 2>nul
if errorlevel 1 echo   scipy 不可用（已降级，正常）
"%VPY%" -c "import pydirectinput;print('  pydirectinput ok')" 2>nul
if errorlevel 1 echo   pydirectinput 不可用（只有最终收杆脚本才需要）

echo.
echo [完成] 环境就绪。下一步请跑 [2] 自检音频通路。
echo.
pause
goto menu

rem ---------------- [2] 自检音频通路 ----------------
:verify
call :need_venv
echo.
echo ----------------------------------------------------------------------------
echo  自检音频通路
echo ----------------------------------------------------------------------------
echo   会把 mcfish\splash.ogg 播一遍，同时采回环，报告 raw / mel 两种匹配分。
echo   这一步是「能不能用」的分界线：
echo     分数上得去  -^> 播放-^>系统混音-^>回环采集-^>模板匹配 全通
echo     分数上不去  -^> 是设备路由/独占模式的问题，先在系统里解决
echo.
echo   注意：脚本会先做「自校准」—— 播一遍、录回来、用录音当模板。
echo   实测这一步是必需的（用解码文件当模板，打真实回环只有 raw 0.26）。
echo.
set "SND="
set /p "SND=音效文件? [直接回车用 mcfish\splash.ogg]: "
if not defined SND set "SND=mcfish\splash.ogg"
echo.
"%VPY%" -m mcfish.autofish --sound "%SND%" --verify-only
echo.
echo   退出码: 0=通路可用  2=分数不够  1=设备/文件问题
pause
goto menu

rem ---------------- [3] 开始自动钓鱼 ----------------
:autofish
call :need_venv
echo.
echo ----------------------------------------------------------------------------
echo  开始自动钓鱼
echo ----------------------------------------------------------------------------
echo   流程: 右键抛竿 -^> 等咬钩音 -^> 右键收杆 -^> 循环
echo.
echo   会先问三件事：目标窗口、音效文件、钓多少次。
echo   「目标窗口」填标题关键字即可（用 [9] 可以先列出来）。
echo   开始前脚本会把窗口切到前台，之后每次点击前都会确认它还在前台 ——
echo   不然后台的右键会打到别的程序上。
echo.
echo   钓鱼期间尽量不要用别的程序抢焦点。
echo.
set "WIN="
set /p "WIN=窗口标题关键字? [直接回车用 Minecraft]: "
if not defined WIN set "WIN=Minecraft"
set "SND="
set /p "SND=音效文件? [直接回车用 mcfish\splash.ogg]: "
if not defined SND set "SND=mcfish\splash.ogg"
set "N="
set /p "N=钓多少次? [直接回车用 50，填 0 = 一直钓]: "
if not defined N set "N=50"
set "FEA="
set /p "FEA=特征 raw 还是 mel? [直接回车用 mel；音高稳定才用 raw]: "
if not defined FEA set "FEA=mel"
echo.
echo   点击方式:
echo     1 = 前台（默认）: 会把游戏切到前台，钓鱼时你不能用电脑
echo     2 = 后台        : 不抢焦点，游戏窗口可以盖住，你还能用电脑
echo                       但这取决于游戏认不认消息投递 —— 建议先用 [B] 试一下
set "MODE="
set /p "MODE=选 1 还是 2? [直接回车用 1]: "
if not defined MODE set "MODE=1"
set "BG="
if "%MODE%"=="2" set "BG=--background"
echo.
echo   开始: 窗口=%WIN%  音效=%SND%  次数=%N%  特征=%FEA%  点击=%MODE%
echo   Ctrl+C 可随时停止。
echo.
"%VPY%" -m mcfish.autofish --sound "%SND%" --window "%WIN%" --count %N% --feature %FEA% %BG%
echo.
pause
goto menu

rem ---------------- [4] 试跑（不点鼠标） ----------------
:dryrun
call :need_venv
echo.
echo ----------------------------------------------------------------------------
echo  试跑：不点鼠标，只看实时匹配分
echo ----------------------------------------------------------------------------
echo   用来看「实际响的时候分数能到多少」，方便定阈值。
echo   请在游戏里手动抛竿收杆，或让游戏放着声音，观察分数。
echo.
set "SND="
set /p "SND=音效文件? [直接回车用 mcfish\splash.ogg]: "
if not defined SND set "SND=mcfish\splash.ogg"
set "FEA="
set /p "FEA=特征 raw 还是 mel? [直接回车用 mel]: "
if not defined FEA set "FEA=mel"
echo.
"%VPY%" -m mcfish.autofish --sound "%SND%" --feature %FEA% --dry-run --count 0 --max-wait 30
pause
goto menu

rem ---------------- [5] 回环探针 ----------------
:probe
call :need_venv
echo.
echo ----------------------------------------------------------------------------
echo  回环探针：录 10 秒，检查能否采到「默认播放设备」的声音
echo ----------------------------------------------------------------------------
echo   请让游戏或播放器持续出声（走路、放音乐都行）。
echo.
"%VPY%" -m mcfish.record --seconds 10 --probe --out calib\probe
echo.
echo   退出码: 0=有信号  2=静音  1=依赖问题
echo   如果静音，依次排查:
echo     1) 游戏/系统音量是否为 0
echo     2) 游戏是否用了独占模式或 ASIO 直通
echo     3) 声音是不是从「默认播放设备」出来的
echo     4) 放首歌，边放边看电平条动不动
echo.
pause
goto menu

rem ---------------- [6] 标定录音 ----------------
:calib_rec
call :need_venv
echo.
echo ----------------------------------------------------------------------------
echo  标定录音
echo ----------------------------------------------------------------------------
echo   操作:
echo     每次「咬钩 / 水花」 -^> 按一下【回车】
echo     每次「抛竿入水」   -^> 按一下【空格】
echo   两个都标，才能确认 splash 到底响在哪一步。
echo.
set "SECS="
set /p "SECS=录多少秒? [直接回车用 90]: "
if not defined SECS set "SECS=90"
set "OUTN="
set /p "OUTN=输出名? [直接回车用 calib\s1]: "
if not defined OUTN set "OUTN=calib\s1"
echo.
echo   开始录 %SECS% 秒 -^> %OUTN%
echo   至少标 5 次咬钩。
echo.
"%VPY%" -m mcfish.record --seconds %SECS% --out "%OUTN%"
echo.
echo   下一步跑 [7] 分析。
pause
goto menu

rem ---------------- [7] 分析标定 ----------------
:calib_run
call :need_venv
echo.
echo ----------------------------------------------------------------------------
echo  分析标定结果
echo ----------------------------------------------------------------------------
set "INP="
set /p "INP=录音名? [直接回车用 calib\s1]: "
if not defined INP set "INP=calib\s1"
if not exist "%ROOT%\%INP%.npy" (
  echo [!] 找不到 %INP%.npy，请先跑 [6] 录音。
  pause
  goto menu
)
echo.
"%VPY%" -m mcfish.calibrate --in "%INP%"
echo.
echo   看「逐事件分数」表来判断:
echo     raw 每次都在 0.9+     -^> 音高稳定，用 raw NCC
echo     raw 散落、mel 都 0.9+ -^> 音高有变化，需改用 log-mel
echo     抛竿音分数也高        -^> 这个音不能用来触发收杆
echo.
pause
goto menu

rem ---------------- [8] 实时分数 ----------------
:live
call :need_venv
echo.
echo ----------------------------------------------------------------------------
echo  实时分数（用标定好的模板）
echo ----------------------------------------------------------------------------
set "TPL="
set /p "TPL=模板路径? [直接回车用 calib\template.npy]: "
if not defined TPL set "TPL=calib\template.npy"
if not exist "%ROOT%\%TPL%" (
  echo [!] 找不到 %TPL%，请先跑 [6] + [7]。
  pause
  goto menu
)
set "SECS="
set /p "SECS=录多少秒? [直接回车用 30]: "
if not defined SECS set "SECS=30"
echo.
"%VPY%" -m mcfish.record --seconds %SECS% --template "%TPL%"
pause
goto menu

rem ---------------- [G] 图形界面 ----------------
:gui
call :need_venv
echo.
echo ----------------------------------------------------------------------------
echo  图形界面
echo ----------------------------------------------------------------------------
rem pythonw 不带黑窗；没有它就退回 python.exe（会多一个控制台窗口，正好看报错）
if exist "%VENV%\Scripts\pythonw.exe" (
  start "autofish" "%VENV%\Scripts\pythonw.exe" -m mcfish.gui
) else (
  start "autofish" "%VPY%" -m mcfish.gui
)
echo   已经打开了。界面里填好参数点「开始钓鱼」，随时「停止」。
echo.
pause
goto menu

rem ---------------- [T] 自测 ----------------
:selftest
call :need_venv
echo.
echo ----------------------------------------------------------------------------
echo  自测（不需要声卡/麦克风）
echo ----------------------------------------------------------------------------
echo.
echo [1/5] 检测器自测
"%VPY%" "%ROOT%\tools\test_detector.py"
echo.
echo [2/5] 标定流程端到端自测
"%VPY%" "%ROOT%\tools\test_calibrate_cli.py"
echo.
echo [3/5] 图形界面自测
"%VPY%" "%ROOT%\tools\test_gui.py"
echo.
echo [4/5] 后台点击自测（会开一个自己的小窗口，不碰游戏）
"%VPY%" "%ROOT%\tools\test_background_click.py"
echo.
echo [5/5] 快捷键自测（窗口内 F5/F6 + 全局 Ctrl+D / Ctrl+F）
"%VPY%" "%ROOT%\tools\test_hotkey.py"
echo.
echo   五个都应以「通过」和 exit 0 结束。
echo.
echo   想跑「真实回环 + 真实播放」的端到端测试（会出声）:
echo     "%VPY%" tools\test_autofish_loopback.py
echo.
pause
goto menu

rem ---------------- [A] 管理员图形界面 ----------------
:guiadmin
call :need_venv
echo.
echo ----------------------------------------------------------------------------
echo  以管理员身份打开图形界面
echo ----------------------------------------------------------------------------
echo   后台模式被 UIPI 权限拦住时用这个：管理员(High) 才给得了
echo  普通权限(Medium) 的游戏窗口发输入消息。会弹 UAC，点「是」。
echo.
powershell -NoProfile -Command "Start-Process -FilePath '%VPY%' -ArgumentList '-m','mcfish.gui' -WorkingDirectory '%ROOT%' -Verb RunAs"
if errorlevel 1 (
  echo [!] 提权没成功（或你点了「否」）。那就只能用前台模式了。
  echo     想确认权限级别:  "%VPY%" tools\diag_bg_click.py --window Minecraft
  pause
  goto menu
)
echo   已用管理员权限打开。界面里「点击方式」选后台，再点「后台试一下」。
echo.
pause
goto menu

rem ---------------- [B] 后台点击自测 ----------------
:bgtest
call :need_venv
echo.
echo ----------------------------------------------------------------------------
echo  后台点击自测
echo ----------------------------------------------------------------------------
echo   后台模式 = 不抢焦点，把点击直接投递给游戏窗口，钓鱼时你还能用电脑。
echo   能不能被游戏认，取决于游戏怎么读输入 —— 先在这里试一次：
echo   会往游戏窗口投递**一次**右键，然后你看游戏里有没有反应（比如抛出鱼竿）。
echo.
set "WIN="
set /p "WIN=窗口标题关键字? [直接回车用 Minecraft]: "
if not defined WIN set "WIN=Minecraft"
echo.
"%VPY%" -m mcfish.autofish --window "%WIN%" --background-test
echo.
echo   游戏里有反应 -> 以后加 --background（菜单 [3] 里选 2）就能后台钓
echo   没反应       -> 这游戏只读 Raw Input，只能用前台模式（钓鱼时别用电脑）
echo.
echo   如果这里报「投递被拒绝 / 错误码 5」，那是 UIPI 权限问题（不是游戏不认）：
echo     游戏是普通权限，而 Windows 不许低权限进程给高权限窗口发输入消息。
echo     办法: 用 [A] 以管理员身份开界面；或者右键 gui_admin.bat -> 以管理员身份运行。
echo   想弄清两边的权限级别:
echo     "%VPY%" tools\diag_bg_click.py --window "%WIN%"
echo.
pause
goto menu

rem ---------------- [9] 列窗口/设备 ----------------
:listall
call :need_venv
echo.
echo ----------------------------------------------------------------------------
echo  可见窗口（hwnd / pid / exe / 标题）
echo ----------------------------------------------------------------------------
"%VPY%" -m mcfish.autofish --list-windows
echo.
echo ----------------------------------------------------------------------------
echo  音频设备
echo ----------------------------------------------------------------------------
"%VPY%" -m mcfish.autofish --list-devices
echo.
echo   --window 填标题里的关键字即可；只想采某个设备时用:
echo     --audio "loopback:设备名"
echo.
pause
goto menu

rem ---------------- [N] 看报告 ----------------
:notes
if exist "%ROOT%\REPORT.md" (
  start "" notepad "%ROOT%\REPORT.md"
) else (
  echo [!] 找不到 REPORT.md
  pause
)
goto menu