<#
.SYNOPSIS
    Run tests, build the standalone Windows executable, and refresh the macOS source archive.
.PARAMETER SourceOnly
    Only run tests and rebuild AIProxyLauncher-macOS-Source.zip without invoking PyInstaller.
.PARAMETER SkipSmoke
    Skip launching the built executable with --ui-smoke.
#>
param(
    [switch]$SourceOnly,
    [switch]$SkipSmoke,
    [string]$Python = "python"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "==> 1/4 Checking Python & running unit tests..." -ForegroundColor Cyan
& $Python -c "import sys, tkinter; assert sys.version_info >= (3, 11), sys.version"
& $Python -B -m unittest discover -s . -v

$OutDir = Join-Path $Root "新版"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

Write-Host "==> 2/4 Refreshing macOS source archive..." -ForegroundColor Cyan
$Stage = Join-Path ([IO.Path]::GetTempPath()) ("ai-proxy-launcher-build-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $Stage | Out-Null

try {
    $MacBundleDir = Join-Path $Stage "AIProxyLauncher-macOS"
    New-Item -ItemType Directory -Force -Path $MacBundleDir | Out-Null
    $SourceFiles = @(
        "launcher.py",
        "win32net.py",
        "relay.py",
        "proxies.py",
        "envcheck.py",
        "tray.py",
        "repair.py",
        "widgets.py",
        "dialogs.py",
        "interface.py",
        "macos.py",
        "test_launcher.py",
        "test_macos.py",
        "macos-start.command",
        "build-macos.sh",
        "macOS使用说明.md",
        "使用说明.md"
    )
    foreach ($file in $SourceFiles) {
        $src = Join-Path $Root $file
        if (Test-Path -LiteralPath $src) {
            Copy-Item -LiteralPath $src -Destination $MacBundleDir -Force
        }
    }
    $IconsDir = Join-Path $Root "icons"
    if (Test-Path -LiteralPath $IconsDir) {
        Copy-Item -LiteralPath $IconsDir -Destination (Join-Path $MacBundleDir "icons") -Recurse -Force
    }
    $MacZip = Join-Path $OutDir "AIProxyLauncher-macOS-Source.zip"
    if (Test-Path -LiteralPath $MacZip) {
        Remove-Item -LiteralPath $MacZip -Force
    }
    Compress-Archive -Path $MacBundleDir -DestinationPath $MacZip -CompressionLevel Optimal
    Write-Host "    Updated: $MacZip" -ForegroundColor Green

    if ($SourceOnly) {
        Write-Host "==> SourceOnly specified; skipping Windows .exe build." -ForegroundColor Yellow
        exit 0
    }

    Write-Host "==> 3/4 Building Windows executable with PyInstaller..." -ForegroundColor Cyan
    $hasPyInstaller = $true
    $prevErrPref = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $Python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('PyInstaller') else 1)"
    if ($LASTEXITCODE -ne 0) {
        $hasPyInstaller = $false
    }
    $ErrorActionPreference = $prevErrPref
    if (-not $hasPyInstaller) {
        Write-Host "    Installing PyInstaller into a temporary build venv..." -ForegroundColor Yellow
        $Venv = Join-Path $Stage "venv"
        & $Python -m venv $Venv
        $Builder = Join-Path $Venv "Scripts\python.exe"
        & $Builder -m pip install --upgrade pip
        & $Builder -m pip install "pyinstaller>=6.10"
    } else {
        $Builder = $Python
    }

    $WorkDir = Join-Path $Stage "work"
    $DistDir = Join-Path $Stage "dist"
    $IconPath = Join-Path $Root "launcher.ico"
    $PyArgs = @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name", "AIProxyLauncher",
        "--distpath", $DistDir,
        "--workpath", $WorkDir,
        "--specpath", $Stage
    )
    if (Test-Path -LiteralPath $IconPath) {
        $PyArgs += @("--icon", $IconPath, "--add-data", "$IconPath;.")
    }
    if (Test-Path -LiteralPath $IconsDir) {
        $PyArgs += @("--add-data", "$IconsDir;icons")
    }
    $PyArgs += (Join-Path $Root "launcher.py")
    & $Builder @PyArgs

    $BuiltExe = Join-Path $DistDir "AIProxyLauncher.exe"
    if (-not (Test-Path -LiteralPath $BuiltExe)) {
        throw "Build did not produce $BuiltExe"
    }

    if (-not $SkipSmoke) {
        Write-Host "==> 4/4 Running UI smoke test on built executable..." -ForegroundColor Cyan
        $SmokeFile = Join-Path $Stage "ui-smoke.txt"
        $proc = Start-Process -FilePath $BuiltExe -ArgumentList @("--ui-smoke", "--report", $SmokeFile) -Wait -PassThru
        if ($proc.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $SmokeFile)) {
            throw "UI smoke check failed (exit code $($proc.ExitCode))"
        }
        Write-Host ("    " + (Get-Content -LiteralPath $SmokeFile -Raw).Trim()) -ForegroundColor Green
    }

    $TargetExe = Join-Path $OutDir "AIProxyLauncher.exe"
    Copy-Item -LiteralPath $BuiltExe -Destination $TargetExe -Force
    Write-Host "==> Build complete: $TargetExe" -ForegroundColor Green
}
finally {
    if (Test-Path -LiteralPath $Stage) {
        Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue
    }
}

