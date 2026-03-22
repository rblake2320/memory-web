# MemoryWeb - start all services in dependency order
# Logs to D:\memory-web\logs\startup.log
# Usage: powershell.exe -File "D:\memory-web\start_all.ps1"

$ErrorActionPreference = "Continue"
$LogDir = "D:\memory-web\logs"
$LogFile = "$LogDir\startup.log"
$MwDir = "D:\memory-web"
$Venv = "D:\memory-web\.venv\Scripts"
$PgData = "D:\PostgreSQL\data"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function WaitPort($port, $label, $timeoutSec=15) {
    for ($i=1; $i -le $timeoutSec; $i++) {
        $conn = Test-NetConnection -ComputerName 127.0.0.1 -Port $port -WarningAction SilentlyContinue -InformationLevel Quiet
        if ($conn) { Log "  $label port $port READY"; return $true }
        Start-Sleep 1
    }
    Log "  ERROR: $label port $port not ready after ${timeoutSec}s"
    return $false
}

Log "========== MemoryWeb startup =========="

# 1. PostgreSQL
Log "1/5  PostgreSQL..."
$pgReady = & pg_isready -h 127.0.0.1 -p 5432 2>&1
if ($pgReady -match "accepting connections") {
    Log "  PostgreSQL already running"
} else {
    & pg_ctl start -D $PgData -l "$PgData\postgresql.log" 2>&1 | Out-Null
    Start-Sleep 3
    $pgReady2 = & pg_isready -h 127.0.0.1 -p 5432 2>&1
    if ($pgReady2 -match "accepting connections") {
        Log "  PostgreSQL started OK"
    } else {
        Log "  FATAL: PostgreSQL failed to start - aborting. Check $PgData\postgresql.log"
        exit 1
    }
}

# 2. Redis (Docker)
Log "2/5  Redis (Docker)..."
$redisState = docker inspect memoryweb-redis --format "{{.State.Status}}" 2>&1
if ($redisState -eq "running") {
    Log "  Redis already running"
} else {
    docker update --restart unless-stopped memoryweb-redis 2>&1 | Out-Null
    docker start memoryweb-redis 2>&1 | Out-Null
    if (WaitPort 6379 "Redis" 15) {
        Log "  Redis started OK"
    } else {
        Log "  WARN: Redis not ready - ingest endpoints will return 503 (search still works)"
    }
}

# 3. Ollama
Log "3/5  Ollama..."
$ollamaUp = Test-NetConnection -ComputerName 127.0.0.1 -Port 11434 -WarningAction SilentlyContinue -InformationLevel Quiet
if ($ollamaUp) {
    Log "  Ollama already running"
} else {
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden -ErrorAction SilentlyContinue
    Start-Sleep 3
    $ollamaUp2 = Test-NetConnection -ComputerName 127.0.0.1 -Port 11434 -WarningAction SilentlyContinue -InformationLevel Quiet
    if ($ollamaUp2) {
        Log "  Ollama started OK"
    } else {
        Log "  WARN: Ollama not running - memory synthesis (Tier 3 LLM) will fail"
    }
}

# 4. MemoryWeb (uvicorn)
Log "4/5  MemoryWeb (uvicorn on :8100)..."
$mwUp = Test-NetConnection -ComputerName 127.0.0.1 -Port 8100 -WarningAction SilentlyContinue -InformationLevel Quiet
if ($mwUp) {
    Log "  Port 8100 in use - killing stale process..."
    $pids = (Get-NetTCPConnection -LocalPort 8100 -ErrorAction SilentlyContinue).OwningProcess | Sort-Object -Unique
    foreach ($p in $pids) {
        $proc = Get-Process -Id $p -ErrorAction SilentlyContinue
        if ($proc -and $proc.ProcessName -notmatch "powershell|pwsh") {
            Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep 2
}

Get-ChildItem $MwDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$mwProc = Start-Process -FilePath "$Venv\python.exe" `
    -ArgumentList @("-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8100") `
    -WorkingDirectory $MwDir -PassThru -WindowStyle Hidden `
    -RedirectStandardError "$LogDir\uvicorn_err.log"

Log "  MemoryWeb PID $($mwProc.Id) - waiting for HTTP..."
$mwReady = $false
for ($i=1; $i -le 30; $i++) {
    Start-Sleep 1
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8100/" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        Log "  MemoryWeb UP (HTTP $($r.StatusCode))"
        $mwReady = $true
        break
    } catch {}
}
if (-not $mwReady) {
    Log "  ERROR: MemoryWeb did not respond after 30s - check $LogDir\uvicorn_err.log"
}

# 5. Celery worker
Log "5/5  Celery worker..."
$celeryRunning = Get-WmiObject Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "celery" } | Select-Object -First 1
if ($celeryRunning) {
    Log "  Celery already running (PID $($celeryRunning.ProcessId))"
} else {
    $celeryProc = Start-Process -FilePath "$Venv\celery.exe" `
        -ArgumentList @("-A","app.celery_app","worker","-P","threads","--concurrency=4","-n","mw-worker@localhost","--loglevel=info") `
        -WorkingDirectory $MwDir -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput "$LogDir\celery.log" `
        -RedirectStandardError "$LogDir\celery_err.log"
    Start-Sleep 4
    if (-not $celeryProc.HasExited) {
        Log "  Celery worker started OK (PID $($celeryProc.Id))"
    } else {
        Log "  ERROR: Celery exited with code $($celeryProc.ExitCode) - check $LogDir\celery_err.log"
    }
}

Log "========== Startup complete =========="
Log "Dashboard: http://127.0.0.1:8100"
Log "Logs dir:  $LogDir"
Write-Host ""
Write-Host "Dashboard: http://127.0.0.1:8100" -ForegroundColor Cyan
Write-Host "Logs:      $LogDir" -ForegroundColor Gray
