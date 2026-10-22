# Shows task state, running tg-claw processes, and bot status/log tail.
. "$PSScriptRoot\_common.ps1"
Get-ScheduledTask $ServiceName | Select-Object TaskName, State | Format-Table -AutoSize
Get-ScheduledTaskInfo $ServiceName | Format-List LastRunTime, LastTaskResult
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -match 'tg_claw_daemon|-m src' } |
    Select-Object ProcessId, ParentProcessId, CreationDate | Format-Table -AutoSize
Get-Content "$StateDir\tg_claw_status.json" -ErrorAction SilentlyContinue
Get-Content "$LogsDir\tg_claw.log" -Tail 5 -Encoding UTF8 -ErrorAction SilentlyContinue
