param(
    [string]$Python = "",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BuildRoot = Join-Path $ProjectRoot ".portable-build"
$VenvRoot = Join-Path $BuildRoot "venv"
$ReleaseRoot = Join-Path $ProjectRoot "release"
$Version = "1.10.0"
# Keep earlier release builds intact; these directories contain generated files only.
$DistRoot = Join-Path $BuildRoot "dist-v$Version"

if (-not $Python) {
    $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($PyLauncher) {
        $Python = "py"
    } else {
        $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $PythonCommand) {
            throw "未找到 Python。构建机需要 64 位 Python 3.12；最终便携包不要求用户安装 Python。"
        }
        $Python = $PythonCommand.Source
    }
}

New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
if (-not (Test-Path (Join-Path $VenvRoot "Scripts\python.exe"))) {
    if ($Python -eq "py") {
        & py -3.12 -m venv $VenvRoot
    } else {
        & $Python -m venv $VenvRoot
    }
    if ($LASTEXITCODE -ne 0) { throw "创建构建环境失败。" }
}
$BuildPython = Join-Path $VenvRoot "Scripts\python.exe"

if (-not $SkipInstall) {
    & $BuildPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "更新 pip 失败。" }
    & $BuildPython -m pip install -r (Join-Path $ProjectRoot "requirements-ai.txt") "pyinstaller>=6.16,<7"
    if ($LASTEXITCODE -ne 0) { throw "安装构建依赖失败。" }
}

& $BuildPython -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $DistRoot `
    --workpath (Join-Path $BuildRoot "work-v$Version") `
    (Join-Path $ProjectRoot "StudentPhotoFlow.spec")
if ($LASTEXITCODE -ne 0) { throw "便携版构建失败，未打包旧文件。" }

$PortableRoot = Join-Path $DistRoot "StudentPhotoFlow"
Copy-Item -LiteralPath (Join-Path $ProjectRoot "启动工具.bat") -Destination $PortableRoot -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "README.md") -Destination $PortableRoot -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "审核结果独立导出说明.md") -Destination $PortableRoot -Force

New-Item -ItemType Directory -Force -Path $ReleaseRoot | Out-Null
$ZipPath = Join-Path $ReleaseRoot "StudentPhotoFlow_Windows_x64_v$Version.zip"
if (Test-Path $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
Compress-Archive -Path (Join-Path $PortableRoot "*") -DestinationPath $ZipPath -CompressionLevel Optimal
Write-Host "便携版构建完成：$ZipPath"
