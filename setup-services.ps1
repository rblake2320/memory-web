# Run elevated: Right-click PowerShell -> Run as Administrator, then:
# powershell -ExecutionPolicy Bypass -File D:\memory-web\setup-services.ps1

$NSSM = "D:\tools\nssm\nssm.exe"

# ── Configure MemoryWeb-API ────────────────────────────────
Write-Host "Configuring MemoryWeb-API..." -ForegroundColor Cyan
& $NSSM set MemoryWeb-API AppDirectory "D:\memory-web"
& $NSSM set MemoryWeb-API AppStdout "D:\memory-web\logs\memoryweb.log"
& $NSSM set MemoryWeb-API AppStderr "D:\memory-web\logs\uvicorn_err.log"
& $NSSM set MemoryWeb-API AppStdoutCreationDisposition 4
& $NSSM set MemoryWeb-API AppStderrCreationDisposition 4
& $NSSM set MemoryWeb-API AppRotateFiles 1
& $NSSM set MemoryWeb-API AppRotateBytes 10485760
& $NSSM set MemoryWeb-API AppRestartDelay 5000
& $NSSM set MemoryWeb-API AppThrottle 10000
& $NSSM set MemoryWeb-API Description "MemoryWeb API - FastAPI/Uvicorn on port 8100"
& $NSSM set MemoryWeb-API AppEnvironmentExtra "MW_DATABASE_URL=postgresql://memoryweb:memoryweb@localhost:5433/memoryweb" "MW_REDIS_URL=redis://localhost:6379/1" "MW_DB_SCHEMA=memoryweb" "MW_PORT=8100" "MW_OLLAMA_BASE_URL=http://localhost:11434" "MW_EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2" "MW_EMBED_DIM=384"

# ── Install + Configure MemoryWeb-Celery ───────────────────
Write-Host "Installing MemoryWeb-Celery..." -ForegroundColor Cyan
& $NSSM install MemoryWeb-Celery "C:\Python312\python.exe" "-m celery -A app.celery_app worker --pool=solo --loglevel=info"
& $NSSM set MemoryWeb-Celery AppDirectory "D:\memory-web"
& $NSSM set MemoryWeb-Celery AppStdout "D:\memory-web\logs\celery.log"
& $NSSM set MemoryWeb-Celery AppStderr "D:\memory-web\logs\celery_err.log"
& $NSSM set MemoryWeb-Celery AppStdoutCreationDisposition 4
& $NSSM set MemoryWeb-Celery AppStderrCreationDisposition 4
& $NSSM set MemoryWeb-Celery AppRotateFiles 1
& $NSSM set MemoryWeb-Celery AppRotateBytes 10485760
& $NSSM set MemoryWeb-Celery AppRestartDelay 5000
& $NSSM set MemoryWeb-Celery AppThrottle 10000
& $NSSM set MemoryWeb-Celery Description "MemoryWeb Celery Worker - background ingestion"
& $NSSM set MemoryWeb-Celery Start SERVICE_AUTO_START
& $NSSM set MemoryWeb-Celery AppEnvironmentExtra "MW_DATABASE_URL=postgresql://memoryweb:memoryweb@localhost:5433/memoryweb" "MW_REDIS_URL=redis://localhost:6379/1" "MW_CELERY_BROKER_URL=redis://localhost:6379/1" "MW_CELERY_RESULT_BACKEND=redis://localhost:6379/1" "MW_DB_SCHEMA=memoryweb" "MW_OLLAMA_BASE_URL=http://localhost:11434" "MW_EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2" "MW_EMBED_DIM=384"

# ── Kill old manual uvicorn and start services ─────────────
Write-Host "Stopping old manual processes..." -ForegroundColor Cyan
Get-Process -Name python -ErrorAction SilentlyContinue | ForEach-Object {
    try {
        $cmd = (Get-WmiObject Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine
        if ($cmd -match "uvicorn.*8100") {
            Write-Host "  Killing PID $($_.Id)"
            Stop-Process -Id $_.Id -Force
        }
    } catch {}
}
Start-Sleep 2

Write-Host "Starting services..." -ForegroundColor Cyan
& $NSSM start MemoryWeb-API
& $NSSM start MemoryWeb-Celery

Start-Sleep 5
Get-Service MemoryWeb-API, MemoryWeb-Celery | Format-Table Name, Status, StartType -AutoSize
Write-Host "Done." -ForegroundColor Green
