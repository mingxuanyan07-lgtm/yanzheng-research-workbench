param([ValidateRange(1, 65535)][int]$Port = 8097)

$ErrorActionPreference = 'Stop'
$researchPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $researchPython -PathType Leaf)) {
    throw '尚未创建项目环境，请先运行 webapp/Setup-Workbench.ps1。'
}
& $researchPython -X utf8 -B (Join-Path $PSScriptRoot '..\scripts\check_workbench.py') --port $Port
exit $LASTEXITCODE
