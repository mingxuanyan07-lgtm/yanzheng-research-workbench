$ErrorActionPreference = 'Stop'
$researchAppRoot = $PSScriptRoot
$researchPython = Join-Path $researchAppRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $researchPython -PathType Leaf)) {
    $researchBasePython = Get-Command python -ErrorAction Stop
    & $researchBasePython.Source -m venv (Join-Path $researchAppRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw '创建 Python 虚拟环境失败，请使用 Python 3.12 或以上版本（NumPy 依赖要求）。' }
}
& $researchPython -m pip install -r (Join-Path $researchAppRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw '安装依赖失败。' }
Write-Host '环境准备完成。运行 Start-Workbench.ps1 启动网站。' -ForegroundColor Green
