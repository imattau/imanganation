import { strToU8, unzipSync, zipSync } from 'fflate';
import { flattenLayerStack } from './layerStack';
import type { LayerStackNode } from './project';

const DB_NAME = 'imanganation-mobile';
const STORE = 'page-assets';
const FILE_STORE = 'project-files';

function openAssetDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 2);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE)) {
        request.result.createObjectStore(STORE, { keyPath: 'key' });
      }
      if (!request.result.objectStoreNames.contains(FILE_STORE)) {
        request.result.createObjectStore(FILE_STORE, { keyPath: 'key' });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error('Could not open local page storage.'));
  });
}

export async function saveProjectFile(projectId: string, path: string, bytes: Uint8Array): Promise<void> {
  const db = await openAssetDb();
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(FILE_STORE, 'readwrite');
      tx.objectStore(FILE_STORE).put({ key: `${projectId}/${path}`, bytes });
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error ?? new Error(`Could not save ${path}.`));
    });
  } finally { db.close(); }
}

export async function listProjectFiles(projectId: string): Promise<Array<{ path: string; bytes: Uint8Array }>> {
  const db = await openAssetDb();
  try {
    return await new Promise((resolve, reject) => {
      const request = db.transaction(FILE_STORE, 'readonly').objectStore(FILE_STORE).getAll();
      request.onsuccess = () => resolve((request.result as Array<{ key: string; bytes: Uint8Array }>)
        .filter((entry) => entry.key.startsWith(`${projectId}/`))
        .map((entry) => ({ path: entry.key.slice(projectId.length + 1), bytes: entry.bytes })));
      request.onerror = () => reject(request.error ?? new Error('Could not read project files.'));
    });
  } finally { db.close(); }
}

export async function getProjectFile(projectId: string, path: string): Promise<Uint8Array | null> {
  const db = await openAssetDb();
  try {
    return await new Promise((resolve, reject) => {
      const request = db.transaction(FILE_STORE, 'readonly').objectStore(FILE_STORE).get(`${projectId}/${path}`);
      request.onsuccess = () => resolve(request.result?.bytes ?? null);
      request.onerror = () => reject(request.error ?? new Error(`Could not read ${path}.`));
    });
  } finally { db.close(); }
}

export async function deleteProjectAssets(projectId: string): Promise<void> {
  const db = await openAssetDb();
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction([STORE, FILE_STORE], 'readwrite');
      for (const storeName of [STORE, FILE_STORE]) {
        const store = tx.objectStore(storeName);
        const request = store.getAllKeys();
        request.onsuccess = () => {
          for (const key of request.result) if (typeof key === 'string' && key.startsWith(`${projectId}/`)) store.delete(key);
        };
      }
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error ?? new Error('Could not remove project assets.'));
    });
  } finally { db.close(); }
}

export async function savePageAsset(projectId: string, pageId: string, bytes: Uint8Array): Promise<void> {
  const db = await openAssetDb();
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).put({ key: `${projectId}/${pageId}`, bytes });
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error ?? new Error('Could not save the page file.'));
    });
  } finally { db.close(); }
}

export async function getPageAsset(projectId: string, pageId: string): Promise<Uint8Array | null> {
  const db = await openAssetDb();
  try {
    return await new Promise((resolve, reject) => {
      const request = db.transaction(STORE, 'readonly').objectStore(STORE).get(`${projectId}/${pageId}`);
      request.onsuccess = () => resolve(request.result?.bytes ?? null);
      request.onerror = () => reject(request.error ?? new Error('Could not read the page file.'));
    });
  } finally { db.close(); }
}

function canvasPng(width: number, height: number, draw?: (context: CanvasRenderingContext2D) => void): Promise<Uint8Array> {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d');
  if (!context) return Promise.reject(new Error('Canvas rendering is unavailable.'));
  if (draw) draw(context);
  return new Promise((resolve, reject) => canvas.toBlob(async (blob) => {
    if (!blob) return reject(new Error('Could not encode the page image.'));
    resolve(new Uint8Array(await blob.arrayBuffer()));
  }, 'image/png'));
}

