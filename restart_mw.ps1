$pids = (Get-NetTCPConnection -LocalPort 8100 -ErrorAction SilentlyContinue).OwningProcess | Sort-Object -Unique
foreach ($p in $pids) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }
Start-Sleep 2
Write-Host "Port 8100 cleared"

$mwProc = Start-Process -FilePath "D:\memory-web\.venv\Scripts\python.exe" `
    -ArgumentList @("-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8100") `
    -WorkingDirectory "D:\memory-web" -PassThru -WindowStyle Hidden `
    -RedirectStandardError "D:\memory-web\logs\uvicorn_err.log"
Write-Host "Started uvicorn PID $($mwProc.Id)"
