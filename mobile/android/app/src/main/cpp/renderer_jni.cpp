#include <jni.h>
#include "stable-diffusion.h"
#include <algorithm>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

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

extern "C" JNIEXPORT jbyteArray JNICALL
Java_io_github_imattau_imanganation_RendererPlugin_nativeGenerate(JNIEnv *env, jclass, jstring prompt,
                                                                   jstring negativePrompt, jint width,
                                                                   jint height, jlong seed, jint steps,
                                                                   jfloat guidance) {
    const char *promptText = env->GetStringUTFChars(prompt, nullptr);
    const char *negativeText = negativePrompt == nullptr ? nullptr : env->GetStringUTFChars(negativePrompt, nullptr);
    if (promptText == nullptr) return nullptr;

    std::lock_guard<std::mutex> lock(contextMutex);
    if (context == nullptr) {
        env->ReleaseStringUTFChars(prompt, promptText);
        if (negativeText != nullptr) env->ReleaseStringUTFChars(negativePrompt, negativeText);
        return nullptr;
    }
    sd_img_gen_params_t params;
    sd_img_gen_params_init(&params);
    params.prompt = promptText;
    params.negative_prompt = negativeText;
    params.width = width;
    params.height = height;
    params.seed = seed;
    params.batch_count = 1;
    sd_sample_params_init(&params.sample_params);
    params.sample_params.sample_steps = steps;
    params.sample_params.guidance.txt_cfg = guidance;

    sd_image_t *images = nullptr;
    int imageCount = 0;
    bool success = generate_image(context, &params, &images, &imageCount);
    env->ReleaseStringUTFChars(prompt, promptText);
    if (negativeText != nullptr) env->ReleaseStringUTFChars(negativePrompt, negativeText);
    if (!success || images == nullptr || imageCount < 1 || images[0].data == nullptr) {
        if (images != nullptr) free_sd_images(images, imageCount);
        return nullptr;
    }
    const size_t pixelCount = static_cast<size_t>(images[0].width) * images[0].height;
    const auto byteCount = static_cast<jsize>(pixelCount * 4);
    std::vector<jbyte> rgba(static_cast<size_t>(byteCount));
    for (size_t i = 0; i < pixelCount; ++i) {
        const uint8_t *source = images[0].data + i * images[0].channel;
        uint8_t red = source[0], green = source[0], blue = source[0], alpha = 255;
        if (images[0].channel >= 3) { red = source[0]; green = source[1]; blue = source[2]; }
        if (images[0].channel >= 4) alpha = source[3];
        rgba[i * 4] = static_cast<jbyte>(red);
        rgba[i * 4 + 1] = static_cast<jbyte>(green);
        rgba[i * 4 + 2] = static_cast<jbyte>(blue);
        rgba[i * 4 + 3] = static_cast<jbyte>(alpha);
    }
    jbyteArray output = env->NewByteArray(byteCount);
    if (output != nullptr) env->SetByteArrayRegion(output, 0, byteCount, rgba.data());
    free_sd_images(images, imageCount);
    return output;
}
