# Windows environment preparation. No microphone, tests or server are started.
[CmdletBinding()]
param(
    [string]$Python = '',
    [switch]$SkipModel
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $projectRoot '.venv\Scripts\python.exe'

function Assert-PythonRuntime {
    param([string]$Executable, [string[]]$Prefix = @())
    & $Executable @Prefix -c 'import struct, sys; sys.exit(0 if sys.version_info[:2] == (3, 12) and struct.calcsize(chr(80)) == 8 else 1)'
    if ($LASTEXITCODE -ne 0) {
        throw 'Python 3.12 x64 is required. Use -Python with its executable path. For an incompatible .venv, stop the application and move that directory aside first.'
    }
}

if ($env:OS -ne 'Windows_NT') { throw 'This installer supports Windows only.' }

Push-Location -LiteralPath $projectRoot
try {
    foreach ($requiredFile in @('pyproject.toml', 'requirements-lock.txt', 'config.example.json',
            'voice_platform\admin_ui\index.html', 'voice_platform\admin_ui\admin.js',
            'voice_platform\admin_ui\admin.css')) {
        if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
            throw "Missing repository file: $requiredFile. Download the complete repository again."
        }
    }

    if (-not (Test-Path -LiteralPath $runtime -PathType Leaf)) {
        $prefix = @()
        if ($Python) {
            $basePython = (Get-Command -Name $Python -CommandType Application -ErrorAction Stop).Source
        } else {
            $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue
            if ($launcher) {
                $basePython = $launcher.Source
                $prefix = @('-3.12')
            } else {
                $interpreter = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue
                if (-not $interpreter -or $interpreter.Source -like '*\Microsoft\WindowsApps\*') {
                    throw 'Install Python 3.12 x64 first, or use -Python with its full executable path.'
                }
                $basePython = $interpreter.Source
            }
        }
        Assert-PythonRuntime -Executable $basePython -Prefix $prefix
        Write-Host '[1/5] Creating the project virtual environment...'
        & $basePython @prefix -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
    } else {
        Write-Host '[1/5] Reusing the project virtual environment.'
    }
    Assert-PythonRuntime -Executable $runtime

    Write-Host '[2/5] Installing the locked Python dependencies...'
    & $runtime -m pip install --requirement requirements-lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check the network and package source, then rerun.' }

    Write-Host '[3/5] Installing openspeech...'
    & $runtime -m pip install --no-deps --editable .
    if ($LASTEXITCODE -ne 0) { throw 'Project installation failed.' }
    & $runtime -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Installed dependencies are inconsistent. See the pip output above.' }

    Write-Host '[4/5] Preparing the local configuration...'
    if (-not (Test-Path -LiteralPath 'config.local.json')) {
        & $runtime -m voice_platform init-config
        if ($LASTEXITCODE -ne 0) { throw 'Configuration initialization failed.' }
    } else {
        Write-Host 'Keeping the existing config.local.json and connection credentials.'
    }

    if ($SkipModel) {
        Write-Host '[5/5] Skipping the device wake model.'
    } else {
        Write-Host '[5/5] Preparing the Chinese wake model...'
        & $runtime scripts/download_model.py
        if ($LASTEXITCODE -ne 0) { throw 'Wake model download failed. Check the network, then rerun.' }
    }
    Write-Host 'Environment ready. Existing configuration, models and conversation data have been kept.'
} finally {
    Pop-Location
}
