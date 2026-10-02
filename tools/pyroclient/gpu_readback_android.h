#pragma once
#include <string>
#include <vector>
struct AHardwareBuffer;

// Producer fence must have completed and no decoder writes may run until return.
bool readback_android_buffer(AHardwareBuffer *buffer, bool flip_y,
                              std::vector<unsigned char> &rgba, std::string &error);
