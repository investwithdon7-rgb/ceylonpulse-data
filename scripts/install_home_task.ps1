# Installs the "CeylonPulse home refresh" scheduled task for scripts/local_refresh.py.
# It starts hourly and at logon; the script itself fetches each source only once a
# day (after 18:15 Sri Lanka time) and does nothing on the other starts.
# Run: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_home_task.ps1
$name = 'CeylonPulse home refresh'
$script = Join-Path $PSScriptRoot 'local_refresh.py'
$a = New-ScheduledTaskAction -Execute 'C:\Python314\pythonw.exe' -Argument "`"$script`""
$hourly = New-ScheduledTaskTrigger -Once -At (Get-Date '00:10') -RepetitionInterval (New-TimeSpan -Hours 1)
$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $a -Trigger @($hourly, $logon) -Settings $s -Force | Out-Null
$i = Get-ScheduledTaskInfo -TaskName $name
"Installed '$name'. Next start: $($i.NextRunTime)"
