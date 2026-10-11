package io.github.imattau.imanganation;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(android.os.Bundle savedInstanceState) {
        registerPlugin(ModelStorePlugin.class);
        registerPlugin(RendererPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
