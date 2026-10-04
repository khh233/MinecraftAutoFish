@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "VPYW=%ROOT%\.venv\Scripts\pythonw.exe"
set "VPY=%ROOT%\.venv\Scripts\python.exe"

title autofish

if not exist "%VPYW%" if not exist "%VPY%" (
  echo.
  echo [!] 还没建虚拟环境（找不到 .venv\Scripts\python.exe）。
  echo     双击 run.bat，选 [1] 建环境 + 装依赖，然后再回来双击这个。
  echo.
  pause
  exit /b 1
)

rem pythonw.exe 不带黑窗；万一它不在就用 python.exe（会留一个控制台窗口，
rem 出问题的时候那个窗口正好能看到报错）。
if exist "%VPYW%" (
  start "autofish" "%VPYW%" -m mcfish.gui
) else (
  start "autofish" "%VPY%" -m mcfish.gui
)
exit /b 0