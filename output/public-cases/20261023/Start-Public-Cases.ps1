$ErrorActionPreference = 'Stop'
$caseOutput = $PSScriptRoot
$caseRoot = Split-Path (Split-Path (Split-Path $caseOutput -Parent) -Parent) -Parent
$casePython = Join-Path $caseRoot 'webapp\.venv\Scripts\python.exe'
$caseServer = Join-Path $caseRoot 'scripts\public_case_server.py'
if (!(Test-Path -LiteralPath $casePython) -or !(Test-Path -LiteralPath $caseServer)) {
    throw 'Run this launcher from the original project output/public-cases/20261023 folder.'
}
$caseIdentity = Join-Path $caseOutput 'SERVER.json'
if (Test-Path -LiteralPath $caseIdentity) {
    $caseExisting = Get-Content -LiteralPath $caseIdentity -Raw | ConvertFrom-Json
    try {
        $caseUri = [Uri]$caseExisting.base
        if ($caseUri.Host -eq '127.0.0.1' -and (Get-Process -Id $caseExisting.pid -ErrorAction SilentlyContinue)) {
            $caseProjects = Invoke-RestMethod -Uri ($caseExisting.base + '/api/projects') -TimeoutSec 3
            if ($caseProjects.projects.id -contains '864bfd6b74de468882d7a155c329eaee') {
                Start-Process ($caseExisting.base + '/showcase')
                exit
            }
        }
    } catch { }
}
$caseProcess = Start-Process -FilePath $casePython -ArgumentList @('-X','utf8','-B',('"' + $caseServer + '"')) -WorkingDirectory $caseRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $caseOutput 'server-latest.log') -RedirectStandardError (Join-Path $caseOutput 'server-latest-error.log')
for ($caseAttempt = 0; $caseAttempt -lt 40; $caseAttempt++) {
    Start-Sleep -Milliseconds 250
    if ($caseProcess.HasExited) { throw 'Case server could not start; see server-latest-error.log.' }
    if (Test-Path -LiteralPath $caseIdentity) {
        $caseStarted = Get-Content -LiteralPath $caseIdentity -Raw | ConvertFrom-Json
        if ($caseStarted.pid -eq $caseProcess.Id) {
            Start-Process ($caseStarted.base + '/showcase')
            exit
        }
    }
}
throw 'Case server did not become ready within 10 seconds.'
