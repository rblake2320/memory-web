# Fix NSSM services to use venv Python instead of system Python
# Run as Admin

$NSSM = "D:\tools\nssm\nssm.exe"
$VENV_PYTHON = "D:\memory-web\.venv\Scripts\python.exe"

# Stop both
& $NSSM stop MemoryWeb-API 2>$null
& $NSSM stop MemoryWeb-Celery 2>$null
Start-Sleep 2

# Fix MemoryWeb-API to use venv Python
& $NSSM set MemoryWeb-API Application $VENV_PYTHON
& $NSSM set MemoryWeb-API AppParameters "-m uvicorn app.main:app --host 0.0.0.0 --port 8100 --workers 1"

# Fix MemoryWeb-Celery to use venv Python
& $NSSM set MemoryWeb-Celery Application $VENV_PYTHON
& $NSSM set MemoryWeb-Celery AppParameters "-m celery -A app.celery_app worker --pool=solo --loglevel=info"

# Kill any lingering manual python processes on port 8100
Get-Process -Name python -ErrorAction SilentlyContinue | ForEach-Object {
    try {
        $cmd = (Get-WmiObject Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine
        if ($cmd -match "uvicorn.*8100") {
            Stop-Process -Id $_.Id -Force
            Write-Host "Killed manual uvicorn PID $($_.Id)"
        }
    } catch {}
}
Start-Sleep 2

# Start both
& $NSSM start MemoryWeb-API
& $NSSM start MemoryWeb-Celery

Start-Sleep 5
Get-Service MemoryWeb-API, MemoryWeb-Celery | Format-Table Name, Status, StartType -AutoSize
Write-Host "Done." -ForegroundColor Green
