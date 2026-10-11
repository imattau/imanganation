import { zipSync } from 'fflate';
import { Capacitor } from '@capacitor/core';
import { Filesystem, Directory } from '@capacitor/filesystem';
import { Share } from '@capacitor/share';
import type { MangaProject } from './project';
import { getPageAsset, renderOraPreview } from './pageAssets';

export const VERTICAL_PRESETS = {
  custom: { label: 'Custom', width: 800, sliceHeight: 1280 },
  webtoon: { label: 'WEBTOON CANVAS', width: 800, sliceHeight: 1280 },
  tapas: { label: 'Tapas', width: 940, sliceHeight: 5000 },
} as const;

function imageFromBytes(bytes: Uint8Array): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    const url = URL.createObjectURL(new Blob([Uint8Array.from(bytes).buffer as ArrayBuffer], { type: 'image/png' }));
    image.onload = () => { URL.revokeObjectURL(url); resolve(image); };
    image.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Could not read an OpenRaster page preview.')); };
    image.src = url;
  });
}

function canvasPng(canvas: HTMLCanvasElement): Promise<Uint8Array> {
  return new Promise((resolve, reject) => canvas.toBlob(async (blob) => {
    if (!blob) return reject(new Error('Could not create an export slice.'));
    resolve(new Uint8Array(await blob.arrayBuffer()));
  }, 'image/png'));
}

export async function makeVerticalComic(project: MangaProject, width: number, sliceHeight: number, gap = 48): Promise<Uint8Array[]> {
  if (!Number.isInteger(width) || width < 1 || width > 10000) throw new Error('Export width must be between 1 and 10,000 pixels.');
  if (!Number.isInteger(sliceHeight) || sliceHeight < 1 || sliceHeight > 50000) throw new Error('Slice height must be between 1 and 50,000 pixels.');
  if (!Number.isInteger(gap) || gap < 0 || gap > 2000) throw new Error('Page gap must be between 0 and 2,000 pixels.');
  if (!project.pages.length) throw new Error('Add at least one page before exporting.');

  const ordered = project.pages;
  const images: HTMLImageElement[] = [];
  for (const page of ordered) {
    const bytes = await getPageAsset(project.project.id, page.id);
    if (!bytes) throw new Error(`${page.label} is missing its local page asset.`);
    const preview = await renderOraPreview(bytes, page.layer_state, page.mobile_strokes, page.layer_stack);
    images.push(await imageFromBytes(preview));
  }

  const pageHeights = images.map((image) => Math.max(1, Math.round(image.naturalHeight * width / image.naturalWidth)));
  const fullHeight = pageHeights.reduce((sum, height) => sum + height, 0) + gap * Math.max(0, pageHeights.length - 1);
  const combined = document.createElement('canvas');
  combined.width = width;
  combined.height = fullHeight;
  const context = combined.getContext('2d');
  if (!context) throw new Error('Canvas rendering is unavailable.');
  context.fillStyle = '#ffffff';
  context.fillRect(0, 0, width, fullHeight);
  let y = 0;
  images.forEach((image, index) => {
    context.drawImage(image, 0, y, width, pageHeights[index]);
    y += pageHeights[index] + gap;
  });

  const slices: Uint8Array[] = [];
  for (let top = 0; top < fullHeight; top += sliceHeight) {
    const height = Math.min(sliceHeight, fullHeight - top);
    const slice = document.createElement('canvas');
    slice.width = width;
    slice.height = height;
    slice.getContext('2d')?.drawImage(combined, 0, top, width, height, 0, 0, width, height);
    slices.push(await canvasPng(slice));
  }
  return slices;
}

export async function saveVerticalArchive(title: string, slices: Uint8Array[]): Promise<void> {
  const safeTitle = title.trim().replace(/[^\p{L}\p{N}_-]+/gu, '-').replace(/^-|-$/g, '') || 'episode';
  const files: Record<string, Uint8Array> = {};
  slices.forEach((bytes, index) => { files[`${safeTitle}-${String(index + 1).padStart(3, '0')}.png`] = bytes; });
  const archive = zipSync(files, { level: 0 });
  const filename = `${safeTitle}-vertical.zip`;
  if (Capacitor.isNativePlatform()) {
    const saved = await Filesystem.writeFile({ path: filename, data: toBase64(archive), directory: Directory.Cache });
    await Share.share({ title: 'Vertical comic export', url: saved.uri, dialogTitle: 'Save or share your comic' });
    return;
  }
  const url = URL.createObjectURL(new Blob([Uint8Array.from(archive).buffer as ArrayBuffer], { type: 'application/zip' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function toBase64(bytes: Uint8Array): string {
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  return btoa(binary);
}
