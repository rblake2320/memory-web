# ============================================================
# MemoryWeb Windows Service Installer
# Run as Administrator: Right-click -> Run with PowerShell (Admin)
# ============================================================

$ErrorActionPreference = "Stop"
$NSSM = "D:\tools\nssm\nssm.exe"
$PYTHON = "C:\Python312\python.exe"
$MW_DIR = "D:\memory-web"
$LOG_DIR = "$MW_DIR\logs"

# Ensure log directory exists
New-Item -ItemType Directory -Force -Path $LOG_DIR | Out-Null

# ── 1. Kill existing manual processes ───────────────────────
Write-Host "`n[1/4] Stopping any manually running MemoryWeb processes..." -ForegroundColor Cyan
Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -eq $PYTHON
} | ForEach-Object {
    try {
        $cmdLine = (Get-WmiObject Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine
        if ($cmdLine -match "uvicorn.*8100|celery.*worker") {
            Write-Host "  Stopping PID $($_.Id): $cmdLine"
            Stop-Process -Id $_.Id -Force
        }
    } catch {}
}
Start-Sleep -Seconds 2

# ── 2. Install MemoryWeb API service ────────────────────────
Write-Host "`n[2/4] Installing MemoryWeb-API service..." -ForegroundColor Cyan

# Remove if exists (idempotent)
& $NSSM stop MemoryWeb-API 2>$null
& $NSSM remove MemoryWeb-API confirm 2>$null

& $NSSM install MemoryWeb-API $PYTHON
& $NSSM set MemoryWeb-API AppParameters "-m uvicorn app.main:app --host 0.0.0.0 --port 8100 --workers 1"
& $NSSM set MemoryWeb-API AppDirectory $MW_DIR
& $NSSM set MemoryWeb-API AppStdout "$LOG_DIR\memoryweb.log"
& $NSSM set MemoryWeb-API AppStderr "$LOG_DIR\uvicorn_err.log"
& $NSSM set MemoryWeb-API AppStdoutCreationDisposition 4  # Append
& $NSSM set MemoryWeb-API AppStderrCreationDisposition 4  # Append
& $NSSM set MemoryWeb-API AppRotateFiles 1
& $NSSM set MemoryWeb-API AppRotateBytes 10485760  # 10MB rotation
& $NSSM set MemoryWeb-API AppRestartDelay 5000  # 5s before restart
& $NSSM set MemoryWeb-API AppThrottle 10000  # Throttle rapid crashes
& $NSSM set MemoryWeb-API Description "MemoryWeb API - FastAPI/Uvicorn on port 8100"
& $NSSM set MemoryWeb-API Start SERVICE_AUTO_START
& $NSSM set MemoryWeb-API ObjectName LocalSystem
# Set environment: load .env variables
& $NSSM set MemoryWeb-API AppEnvironmentExtra "MW_DATABASE_URL=postgresql://memoryweb:memoryweb@localhost:5433/memoryweb" "MW_REDIS_URL=redis://localhost:6379/1" "MW_DB_SCHEMA=memoryweb" "MW_PORT=8100" "MW_OLLAMA_BASE_URL=http://localhost:11434"

Write-Host "  MemoryWeb-API installed." -ForegroundColor Green

# ── 3. Install Celery Worker service ────────────────────────
Write-Host "`n[3/4] Installing MemoryWeb-Celery service..." -ForegroundColor Cyan

& $NSSM stop MemoryWeb-Celery 2>$null
& $NSSM remove MemoryWeb-Celery confirm 2>$null

& $NSSM install MemoryWeb-Celery $PYTHON
& $NSSM set MemoryWeb-Celery AppParameters "-m celery -A app.celery_app worker --pool=solo --loglevel=info"
& $NSSM set MemoryWeb-Celery AppDirectory $MW_DIR
& $NSSM set MemoryWeb-Celery AppStdout "$LOG_DIR\celery.log"
& $NSSM set MemoryWeb-Celery AppStderr "$LOG_DIR\celery_err.log"
& $NSSM set MemoryWeb-Celery AppStdoutCreationDisposition 4
& $NSSM set MemoryWeb-Celery AppStderrCreationDisposition 4
& $NSSM set MemoryWeb-Celery AppRotateFiles 1
& $NSSM set MemoryWeb-Celery AppRotateBytes 10485760
& $NSSM set MemoryWeb-Celery AppRestartDelay 5000
& $NSSM set MemoryWeb-Celery AppThrottle 10000
& $NSSM set MemoryWeb-Celery Description "MemoryWeb Celery Worker - background ingestion pipeline"
& $NSSM set MemoryWeb-Celery Start SERVICE_AUTO_START
& $NSSM set MemoryWeb-Celery ObjectName LocalSystem
& $NSSM set MemoryWeb-Celery AppEnvironmentExtra "MW_DATABASE_URL=postgresql://memoryweb:memoryweb@localhost:5433/memoryweb" "MW_REDIS_URL=redis://localhost:6379/1" "MW_CELERY_BROKER_URL=redis://localhost:6379/1" "MW_CELERY_RESULT_BACKEND=redis://localhost:6379/1" "MW_DB_SCHEMA=memoryweb" "MW_OLLAMA_BASE_URL=http://localhost:11434"

Write-Host "  MemoryWeb-Celery installed." -ForegroundColor Green

# ── 4. Start both services ──────────────────────────────────
Write-Host "`n[4/4] Starting services..." -ForegroundColor Cyan

& $NSSM start MemoryWeb-API
& $NSSM start MemoryWeb-Celery

Start-Sleep -Seconds 5

# Verify
Write-Host "`n=== Service Status ===" -ForegroundColor Yellow
Get-Service MemoryWeb-API, MemoryWeb-Celery | Format-Table Name, Status, StartType -AutoSize

# Health check
Write-Host "`nHealth check:" -ForegroundColor Yellow
try {
    $health = Invoke-RestMethod -Uri "http://localhost:8100/api/status" -TimeoutSec 10
    foreach ($svc in $health.services) {
        $icon = if ($svc.healthy) { "[OK]" } else { "[!!]" }
        Write-Host "  $icon $($svc.name)" -ForegroundColor $(if ($svc.healthy) { "Green" } else { "Red" })
    }
    Write-Host "  Memories: $($health.stats.memories)" -ForegroundColor Cyan
} catch {
    Write-Host "  API not responding yet - check logs at $LOG_DIR" -ForegroundColor Yellow
}

Write-Host "`nDone. Both services will auto-start on boot." -ForegroundColor Green
Write-Host "Manage with: nssm start/stop/restart MemoryWeb-API" -ForegroundColor Gray
Write-Host "             nssm start/stop/restart MemoryWeb-Celery" -ForegroundColor Gray
