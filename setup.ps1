# Compatibility entry point. The implementation lives in scripts/.
[CmdletBinding()]
param(
    [string]$Python = '',
    [switch]$SkipModel
)

$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'scripts\setup.ps1') @PSBoundParameters