export async function createBlankOra(width = 1200, height = 1800): Promise<Uint8Array> {
  const preview = await canvasPng(width, height, (context) => {
    context.fillStyle = '#ffffff';
    context.fillRect(0, 0, width, height);
  });
  const transparentLayer = await canvasPng(width, height);
  const thumbScale = Math.min(256 / width, 256 / height);
  const thumbnail = await canvasPng(Math.max(1, Math.round(width * thumbScale)), Math.max(1, Math.round(height * thumbScale)), (context) => {
    context.fillStyle = '#fff';
    context.fillRect(0, 0, context.canvas.width, context.canvas.height);
  });
  const stackXml = `<?xml version="1.0" encoding="UTF-8"?><image version="0.0.3" w="${width}" h="${height}" xres="300" yres="300"><stack><layer name="Artwork" src="data/layer0.png" opacity="1.0" visibility="visible" composite-op="svg:src-over"/></stack></image>`;
  return zipSync({
    mimetype: [strToU8('image/openraster'), { level: 0 }],
    'stack.xml': strToU8(stackXml),
    'mergedimage.png': preview,
    'Thumbnails/thumbnail.png': thumbnail,
    'data/layer0.png': transparentLayer,
  }, { level: 0 });
}

export async function composePageOra(
  projectId: string,
  pageId: string,
  panels: Array<{ id: string; label: { page: number; panel: number }; action: string; placement?: { page: string; frame: [number, number, number, number] } | null; active_take?: string | null; takes?: string[] }>,
  takes: Record<string, { file: string }>,
  objects: Array<{ kind: string; text?: string; x: number; y: number; width: number; height: number; options?: Record<string, number | string | boolean> }> = [],
  width = 1200,
  height = 1800,
): Promise<Uint8Array> {
  const artwork = document.createElement('canvas');
  artwork.width = width;
  artwork.height = height;
  const art = artwork.getContext('2d');
  if (!art) throw new Error('Canvas rendering is unavailable.');
  art.fillStyle = '#ffffff';
  art.fillRect(0, 0, width, height);
  const layout = document.createElement('canvas');
  layout.width = width;
  layout.height = height;
  const ink = layout.getContext('2d');
  if (!ink) throw new Error('Canvas rendering is unavailable.');
  const pagePanels = panels.filter((panel) => panel.placement?.page === pageId);
  for (const panel of pagePanels) {
    const frame = panel.placement!.frame;
    const [x, y, frameWidth, frameHeight] = frame;
    const takeId = panel.active_take ?? null;
    const take = takeId ? takes[takeId] : undefined;
    const imageBytes = take ? await getProjectFile(projectId, take.file) : null;
    if (imageBytes && take?.file.toLowerCase().match(/\.(png|jpe?g|webp)$/)) {
      const image = await new Promise<HTMLImageElement>((resolve, reject) => {
        const url = URL.createObjectURL(new Blob([toArrayBuffer(imageBytes)]));
        const element = new Image();
        element.onload = () => { URL.revokeObjectURL(url); resolve(element); };
        element.onerror = () => { URL.revokeObjectURL(url); reject(new Error(`Could not open the artwork for panel ${panel.label.page}.${panel.label.panel}.`)); };
        element.src = url;
      });
      art.save();
      art.beginPath();
      art.rect(x + 3, y + 3, frameWidth - 6, frameHeight - 6);
      art.clip();
      const scale = Math.max(frameWidth / image.naturalWidth, frameHeight / image.naturalHeight);
      const drawWidth = image.naturalWidth * scale;
      const drawHeight = image.naturalHeight * scale;
      art.drawImage(image, x + (frameWidth - drawWidth) / 2, y + (frameHeight - drawHeight) / 2, drawWidth, drawHeight);
      art.restore();
    } else {
      art.fillStyle = '#f0eee9';
      art.fillRect(x + 3, y + 3, frameWidth - 6, frameHeight - 6);
      art.fillStyle = '#8a93a1';
      art.font = '600 25px sans-serif';
      art.fillText('Artwork pending', x + 24, y + Math.min(frameHeight - 20, 58));
    }
    ink.strokeStyle = '#293a56';
    ink.lineWidth = 7;
    ink.strokeRect(x, y, frameWidth, frameHeight);
    ink.fillStyle = '#293a56';
    ink.font = '600 22px sans-serif';
    const caption = `P${panel.label.page}.${panel.label.panel}  ${panel.action.slice(0, 54)}`;
    ink.fillText(caption, x + 16, y + 31, Math.max(20, frameWidth - 32));
  }
  for (const object of objects) drawPageObject(ink, object);
  const artBytes = await canvasBlob(artwork);
  const layoutBytes = await canvasBlob(layout);
  const preview = document.createElement('canvas');
  preview.width = width;
  preview.height = height;
  const merged = preview.getContext('2d');
  if (!merged) throw new Error('Canvas rendering is unavailable.');
  merged.drawImage(artwork, 0, 0);
  merged.drawImage(layout, 0, 0);
  const previewBytes = await canvasBlob(preview);
  const thumbScale = Math.min(256 / width, 256 / height);
  const thumbnail = document.createElement('canvas');
  thumbnail.width = Math.max(1, Math.round(width * thumbScale));
  thumbnail.height = Math.max(1, Math.round(height * thumbScale));
  thumbnail.getContext('2d')?.drawImage(preview, 0, 0, thumbnail.width, thumbnail.height);
  const stackXml = `<?xml version="1.0" encoding="UTF-8"?><image version="0.0.3" w="${width}" h="${height}" xres="300" yres="300"><stack><layer name="Lettering and manga effects" src="data/layout.png" opacity="1.0" visibility="visible" composite-op="svg:src-over"/><layer name="Artwork" src="data/artwork.png" opacity="1.0" visibility="visible" composite-op="svg:src-over"/></stack></image>`;
  return zipSync({
    mimetype: [strToU8('image/openraster'), { level: 0 }],
    'stack.xml': strToU8(stackXml),
    'mergedimage.png': previewBytes,
    'Thumbnails/thumbnail.png': await canvasBlob(thumbnail),
    'data/layout.png': layoutBytes,
    'data/artwork.png': artBytes,
  }, { level: 0 });
}

