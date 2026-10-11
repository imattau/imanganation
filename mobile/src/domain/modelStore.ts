import { registerPlugin, type PluginListenerHandle } from '@capacitor/core';

export interface LocalModel {
  id: string;
  name: string;
  fileName: string;
  path: string;
  sizeBytes: number;
  sha256: string;
  importedAt: string;
}

export interface ModelImportProgress {
  id: string;
  name: string;
  bytes: number;
  totalBytes: number;
}

interface ModelStorePlugin {
  importModel(options?: { expectedSha256?: string }): Promise<LocalModel>;
  listModels(): Promise<{ models: LocalModel[]; availableBytes: number }>;
  deleteModel(options: { id: string }): Promise<void>;
  addListener(eventName: 'importProgress', listener: (event: ModelImportProgress) => void): Promise<PluginListenerHandle>;
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
