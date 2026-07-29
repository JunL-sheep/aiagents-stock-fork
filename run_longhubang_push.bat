@echo off
REM 智瞰龙虎 · 盘后全量推送（定时任务入口）
REM 由 Windows 任务计划程序每交易日 19:00 调用

cd /d D:\projects\projects\aiagents-stock-main\aiagents-stock-main

REM 激活虚拟环境（如已激活则跳过）
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)

python longhubang_daily_push.py >> logs\longhubang_daily_push.log 2>&1
