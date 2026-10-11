cmake_minimum_required(VERSION 3.22.1)
project(imanganation_android LANGUAGES C CXX)

include(FetchContent)
set(SD_STANDALONE OFF CACHE BOOL "Build only the embedded renderer library" FORCE)
set(SD_BUILD_EXAMPLES OFF CACHE BOOL "Disable renderer CLI and server" FORCE)
set(SD_BUILD_SHARED_LIBS OFF CACHE BOOL "Link renderer into the JNI bridge" FORCE)
set(SD_WEBP OFF CACHE BOOL "Disable unused renderer WebP I/O" FORCE)
set(SD_WEBM OFF CACHE BOOL "Disable unused renderer WebM I/O" FORCE)
set(GGML_OPENMP OFF CACHE BOOL "OpenMP is unavailable on Android" FORCE)
set(GGML_NATIVE OFF CACHE BOOL "Build for the selected Android ABI" FORCE)

FetchContent_Declare(
    stable_diffusion_cpp
    GIT_REPOSITORY https://github.com/leejet/stable-diffusion.cpp.git
    GIT_TAG 2988060a4338a5dcd94721c964a8e87003c1ac8b
    GIT_SHALLOW FALSE
    GIT_SUBMODULES_RECURSE TRUE
)
FetchContent_MakeAvailable(stable_diffusion_cpp)

add_library(imanganation_renderer SHARED renderer_jni.cpp)
target_compile_features(imanganation_renderer PRIVATE cxx_std_17)
target_link_libraries(imanganation_renderer PRIVATE -Wl,--whole-archive stable-diffusion -Wl,--no-whole-archive log android)
