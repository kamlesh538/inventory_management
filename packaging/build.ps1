# Builds dist\JBHonda-Setup-<version>.exe on a Windows 10/11 PC.
#
# Needs: uv (https://docs.astral.sh/uv/) and Inno Setup 6 (https://jrsoftware.org/isdl.php).
# Run from the project folder in PowerShell:   powershell -ExecutionPolicy Bypass -File packaging\build.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$version = (Select-String -Path src\jbhonda\__init__.py -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
Write-Host "Building version $version"

uv sync --group build
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }

$env:QT_QPA_PLATFORM = "offscreen"
uv run pytest
if ($LASTEXITCODE -ne 0) { throw "Tests failed; not building." }
Remove-Item Env:QT_QPA_PLATFORM

uv run pyinstaller packaging\jbhonda.spec --noconfirm --clean --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# Start the built app once against a throwaway data folder to prove it runs.
$env:JBH_DATA_DIR = Join-Path $env:TEMP "jbhonda-smoke"
Remove-Item -Recurse -Force $env:JBH_DATA_DIR -ErrorAction SilentlyContinue
$p = Start-Process dist\JBHonda\JBHonda.exe -ArgumentList "--smoke-test" -PassThru -Wait
Remove-Item Env:JBH_DATA_DIR
if ($p.ExitCode -ne 0) { throw "The built app did not start correctly (exit code $($p.ExitCode))." }

$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found. Install it from https://jrsoftware.org/isdl.php" }
& $iscc "/DAppVersion=$version" packaging\installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

Write-Host ""
Write-Host "Installer ready: dist\JBHonda-Setup-$version.exe" -ForegroundColor Green
