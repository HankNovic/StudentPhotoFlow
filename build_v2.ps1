param([switch]$SkipInstall,[string]$Output)
$ErrorActionPreference='Stop'
$ProjectRoot=$PSScriptRoot
$BuildPython=Join-Path $ProjectRoot '.portable-build\venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $BuildPython)) { throw 'Initialize with: py -3.12 -m venv .portable-build\venv, then run build_v2.ps1 again.' }
if (-not $Output) { $Output=Join-Path $ProjectRoot 'release\candidates' }
if (-not $SkipInstall) {
    & $BuildPython -m pip install -r (Join-Path $ProjectRoot 'requirements-v2.txt') 'pyinstaller>=6.16,<7'
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    Push-Location (Join-Path $ProjectRoot 'v2\web-vue')
    try {
        npm ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    } finally { Pop-Location }
}
& $BuildPython (Join-Path $ProjectRoot 'build_candidate.py') --output $Output
if ($LASTEXITCODE -ne 0) { throw 'Candidate build failed. See build.log in the output directory.' }
