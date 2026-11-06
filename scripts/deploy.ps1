# One-command Windows deployment. No tests or cloud API calls are performed.
[CmdletBinding()]
param(
    [string]$Python = '',
    [switch]$PrepareOnly,
    [switch]$ServerOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$projectRoot = Split-Path -Parent $PSScriptRoot

Push-Location -LiteralPath $projectRoot
try {
    # Refuse to update a live environment. Never stop existing processes here.
    $configPath = Join-Path $projectRoot 'config.local.json'
    if (-not (Test-Path -LiteralPath $configPath)) {
        $configPath = Join-Path $projectRoot 'config.example.json'
    }
    $settings = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $port = [int]$settings.server.port
    if ($port -lt 1 -or $port -gt 65535) { throw 'Invalid server port in the configuration.' }
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        throw "Port $port is in use. Stop the server before deploying. For daily use, skip deployment and run start-device.cmd."
    }
    $projectPattern = [regex]::Escape($projectRoot)
    $deviceProcesses = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
        Where-Object { $_.CommandLine -and ($_.CommandLine -match $projectPattern -or
            ($_.ExecutablePath -and $_.ExecutablePath -match $projectPattern)) -and
            $_.CommandLine -match '\bvoice_platform\s+(tray|listen|wake-test)\b' })
    if ($deviceProcesses.Count -gt 0) {
        throw 'Exit the existing voice device before deploying. Use the tray menu or Ctrl+C in its terminal.'
    }

    if (-not $PrepareOnly) {
        $bindHost = [string]$settings.server.host
        if ($bindHost -in @('::', '::1')) {
            $adminUrl = "http://[::1]:$port/admin"
        } elseif ($bindHost -in @('0.0.0.0', '127.0.0.1', 'localhost')) {
            $adminUrl = "http://127.0.0.1:$port/admin"
        } else {
            throw 'The admin page requires loopback access. Set server.host to 127.0.0.1 or 0.0.0.0; see the deployment guide.'
        }
    }

    & (Join-Path $PSScriptRoot 'setup.ps1') -Python $Python -SkipModel:$ServerOnly

    if ($PrepareOnly) {
        Write-Host 'Deployment preparation complete.'
        Write-Host 'Next: run start-server.cmd and configure the model in the admin page.'
        if (-not $ServerOnly) { Write-Host 'Then run start-device.cmd to enable the microphone.' }
        return
    }
    Write-Host 'Deployment preparation complete. Starting the server in this window...'
    Write-Host "After the server reports startup complete, open: $adminUrl"
    if (-not $ServerOnly) { Write-Host 'Configure the model, then run start-device.cmd to enable the microphone.' }
    Write-Host 'Keep this window open. Press Ctrl+C to stop the server.'
    & (Join-Path $projectRoot '.venv\Scripts\python.exe') -m voice_platform serve
    if ($LASTEXITCODE -ne 0) { throw 'The server exited with an error. See the output above.' }
} catch {
    Write-Host "Deployment failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    Pop-Location
}
