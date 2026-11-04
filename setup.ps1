param([string]$Python = '')
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    if ($Python) {
        & $Python -m venv .venv
    } else {
        & py -3.12 -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12, or pass -Python with its executable path.' }
}
$runtime = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath 'requirements-lock.txt') {
    & $runtime -m pip install -r requirements-lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
& $runtime -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw 'Voice platform installation failed.' }
if (-not (Test-Path -LiteralPath 'config.local.json')) {
    & $runtime -m voice_platform init-config
    if ($LASTEXITCODE -ne 0) { throw 'Configuration initialization failed.' }
}
& $runtime scripts/download_model.py
if ($LASTEXITCODE -ne 0) { throw 'Wake model download failed.' }
Write-Host 'Setup complete. Configure cloud credentials in config.local.json, then use start-server.cmd and start-device.cmd.'