function drawPageObject(
  context: CanvasRenderingContext2D,
  object: { kind: string; text?: string; x: number; y: number; width: number; height: number; options?: Record<string, number | string | boolean> },
): void {
  const { x, y, width, height } = object;
  context.save();
  if (object.kind === 'bubble') {
    context.fillStyle = '#fff'; context.strokeStyle = '#111'; context.lineWidth = 7;
    context.beginPath(); context.ellipse(x + width / 2, y + height / 2, width / 2, height / 2, 0, 0, Math.PI * 2); context.fill(); context.stroke();
    context.fillStyle = '#111'; context.font = '600 28px sans-serif'; context.textAlign = 'center'; context.textBaseline = 'middle';
    wrapCanvasText(context, object.text || 'Dialogue', x + 18, y + 18, width - 36, 34);
  } else if (object.kind === 'sfx') {
    context.fillStyle = String(object.options?.color ?? '#d96d45'); context.strokeStyle = '#fff'; context.lineWidth = 9; context.font = `900 ${Math.max(38, Math.min(width / Math.max(2, (object.text || 'SFX').length) * 1.5, 120))}px sans-serif`; context.textAlign = 'center'; context.textBaseline = 'middle';
    context.strokeText(object.text || 'SFX', x + width / 2, y + height / 2, width); context.fillText(object.text || 'SFX', x + width / 2, y + height / 2, width);
  } else if (object.kind === 'screentone') {
    context.beginPath(); context.rect(x, y, width, height); context.clip(); context.fillStyle = '#111';
    const spacing = Number(object.options?.spacing ?? 18); const radius = Number(object.options?.radius ?? 2.8);
    for (let row = 0, py = y; py <= y + height; row++, py += spacing) {
      for (let px = x + (row % 2 ? spacing / 2 : 0); px <= x + width; px += spacing) {
        context.beginPath(); context.arc(px, py, radius, 0, Math.PI * 2); context.fill();
      }
    }
  } else if (object.kind === 'speed-lines') {
    const cx = x + width / 2; const cy = y + height / 2; const count = Number(object.options?.count ?? 72); const radius = Math.hypot(width, height) / 2;
    const clear = Math.min(width, height) * Number(object.options?.clear ?? 0.18);
    context.fillStyle = '#111';
    for (let i = 0; i < count; i++) {
      const angle = i * Math.PI * 2 / count; const half = 2 + (i % 3);
      context.beginPath(); context.moveTo(cx + Math.cos(angle) * clear, cy + Math.sin(angle) * clear);
      context.lineTo(cx + Math.cos(angle) * radius - Math.sin(angle) * half, cy + Math.sin(angle) * radius + Math.cos(angle) * half);
      context.lineTo(cx + Math.cos(angle) * radius + Math.sin(angle) * half, cy + Math.sin(angle) * radius - Math.cos(angle) * half); context.closePath(); context.fill();
    }
  } else if (object.kind === 'impact-burst') {
    const cx = x + width / 2; const cy = y + height / 2; const spikes = Number(object.options?.spikes ?? 18); const outer = Math.min(width, height) / 2; const inner = outer * 0.36;
    context.fillStyle = '#111'; context.beginPath();
    for (let i = 0; i < spikes * 2; i++) {
      const angle = -Math.PI / 2 + i * Math.PI / spikes; const radius = i % 2 === 0 ? outer : inner;
      if (i === 0) context.moveTo(cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius);
      else context.lineTo(cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius);
    }
    context.closePath(); context.fill();
    context.fillStyle = '#fff'; context.font = '900 46px sans-serif'; context.textAlign = 'center'; context.textBaseline = 'middle';
    context.fillText(object.text || '!', cx, cy, width * 0.75);
  }
  context.restore();
}

