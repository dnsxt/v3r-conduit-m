@echo off
setlocal EnableExtensions
echo Installing V3R Agent...

set "NEXUS=%USERPROFILE%\Desktop\v3r_nexus"
mkdir "%NEXUS%" 2>nul
mkdir "%NEXUS%\logs" 2>nul
mkdir "%NEXUS%\temp" 2>nul

set "TEMPLATE=%~dp0command_headers_template.txt"
set "TARGET=%NEXUS%\command_headers.txt"
if not exist "%TARGET%" (
    if exist "%TEMPLATE%" (
        copy /Y "%TEMPLATE%" "%TARGET%" >nul
    )
)

set "EXE=%~dp0v3r_agent.exe"
if exist "%EXE%" (
    powershell -NoProfile -Command ^
      "$w = New-Object -ComObject WScript.Shell; ^
       $s = $w.CreateShortcut([Environment]::GetFolderPath('Startup') + '\V3R Agent.lnk'); ^
       $s.TargetPath = '%EXE%'; ^
       $s.WorkingDirectory = '%NEXUS%'; ^
       $s.Save()"
    echo Startup shortcut points to v3r_agent.exe
) else (
    echo Note: v3r_agent.exe not found next to this script; skipping Startup shortcut.
    echo Run from Python: pip install -r requirements.txt ^&^& python v3r_agent.py
)

echo.
echo Installation complete. Nexus folder: %NEXUS%
pause
