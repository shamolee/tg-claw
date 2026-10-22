# Shows task state, running TeleClaw processes, and bot status/log tail.
. "$PSScriptRoot\_common.ps1"
Get-ScheduledTask $ServiceName | Select-Object TaskName, State | Format-Table -AutoSize
Get-ScheduledTaskInfo $ServiceName | Format-List LastRunTime, LastTaskResult
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -match 'teleclaw_daemon|-m src' } |
    Select-Object ProcessId, ParentProcessId, CreationDate | Format-Table -AutoSize
Get-Content "$StateDir\teleclaw_status.json" -ErrorAction SilentlyContinue
Get-Content "$LogsDir\teleclaw.log" -Tail 5 -Encoding UTF8 -ErrorAction SilentlyContinue
