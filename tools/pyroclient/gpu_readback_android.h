#pragma once
#include <string>
#include <vector>
#include "gpu_readback_gles.h"
struct AHardwareBuffer;

// Producer fence must have completed and no decoder writes may run until return.
bool readback_android_buffer(AHardwareBuffer *buffer, bool flip_y,
                              std::vector<unsigned char> &rgba, std::string &error,
                              const q3pw::BeforeReadback &before = {}, uint32_t draw_repeats = 1);
