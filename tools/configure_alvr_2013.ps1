# Applies the Galaxy XR Wi-Fi configuration (a community Galaxy XR guide's values) to an ALVR 20.13
# streamer via its dashboard API, then reads session.json back to prove every value persisted.
param(
    [ValidateSet('H264','Hevc','PyroWave')][string]$Codec = 'H264',
    [ValidateRange(10,1200)][int]$Mbps = 400,          # NVENC H.264 crashes above 1200
    [int]$EyeSize = 2560,
    # 0 = square (use $EyeSize). The panel is 3552x3840 and the FOV 94.4x105.1 deg, so square
    # under-samples vertically; new plans pass a real height.
    [int]$EyeHeight = 0,
    # 60 is real: the runtime enumerates [60.000004, 72.00001, 90.0] and dumpsys display
    # lists all three at 7104x3840. 60 Hz is what the quality presets spend on resolution.
    [ValidateSet(60,72,90)][int]$RefreshHz = 72,
    [double]$MaxBufferingFrames = 2.0,
    # ALVR sleeps until the next vsync before producing a frame when this is on
    # (server_openvr/src/lib.rs:592), which is up to a frame period of deliberate latency. Its
    # default is on; off makes the server yield instead.
    [ValidateSet('on','off')][string]$FramePacing = 'on',
    # SGSR, already in the client's stream shader and shipped disabled. Edge-directed sharpening
    # plus upscale, in the same fragment invocation as the foveation inverse warp -- so it costs
    # client GPU and nothing on the decoder, which is the constraint everything else here hits.
    [ValidateSet('on','off')][string]$Upscaling = 'off',
    [ValidateRange(1.0,3.0)][double]$UpscaleFactor = 1.5,     # scales the swapchain, not the encode
    [ValidateRange(1.0,2.0)][double]$EdgeSharpness = 2.0,
    [ValidateRange(1,7)][int]$NvencPreset = 1,
    [ValidateSet('Tcp','Udp')][string]$Protocol = 'Tcp',
    # Scalars rather than [double[]]: invoked through -File, PowerShell hands "0.45,0.4" over as a
    # single string and refuses to convert it, so an array parameter cannot be passed this way.
    [double]$FoveationCenterX = 0.45,
    [double]$FoveationCenterY = 0.40,
    [double]$FoveationEdgeX = 3.0,
    [double]$FoveationEdgeY = 4.0,
    [ValidateSet('on','off')][string]$Foveation = 'on',
    [ValidateSet('on','off')][string]$ColorCorrection = 'off',   # guide uses on (sat 0.6, sharpen 0.65); off for fidelity scoring
    [ValidateSet('Default','Maximum')][string]$SocketBuffers = 'Default',   # Maximum let 800 Mbps queue ~15 s of video
    [ValidateRange(1,1024)][int]$MaxQueuedFrames = 12,                     # ~170 ms at 72 Hz: drop, don't backlog
    [ValidateRange(500,65000)][int]$PacketSize = 1400,                      # ALVR shard size; larger suits TCP/adb
    [string]$DecoderName = '',                     # e.g. c2.qti.avc.decoder.low_latency (needs the xrw client patch)
    [ValidateSet(0,1)][int]$LowLatency = 0,        # Android MediaFormat KEY_LOW_LATENCY
    # PyroWave (beta build): the session's video.pyrowave section and foveation's follow_gaze.
    # Research env vars (ALVR_PYROWAVE_UDP, ALVR_PYROWAVE_WAVELET, ALVR_GAZE_FOVEATION) and the
    # headset property debug.xrwired.decode_path still override these when set.
    [ValidateSet('Udp','Tcp')][string]$PyroTransport = 'Udp',
    [ValidateSet('97','53')][string]$Wavelet = '97',
    [ValidateSet('compute','fragment','auto')][string]$DecodePath = 'compute',
    [ValidateSet('on','off')][string]$FollowGaze = 'on',
    [string]$Root = (Join-Path $PSScriptRoot 'ALVR-20.13.0'),
    [string]$ClientHostname = '',
    [string]$ClientWifiIp = '',
    [switch]$WhatIf,
    [switch]$Restore
)
$ErrorActionPreference = 'Stop'
$session = Join-Path $Root 'session.json'
# Backups sit beside the ALVR install this edits (the runtime root), where the harness restores
# them from; with the old flat layout that is the same folder as the script's.
$backupDir = Join-Path (Split-Path $Root -Parent) 'backups'
$api = 'http://127.0.0.1:8082/api/dashboard-request'
$headers = @{ 'X-ALVR' = 'true' }

