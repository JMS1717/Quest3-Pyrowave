# dspprobe -- is the Hexagon DSP reachable from an app on the Galaxy XR?

Question: could PyroWave (or any decode stage) run on the cDSP/HVX instead of the Adreno, whose
decode fence balloons when hot? Six checks, probed from adb shell (uid 2000) with `dmabuf_probe`
(build with the NDK clang for `aarch64-linux-android30`, push to `/data/local/tmp`).

| # | check | result |
|---|---|---|
| 1 | cDSP/Hexagon exposed | **hardware present, node absent.** `remoteproc-cdsp-md`, the `qcom,cma-secure-cdsp` heap and QNN/SNPE HTP **V69** skeletons exist, but `/dev` has only `adsprpc-smd` (aDSP). No `cdsprpc-smd`, no `fastrpc-cdsp`. |
| 2 | our process can open the FastRPC node | **no.** `/dev/adsprpc-smd` is `crw-rw-r-- system:system` (`vendor_qdsp_device`); shell gets EACCES and an app uid is not in `system` either, so DAC blocks it before SELinux is consulted. `libcdsprpc.so` loads and exports `remote_handle64_open`, but has nothing to open. |
| 3 | allocate a DMA-BUF | **yes.** `/dev/dma_heap/system` (AOSP `dmabuf_system_heap_device`): 4 MiB alloc + mmap ok. |
| 4 | FastRPC map it into Hexagon | unreachable (blocked at 2). |
| 5 | run a trivial HVX kernel | unreachable; would also need the Hexagon SDK and a signed or unsigned-PD skeleton. |
| 6 | Adreno/Vulkan import without a CPU copy | **yes in principle.** `VK_KHR_external_memory_fd` and `VK_ANDROID_external_memory_android_hardware_buffer` are advertised (no `VK_EXT_external_memory_dma_buf`); a dma-buf reaches Vulkan as an AHardwareBuffer, which libpyroclient already imports. Moot without 2. |

**Conclusion:** the DSP route is closed to an unprivileged app on this firmware. It would need a
vendor-privileged process or root. The aDSP node being the only one exposed, alongside
`android.hardware.perceptionhost.farf` under `/vendor/lib/rfsa/adsp`, suggests the DSPs are
reserved for the platform's own perception/tracking stack.
