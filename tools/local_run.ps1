<#
  手元での試験実行。
  1) 初回だけ .venv を作って依存を入れる
  2) 机上検証（通信なし）を流す
  3) 収集を実行する（--limit 5 で軽く試す）

  使い方:
    powershell -ExecutionPolicy Bypass -File tools\local_run.ps1
    powershell -ExecutionPolicy Bypass -File tools\local_run.ps1 -Site 4gamer -Limit 5 -DryRun
#>
param(
  [string]$Site = "",
  [int]$Limit = 0,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path ".venv")) {
  Write-Host "== 仮想環境を作成 =="
  python -m venv .venv
}
$py = ".venv\Scripts\python.exe"

Write-Host "== 依存を確認 =="
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements.txt

Write-Host "== 机上検証（通信なし） =="
& $py -m collector.selftest
if ($LASTEXITCODE -ne 0) { throw "机上検証で NG が出ました。設定を見直してください。" }

$args = @()
if ($Site)  { $args += @("--site", $Site) }
if ($Limit -gt 0) { $args += @("--limit", "$Limit") }
if ($DryRun) { $args += "--dry-run" }

Write-Host "== 収集 =="
& $py -m collector.main @args | Tee-Object -FilePath "run_log_local.txt"
Write-Host "ログ: run_log_local.txt"
