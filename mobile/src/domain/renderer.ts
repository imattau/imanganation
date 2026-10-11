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
  modelLoaded?: boolean;
  modelVersion?: string;
  reason?: string;
}

interface NativeRendererPlugin {
  capabilities(): Promise<RendererCapabilities & { generationAvailable: boolean; engine?: string }>;
  loadModel(options: { path: string }): Promise<{ modelVersion: string }>;
  unloadModel(): Promise<void>;
  generate(options: { prompt: string; negativePrompt?: string; width: number; height: number; seed?: number; steps?: number; guidance?: number }): Promise<NativeImageResult>;
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

export interface NativeImageResult {
  dataUrl: string;
  width: number;
  height: number;
  modelVersion: string;
}

/** Stable UI boundary; native runtime discovery is separate from generation readiness. */
export interface RendererAdapter {
  capabilities(): Promise<RendererCapabilities>;
  loadModel(path: string): Promise<string>;
  unloadModel(): Promise<void>;
  generate(options: { prompt: string; negativePrompt?: string; width?: number; height?: number; seed?: number; steps?: number; guidance?: number }): Promise<NativeImageResult>;
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
          operations: native.generationAvailable ? ['render'] : [],
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
  async loadModel(path: string): Promise<string> {
    if (!Capacitor.isNativePlatform() || Capacitor.getPlatform() !== 'android') throw new Error('Model loading is available in the Android app.');
    const result = await NativeRenderer.loadModel({ path });
    return result.modelVersion;
  }
  async unloadModel(): Promise<void> {
    if (Capacitor.isNativePlatform() && Capacitor.getPlatform() === 'android') await NativeRenderer.unloadModel();
  }
  async generate(options: { prompt: string; negativePrompt?: string; width?: number; height?: number; seed?: number; steps?: number; guidance?: number }): Promise<NativeImageResult> {
    if (!Capacitor.isNativePlatform() || Capacitor.getPlatform() !== 'android') throw new Error('On-device generation is available only in the Android app.');
    return NativeRenderer.generate({ prompt: options.prompt, negativePrompt: options.negativePrompt, width: options.width ?? 512, height: options.height ?? 512, seed: options.seed, steps: options.steps, guidance: options.guidance });
  }
  async start(request: RenderRequest): Promise<RenderJob> {
    if (request.operation !== 'render') throw new Error('This Android renderer currently supports text-to-image renders only.');
    const result = await this.generate({ prompt: request.prompt, negativePrompt: request.negativePrompt, width: request.width, height: request.height, seed: request.seed });
    return { id: `render_${crypto.randomUUID()}`, state: 'succeeded', progress: 100, resultUri: result.dataUrl, message: `Rendered with ${result.modelVersion}.` };
  }
  async cancel(): Promise<void> { throw new Error('This renderer build does not support interrupting an in-progress native render yet.'); }
  subscribe(): () => void { return () => undefined; }
}

export const renderer: RendererAdapter = new UnavailableRenderer();

