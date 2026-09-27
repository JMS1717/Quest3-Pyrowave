$log = "C:\Program Files (x86)\Steam\logs\vrserver.txt"
Get-Content $log -Tail 2500 |
    Select-String -Pattern "127\.0\.0\.1|manual IP|Could not initiate|handshake|Connection|9943|socket" |
    Select-Object -Last 200 | ForEach-Object { $_.Line }
