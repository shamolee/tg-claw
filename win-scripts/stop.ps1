# Stops tg-claw completely: scheduled task plus the whole process tree.
# Run in an elevated PowerShell if processes were started by the boot task.
. "$PSScriptRoot\_common.ps1"

Stop-ScheduledTask -TaskName $ServiceName -ErrorAction SilentlyContinue

$locks = 'wrapper.lock', 'tg_claw.lock' | ForEach-Object { Join-Path $LocksDir $_ }
$pids = @()
foreach ($p in $locks) {
    if (Test-Path $p) { $pids += (Get-Content $p -Raw | ConvertFrom-Json).pid }
}
$pids += Get-CimInstance Win32_Process |
    Where-Object { $_.Name -match '^python' -and $_.CommandLine -match [regex]::Escape($root) -or
                   $_.CommandLine -match 'tg_claw_daemon|-m src' } |
    ForEach-Object { $_.ProcessId }

foreach ($id in ($pids | Where-Object { $_ } | Sort-Object -Unique)) {
    if (Get-Process -Id $id -ErrorAction SilentlyContinue) { taskkill /F /T /PID $id | Out-Null }
}

Start-Sleep 2
$left = Get-Process -Id ($pids | Where-Object { $_ }) -ErrorAction SilentlyContinue
if ($left) {
    Write-Warning "Still running (try an elevated shell): $($left.Id -join ', ')"
    exit 1
}
Remove-Item $locks -ErrorAction SilentlyContinue
Write-Host "$ServiceName stopped."

