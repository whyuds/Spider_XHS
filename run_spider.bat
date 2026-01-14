@echo off
setlocal

chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

:: 设置日志目录和文件
set "LOG_DIR=%USERPROFILE%\Spider_XHS_Logs"
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "LOG_TS=%%i"
set "LOG_FILE=%LOG_DIR%\spider_%LOG_TS%.log"

:: 创建日志目录（如果不存在）
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:: 切换到项目目录并运行
cd /d "d:\git\github\Spider_XHS-1"
call .venv\Scripts\activate.bat

echo ========================================== >> "%LOG_FILE%" 2>&1
echo 任务开始时间: %date% %time% >> "%LOG_FILE%" 2>&1
echo ========================================== >> "%LOG_FILE%" 2>&1

python -X utf8 main.py >> "%LOG_FILE%" 2>&1

echo ========================================== >> "%LOG_FILE%" 2>&1
echo 任务结束时间: %date% %time% >> "%LOG_FILE%" 2>&1
echo ========================================== >> "%LOG_FILE%" 2>&1
