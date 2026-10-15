@echo off
chcp 65001 >nul
title 研证 - 本地科研工作台
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0webapp\Start-Workbench.ps1"
if errorlevel 1 pause
