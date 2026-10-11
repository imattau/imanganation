import { registerPlugin, type PluginListenerHandle } from '@capacitor/core';

export interface LocalModel {
  id: string;
  name: string;
  fileName: string;
  path: string;
  sizeBytes: number;
  sha256: string;
  importedAt: string;
  downloadKey?: string;
  origin?: string;
  licenseName?: string;
  licenseUrl?: string;
  sourceUrl?: string;
}

export interface ModelImportProgress {
  id: string;
  name: string;
  bytes: number;
  totalBytes: number;
}

export interface ModelDownloadProgress extends ModelImportProgress {
  downloadKey: string;
  state: 'downloading' | 'verifying' | 'complete' | 'cancelled' | 'failed';
  error?: string;
}

export interface ModelDownloadRequest {
  downloadKey: string;
  name: string;
  url: string;
  sizeBytes: number;
  sha256: string;
  licenseName: string;
  licenseUrl: string;
  sourceUrl: string;
}

interface ModelStorePlugin {
  importModel(options?: { expectedSha256?: string }): Promise<LocalModel>;
  downloadModel(request: ModelDownloadRequest): Promise<{ downloadKey: string; state: 'downloading' }>;
  cancelDownload(options: { downloadKey: string }): Promise<void>;
  listModels(): Promise<{ models: LocalModel[]; availableBytes: number }>;
  deleteModel(options: { id: string }): Promise<void>;
  addListener(eventName: 'importProgress', listener: (event: ModelImportProgress) => void): Promise<PluginListenerHandle>;
  addListener(eventName: 'downloadProgress', listener: (event: ModelDownloadProgress) => void): Promise<PluginListenerHandle>;
  addListener(eventName: 'downloadComplete', listener: (event: LocalModel & { downloadKey: string }) => void): Promise<PluginListenerHandle>;
}

export const modelStore = registerPlugin<ModelStorePlugin>('ModelStore');

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return 'Unknown size';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
  return `${value.toFixed(value >= 10 ? 1 : 2)} ${units[unit]}`;
}
