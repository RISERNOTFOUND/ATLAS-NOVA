@echo off
setlocal enabledelayedexpansion
title VERIFY_EXTRACTED_RELEASE - ATLAS-NOVA

echo ============================================================
echo   ATLAS-NOVA Extracted Release Integrity Verifier
echo   Created by RISERNOTFOUND.
echo ============================================================
echo.

set "REL_ROOT=%~dp0"
if "%REL_ROOT:~-1%"=="\" set "REL_ROOT=%REL_ROOT:~0,-1%"

if not exist "%REL_ROOT%\file-manifest.json" (
    echo FAILURE: file-manifest.json not found in "%REL_ROOT%".
    pause
    exit /b 1
)

set "PY_CMD=python"
if exist "%REL_ROOT%\.venv\Scripts\python.exe" set "PY_CMD=%REL_ROOT%\.venv\Scripts\python.exe"

"%PY_CMD%" -c "import hashlib, json, os, sys; root=r'%REL_ROOT%'; manifest=json.load(open(os.path.join(root,'file-manifest.json'),encoding='utf-8')); expected={e['path']:e for e in manifest['files']}; ignore={'file-manifest.json','file-manifest.json.sig','file-manifest.json.asc','VERIFY_EXTRACTED_RELEASE.bat'}; actual={}; [actual.__setitem__(os.path.relpath(os.path.join(d,f),root).replace('\\\\','/'), os.path.join(d,f)) for d,_,fs in os.walk(root) for f in fs if os.path.relpath(os.path.join(d,f),root).replace('\\\\','/') not in ignore and not d.startswith(os.path.join(root,'.venv'))]; missing=[p for p in expected if p not in actual]; extra=[p for p in actual if p not in expected]; bad=[]; ok=0; [(ok:=ok+1, print(f'  OK   {p}')) if hashlib.sha256(open(actual[p],'rb').read()).hexdigest()==expected[p]['sha256'] else (bad.append(p), print(f'  FAIL {p}')) for p in sorted(expected) if p in actual]; print(f'\nSummary: {ok}/{len(expected)} files verified, {len(missing)} missing, {len(bad)} modified, {len(extra)} unexpected.'); sys.exit(1 if (missing or bad or extra) else 0)"
if errorlevel 1 (
    echo.
    echo FAILURE: Extracted release verification failed!
    pause
    exit /b 1
)

echo.
echo SUCCESS: All %REL_ROOT% files match file-manifest.json.
pause
