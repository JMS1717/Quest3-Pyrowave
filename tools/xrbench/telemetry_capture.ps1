# ALVR telemetry capture for xrbench: records StatisticsSummary/GraphStatistics/HeadsetTelemetry events from the
# streamer's /api/events websocket for -Seconds, plus nvidia-smi GPU samples and the headset's
# thermal/battery state before and after. Writes events.json, gpu.csv, capture.json into -Out.
param(
    [Parameter(Mandatory)][string]$Out,
    [int]$Seconds = 60,
    [string]$Session = '',
    [string]$Adb = 'adb',
    [string]$Serial = '',
    [string]$EventsUri = 'ws://127.0.0.1:8082/api/events'
)
$ErrorActionPreference = 'Stop'
[void](New-Item -ItemType Directory -Path $Out -Force)
$adbArgs = if ($Serial) { @('-s', $Serial) } else { @() }
if ($Session -and (Test-Path $Session)) { Copy-Item $Session (Join-Path $Out 'session.json') }
Get-CimInstance Win32_Processor | Select-Object Name | ConvertTo-Json | Set-Content (Join-Path $Out 'cpu.json')
& $Adb @adbArgs shell dumpsys thermalservice | Set-Content (Join-Path $Out 'thermal-start.txt')
& $Adb @adbArgs shell dumpsys battery | Set-Content (Join-Path $Out 'battery-start.txt')

$gpu = Start-Job -ArgumentList $Out, ($Seconds + 2) -ScriptBlock {
    param($outputRoot, $limit)
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    [System.IO.File]::WriteAllText("$outputRoot\gpu.csv", '')
    while ($watch.Elapsed.TotalSeconds -lt $limit) {
        $line = & 'C:\Windows\System32\nvidia-smi.exe' --query-gpu=timestamp,name,driver_version,utilization.gpu,utilization.encoder,utilization.decoder,memory.used,temperature.gpu,power.draw,clocks.current.graphics,clocks.current.memory --format=csv,noheader,nounits
        [System.IO.File]::AppendAllText("$outputRoot\gpu.csv", ($line + "`r`n"))
        Start-Sleep -Milliseconds 1000
    }
}
$socket = [System.Net.WebSockets.ClientWebSocket]::new()
$socket.Options.SetRequestHeader('X-ALVR', 'true')
$events = [System.Collections.Generic.List[object]]::new()
$cancel = [System.Threading.CancellationTokenSource]::new()
$elapsed = [System.Diagnostics.Stopwatch]::new()
$captureError = $null
try {
    $socket.ConnectAsync([Uri]$EventsUri, $cancel.Token).GetAwaiter().GetResult() | Out-Null
    $cancel.CancelAfter($Seconds * 1000)
    $elapsed.Start()
    $buffer = New-Object byte[] 65536
    while (!$cancel.IsCancellationRequested) {
        $message = ''
        do {
            $read = $socket.ReceiveAsync([ArraySegment[byte]]::new($buffer), $cancel.Token).GetAwaiter().GetResult()
            if ($read.MessageType -eq [System.Net.WebSockets.WebSocketMessageType]::Close) { throw 'Event stream closed early' }
            $message += [Text.Encoding]::UTF8.GetString($buffer, 0, $read.Count)
        } while (!$read.EndOfMessage)
        $event = $message | ConvertFrom-Json
        # HeadsetTelemetry (beta build): headset thermals and PyroWave decode statistics, 1 Hz
        if ($event.event_type.id -in @('StatisticsSummary', 'GraphStatistics', 'HeadsetTelemetry')) { $events.Add($event) }
        # the server's one-line "PyroWave effective config" (info level, so not in crash_log)
        elseif ($event.event_type.id -eq 'Log' -and "$($event.event_type.data.content)" -like '*PyroWave effective config*') { $events.Add($event) }
    }
} catch {
    if (!$cancel.IsCancellationRequested) { $captureError = $_.Exception.Message }
} finally {
    $elapsed.Stop()
    $gpu | Wait-Job -Timeout 10 | Out-Null
    $gpu | Receive-Job | Out-Null
    $gpu | Remove-Job -Force
    $socket.Dispose()
    $cancel.Dispose()
    ConvertTo-Json -InputObject $events.ToArray() -Depth 12 | Set-Content (Join-Path $Out 'events.json')
    & $Adb @adbArgs shell dumpsys thermalservice | Set-Content (Join-Path $Out 'thermal-end.txt')
    & $Adb @adbArgs shell dumpsys battery | Set-Content (Join-Path $Out 'battery-end.txt')
    [pscustomobject]@{ duration_seconds = $elapsed.Elapsed.TotalSeconds; event_count = $events.Count; error = $captureError
                       completed = ($elapsed.Elapsed.TotalSeconds -ge ($Seconds - 1) -and !$captureError) } |
        ConvertTo-Json | Tee-Object -FilePath (Join-Path $Out 'capture.json')
}
