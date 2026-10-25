param([ValidateRange(1, 65535)][int]$Port = 8097)

$ErrorActionPreference = 'Stop'
$researchAppRoot = $PSScriptRoot
$researchPython = Join-Path $researchAppRoot '.venv\Scripts\python.exe'
$researchUrl = "http://127.0.0.1:$Port"

if (-not (Test-Path -LiteralPath $researchPython -PathType Leaf)) {
    throw '请先在 webapp 目录运行 Setup-Workbench.ps1 创建项目环境。'
}

$researchExpectedBuild = & $researchPython -X utf8 -B (Join-Path $researchAppRoot '..\scripts\check_workbench.py') --build-id
if ($LASTEXITCODE -ne 0) {
    throw '运行文件检查失败。请运行 Check-Workbench.ps1 查看环境，保留完整项目后重试。'
}
$researchExpectedBuild = ([string]$researchExpectedBuild).Trim()

try {
    $researchStatus = Invoke-RestMethod -Uri "$researchUrl/api/status" -TimeoutSec 2
} catch {
    $researchStatus = $null
}

if ($researchStatus -and $researchStatus.mode -in @('demo', 'qwen') -and $researchStatus.storage -eq 'sqlite') {
    if (-not $researchStatus.build -or $researchStatus.build.id -ne $researchExpectedBuild) {
        Write-Host '检测到研证仍在运行旧版本，尚未加载本次更新。' -ForegroundColor Yellow
        Write-Host '请在原启动窗口按 Ctrl+C 停止，再重新运行本启动器。研究记录会保留。'
        exit 1
    }
    Write-Host "研证已经运行：$researchUrl" -ForegroundColor Cyan
    Start-Process $researchUrl
    exit 0
}

$researchPortProbe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try {
    $researchPortProbe.Start()
} catch {
    throw "本地端口 $Port 已被其他程序占用，未启动新服务。请先检查并关闭占用程序。"
} finally {
    $researchPortProbe.Stop()
}

Set-Location -LiteralPath $researchAppRoot
Write-Host '研证 · 本地科研工作台' -ForegroundColor Cyan
Write-Host "打开浏览器访问：$researchUrl"
Write-Host '本地工作台已准备启动，模型状态以页面显示为准。'
if ($env:YANZHENG_DB_PATH) {
    Write-Host '本次使用 YANZHENG_DB_PATH 指定的研究库。'
} else {
    Write-Host '研究记录保存在 webapp/data/research.db。'
}
Write-Host '按 Ctrl+C 停止服务；下次启动后记录仍然保留。'
& $researchPython -B -m uvicorn app:app --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
