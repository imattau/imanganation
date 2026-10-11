#include <jni.h>
#include "stable-diffusion.h"

extern "C" JNIEXPORT jstring JNICALL
Java_io_github_imattau_imanganation_RendererPlugin_nativeRuntimeVersion(JNIEnv *env, jclass) {
    const char *commit = sd_commit();
    return env->NewStringUTF(commit != nullptr ? commit : "unknown");
}
