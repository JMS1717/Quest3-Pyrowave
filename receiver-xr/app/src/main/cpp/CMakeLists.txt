cmake_minimum_required(VERSION 3.22.1)
project(xrwired_xr LANGUAGES C CXX)

# The OpenXR loader is vendored (see ../../../../THIRD_PARTY.md) so the build never
# needs the network: headers live in third_party/, the .so in src/main/jniLibs/.
set(XRWIRED_OPENXR_INCLUDE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/third_party/openxr/include")
set(XRWIRED_JNILIBS_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../jniLibs/${ANDROID_ABI}")

add_library(openxr_loader SHARED IMPORTED)
set_target_properties(
    openxr_loader
    PROPERTIES IMPORTED_LOCATION "${XRWIRED_JNILIBS_DIR}/libopenxr_loader.so"
)

add_library(
    xrwired_xr
    SHARED
    xr_core.cpp
    stereo_renderer.cpp
)

target_compile_features(xrwired_xr PRIVATE cxx_std_17)
target_compile_definitions(xrwired_xr PRIVATE XR_USE_PLATFORM_ANDROID XR_USE_GRAPHICS_API_OPENGL_ES)
target_include_directories(xrwired_xr PRIVATE ${XRWIRED_OPENXR_INCLUDE_DIR})

find_library(log-lib log)
find_library(android-lib android)
find_library(egl-lib EGL)
find_library(glesv3-lib GLESv3)

target_link_libraries(
    xrwired_xr
    PRIVATE
    openxr_loader
    ${android-lib}
    ${log-lib}
    ${egl-lib}
    ${glesv3-lib}
)
