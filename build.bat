@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"
set "VPY=%~dp0.venv\Scripts\python.exe"
if not exist "%VPY%" (
    echo [X] 没找到 .venv\Scripts\python.exe
    echo     先双击 run.bat 选 [1] 把环境建起来。
    pause
    exit /b 1
)
echo ============================================================
echo  Nuitka + Zig 打包 autofish
echo  默认文件夹模式，产物: build\autofish.dist\
echo  只打包 mcfish 程序代码（测试脚本不进包），图标取自 icon.png
echo  首次编译十几分钟，之后有缓存会快很多，请耐心等待
echo ============================================================
echo.
"%VPY%" "%~dp0packaging\build.py" %*
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
    echo [X] 打包失败，退出码 %RC%
    pause
    exit /b %RC%
)
echo [OK] 产物: %~dp0build\autofish.dist\  （整个文件夹拷走，别只拷 exe）
pause
