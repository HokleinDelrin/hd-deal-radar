@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist data mkdir data
if not exist cache mkdir cache
python -m radar.cli serve
