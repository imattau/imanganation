package io.github.imattau.imanganation;

import android.app.ActivityManager;
import android.graphics.Bitmap;
import android.content.Context;
import android.os.Build;
import java.io.File;
import java.io.ByteArrayOutputStream;
import android.util.Base64;
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
    private static native byte[] nativeGenerate(String prompt, String negativePrompt, int width, int height, long seed, int steps, float guidance);

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
        String loadedVersion = runtimeLoaded ? nativeLoadedModelVersion() : "";
        result.put("modelLoaded", !loadedVersion.isEmpty());
        result.put("modelVersion", loadedVersion);
        result.put("generationAvailable", runtimeLoaded && !loadedVersion.isEmpty());
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

    @PluginMethod
    public void generate(PluginCall call) {
        String prompt = call.getString("prompt");
        String negativePrompt = call.getString("negativePrompt", "");
        int width = call.getInt("width", 512);
        int height = call.getInt("height", 512);
        int steps = call.getInt("steps", 20);
        float guidance = call.getDouble("guidance", 7.0).floatValue();
        long seed = call.getLong("seed", -1L);
        if (prompt == null || prompt.trim().isEmpty() || prompt.length() > 4000) {
            call.reject("Enter a prompt of 1 to 4000 characters.");
            return;
        }
        if (width < 256 || width > 1024 || height < 256 || height > 1024 || width % 64 != 0 || height % 64 != 0) {
            call.reject("Image dimensions must be multiples of 64 between 256 and 1024.");
            return;
        }
        if (steps < 1 || steps > 50 || guidance < 1 || guidance > 20) {
            call.reject("Sampling steps or guidance scale are outside the supported range.");
            return;
        }
        if (!runtimeLoaded || nativeLoadedModelVersion().isEmpty()) {
            call.reject("Load a compatible model before generating.");
            return;
        }
        call.setKeepAlive(true);
        modelExecutor.execute(() -> {
            Bitmap bitmap = null;
            try {
                byte[] rgba = nativeGenerate(prompt, negativePrompt, width, height, seed, steps, guidance);
                if (rgba == null || rgba.length != width * height * 4) throw new Exception("Renderer returned no image.");
                int[] pixels = new int[width * height];
                for (int i = 0; i < pixels.length; i++) {
                    int offset = i * 4;
                    pixels[i] = ((rgba[offset + 3] & 0xff) << 24)
                        | ((rgba[offset] & 0xff) << 16)
                        | ((rgba[offset + 1] & 0xff) << 8)
                        | (rgba[offset + 2] & 0xff);
                }
                bitmap = Bitmap.createBitmap(pixels, width, height, Bitmap.Config.ARGB_8888);
                ByteArrayOutputStream png = new ByteArrayOutputStream();
                if (!bitmap.compress(Bitmap.CompressFormat.PNG, 100, png)) throw new Exception("Could not encode the rendered image.");
                JSObject result = new JSObject();
                result.put("dataUrl", "data:image/png;base64," + Base64.encodeToString(png.toByteArray(), Base64.NO_WRAP));
                result.put("width", width);
                result.put("height", height);
                result.put("modelVersion", nativeLoadedModelVersion());
                call.resolve(result);
            } catch (Exception error) {
                call.reject("Image generation failed: " + error.getMessage(), error);
            } finally {
                if (bitmap != null) bitmap.recycle();
            }
        });
    }
}