function wrapCanvasText(context: CanvasRenderingContext2D, text: string, x: number, y: number, maxWidth: number, lineHeight: number): void {
  const words = text.split(/\s+/);
  const lines: string[] = [];
  let line = '';
  for (const word of words) {
    const candidate = line ? `${line} ${word}` : word;
    if (line && context.measureText(candidate).width > maxWidth) { lines.push(line); line = word; }
    else line = candidate;
  }
  if (line) lines.push(line);
  const startY = y + Math.max(0, (maxWidth / lineHeight - lines.length) * lineHeight / 2);
  lines.forEach((value, index) => context.fillText(value, x + maxWidth / 2, startY + index * lineHeight, maxWidth));
}

function canvasBlob(canvas: HTMLCanvasElement): Promise<Uint8Array> {
  return new Promise((resolve, reject) => canvas.toBlob(async (blob) => {
    if (!blob) return reject(new Error('Could not encode the OpenRaster page.'));
    resolve(new Uint8Array(await blob.arrayBuffer()));
  }, 'image/png'));
}

export async function loadOraPreview(projectId: string, pageId: string): Promise<Blob | null> {
  const bytes = await getPageAsset(projectId, pageId);
  if (!bytes) return null;
  const files = unzipSync(bytes);
  const preview = files['mergedimage.png'];
  return preview ? new Blob([toArrayBuffer(preview)], { type: 'image/png' }) : null;
}

export interface OraLayerAsset {
  name: string;
  src: string;
  visible: boolean;
  opacity: number;
  image: Uint8Array;
}

