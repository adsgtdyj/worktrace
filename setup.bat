@echo off
chcp 65001 >nul
echo ==========================================
echo  时间追踪工具 - 安装脚本
echo ==========================================
echo.

REM 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到Python，请先安装Python 3.x
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/3] 检测到Python版本:
python --version
echo.

REM 安装依赖
echo [2/3] 正在安装依赖...
pip install -r requirements.txt
if errorlevel 1 (
    echo [警告] 部分依赖安装失败，程序可能无法正常运行
    echo 请手动运行: pip install pywin32
) else (
    echo [成功] 依赖安装完成
)
echo.

REM 创建数据目录
echo [3/3] 初始化数据目录...
if not exist "data" mkdir data
echo [成功] 数据目录已创建
echo.

echo ==========================================
echo  安装完成！
echo ==========================================
echo.
echo 使用方法:
echo   1. 双击 time_tracker_v2.py 启动追踪
echo   2. 按 Ctrl+C 停止并生成报告
echo   3. 双击 dashboard.html 查看仪表盘
echo.
pause
