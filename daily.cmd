@echo off
rem 每日采集入口 —— 给 Windows 计划任务调用。
rem 为什么不直接把 python 命令写进计划任务：本目录路径含 &（Softwares&Tools），
rem 在 schtasks 的 /TR 里会被当成命令分隔符；包一层 cmd 最稳，也方便统一日志与 cd。
chcp 65001 >nul
cd /d "%~dp0"
if not exist data mkdir data

rem --once --rounds 1：立刻跑一轮然后退出（自动判断今天是周一全量还是平日轻量）
python -m radar.cli daemon --once --rounds 1 >> "data\daily.log" 2>&1
exit /b %errorlevel%
