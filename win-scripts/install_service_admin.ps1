# Run in an ELEVATED PowerShell (Run as administrator).
# Registers tg-claw to start at boot and keep running after you log out (no password stored).
. "$PSScriptRoot\_common.ps1"
$user = "$env:USERDOMAIN\$env:USERNAME"

# The task's environment may not have claude on PATH, so pass its folder to the launcher.
$claude = Get-Command claude -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $claude) { Write-Error 'claude not found on PATH. Install Claude Code first.'; exit 1 }
$claudeDir = Split-Path -Parent $claude.Source

# run_tg_claw.cmd does its own logging into $LogsDir\task_out.log, rotated past task_log_max_mb.
# cmd compares sizes as 32-bit numbers, so cap at 2047 MB.
$taskLogBytes = [Math]::Min([int](Get-ConfigValue 'task_log_max_mb' '5'), 2047) * 1MB
$a = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"`"$root\run_tg_claw.cmd`" `"$claudeDir`" `"$LogsDir`" $taskLogBytes`"" -WorkingDirectory $root
$t = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -AtLogOn -User $user))
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
     -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$p = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
Register-ScheduledTask -TaskName $ServiceName -Action $a -Trigger $t -Settings $s -Principal $p -Force
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'tg_claw_daemon|-m src(\s|$)' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 3
Start-ScheduledTask $ServiceName
Start-Sleep 20
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'tg_claw_daemon|-m src(\s|$)' } | Select-Object ProcessId, Name, SessionId
Get-ScheduledTask $ServiceName | Get-ScheduledTaskInfo