/** Read the editable raster layers from an OpenRaster page instead of relying on its flattened preview. */
export async function loadOraLayers(projectId: string, pageId: string): Promise<OraLayerAsset[]> {
  const bytes = await getPageAsset(projectId, pageId);
  if (!bytes) return [];
  const files = unzipSync(bytes);
  const xml = files['stack.xml'];
  if (!xml) return [];
  const document = new DOMParser().parseFromString(new TextDecoder().decode(xml), 'application/xml');
  return Array.from(document.getElementsByTagName('layer')).flatMap((node) => {
    const src = node.getAttribute('src');
    const image = src ? files[src] : undefined;
    if (!src || !image) return [];
    const opacity = Number(node.getAttribute('opacity') ?? '1');
    return [{
      name: node.getAttribute('name') || src.split('/').at(-1) || 'Layer', src,
      visible: node.getAttribute('visibility') !== 'hidden',
      opacity: Number.isFinite(opacity) ? Math.max(0, Math.min(1, opacity)) : 1,
      image,
    }];
  });
}

export async function loadOraLayerStack(projectId: string, pageId: string): Promise<LayerStackNode[]> {
  const bytes = await getPageAsset(projectId, pageId); if (!bytes) return [];
  const xml = unzipSync(bytes)['stack.xml']; if (!xml) return [];
  const doc = new DOMParser().parseFromString(new TextDecoder().decode(xml), 'application/xml');
  const root = doc.getElementsByTagName('image')[0]?.getElementsByTagName('stack')[0]; if (!root) return [];
  const parse = (parent: Element, path: string): LayerStackNode[] => Array.from(parent.children).flatMap<LayerStackNode>((node, index) => {
    if (node.tagName === 'layer') {
      const src = node.getAttribute('src'); if (!src) return [];
      return [{ id: src, type: 'layer' as const, name: node.getAttribute('name') || src.split('/').at(-1) || 'Layer' }];
    }
    if (node.tagName === 'stack') return [{ id: `ora-group-${path}-${index}`, type: 'group' as const, name: node.getAttribute('name') || `Group ${index + 1}`, visible: node.getAttribute('visibility') !== 'hidden', children: parse(node, `${path}-${index}`) }];
    return [];
  });
  return parse(root, 'root');
}

