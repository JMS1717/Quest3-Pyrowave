# GPU load and queue probes

Tools for latency work on the streaming PC ([docs/LATENCY.md](../../../docs/LATENCY.md)).

- `gpuload.exe`: a game-like GPU load. It keeps the GPU busy for `--busy-ms` (default 5) every
  `--period-ms` (default 8.333, i.e. 120 Hz), calibrated with timestamp queries. It stops after
  `--seconds` or when `--stop-file` exists. Run it in the background while a live cell runs to see
  the streamer's stages under contention.
- `gpuload.exe --probe`: time a tiny D3D11 dispatch from submit to completion, while another
  `gpuload` runs.
- `vkprobe.exe`: the same for Vulkan, per queue kind: graphics or compute, at medium, high or
  realtime global priority.

Build from an x64 developer prompt (Visual Studio C++ tools, Vulkan SDK):

```
cl /O2 /EHsc gpuload.cpp d3d11.lib dxgi.lib d3dcompiler.lib
glslangValidator -V --vn kProbeSpv -o vkprobe_spv.h probe.comp
cl /O2 /EHsc vkprobe.cpp /I%VULKAN_SDK%\Include %VULKAN_SDK%\Lib\vulkan-1.lib
```
