# Starts the tg-claw scheduled task and shows its state.
. "$PSScriptRoot\_common.ps1"
Start-ScheduledTask -TaskName $ServiceName
Start-Sleep 5
& "$PSScriptRoot\status.ps1"
