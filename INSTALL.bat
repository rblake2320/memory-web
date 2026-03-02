@echo off
setlocal EnableDelayedExpansion
title MemoryWeb Installer

echo.
echo  ==========================================
echo    MemoryWeb - Windows Installer
echo  ==========================================
echo.
echo  This will download and start MemoryWeb.
echo  First-time setup downloads ~1.5 GB of
echo  Docker images (takes 2-5 minutes).
echo.
echo  REQUIREMENT: Docker Desktop must be
echo  installed and running before continuing.
echo.
echo  Get Docker Desktop (free):
echo  https://docs.docker.com/desktop/install/windows-install/
echo.
pause

:: ── Step 1: Check Docker is installed ────────────────────────────────────────
echo.
echo  [1/4] Checking Docker Desktop...
echo.
where docker >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo  ^^! Docker Desktop not found.
    echo.
    echo  Install Docker Desktop from:
    echo  https://docs.docker.com/desktop/install/windows-install/
    echo.
    echo  After installing, restart your computer, then
    echo  double-click INSTALL.bat again.
    echo.
    pause
    exit /b 1
)

:: Check Docker daemon is actually running
docker info >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo  ^^! Docker Desktop is installed but not running.
    echo.
    echo  Please:
    echo    1. Open Docker Desktop from your Start menu
    echo    2. Wait for the whale icon in the taskbar to stop animating
    echo    3. Then double-click INSTALL.bat again
    echo.
    pause
    exit /b 1
)
echo  [OK] Docker Desktop is running.

:: ── Step 2: Create .env config ───────────────────────────────────────────────
echo.
echo  [2/4] Configuring MemoryWeb...
echo.
if not exist ".env" (
    if exist ".env.example" (
        copy /Y ".env.example" ".env" >nul
        echo  [OK] Created .env config file with default settings.
        echo       You can edit .env later to change settings.
    ) else (
        echo  [OK] Using built-in defaults.
    )
) else (
    echo  [OK] Found existing .env - keeping your settings.
)

:: ── Step 3: Start all services ───────────────────────────────────────────────
echo.
echo  [3/4] Starting MemoryWeb services...
echo.
echo  On first run this downloads PostgreSQL, Redis, and the
echo  AI models. Please wait - this may take several minutes.
echo.

docker compose up -d
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  ^^! Failed to start services.
    echo.
    echo  Common fixes:
    echo    - Make sure Docker Desktop is running
    echo    - Check you have enough disk space (need ~3 GB free)
    echo    - Try: docker compose down -v  then run INSTALL.bat again
    echo.
    echo  For help: https://github.com/rblake2320/memoryweb/issues
    echo.
    pause
    exit /b 1
)

:: ── Step 4: Wait for app to be ready ─────────────────────────────────────────
echo.
echo  [4/4] Waiting for MemoryWeb to start...
echo.

set ATTEMPTS=0
set MAX=72

:health_loop
if %ATTEMPTS% GEQ %MAX% (
    echo.
    echo  Services are taking longer than expected.
    echo  Opening browser anyway - if it doesn't load yet,
    echo  wait 30 seconds and refresh.
    goto :open_browser
)

set /a ATTEMPTS+=1
set /a PCT=ATTEMPTS*100/MAX

:: Use curl (built into Windows 10/11) to check health endpoint
curl -sf --max-time 3 http://localhost:8100/api/health >nul 2>&1
if %ERRORLEVEL% EQU 0 goto :ready

echo  Starting... (%ATTEMPTS%/%MAX%)
timeout /t 5 /nobreak >nul
goto :health_loop

:ready
echo.
echo  ^^^ MemoryWeb is ready!

:open_browser
echo.
echo  ==========================================
echo    MemoryWeb is running!
echo  ==========================================
echo.
echo  Opening http://localhost:8100 in your browser...
echo.
echo  ---- What to do next --------------------------------
echo.
echo  1. Click "Load Sample Data" to explore immediately, or
echo  2. Upload your AI conversation files:
echo       - Claude: export from ~/.claude/projects/
echo       - ChatGPT: Settings > Data Controls > Export Data
echo.
echo  ---- Managing MemoryWeb -----------------------------
echo.
echo  Stop:     docker compose down
echo  Restart:  docker compose up -d
echo  Logs:     docker compose logs -f app
echo.
echo  ---- Support ----------------------------------------
echo.
echo  GitHub: https://github.com/rblake2320/memoryweb
echo.
echo  Press any key to open the dashboard...
pause >nul

start "" http://localhost:8100
