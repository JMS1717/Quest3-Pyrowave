// Probe: can this process allocate a DMA-BUF from /dev/dma_heap/system, mmap it, and open the
// cDSP FastRPC node? Run as shell (uid 2000) via adb; the app domain may differ.
#include <fcntl.h>
#include <linux/dma-heap.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <errno.h>
#include <dlfcn.h>
int main() {
    int h = open("/dev/dma_heap/system", O_RDONLY | O_CLOEXEC);
    printf("open /dev/dma_heap/system: %s\n", h < 0 ? strerror(errno) : "ok");
    if (h >= 0) {
        struct dma_heap_allocation_data d = { .len = 4 << 20, .fd_flags = O_RDWR | O_CLOEXEC };
        int r = ioctl(h, DMA_HEAP_IOCTL_ALLOC, &d);
        printf("alloc 4 MiB: %s (fd %d)\n", r < 0 ? strerror(errno) : "ok", r < 0 ? -1 : (int)d.fd);
        if (r >= 0) {
            void* p = mmap(NULL, 4 << 20, PROT_READ | PROT_WRITE, MAP_SHARED, d.fd, 0);
            printf("mmap: %s\n", p == MAP_FAILED ? strerror(errno) : "ok");
            if (p != MAP_FAILED) { memset(p, 0xAB, 4096); munmap(p, 4 << 20); }
            close(d.fd);
        }
        close(h);
    }
    for (const char* n : (const char*[]){"/dev/adsprpc-smd", "/dev/cdsprpc-smd", "/dev/fastrpc-cdsp"}) {
        int f = open(n, O_RDWR | O_NONBLOCK);
        printf("open %s rw: %s\n", n, f < 0 ? strerror(errno) : "ok");
        if (f >= 0) close(f);
    }
    void* lib = dlopen("libcdsprpc.so", RTLD_NOW);
    printf("dlopen libcdsprpc.so: %s\n", lib ? "ok" : dlerror());
    if (lib) {
        void* f = dlsym(lib, "remote_handle64_open");
        printf("remote_handle64_open symbol: %s\n", f ? "ok" : "missing");
    }
    return 0;
}
