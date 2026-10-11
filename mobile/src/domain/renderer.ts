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
  reason?: string;
}

export interface RenderJob {
  id: string;
  state: 'queued' | 'loading' | 'running' | 'succeeded' | 'cancelled' | 'failed';
  progress: number;
  message?: string;
  resultUri?: string;
  error?: string;
}

/** Stable UI boundary; Android runtime implementation will be registered behind this interface. */
export interface RendererAdapter {
  capabilities(): Promise<RendererCapabilities>;
  start(request: RenderRequest): Promise<RenderJob>;
  cancel(jobId: string): Promise<void>;
  subscribe(listener: (job: RenderJob) => void): () => void;
}

export class UnavailableRenderer implements RendererAdapter {
  async capabilities(): Promise<RendererCapabilities> {
    return {
      available: false,
      engines: [],
      operations: [],
      reason: 'The Android image-generation runtime has not been integrated yet.',
    };
  }
  async start(): Promise<RenderJob> {
    throw new Error('Local image generation is not available in this build.');
  }
  async cancel(): Promise<void> {}
  subscribe(): () => void { return () => undefined; }
}

export const renderer: RendererAdapter = new UnavailableRenderer();