/** Flatten editable ORA layers with their app-side opacity and color effects for exports. */
export async function renderOraPreview(bytes: Uint8Array, states: import('./project').MangaPage['layer_state'] = {}, strokes: import('./project').PageStroke[] = [], layerStack: import('./project').MangaPage['layer_stack'] = []): Promise<Uint8Array> {
  const files = unzipSync(bytes);
  const fallback = files['mergedimage.png'];
  const xml = files['stack.xml'];
  if (!xml || !fallback) throw new Error('This OpenRaster page is missing its layer stack or merged preview.');
  const document = new DOMParser().parseFromString(new TextDecoder().decode(xml), 'application/xml');
  const imageNode = document.getElementsByTagName('image')[0];
  const stackNode = imageNode?.getElementsByTagName('stack')[0];
  const width = Number(imageNode?.getAttribute('w'));
  const height = Number(imageNode?.getAttribute('h'));
  if (!stackNode || !Number.isFinite(width) || !Number.isFinite(height)) return fallback;
  const children = Array.from(stackNode.getElementsByTagName('layer'));
  const groupVisibility = new Map<string, boolean>();
  const visitVisibility = (node: Element, parentVisible: boolean) => Array.from(node.children).forEach((child) => {
    if (child.tagName === 'layer') { const src = child.getAttribute('src'); if (src) groupVisibility.set(src, parentVisible); }
    else if (child.tagName === 'stack') visitVisibility(child, parentVisible && child.getAttribute('visibility') !== 'hidden');
  });
  visitVisibility(stackNode, true);
  const unsupportedBlend = Array.from(stackNode.querySelectorAll('[composite-op]')).some((node) => {
    const composite = node.getAttribute('composite-op');
    return composite && composite !== 'svg:src-over';
  });
  if (unsupportedBlend) {
    if (Object.keys(states ?? {}).length || strokes.length) throw new Error('Exporting edits on non-standard OpenRaster blend modes is not supported yet.');
    return fallback;
  }
  let layers = children.flatMap((node) => {
    const src = node.getAttribute('src');
    const data = src ? files[src] : undefined;
    if (!src || !data) return [];
    const opacity = Number(node.getAttribute('opacity') ?? '1');
    return [{ node, src, data, opacity: Number.isFinite(opacity) ? opacity : 1 }];
  });
  if (layerStack?.length) {
    const order = flattenLayerStack(layerStack).filter((item) => item.visible).map((item) => item.src);
    const bySource = new Map(layers.map((layer) => [layer.src, layer]));
    layers = order.map((src) => bySource.get(src)).filter((layer): layer is NonNullable<typeof layer> => Boolean(layer));
  }
  if (!layers.length) return fallback;
  const canvas = document.createElement('canvas');
  canvas.width = width; canvas.height = height;
  const context = canvas.getContext('2d');
  if (!context) return fallback;
  for (const layer of [...layers].reverse()) {
    const state = states?.[layer.src];
    if (!layerStack?.length && groupVisibility.get(layer.src) === false) continue;
    if ((state?.visible ?? (layer.node.getAttribute('visibility') !== 'hidden')) === false) continue;
    const opacity = Math.max(0, Math.min(1, state?.opacity ?? layer.opacity));
    if (!opacity) continue;
    const effects = state?.effects?.filter((effect) => effect.enabled) ?? [];
    context.filter = effects.map((effect) => effect.kind === 'grayscale'
      ? `grayscale(${Math.max(0, Math.min(1, effect.amount))})`
      : `contrast(${Math.max(0.2, Math.min(2, 1 + effect.amount))})`).join(' ');
    context.globalAlpha = opacity;
    const image = await createImageBitmap(new Blob([Uint8Array.from(layer.data).buffer as ArrayBuffer], { type: 'image/png' }));
    let drawingImage: CanvasImageSource = image;
    if (state?.mask?.enabled) {
      const masked = document.createElement('canvas'); masked.width = width; masked.height = height; const maskContext = masked.getContext('2d');
      if (maskContext) { maskContext.drawImage(image, 0, 0, width, height); const maskImage = await createImageBitmap(await (await fetch(state.mask.data)).blob()); maskContext.globalCompositeOperation = state.mask.inverted ? 'destination-out' : 'destination-in'; maskContext.drawImage(maskImage, 0, 0, width, height); maskImage.close(); drawingImage = masked; }
    }
    const x = Number(layer.node.getAttribute('x') ?? '0');
    const y = Number(layer.node.getAttribute('y') ?? '0');
    const transform = state?.transform;
    if (transform) {
      context.translate(width / 2 + transform.x, height / 2 + transform.y);
      context.rotate(transform.rotation * Math.PI / 180);
      context.scale(transform.scaleX, transform.scaleY);
      context.drawImage(drawingImage, x - width / 2, y - height / 2, width, height);
      context.setTransform(1, 0, 0, 1, 0, 0);
    } else context.drawImage(drawingImage, x, y);
    image.close();
  }
  context.filter = 'none'; context.globalAlpha = 1;
  for (const stroke of strokes) {
    if (states?.[stroke.source]?.visible === false) continue;
    context.save(); context.globalCompositeOperation = stroke.mode === 'erase' ? 'destination-out' : 'source-over';
    context.strokeStyle = stroke.color; context.fillStyle = stroke.color; context.lineWidth = stroke.width; context.lineCap = 'round'; context.lineJoin = 'round';
    if (stroke.points.length < 2) { const [x, y] = stroke.points[0] ?? [0, 0]; context.beginPath(); context.arc(x, y, stroke.width / 2, 0, Math.PI * 2); context.fill(); }
    else { context.beginPath(); context.moveTo(...stroke.points[0]); stroke.points.slice(1).forEach(([x, y]) => context.lineTo(x, y)); context.stroke(); }
    context.restore();
  }
  return canvasBlob(canvas);
}

export function oraToFiles(bytes: Uint8Array): Record<string, Uint8Array> {
  return unzipSync(bytes);
}

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return Uint8Array.from(bytes).buffer as ArrayBuffer;
}
