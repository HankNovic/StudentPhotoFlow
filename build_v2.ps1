param([switch]$SkipInstall)
$ErrorActionPreference='Stop'
$ProjectRoot=$PSScriptRoot
$BuildPython=Join-Path $ProjectRoot '.portable-build\venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $BuildPython)) { throw 'Run build_portable.ps1 to initialize the build environment.' }
if (-not $SkipInstall) {
    & $BuildPython -m pip install -r (Join-Path $ProjectRoot 'requirements-v2.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
& $BuildPython -m PyInstaller --noconfirm --distpath (Join-Path $ProjectRoot '.portable-build\dist-v2') --workpath (Join-Path $ProjectRoot '.portable-build\work-v2') (Join-Path $ProjectRoot 'StudentPhotoFlowV2.spec')
if ($LASTEXITCODE -ne 0) { throw 'V2 build failed.' }
$PortableRoot=Join-Path $ProjectRoot '.portable-build\dist-v2\StudentPhotoFlowV2'
Get-ChildItem -LiteralPath $ProjectRoot -Filter 'V2*.md' | Copy-Item -Destination $PortableRoot
$ZipPath=Join-Path $ProjectRoot ('release\StudentPhotoFlow_Windows_x64_v2.0.1_'+(Get-Date -Format 'yyyyMMdd_HHmmss')+'.zip')
Compress-Archive -LiteralPath $PortableRoot -DestinationPath $ZipPath -CompressionLevel Optimal
Write-Output $ZipPath
