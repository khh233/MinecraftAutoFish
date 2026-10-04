@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "PYW=%ROOT%\.venv\Scripts\pythonw.exe"
set "VPY=%ROOT%\.venv\Scripts\python.exe"

title autofish (管理员)

if not exist "%PYW%" if not exist "%VPY%" (
  echo.
  echo [!] 还没建虚拟环境。先双击 run.bat 选 [1] 建好环境。
  echo.
  pause
  exit /b 1
)
if not exist "%PYW%" set "PYW=%VPY%"

echo ============================================================================
echo  autofish 图形界面（以管理员身份运行）
echo ============================================================================
echo.
echo  为什么要提权：后台模式是把鼠标消息直接投递给游戏窗口，
echo  而 Windows 的 UIPI 规定「低权限进程不能给高权限窗口发输入消息」。
echo  游戏是普通权限(Medium)，所以要投递成功，我们得是管理员(High)。
echo  会弹一个 UAC 确认框，点「是」即可。
echo.
echo  只用前台模式的话，不需要管理员，直接双击 gui.bat 就行。
echo.

powershell -NoProfile -Command "Start-Process -FilePath '%PYW%' -ArgumentList '-m','mcfish.gui' -WorkingDirectory '%ROOT%' -Verb RunAs"
if errorlevel 1 (
  echo.
  echo [!] 提权没成功（或者你点了「否」）。
  echo     那就只能用前台模式了：双击 gui.bat，「点击方式」选前台。
  echo     想弄清两边的权限级别，可以跑：
  echo       "%VPY%" tools\diag_bg_click.py --window Minecraft
  echo.
  pause
  exit /b 1
)
exit /b 0