if ($Restore) {
    $latest = Get-ChildItem $backupDir -Filter 'alvr2013-session-*.json' | Sort-Object LastWriteTime | Select-Object -Last 1
    if (-not $latest) { throw 'No alvr2013 session backup found.' }
    Copy-Item $latest.FullName $session -Force
    Write-Host "Restored $($latest.Name). Restart the streamer to load it."
    return
}

$settings = [ordered]@{
    'video.preferred_codec.variant'                          = $Codec
    'video.bitrate.mode.variant'                             = 'ConstantMbps'
    'video.bitrate.mode.ConstantMbps'                        = $Mbps
    'video.bitrate.adapt_to_framerate.enabled'               = $false
    'video.preferred_fps'                                    = [double]$RefreshHz
    'video.foveated_encoding.enabled'                        = ($Foveation -eq 'on')
    'video.foveated_encoding.content.force_enable'           = $false
    'video.foveated_encoding.content.center_size_x'          = $FoveationCenterX
    'video.foveated_encoding.content.center_size_y'          = $FoveationCenterY
    'video.foveated_encoding.content.center_shift_x'         = 0.0
    'video.foveated_encoding.content.center_shift_y'         = 0.0
    'video.foveated_encoding.content.edge_ratio_x'           = $FoveationEdgeX
    'video.foveated_encoding.content.edge_ratio_y'           = $FoveationEdgeY
    'video.foveated_encoding.content.follow_gaze'            = ($FollowGaze -eq 'on')
    'video.pyrowave.transport.variant'                       = $PyroTransport
    'video.pyrowave.wavelet.variant'                         = "Cdf$Wavelet"
    'video.pyrowave.decode_path.variant'                     = (Get-Culture).TextInfo.ToTitleCase($DecodePath)
    'video.color_correction.enabled'                         = ($ColorCorrection -eq 'on')
    'video.color_correction.content.saturation'              = 0.6
    'video.color_correction.content.sharpening'              = 0.65
    'video.max_buffering_frames'                             = $MaxBufferingFrames
    'video.upscaling.enabled'                                = ($Upscaling -eq 'on')
    'video.upscaling.content.upscale_factor'                 = $UpscaleFactor
    'video.upscaling.content.edge_sharpness'                 = $EdgeSharpness
    'video.enforce_server_frame_pacing'                      = ($FramePacing -eq 'on')
    'video.encoder_config.rate_control_mode.variant'         = 'Cbr'
    'video.encoder_config.filler_data'                       = $false
    'video.encoder_config.entropy_coding.variant'            = 'Cavlc'
    'video.encoder_config.use_10bit'                         = $false
    'video.encoder_config.server_overrides_use_10bit'        = $true
    'video.encoder_config.use_full_range'                    = $true
    'video.encoder_config.server_overrides_use_full_range'   = $true
    'video.encoder_config.nvenc.quality_preset.variant'      = "P$NvencPreset"
    'video.clientside_foveation.enabled'                     = $false
    'connection.stream_protocol.variant'                     = $Protocol
    'connection.avoid_video_glitching'                       = $true
    'connection.dscp.set'                                    = $true
    'connection.dscp.content.variant'                        = 'ExpeditedForwarding'
    'connection.max_queued_server_video_frames'              = $MaxQueuedFrames
    'connection.packet_size'                                 = $PacketSize
}
# ALVR's defaults plus the optional low-latency key and decoder-by-name selector.
# Append one pair at a time: a leading-comma list inside @(...) double-wraps its first element.
$codecOptions = @()
$codecOptions += ,@('operating-rate', [ordered]@{ ty = [ordered]@{ variant = 'Int32' }; value = '2147483647' })
$codecOptions += ,@('priority', [ordered]@{ ty = [ordered]@{ variant = 'Int32' }; value = '0' })
$codecOptions += ,@('vendor.qti-ext-dec-low-latency.enable', [ordered]@{ ty = [ordered]@{ variant = 'Int32' }; value = '1' })
if ($LowLatency) { $codecOptions += ,@('low-latency', [ordered]@{ ty = [ordered]@{ variant = 'Int32' }; value = '1' }) }
if ($DecoderName) { $codecOptions += ,@('xrw.decoder_name', [ordered]@{ ty = [ordered]@{ variant = 'String' }; value = $DecoderName }) }
$settings['video.mediacodec_extra_options.content'] = $codecOptions
foreach ($buffer in 'server_send_buffer_bytes', 'server_recv_buffer_bytes', 'client_send_buffer_bytes', 'client_recv_buffer_bytes') {
    $settings["connection.$buffer.variant"] = $SocketBuffers
}
$eyeHeightResolved = if ($EyeHeight -gt 0) { $EyeHeight } else { $EyeSize }
foreach ($res in 'transcoding_view_resolution', 'emulated_headset_view_resolution') {
    $settings["video.$res.variant"] = 'Absolute'
    $settings["video.$res.Absolute.width"] = $EyeSize
    $settings["video.$res.Absolute.height.set"] = $true
    $settings["video.$res.Absolute.height.content"] = $eyeHeightResolved
}

