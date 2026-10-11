import { Capacitor, registerPlugin } from '@capacitor/core';

export type RenderOperation = 'render' | 'design' | 'refine' | 'inpaint';

export interface RenderRequest {
  operation: RenderOperation;
  projectId: string;
  panelId?: string;
  prompt: string;
  negativePrompt?: string;
  seed?: number;
  width?: number;
  height?: number;
  references?: Array<{ name: string; version?: string; uri: string; weight?: number }>;
  maskUri?: string;
  sourceUri?: string;
  options?: Record<string, unknown>;
}

export interface RendererCapabilities {
  available: boolean;
  engines: string[];
  operations: RenderOperation[];
  runtimePackaged?: boolean;
  runtimeVersion?: string;
  supportedAbis?: string[];
  totalMemoryBytes?: number;
  availableMemoryBytes?: number;
  lowMemory?: boolean;
  reason?: string;
}

interface NativeRendererPlugin {
  capabilities(): Promise<RendererCapabilities & { generationAvailable: boolean; engine?: string }>;
}

const NativeRenderer = registerPlugin<NativeRendererPlugin>('Renderer');

export interface RenderJob {
  id: string;
  state: 'queued' | 'loading' | 'running' | 'succeeded' | 'cancelled' | 'failed';
  progress: number;
  message?: string;
  resultUri?: string;
  error?: string;
}

/** Stable UI boundary; native runtime discovery is separate from generation readiness. */
export interface RendererAdapter {
  capabilities(): Promise<RendererCapabilities>;
  start(request: RenderRequest): Promise<RenderJob>;
  cancel(jobId: string): Promise<void>;
  subscribe(listener: (job: RenderJob) => void): () => void;
}

export class UnavailableRenderer implements RendererAdapter {
  async capabilities(): Promise<RendererCapabilities> {
    if (Capacitor.isNativePlatform() && Capacitor.getPlatform() === 'android') {
      try {
        const native = await NativeRenderer.capabilities();
        return {
          ...native,
          available: native.generationAvailable,
          engines: native.runtimePackaged && native.engine ? [native.engine] : [],
          operations: [],
        };
      } catch {
        return {
          available: false,
          engines: [],
          operations: [],
          reason: 'Could not inspect the Android native renderer runtime.',
        };
      }
    }
    return {
      available: false,
      engines: [],
      operations: [],
      reason: 'The native renderer is available only in the Android app.',
    };
  }
  async start(): Promise<RenderJob> {
    throw new Error('Local image generation is not available in this build.');
  }
  async cancel(): Promise<void> {}
  subscribe(): () => void { return () => undefined; }
}

export const renderer: RendererAdapter = new UnavailableRenderer();

