@echo off
rem 手元のWindowsで収集を試すための入口。GitHubに上げる前の動作確認に使う。
rem 使い方: このBATにサイトID（例 4gamer）をドラッグ＆ドロップ、または引数なしで全サイト。
setlocal
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\local_run.ps1" -Site "%~1"
pause
