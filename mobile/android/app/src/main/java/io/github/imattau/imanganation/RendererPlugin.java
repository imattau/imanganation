package io.github.imattau.imanganation;

import android.app.ActivityManager;
import android.content.Context;
import android.os.Build;
import java.io.File;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

@CapacitorPlugin(name = "Renderer")
public class RendererPlugin extends Plugin {
    private static final String ENGINE = "stable-diffusion.cpp";
    private static boolean runtimeLoaded;
    private static String runtimeVersion = "";
    private final ExecutorService modelExecutor = Executors.newSingleThreadExecutor();

    static {
        try {
            System.loadLibrary("imanganation_renderer");
            runtimeVersion = nativeRuntimeVersion();
            runtimeLoaded = true;
        } catch (UnsatisfiedLinkError error) {
            runtimeLoaded = false;
        }
    }

    private static native String nativeRuntimeVersion();
    private static native String nativeLoadModel(String path);
    private static native void nativeUnloadModel();
    private static native String nativeLoadedModelVersion();

    @PluginMethod
    public void capabilities(PluginCall call) {
        ActivityManager manager = (ActivityManager) getContext().getSystemService(Context.ACTIVITY_SERVICE);
        ActivityManager.MemoryInfo memoryInfo = new ActivityManager.MemoryInfo();
        manager.getMemoryInfo(memoryInfo);
        JSArray abis = new JSArray();
        for (String abi : Build.SUPPORTED_ABIS) abis.put(abi);

        JSObject result = new JSObject();
        result.put("runtimePackaged", runtimeLoaded);
        result.put("engine", runtimeLoaded ? ENGINE : "");
        result.put("runtimeVersion", runtimeVersion);
        result.put("supportedAbis", abis);
        result.put("totalMemoryBytes", memoryInfo.totalMem);
        result.put("availableMemoryBytes", memoryInfo.availMem);
        result.put("lowMemory", memoryInfo.lowMemory);
        result.put("generationAvailable", false);
        String loadedVersion = runtimeLoaded ? nativeLoadedModelVersion() : "";
        result.put("modelLoaded", !loadedVersion.isEmpty());
        result.put("modelVersion", loadedVersion);
        result.put("reason", runtimeLoaded
            ? "Native inference library is packaged. Model loading and generation are not connected yet."
            : "Native inference library is not available for this device ABI.");
        call.resolve(result);
    }

    @PluginMethod
    public void loadModel(PluginCall call) {
        String path = call.getString("path");
        if (!runtimeLoaded) {
            call.reject("Native renderer runtime is unavailable on this device.");
            return;
        }
        if (path == null) {
            call.reject("A model file path is required.");
            return;
        }
        try {
            File modelsDir = new File(getContext().getFilesDir(), "models").getCanonicalFile();
            File modelFile = new File(path).getCanonicalFile();
            if (!modelFile.isFile() || !modelFile.getPath().startsWith(modelsDir.getPath() + File.separator)) {
                call.reject("Choose a model stored in the app's model library.");
                return;
            }
        } catch (Exception error) {
            call.reject("Could not validate the model file path.", error);
            return;
        }
        call.setKeepAlive(true);
        modelExecutor.execute(() -> {
            try {
                String version = nativeLoadModel(path);
                if (version == null || version.isEmpty()) {
                    call.reject("The native renderer could not load this model. Check model format and available memory.");
                } else {
                    JSObject result = new JSObject();
                    result.put("modelVersion", version);
                    call.resolve(result);
                }
            } catch (Exception error) {
                call.reject("Model loading failed: " + error.getMessage(), error);
            }
        });
    }

    @PluginMethod
    public void unloadModel(PluginCall call) {
        if (runtimeLoaded) nativeUnloadModel();
        call.resolve();
    }
}
