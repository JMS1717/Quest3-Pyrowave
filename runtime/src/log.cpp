#include "log.h"

#include <windows.h>

#include <mutex>
#include <share.h>

namespace xrw {

void log(const std::string& text) {
    const std::string line = "[xrwired-rt] " + text + "\n";
    OutputDebugStringA(line.c_str());

    static std::mutex file_mutex;
    std::lock_guard<std::mutex> lock(file_mutex);
    static FILE* file = nullptr;
    static bool opened = false;
    if (!opened) {
        opened = true;
        HMODULE module = nullptr;
        GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           reinterpret_cast<LPCSTR>(&log), &module);
        char path[MAX_PATH] = {};
        GetModuleFileNameA(module, path, MAX_PATH);
        std::string full(path);
        full = full.substr(0, full.find_last_of('\\')) + "\\xrwired_runtime.log";
        file = _fsopen(full.c_str(), "a", _SH_DENYNO);   // shared: readable live over ssh
    }
    if (file != nullptr) {
        fputs(line.c_str(), file);
        fflush(file);
    }
}

}  // namespace xrw
