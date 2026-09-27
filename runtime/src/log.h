// Shared logging for the runtime: OutputDebugString plus a file next to the DLL (vrserver-style
// launches don't pass environment through, so we don't rely on env vars for the log path).
#pragma once

#include <string>

namespace xrw {
void log(const std::string& text);
}
