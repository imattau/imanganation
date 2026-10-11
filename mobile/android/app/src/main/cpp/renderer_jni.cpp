#include <jni.h>
#include "stable-diffusion.h"
#include <algorithm>
#include <mutex>
#include <string>
#include <thread>

namespace {
std::mutex contextMutex;
sd_ctx_t *context = nullptr;
std::string modelVersion;
}

extern "C" JNIEXPORT jstring JNICALL
Java_io_github_imattau_imanganation_RendererPlugin_nativeRuntimeVersion(JNIEnv *env, jclass) {
    const char *commit = sd_commit();
    return env->NewStringUTF(commit != nullptr ? commit : "unknown");
}

extern "C" JNIEXPORT jstring JNICALL
Java_io_github_imattau_imanganation_RendererPlugin_nativeLoadModel(JNIEnv *env, jclass, jstring path) {
    const char *modelPath = env->GetStringUTFChars(path, nullptr);
    if (modelPath == nullptr) return env->NewStringUTF("");

    std::lock_guard<std::mutex> lock(contextMutex);
    if (context != nullptr) {
        free_sd_ctx(context);
        context = nullptr;
        modelVersion.clear();
    }
    sd_ctx_params_t params;
    sd_ctx_params_init(&params);
    params.model_path = modelPath;
    params.n_threads = static_cast<int>(std::max(1u, std::thread::hardware_concurrency()));
    context = new_sd_ctx(&params);
    env->ReleaseStringUTFChars(path, modelPath);
    if (context == nullptr || !sd_ctx_supports_image_generation(context)) {
        if (context != nullptr) free_sd_ctx(context);
        context = nullptr;
        return env->NewStringUTF("");
    }
    const char *version = sd_get_model_version_name(context);
    modelVersion = version != nullptr ? version : "Unknown model";
    return env->NewStringUTF(modelVersion.c_str());
}

extern "C" JNIEXPORT void JNICALL
Java_io_github_imattau_imanganation_RendererPlugin_nativeUnloadModel(JNIEnv *, jclass) {
    std::lock_guard<std::mutex> lock(contextMutex);
    if (context != nullptr) free_sd_ctx(context);
    context = nullptr;
    modelVersion.clear();
}

extern "C" JNIEXPORT jstring JNICALL
Java_io_github_imattau_imanganation_RendererPlugin_nativeLoadedModelVersion(JNIEnv *env, jclass) {
    std::lock_guard<std::mutex> lock(contextMutex);
    return env->NewStringUTF(modelVersion.c_str());
}
