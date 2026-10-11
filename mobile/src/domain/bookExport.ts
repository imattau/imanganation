import { Capacitor } from '@capacitor/core';
import { Directory, Filesystem } from '@capacitor/filesystem';
import { Share } from '@capacitor/share';
import { strToU8, zipSync } from 'fflate';
import type { MangaProject } from './project';
import { getPageAsset, oraToFiles } from './pageAssets';

type ExportFormat = 'cbz' | 'pdf' | 'pages-zip';

export async function exportBook(project: MangaProject, format: ExportFormat): Promise<void> {
  if (!project.pages.length) throw new Error('Add at least one page before exporting.');
  const images: Array<{ name: string; data: string; width: number; height: number; bytes: Uint8Array }> = [];
  for (const [index, page] of project.pages.entries()) {
    if (!page.file.toLowerCase().endsWith('.ora')) throw new Error(`${page.label} is an XCF page. Android preserves XCF files but cannot render them for export.`);
    const archive = await getPageAsset(project.project.id, page.id);
    if (!archive) throw new Error(`${page.label} has no local page asset.`);
    const preview = oraToFiles(archive)['mergedimage.png'];
    if (!preview) throw new Error(`${page.label} has no merged page preview.`);
    const data = `data:image/png;base64,${toBase64(preview)}`;
    const dimensions = await readDimensions(data);
    images.push({ name: `page-${String(index + 1).padStart(3, '0')}.png`, data, ...dimensions, bytes: preview });
  }
  const safeTitle = safeFilename(project.project.title);
  if (format === 'pdf') {
    const { jsPDF } = await import('jspdf');
    const first = images[0];
    const pdf = new jsPDF({ orientation: first.width >= first.height ? 'landscape' : 'portrait', unit: 'px', format: [first.width, first.height], hotfixes: ['px_scaling'] });
    images.forEach((page, index) => {
      if (index) pdf.addPage([page.width, page.height], page.width >= page.height ? 'landscape' : 'portrait');
      pdf.addImage(page.data, 'PNG', 0, 0, page.width, page.height, undefined, 'FAST');
    });
    await deliver(new Uint8Array(pdf.output('arraybuffer')), `${safeTitle}.pdf`, 'application/pdf');
    return;
  }
  const files: Record<string, Uint8Array> = {};
  for (const page of images) files[page.name] = page.bytes;
  if (format === 'cbz') {
    files['ComicInfo.xml'] = strToU8(`<?xml version="1.0" encoding="utf-8"?>\n<ComicInfo><Title>${xmlEscape(project.project.title)}</Title><Number>${xmlEscape(project.project.chapter ?? '')}</Number><PageCount>${images.length}</PageCount><Manga>${project.project.reading_order === 'rtl' ? 'YesAndRightToLeft' : 'No'}</Manga></ComicInfo>\n`);
  }
  await deliver(zipSync(files, { level: 0 }), `${safeTitle}.${format === 'cbz' ? 'cbz' : 'pages.zip'}`, 'application/zip');
}

async function deliver(bytes: Uint8Array, filename: string, mimeType: string): Promise<void> {
  if (Capacitor.isNativePlatform()) {
    const result = await Filesystem.writeFile({ path: filename, data: toBase64(bytes), directory: Directory.Cache });
    await Share.share({ title: filename, url: result.uri, dialogTitle: 'Export manga' });
    return;
  }
  const url = URL.createObjectURL(new Blob([Uint8Array.from(bytes).buffer as ArrayBuffer], { type: mimeType }));
  const link = document.createElement('a');
  link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function readDimensions(src: string): Promise<{ width: number; height: number }> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
    image.onerror = () => reject(new Error('Could not decode an exported page image.'));
    image.src = src;
  });
}
function safeFilename(value: string): string { return value.trim().replace(/[^\p{L}\p{N}_-]+/gu, '-').replace(/^-|-$/g, '') || 'manga'; }
function xmlEscape(value: string): string { return value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&apos;'); }
function toBase64(bytes: Uint8Array): string {
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}
