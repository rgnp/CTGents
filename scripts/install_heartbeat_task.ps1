# 安装 CTGents 心跳为 Windows 计划任务（每 30 分钟一跳，输出追加到 logs/heartbeat.log）。
# 用法: powershell -ExecutionPolicy Bypass -File scripts/install_heartbeat_task.ps1 [-IntervalMinutes 30]
# 卸载: schtasks /Delete /TN CTGentsHeartbeat /F
param([int]$IntervalMinutes = 30)

$root = Split-Path -Parent $PSScriptRoot
$log = Join-Path $root "logs\heartbeat.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null

# 找 python（优先项目 venv）
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

$action = "cmd /c cd /d `"$root`" && `"$python`" -m src.heartbeat >> `"$log`" 2>&1"
schtasks /Create /F /TN CTGentsHeartbeat /SC MINUTE /MO $IntervalMinutes /TR $action
if ($LASTEXITCODE -eq 0) {
    Write-Host "已安装计划任务 CTGentsHeartbeat（每 $IntervalMinutes 分钟）。日志: $log"
    Write-Host "卸载: schtasks /Delete /TN CTGentsHeartbeat /F"
}
