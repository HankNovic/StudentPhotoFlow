param([string]$Output)
$ErrorActionPreference='Stop'
& (Join-Path $PSScriptRoot 'build_v2.ps1') -SkipInstall -Output $Output
