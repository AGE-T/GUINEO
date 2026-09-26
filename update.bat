@echo off
REM ============================================================
REM GUINEO - Safe Source-Only Update Script
REM ============================================================
REM
REM This script updates ONLY the Python source code from the
REM SpeechStudio_clean.zip, while PRESERVING all your personal
REM data:
REM   - settings\settings.json     (theme, project, volume)
REM   - settings\history\          (all your history entries)
REM   - voices\                    (your voice profiles)
REM   - outputs\                   (your generated audio)
REM   - presets\                   (your saved presets)
REM   - models\                    (the 8GB Higgs model - DO NOT LOSE!)
REM
REM USAGE:
REM   1. Place SpeechStudio_clean.zip in the SAME folder as this script
REM      (i.e., in F:\test\SpeechStudioRX\)
REM   2. Close the GUINEO application if it is running.
REM   3. Double-click update.bat (or run it from a command prompt).
REM
REM The script will:
REM   - Back up your current source code to _backup_source\ (just in case)
REM   - Extract ONLY .py, .txt, .bat, .md files from the zip
REM   - Skip all data folders (settings, voices, outputs, presets, models)
REM
REM ============================================================

setlocal enabledelayedexpansion

set "APP_ROOT=%~dp0"
set "APP_ROOT=%APP_ROOT:~0,-1%"
set "ZIP_FILE=%APP_ROOT%\SpeechStudio_clean.zip"
set "BACKUP_DIR=%APP_ROOT%\_backup_source_%date:~-4,4%%date:~-10,2%%date:~-7,2%_%time:~0,2%%time:~3,2%"

echo.
echo ============================================================
echo  GUINEO Safe Update
echo ============================================================
echo  App root: %APP_ROOT%
echo  Zip file: %ZIP_FILE%
echo.

REM --- Check that the zip exists ---
if not exist "%ZIP_FILE%" (
    echo ERROR: SpeechStudio_clean.zip not found in:
    echo   %APP_ROOT%
    echo.
    echo Place the zip file in the same folder as this script
    echo and run it again.
    echo.
    pause
    exit /b 1
)

REM --- Check that we're in the right place (SpeechStudio.py should exist) ---
if not exist "%APP_ROOT%\SpeechStudio.py" (
    echo ERROR: SpeechStudio.py not found in:
    echo   %APP_ROOT%
    echo.
    echo This script must be placed in the GUINEO application root
    echo (the folder that contains SpeechStudio.py, engine\, ui\, etc.).
    echo.
    pause
    exit /b 1
)

echo Step 1: Backing up current source code...
echo   Backup location: %BACKUP_DIR%
mkdir "%BACKUP_DIR%" 2>nul

REM Backup source folders (engine, ui, tools, spec, docs, research)
for %%D in (engine ui tools spec docs research) do (
    if exist "%APP_ROOT%\%%D" (
        xcopy "%APP_ROOT%\%%D" "%BACKUP_DIR%\%%D\" /E /I /Q /Y >nul 2>&1
    )
)

REM Backup top-level source files
for %%F in (SpeechStudio.py bootstrap.py requirements.txt launch.bat README_START_HERE.txt) do (
    if exist "%APP_ROOT%\%%F" copy "%APP_ROOT%\%%F" "%BACKUP_DIR%\%%F" >nul 2>&1
)

echo   Done.
echo.

echo Step 2: Extracting source code from zip (data folders preserved)...
echo   Extracting: .py .txt .bat .md .yaml .json (config only, NOT settings.json)
echo.

REM Use PowerShell to do a selective extraction — it's more reliable than
REM the built-in unzip for filtering by extension and excluding paths.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "$zip = [System.IO.Compression.ZipFile]::OpenRead('%ZIP_FILE%');" ^
  "$appRoot = '%APP_ROOT%';" ^
  "$extracted = 0;" ^
  "$skipped = 0;" ^
  "foreach ($entry in $zip.Entries) {" ^
  "  $name = $entry.FullName;" ^
  "  if ($name -match '/$') { continue; }" ^
  "  $rel = $name -replace '^SpeechStudio/', '';" ^
  "  if (-not $rel) { continue; }" ^
  "  $skip = $false;" ^
  "  foreach ($excl in @('settings/', 'voices/', 'outputs/', 'presets/', 'models/', 'logs/', 'temp/', '.cache/', '__pycache__/')) {" ^
  "    if ($rel -like ($excl + '*')) { $skip = $true; break; }" ^
  "  }" ^
  "  if ($skip) { $skipped++; continue; }" ^
  "  $ext = [System.IO.Path]::GetExtension($name);" ^
  "  if ($ext -notin @('.py', '.txt', '.bat', '.md', '.yaml', '.yml')) {" ^
  "    $skipped++; continue;" ^
  "  }" ^
  "  $dest = Join-Path $appRoot $rel;" ^
  "  $destDir = [System.IO.Path]::GetDirectoryName($dest);" ^
  "  if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null; }" ^
  "  [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $dest, $true);" ^
  "  $extracted++;" ^
  "}" ^
  "$zip.Dispose();" ^
  "Write-Host ('  Extracted: ' + $extracted + ' files');" ^
  "Write-Host ('  Skipped (data/cache): ' + $skipped + ' files');"

if errorlevel 1 (
    echo.
    echo ERROR: Extraction failed. See the messages above.
    echo Your source code backup is at: %BACKUP_DIR%
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Update complete!
echo ============================================================
echo.
echo  Your personal data was preserved:
echo    - settings\settings.json  (theme, project, volume)
echo    - settings\history\       (history entries)
echo    - voices\                 (voice profiles)
echo    - outputs\                (generated audio)
echo    - presets\                (saved presets)
echo    - models\                 (the Higgs model)
echo.
echo  Source code backup: %BACKUP_DIR%
echo  (safe to delete after you verify the update works)
echo.
echo  You can now launch GUINEO with launch.bat
echo.
pause
endlocal
