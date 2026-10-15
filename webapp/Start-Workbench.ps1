param([int]$Port = 8097)

$ErrorActionPreference = 'Stop'
$researchAppRoot = $PSScriptRoot
$researchPython = Join-Path $researchAppRoot '.venv\Scripts\python.exe'
$researchUrl = "http://127.0.0.1:$Port"

if (-not (Test-Path -LiteralPath $researchPython -PathType Leaf)) {
    throw '请先在 webapp 目录运行 Setup-Workbench.ps1 创建项目环境。'
}

try {
    $researchStatus = Invoke-RestMethod -Uri "$researchUrl/api/status" -TimeoutSec 2
} catch {
    $researchStatus = $null
}

if ($researchStatus -and $researchStatus.mode -in @('demo', 'qwen') -and $researchStatus.storage -eq 'sqlite') {
    Write-Host "研证已经运行：$researchUrl" -ForegroundColor Cyan
    Start-Process $researchUrl
    exit 0
}

Set-Location -LiteralPath $researchAppRoot
Write-Host '研证 · 本地科研工作台' -ForegroundColor Cyan
Write-Host "打开浏览器访问：$researchUrl"
Write-Host '当前为本地演示。研究记录保存在 webapp/data/research.db。'
Write-Host '按 Ctrl+C 停止服务；下次启动后记录仍然保留。'
& $researchPython -B -m uvicorn app:app --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
