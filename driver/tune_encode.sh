#!/bin/zsh
# Sweep SteamVR render_scale and read NVENC encode time straight from the driver log.
# No headset required: the compositor renders its void/dashboard and the driver encodes it.
# usage: PC_SSH=<user>@<pc> PC_WORKSPACE='<workspace>' tune_encode.sh 0.6 0.8 1.0
SSH="ssh -q -o BatchMode=yes -i $HOME/.ssh/id_rsa ${PC_SSH:?set PC_SSH to the ssh destination of the PC, user@host}"
PC_WORKSPACE=${PC_WORKSPACE:?set PC_WORKSPACE to the workspace folder on the PC}
CFG="$PC_WORKSPACE"'\driver\pkg\xrwired\bin\win64\xrwired.cfg'
LOG="$PC_WORKSPACE"'\driver\pkg\xrwired\bin\win64\driver_xrwired.log'
for scale in "$@"; do
  $SSH "powershell -NoProfile -Command \"\$p='$CFG'; (Get-Content \$p) -replace '^render_scale=.*','' | Where-Object {\$_ -ne ''} | Set-Content \$p; Add-Content \$p 'render_scale=$scale'\"" 2>/dev/null
  $SSH 'taskkill /F /IM vrmonitor.exe /IM vrserver.exe /IM vrcompositor.exe' >/dev/null 2>&1
  $SSH "del $LOG 2>nul & powershell -NoProfile -Command \"Start-ScheduledTask XRWiredSteamVR\"" >/dev/null 2>&1
  until $SSH "type $LOG 2>nul" 2>/dev/null | grep -q "encoded=432"; do sleep 5; done
  size=$($SSH "type $LOG" 2>/dev/null | grep -m1 "submitted eye" | tr -d '\r')
  enc=$($SSH "type $LOG" 2>/dev/null | grep "^frames=" | tail -8 | sed -E 's/.*encode=([0-9.]+) ms.*/\1/' | sort -n)
  p50=$(echo "$enc" | awk '{a[NR]=$1} END{print a[int(NR/2)+1]}')
  hi=$(echo "$enc" | tail -1)
  echo "render_scale=$scale | $size | encode p50 ${p50} ms  max ${hi} ms"
done
