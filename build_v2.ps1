param([switch]$SkipInstall,[string]$Output)
$ErrorActionPreference='Stop'
$ProjectRoot=$PSScriptRoot
$BuildPython=Join-Path $ProjectRoot '.portable-build\venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $BuildPython)) { throw 'Run build_portable.ps1 to initialize the build environment.' }
if (-not $Output) { $Output=Join-Path $ProjectRoot 'release\candidates' }
if (-not $SkipInstall) {
    & $BuildPython -m pip install -r (Join-Path $ProjectRoot 'requirements-v2.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    Push-Location (Join-Path $ProjectRoot 'v2\web-vue')
    try {
        npm ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    } finally { Pop-Location }
}
& $BuildPython (Join-Path $ProjectRoot 'build_candidate.py') --output $Output
if ($LASTEXITCODE -ne 0) { throw 'Candidate build failed. See build.log in the output directory.' }
