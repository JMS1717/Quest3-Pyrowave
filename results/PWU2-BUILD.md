# PWU2 build candidate — October 1, 2026

Both platforms built successfully from commit
`24aa67eeb724739d2dd989f00fcbcf424b46f4db` in
[GitHub Actions run 36880199142](https://github.com/JMS1717/Quest3-Pyrowave/actions/runs/36880199142).
Download the **Quest3-Pyrowave-Android** and **Quest3-Pyrowave-Windows** artifacts from that
same run. Both use protocol **20.13.0-quest3.pyro.5**. Older protocol .4 builds cannot pair.

| Check | Result |
|---|---|
| Python benchmark/control tests | 10 passed |
| Host decode-path checks | All passed |
| Client-core assembly/configuration/buffer tests | 15 passed |
| Session/schema/preset tests | 23 passed |
| Dashboard/report tests | 9 passed; 1 external-fixture test ignored |
| APK and Windows archive checksums/CRC | Verified |
| APK signing certificate | Matches the previous installed development certificate |
| Native client, driver and dashboard version | .5 present in each binary |
| Windows archive contents | Required binaries and licenses present; no session/log/signing-key files |
| New-build live VR acceptance | **Pending** |
| Sustained full-resolution 120 FPS | **Not demonstrated** |

SHA-256 of the original Actions files:

```text
d978b50bdf2d38fced9150ba7fa515a54b467f441bfe356878c68430d71b4f70  Quest3-Pyrowave-dev.apk
f558101f9d82eb0845bd65c49e9254d684b6e58d374bdb0b1676b1965fae7d96  Quest3-Pyrowave-Windows.zip
```

The repair sends byte fragments of a complete codec frame instead of assuming every codec
block fits the MTU. Reassembly and codec readiness must both pass before UDP presentation.
TCP also clears previous input and requires complete codec readiness. TCP is the default;
experimental PWU2 UDP does not have FEC or retransmission.

The full-resolution **400 Mbps / 72 Hz / 4:2:0 / TCP** profile is a conservative candidate.
**600 Mbps / 90 Hz** is the next candidate. **600–2000 Mbps / 120 Hz** and higher refresh
settings are experiments. The runtime capability gate still applies. Earlier native decode
checks exceeded the 8.33 ms budget in multiple cases; a passing build does not certify latency,
eye projection, sustained throughput, thermal behavior, or usable live streaming.

The owner's PCVR setup was left for Virtual Desktop use. These artifacts have not been installed
or registered. Hardware acceptance requires explicit permission before headset or SteamVR tests.
See [the corruption investigation](CORRUPTION-FIX.md) for earlier native readback evidence and limits.
