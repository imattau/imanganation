import { Capacitor } from '@capacitor/core';
import { Directory, Filesystem } from '@capacitor/filesystem';
import { Share } from '@capacitor/share';
import { strToU8, unzipSync, zipSync } from 'fflate';
import { isMangaProject } from './project';
import { getPageAsset, listProjectFiles, savePageAsset, saveProjectFile } from './pageAssets';
import { saveProject, type SavedProject } from './repository';

export async function importProjectArchive(file: File): Promise<SavedProject> {
  const archive = unzipSync(new Uint8Array(await file.arrayBuffer()));
  const manifestPath = Object.keys(archive).find((path) => path === 'project.json' || path.endsWith('/project.json'));
  const manifestBytes = manifestPath ? archive[manifestPath] : undefined;
  if (!manifestBytes || !manifestPath) throw new Error('This archive does not contain project.json. Select a ZIP of an .imanga project folder.');
  let value: unknown;
  try { value = JSON.parse(new TextDecoder().decode(manifestBytes)); }
  catch { throw new Error('The project manifest is not valid JSON.'); }
  if (!isMangaProject(value)) throw new Error('This app can import project-container version 1 files only.');
  const manifest = value;
  const prefix = manifestPath.slice(0, -'project.json'.length);
  let script = '';
  let revisions: SavedProject['revisions'] = [];
  const scriptPath = typeof manifest.script === 'object' && manifest.script && 'file' in manifest.script
    ? String(manifest.script.file) : '';
  for (const [entryPath, bytes] of Object.entries(archive)) {
    if (entryPath.endsWith('/')) continue;
    const relativePath = entryPath.startsWith(prefix) ? entryPath.slice(prefix.length) : entryPath;
    if (!safeRelativePath(relativePath)) throw new Error(`The project archive contains an unsafe path: ${relativePath}`);
    if (relativePath === 'project.json') continue;
    if (relativePath === scriptPath) script = new TextDecoder().decode(bytes);
    if (relativePath === 'script/revisions.json') {
      try {
        const value: unknown = JSON.parse(new TextDecoder().decode(bytes));
        if (Array.isArray(value)) revisions = value.filter((item): item is { created: string; script: string } => !!item && typeof item.created === 'string' && typeof item.script === 'string').slice(-30);
      } catch { /* Preserve the unknown file in the archive store; ignore malformed optional history. */ }
    }
    await saveProjectFile(manifest.project.id, relativePath, bytes);
  }
  for (const page of manifest.pages) {
    if (!page.file.toLowerCase().endsWith('.ora')) continue;
    const bytes = archive[`${prefix}${page.file}`] ?? archive[page.file];
    if (bytes) await savePageAsset(manifest.project.id, page.id, bytes);
  }
  const saved = { manifest, script, savedAt: new Date().toISOString(), revisions };
  await saveProject(saved);
  return saved;
}

export async function exportProjectArchive(saved: SavedProject): Promise<void> {
  const files: Record<string, Uint8Array> = {};
  for (const file of await listProjectFiles(saved.manifest.project.id)) {
    if (safeRelativePath(file.path) && file.path !== 'project.json') files[file.path] = file.bytes;
  }
  const scriptRecord = saved.manifest.script as { file?: string } | undefined;
  const scriptPath = scriptRecord?.file || 'script/story.md';
  files[scriptPath] = strToU8(saved.script);
  for (const page of saved.manifest.pages) {
    if (!page.file.toLowerCase().endsWith('.ora')) continue;
    const asset = await getPageAsset(saved.manifest.project.id, page.id);
    if (asset) files[page.file] = asset;
  }
  files['project.json'] = strToU8(JSON.stringify(saved.manifest, null, 2) + '\n');
  const bytes = zipSync(files, { level: 0 });
  const safeTitle = saved.manifest.project.title.replace(/[^\p{L}\p{N}_-]+/gu, '-').replace(/^-|-$/g, '') || 'manga-project';
  const filename = `${safeTitle}.imanga.zip`;
  if (Capacitor.isNativePlatform()) {
    const result = await Filesystem.writeFile({ path: filename, data: toBase64(bytes), directory: Directory.Cache });
    await Share.share({ title: saved.manifest.project.title, url: result.uri, dialogTitle: 'Export project archive' });
    return;
  }
  download(bytes, filename, 'application/zip');
}

function safeRelativePath(path: string): boolean {
  return path.length > 0 && !path.startsWith('/') && !path.includes('\\')
    && path.split('/').every((part) => part !== '..' && part !== '.');
}

function toBase64(bytes: Uint8Array): string {
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

function download(bytes: Uint8Array, filename: string, type: string): void {
  const url = URL.createObjectURL(new Blob([Uint8Array.from(bytes).buffer as ArrayBuffer], { type }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
