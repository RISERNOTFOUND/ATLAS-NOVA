@echo off
setlocal enabledelayedexpansion
title ATLAS-NOVA Optional Release Packager & Signer

echo ============================================================
echo   ATLAS-NOVA Release Packager ^& Optional Digital Signer
echo   Created by RISERNOTFOUND.
echo ============================================================
echo.
echo This script packages ATLAS-NOVA into a clean release ZIP,
echo generates a SHA-256 checksum, and optionally creates a
echo detached digital signature using GPG or Git SSH signing.
echo.
echo Your private key and passphrase are NEVER stored or written
echo to any file by this script.
echo.

set "ROOT_DIR=%~dp0.."
for %%I in ("%ROOT_DIR%") do set "ROOT_DIR=%%~fI"
set "OUT_DIR=%ROOT_DIR%\release"
set "ZIP_NAME=ATLAS-NOVA-PUBLIC-RELEASE.zip"
set "ZIP_PATH=%OUT_DIR%\%ZIP_NAME%"
set "SHA_PATH=%ZIP_PATH%.sha256"

if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"

echo [1/4] Building clean release ZIP...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$src = '%ROOT_DIR%'; $dst = '%ZIP_PATH%'; " ^
  "if (Test-Path $dst) { Remove-Item $dst -Force }; " ^
  "$stage = Join-Path $env:TEMP ('atlas-nova-stage-' + [guid]::NewGuid().ToString('N')); " ^
  "New-Item -ItemType Directory -Path $stage | Out-Null; " ^
  "$destRoot = Join-Path $stage 'ATLAS-NOVA'; " ^
  "robocopy $src $destRoot /E /XD .venv __pycache__ .pytest_cache .git release backups logs /XF .env token.txt token2.txt *.db *.log *.zip *.sha256 | Out-Null; " ^
  "Compress-Archive -Path $destRoot -DestinationPath $dst -Force; " ^
  "Remove-Item $stage -Recurse -Force"

if not exist "%ZIP_PATH%" (
    echo ERROR: Failed to create %ZIP_PATH%
    pause
    exit /b 1
)
echo Created: %ZIP_PATH%
echo.

echo [2/4] Generating and verifying SHA-256 checksum...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$h = (Get-FileHash -Path '%ZIP_PATH%' -Algorithm SHA256).Hash.ToLower(); " ^
  "Set-Content -Path '%SHA_PATH%' -Value ($h + '  %ZIP_NAME%') -Encoding ASCII; " ^
  "Write-Host ('SHA-256: ' + $h)"
echo Created: %SHA_PATH%
echo.

echo [3/4] Optional Digital Signing
echo   1) GPG detached signature (.asc)  [requires gpg]
echo   2) SSH detached signature (.sig)  [requires ssh-keygen]
echo   3) Skip signing (checksum only)
echo.
set /p "SIGN_CHOICE=Select an option (1, 2, or 3): "

if "%SIGN_CHOICE%"=="1" goto :sign_gpg
if "%SIGN_CHOICE%"=="2" goto :sign_ssh
echo Skipping digital signature.
goto :done

:sign_gpg
where gpg >nul 2>&1
if errorlevel 1 (
    echo ERROR: 'gpg' was not found in PATH. Install Gpg4win or Git for Windows.
    pause
    exit /b 1
)
set /p "GPG_KEY=Enter your GPG Key ID or email (leave empty for default key): "
if "%GPG_KEY%"=="" (
    gpg --armor --detach-sign --output "%ZIP_PATH%.asc" "%ZIP_PATH%"
) else (
    gpg --armor --detach-sign --local-user "%GPG_KEY%" --output "%ZIP_PATH%.asc" "%ZIP_PATH%"
)
if errorlevel 1 (
    echo ERROR: GPG signing failed.
    pause
    exit /b 1
)
echo [4/4] Verifying GPG signature...
gpg --verify "%ZIP_PATH%.asc" "%ZIP_PATH%"
if errorlevel 1 (
    echo ERROR: GPG signature verification failed!
    pause
    exit /b 1
)
echo GPG signature verified: %ZIP_PATH%.asc
goto :done

:sign_ssh
where ssh-keygen >nul 2>&1
if errorlevel 1 (
    echo ERROR: 'ssh-keygen' was not found in PATH.
    pause
    exit /b 1
)
set /p "SSH_KEY_PATH=Enter full path to your SSH private key (e.g. C:\Users\You\.ssh\id_ed25519): "
if not exist "%SSH_KEY_PATH%" (
    echo ERROR: Key file not found: %SSH_KEY_PATH%
    pause
    exit /b 1
)
ssh-keygen -Y sign -f "%SSH_KEY_PATH%" -n file "%ZIP_PATH%"
if errorlevel 1 (
    echo ERROR: SSH signing failed.
    pause
    exit /b 1
)
echo SSH signature created: %ZIP_PATH%.sig
echo Note: Verify with: ssh-keygen -Y verify -f allowed_signers -I your_id -n file -s "%ZIP_PATH%.sig" ^< "%ZIP_PATH%"

:done
echo.
echo ============================================================
echo   Done! Output files in: %OUT_DIR%
echo ============================================================
pause
