package io.github.imattau.imanganation;

import android.app.ActivityManager;
import android.content.Context;
import android.os.Build;

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
        result.put("reason", runtimeLoaded
            ? "Native inference library is packaged. Model loading and generation are not connected yet."
            : "Native inference library is not available for this device ABI.");
        call.resolve(result);
    }
}