$pairs = foreach ($key in $settings.Keys) {
    @{ path = @(('session_settings.' + $key).Split('.') | ForEach-Object { @{ Name = $_ } }); value = $settings[$key] }
}
$body = @{ SetValues = @($pairs) } | ConvertTo-Json -Depth 12 -Compress

if ($WhatIf) {
    Write-Host "Would POST $($settings.Count) settings to $api"
    $settings.GetEnumerator() | ForEach-Object { '{0} = {1}' -f $_.Key, $_.Value }
    if ($ClientHostname -and $ClientWifiIp) {
        "client ${ClientHostname}: AddIfMissing (trusted), SetManualIps $ClientWifiIp, Trust"
    }
    return
}

if (-not (Test-Path $backupDir)) { New-Item -ItemType Directory $backupDir | Out-Null }
if (Test-Path $session) {
    Copy-Item $session (Join-Path $backupDir "alvr2013-session-$(Get-Date -Format yyyyMMdd-HHmmss).json")
}
Invoke-RestMethod $api -Method Post -Headers $headers -ContentType 'application/json' -Body $body | Out-Null

if ($ClientHostname -and $ClientWifiIp) {
    # A fresh or reset session has no entry for the headset, and SetManualIps only updates an
    # existing one: add it (trusted) first, then set its IP and trust it in case it was listed.
    $actions = @(
        @{ AddIfMissing = @{ trusted = $true; manual_ips = @($ClientWifiIp) } },
        @{ SetManualIps = @($ClientWifiIp) },
        'Trust'
    )
    foreach ($action in $actions) {
        $client = @{ UpdateClientList = @{ hostname = $ClientHostname; action = $action } } |
            ConvertTo-Json -Depth 8 -Compress
        Invoke-RestMethod $api -Method Post -Headers $headers -ContentType 'application/json' -Body $client | Out-Null
    }
}

# 20.14.1 was reported to drop settings on save, so verify each one landed in session.json.
Start-Sleep -Seconds 2
$saved = (Get-Content $session -Raw | ConvertFrom-Json).session_settings
$mismatches = foreach ($key in $settings.Keys) {
    $node = $saved
    foreach ($part in $key.Split('.')) { $node = $node.$part }
    $want = $settings[$key]
    $same = if ($want -is [array]) { (ConvertTo-Json $node -Depth 8 -Compress) -eq (ConvertTo-Json $want -Depth 8 -Compress) }
            elseif ($want -is [double] -or $want -is [int]) { [math]::Abs([double]$node - [double]$want) -lt 1e-6 }
            else { "$node" -eq "$want" }
    if (-not $same) { '{0}: wanted {1}, saved {2}' -f $key, $want, $node }
}
if ($mismatches) {
    $mismatches | ForEach-Object { Write-Warning $_ }
    throw "$(@($mismatches).Count) setting(s) did not persist to $session"
}
Write-Host "All $($settings.Count) settings persisted: $Codec $Mbps Mbps, ${EyeSize}x${eyeHeightResolved}/eye, $RefreshHz Hz, $Protocol, buffering $MaxBufferingFrames, pacing $FramePacing, upscaling $Upscaling x$UpscaleFactor, NVENC P$NvencPreset, centre $FoveationCenterX/$FoveationCenterY, edge $FoveationEdgeX/$FoveationEdgeY."
