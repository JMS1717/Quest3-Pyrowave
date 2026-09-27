# Third-party components vendored into receiver-xr

These are checked in (rather than resolved from Maven) so the app builds with
`gradle --offline` and never needs network access.

## OpenXR loader for Android

- **Component:** `libopenxr_loader.so` (arm64-v8a)
- **Upstream:** The Khronos Group — OpenXR SDK, published as the Maven artifact
  `org.khronos.openxr:openxr_loader_for_android:1.1.53`
- **License:** Apache License 2.0
- **Path in this repo:** `app/src/main/jniLibs/arm64-v8a/libopenxr_loader.so`
- **Obtained from:** the extracted `openxr_loader_for_android` AAR in a local Gradle cache
  (`.../transformed/openxr_loader_for_android-1.1.53/jni/arm64-v8a/libopenxr_loader.so`)

## OpenXR headers

- **Component:** `openxr.h`, `openxr_platform.h`, `openxr_platform_defines.h`
  (`XR_CURRENT_API_VERSION` 1.1.57)
- **Upstream:** The Khronos Group — OpenXR-SDK headers
- **License:** Apache License 2.0
- **Path in this repo:** `app/src/main/cpp/third_party/openxr/include/openxr/`
- **Obtained from:** the working reference project at
  the same AAR's `prefab` headers

CMake links the loader as an `IMPORTED SHARED` target and Gradle packages it from
`jniLibs`, so exactly one copy of the `.so` ends up in the APK.
