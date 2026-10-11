import { useEffect, useRef, useState } from 'react';
import { Application, ColorMatrixFilter, Container, Graphics, Sprite, Texture } from 'pixi.js';
import type { MangaPage, MangaPanel, PageStroke } from '../domain/project';
import { loadOraLayers } from '../domain/pageAssets';
import { flattenLayerStack } from '../domain/layerStack';

interface Props {
  projectId: string;
  page: MangaPage;
  panels: MangaPanel[];
  layerState?: MangaPage['layer_state'];
  zoom?: number;
  activeTool?: 'move' | 'rectangle' | 'ellipse' | 'fuzzy' | 'transform' | 'brush' | 'eraser';
  selectedSource?: string;
  brushColor?: string;
  brushSize?: number;
  onStrokes?: (strokes: PageStroke[]) => void;
  onLayerTransform?: (source: string, transform: NonNullable<NonNullable<MangaPage['layer_state']>[string]['transform']>) => void;
  clearSelectionToken?: number;
  createMaskToken?: number;
  layerStack?: MangaPage['layer_stack'];
  onLayerMask?: (source: string, mask: NonNullable<NonNullable<MangaPage['layer_state']>[string]['mask']>) => void;
  onSelectionChange?: (selected: boolean) => void;
  onMoveObject?: (objectId: string, x: number, y: number) => void;
  onMovePanel?: (panelId: string, x: number, y: number) => void;
}

export function PageCanvas({ projectId, page, panels, layerState, zoom = 100, activeTool = 'move', selectedSource = '', brushColor = '#202020', brushSize = 14, onStrokes, onLayerTransform, clearSelectionToken = 0, createMaskToken = 0, layerStack, onLayerMask, onSelectionChange, onMoveObject, onMovePanel }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const baseLayerRef = useRef<Container | null>(null);
  const overlayLayerRef = useRef<Container | null>(null);
  const spriteBySource = useRef(new Map<string, Sprite>());
  const [surface, setSurface] = useState({ width: 0, height: 0 });
  const [canvasReady, setCanvasReady] = useState(false);
  const [dragged, setDragged] = useState<{ id: string; x: number; y: number } | null>(null);
  const dragStart = useRef<{ id: string; x: number; y: number; pointerX: number; pointerY: number; scale: number } | null>(null);
  const dragPosition = useRef<{ id: string; x: number; y: number } | null>(null);
  const mergedSurface = useRef<HTMLCanvasElement | null>(null);
  const [selection, setSelection] = useState<{ x: number; y: number; w: number; h: number; mask?: HTMLCanvasElement } | null>(null);
  const gesture = useRef<{ x: number; y: number; lastX: number; lastY: number; points: Array<[number, number]>; transform?: { x: number; y: number } } | null>(null);
  const [liveStroke, setLiveStroke] = useState<PageStroke | null>(null);
  const [transformPreview, setTransformPreview] = useState<{ source: string; transform: NonNullable<NonNullable<MangaPage['layer_state']>[string]['transform']> } | null>(null);
  useEffect(() => { setSelection(null); }, [clearSelectionToken, page.id]);
  useEffect(() => { onSelectionChange?.(Boolean(selection?.mask)); }, [selection?.mask, onSelectionChange]);
  const lastMaskToken = useRef(createMaskToken);
  useEffect(() => {
    if (createMaskToken === lastMaskToken.current) return;
    lastMaskToken.current = createMaskToken;
    if (!selection?.mask || !selectedSource) return;
    const maskCanvas = document.createElement('canvas'); maskCanvas.width = 1200; maskCanvas.height = 1800; maskCanvas.getContext('2d')?.drawImage(selection.mask, 0, 0);
    const context = maskCanvas.getContext('2d'); if (!context) return;
    const pixels = context.getImageData(0, 0, 1200, 1800);
    for (let i = 0; i < pixels.data.length; i += 4) { const alpha = pixels.data[i + 3]; pixels.data[i] = 255; pixels.data[i + 1] = 255; pixels.data[i + 2] = 255; pixels.data[i + 3] = alpha ? 255 : 0; }
    context.putImageData(pixels, 0, 0);
    onLayerMask?.(selectedSource, { data: maskCanvas.toDataURL('image/png'), enabled: true, inverted: false });
  }, [createMaskToken, selection, selectedSource, onLayerMask]);

  useEffect(() => {
    setCanvasReady(false);
    let disposed = false;
    let application: Application | undefined;
    let resize: ResizeObserver | undefined;

    const mount = async () => {
      if (!host.current) return;
      const app = new Application();
      await app.init({ background: '#fffdf8', antialias: true, resolution: Math.min(window.devicePixelRatio || 1, 2), autoDensity: true });
      if (disposed || !host.current) { app.destroy(true); return; }
      application = app;
      host.current.replaceChildren(app.canvas);
      app.canvas.style.width = '100%';
      app.canvas.style.height = '100%';
      app.canvas.style.touchAction = 'none';

      const pageLayer = new Container();
      const baseLayer = new Container();
      const overlayLayer = new Container();
      pageLayer.addChild(baseLayer, overlayLayer);
      baseLayerRef.current = baseLayer; overlayLayerRef.current = overlayLayer;
      app.stage.addChild(pageLayer);
      const draw = async () => {
        if (!host.current || disposed) return;
        const w = Math.max(1, host.current.clientWidth);
        const h = Math.max(1, host.current.clientHeight);
        setSurface({ width: w, height: h });
        app.renderer.resize(w, h);
        baseLayer.removeChildren().forEach((child) => child.destroy({ children: true }));
        spriteBySource.current.clear();
        const pageWidth = 1200;
        const pageHeight = 1800;
        const scale = Math.min((w - 36) / pageWidth, (h - 36) / pageHeight) * zoom / 100;
        const x = (w - pageWidth * scale) / 2;
        const y = (h - pageHeight * scale) / 2;
        const paper = new Graphics().rect(0, 0, pageWidth, pageHeight).fill('#ffffff').stroke({ color: '#d5d9df', width: 5 });
        baseLayer.addChild(paper);

        const oraLayers = page.file.toLowerCase().endsWith('.ora') ? await loadOraLayers(projectId, page.id) : [];
        const sampleCanvas = document.createElement('canvas'); sampleCanvas.width = pageWidth; sampleCanvas.height = pageHeight;
        const sampleContext = sampleCanvas.getContext('2d');
        if (!disposed) {
          // OpenRaster lists layers top-first. Pixi renders children in insertion order, so add bottom-first.
          const sourceOrder = layerStack?.length ? flattenLayerStack(layerStack).filter((item) => item.visible).map((item) => item.src) : oraLayers.map((layer) => layer.src);
          const layerBySource = new Map(oraLayers.map((layer) => [layer.src, layer]));
          for (const source of [...sourceOrder].reverse()) {
            const layer = layerBySource.get(source); if (!layer) continue;
            const state = layerState?.[layer.src];
            if ((state?.visible ?? layer.visible) === false || (state?.opacity ?? layer.opacity) <= 0) continue;
            const bitmap = await createImageBitmap(new Blob([Uint8Array.from(layer.image).buffer as ArrayBuffer], { type: 'image/png' }));
            let textureImage: HTMLCanvasElement | ImageBitmap = bitmap;
            if (state?.mask?.enabled) {
              const masked = document.createElement('canvas'); masked.width = pageWidth; masked.height = pageHeight; const maskContext = masked.getContext('2d');
              if (maskContext) { maskContext.drawImage(bitmap, 0, 0, pageWidth, pageHeight); const maskImage = await createImageBitmap(await (await fetch(state.mask.data)).blob()); maskContext.globalCompositeOperation = state.mask.inverted ? 'destination-out' : 'destination-in'; maskContext.drawImage(maskImage, 0, 0, pageWidth, pageHeight); maskImage.close(); textureImage = masked; }
            }
            if (sampleContext) {
              sampleContext.save(); sampleContext.globalAlpha = Math.max(0, Math.min(1, state?.opacity ?? layer.opacity));
              const filters = state?.effects?.filter((effect) => effect.enabled) ?? [];
              sampleContext.filter = filters.map((effect) => effect.kind === 'grayscale' ? `grayscale(${effect.amount})` : `contrast(${1 + effect.amount})`).join(' ');
              const transform = state?.transform;
              if (transform) { sampleContext.translate(pageWidth / 2 + transform.x, pageHeight / 2 + transform.y); sampleContext.rotate(transform.rotation * Math.PI / 180); sampleContext.scale(transform.scaleX, transform.scaleY); sampleContext.drawImage(textureImage, -pageWidth / 2, -pageHeight / 2, pageWidth, pageHeight); }
              else sampleContext.drawImage(textureImage, 0, 0, pageWidth, pageHeight);
              sampleContext.restore();
            }
            const sprite = new Sprite(Texture.from(textureImage));
            const transform = state?.transform;
            sprite.anchor.set(0.5); sprite.position.set(pageWidth / 2 + (transform?.x ?? 0), pageHeight / 2 + (transform?.y ?? 0));
            sprite.width = pageWidth * (transform?.scaleX ?? 1); sprite.height = pageHeight * (transform?.scaleY ?? 1); sprite.rotation = (transform?.rotation ?? 0) * Math.PI / 180;
            sprite.alpha = Math.max(0, Math.min(1, state?.opacity ?? layer.opacity));
            const activeEffects = state?.effects?.filter((effect) => effect.enabled) ?? [];
            if (activeEffects.length) {
              const filter = new ColorMatrixFilter();
              activeEffects.forEach((effect) => {
                if (effect.kind === 'grayscale') filter.grayscale(Math.max(0, Math.min(1, effect.amount)), true);
                if (effect.kind === 'contrast') filter.contrast(1 + Math.max(-0.8, Math.min(1, effect.amount)), true);
              });
              sprite.filters = [filter];
            }
            baseLayer.addChild(sprite);
            spriteBySource.current.set(layer.src, sprite);
          }
        }
        mergedSurface.current = sampleCanvas;
        pageLayer.scale.set(scale);
        pageLayer.position.set(x, y);
      };
      await draw();
      setCanvasReady(true);
      resize = new ResizeObserver(() => { void draw(); });
      resize.observe(host.current);
    };

    void mount().catch((error: unknown) => {
      if (host.current) host.current.textContent = error instanceof Error ? error.message : 'Canvas could not be initialized.';
    });
    return () => {
      disposed = true;
      resize?.disconnect();
      baseLayerRef.current = null; overlayLayerRef.current = null; spriteBySource.current.clear();
      application?.destroy(true, { children: true, texture: true });
    };
  }, [projectId, page.id, page.file, page.layer_state, layerState, panels, zoom, layerStack]);

  useEffect(() => {
    const overlay = overlayLayerRef.current; if (!overlay || !canvasReady) return;
    overlay.removeChildren().forEach((child) => child.destroy({ children: true }));
    for (const stroke of [...(page.mobile_strokes ?? []), ...(liveStroke?.mode === 'paint' ? [liveStroke] : [])]) {
      if (stroke.source !== selectedSource || layerState?.[stroke.source]?.visible === false) continue;
      const graphic = new Graphics(); const [firstX, firstY] = stroke.points[0] ?? [0, 0];
      if (stroke.points.length > 1) { graphic.moveTo(firstX, firstY); stroke.points.slice(1).forEach(([px, py]) => graphic.lineTo(px, py)); graphic.stroke({ color: stroke.color, width: stroke.width, cap: 'round', join: 'round' }); }
      else graphic.circle(firstX, firstY, stroke.width / 2).fill(stroke.color);
      overlay.addChild(graphic);
    }
    if (selection?.mask) overlay.addChild(new Sprite(Texture.from(selection.mask)));
    else if (selection && (activeTool === 'rectangle' || activeTool === 'ellipse')) {
      const outline = new Graphics();
      if (activeTool === 'ellipse') outline.ellipse(selection.x + selection.w / 2, selection.y + selection.h / 2, selection.w / 2, selection.h / 2).fill({ color: 0x329bff, alpha: 0.22 }).stroke({ color: 0x329bff, width: 3 });
      else outline.rect(selection.x, selection.y, selection.w, selection.h).fill({ color: 0x329bff, alpha: 0.22 }).stroke({ color: 0x329bff, width: 3 });
      overlay.addChild(outline);
    }
  }, [canvasReady, page.mobile_strokes, liveStroke, selectedSource, layerState, selection, activeTool]);

  useEffect(() => {
    if (!transformPreview) return;
    const sprite = spriteBySource.current.get(transformPreview.source); if (!sprite) return;
    const transform = transformPreview.transform;
    sprite.position.set(600 + transform.x, 900 + transform.y);
    sprite.width = 1200 * transform.scaleX; sprite.height = 1800 * transform.scaleY; sprite.rotation = transform.rotation * Math.PI / 180;
  }, [transformPreview]);

  const scale = surface.width && surface.height ? Math.min((surface.width - 36) / 1200, (surface.height - 36) / 1800) * zoom / 100 : 0;
  const pageWidth = 1200 * scale;
  const pageHeight = 1800 * scale;
  const left = (surface.width - pageWidth) / 2;
  const top = (surface.height - pageHeight) / 2;
  const objects = page.mobile_objects ?? [];
  const startDrag = (event: React.PointerEvent<HTMLButtonElement>, id: string, x: number, y: number) => {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragStart.current = { id, x, y, pointerX: event.clientX, pointerY: event.clientY, scale };
    dragPosition.current = { id, x, y };
  };
  const updateDrag = (event: React.PointerEvent<HTMLButtonElement>, id: string, width: number, height: number) => {
    if (!dragStart.current || dragStart.current.id !== id) return;
    const deltaX = (event.clientX - dragStart.current.pointerX) / dragStart.current.scale;
    const deltaY = (event.clientY - dragStart.current.pointerY) / dragStart.current.scale;
    const position = { id, x: Math.max(0, Math.min(1200 - width, dragStart.current.x + deltaX)), y: Math.max(0, Math.min(1800 - height, dragStart.current.y + deltaY)) };
    dragPosition.current = position;
    setDragged(position);
  };
  const finishDrag = (id: string, x: number, y: number, onMove?: (id: string, x: number, y: number) => void) => {
    const current = dragPosition.current?.id === id ? dragPosition.current : null;
    dragStart.current = null;
    dragPosition.current = null;
    if (current && (current.x !== x || current.y !== y)) onMove?.(id, current.x, current.y);
    setDragged(null);
  };

  const toPagePoint = (event: React.PointerEvent<HTMLDivElement>) => ({ x: (event.clientX - host.current!.getBoundingClientRect().left - left) / scale, y: (event.clientY - host.current!.getBoundingClientRect().top - top) / scale });
  const insideSelection = (x: number, y: number) => {
    if (!selection) return true;
    if (x < selection.x || y < selection.y || x >= selection.x + selection.w || y >= selection.y + selection.h) return false;
    if (!selection.mask) return true;
    const pixel = selection.mask.getContext('2d')?.getImageData(Math.floor(x), Math.floor(y), 1, 1).data;
    return Boolean(pixel?.[3]);
  };
  const onCanvasDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if ((event.target as HTMLElement).closest('.page-panel-handle,.page-object-handle')) return;
    if (!scale || activeTool === 'move') return;
    const point = toPagePoint(event); if (point.x < 0 || point.y < 0 || point.x > 1200 || point.y > 1800) return;
    if ((activeTool === 'brush' || activeTool === 'eraser') && !insideSelection(point.x, point.y)) return;
    event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId);
    if (activeTool === 'fuzzy') { selectFuzzy(point.x, point.y); return; }
    if (activeTool === 'rectangle' || activeTool === 'ellipse') { gesture.current = { x: point.x, y: point.y, lastX: point.x, lastY: point.y, points: [] }; setSelection({ x: point.x, y: point.y, w: 0, h: 0 }); return; }
    if (activeTool === 'transform') { gesture.current = { x: point.x, y: point.y, lastX: point.x, lastY: point.y, points: [], transform: { x: layerState?.[selectedSource]?.transform?.x ?? 0, y: layerState?.[selectedSource]?.transform?.y ?? 0 } }; return; }
    if (activeTool === 'brush' || activeTool === 'eraser') { const stroke: PageStroke = { id: `stroke_${Date.now()}`, source: selectedSource, points: [[point.x, point.y]], color: brushColor, width: brushSize, mode: activeTool === 'eraser' ? 'erase' : 'paint' }; gesture.current = { x: point.x, y: point.y, lastX: point.x, lastY: point.y, points: stroke.points }; setLiveStroke(stroke); }
  };
  const onCanvasMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const g = gesture.current; if (!g || !scale) return;
    const point = toPagePoint(event); const x = Math.max(0, Math.min(1200, point.x)); const y = Math.max(0, Math.min(1800, point.y));
    if (activeTool === 'rectangle' || activeTool === 'ellipse') setSelection({ x: Math.min(g.x, x), y: Math.min(g.y, y), w: Math.abs(x - g.x), h: Math.abs(y - g.y) });
    if (activeTool === 'transform' && g.transform) setTransformPreview({ source: selectedSource, transform: { ...(layerState?.[selectedSource]?.transform ?? { x: 0, y: 0, scaleX: 1, scaleY: 1, rotation: 0 }), x: g.transform.x + x - g.x, y: g.transform.y + y - g.y } });
    if ((activeTool === 'brush' || activeTool === 'eraser') && insideSelection(x, y)) { const stroke = liveStroke; if (!stroke) return; const points = [...g.points, [x, y] as [number, number]]; g.points = points; g.lastX = x; g.lastY = y; setLiveStroke({ ...stroke, points }); }
  };
  const onCanvasUp = () => {
    if (transformPreview && gesture.current?.transform) { onLayerTransform?.(transformPreview.source, transformPreview.transform); setTransformPreview(null); }
    if (liveStroke?.mode === 'erase') {
      const erased = (page.mobile_strokes ?? []).filter((stroke) => stroke.source !== liveStroke.source || !stroke.points.some(([sx, sy]) => liveStroke.points.some(([ex, ey]) => Math.hypot(sx - ex, sy - ey) <= liveStroke.width / 2 + stroke.width / 2)));
      onStrokes?.(erased); setLiveStroke(null);
    } else if (liveStroke) { onStrokes?.([...(page.mobile_strokes ?? []), liveStroke]); setLiveStroke(null); }
    if (gesture.current && (activeTool === 'rectangle' || activeTool === 'ellipse') && selection) {
      const mask = document.createElement('canvas'); mask.width = 1200; mask.height = 1800; const ctx = mask.getContext('2d');
      if (ctx) { ctx.fillStyle = 'rgba(50,155,255,.3)'; if (activeTool === 'ellipse') { ctx.beginPath(); ctx.ellipse(selection.x + selection.w / 2, selection.y + selection.h / 2, selection.w / 2, selection.h / 2, 0, 0, Math.PI * 2); ctx.fill(); } else ctx.fillRect(selection.x, selection.y, selection.w, selection.h); setSelection({ ...selection, mask }); }
    }
    gesture.current = null;
  };
  const selectFuzzy = (x: number, y: number) => {
    const source = mergedSurface.current; const ctx = source?.getContext('2d'); if (!source || !ctx) return;
    const pixels = ctx.getImageData(0, 0, 1200, 1800).data; const width = 1200, height = 1800, start = (Math.floor(y) * width + Math.floor(x)) * 4;
    const seed = [pixels[start], pixels[start + 1], pixels[start + 2]]; const visited = new Uint8Array(width * height); const queue = new Int32Array(width * height); let head = 0, tail = 0; const idx = Math.floor(y) * width + Math.floor(x); queue[tail++] = idx; visited[idx] = 1;
    const mask = document.createElement('canvas'); mask.width = width; mask.height = height; const mctx = mask.getContext('2d'); if (!mctx) return; const out = mctx.createImageData(width, height); let minX = width, minY = height, maxX = 0, maxY = 0;
    while (head < tail) { const p = queue[head++], off = p * 4; const dr = pixels[off] - seed[0], dg = pixels[off + 1] - seed[1], db = pixels[off + 2] - seed[2]; if (dr*dr + dg*dg + db*db > 45*45*3) continue; out.data[off] = 50; out.data[off + 1] = 155; out.data[off + 2] = 255; out.data[off + 3] = 75; const px = p % width, py = Math.floor(p / width); minX = Math.min(minX, px); maxX = Math.max(maxX, px); minY = Math.min(minY, py); maxY = Math.max(maxY, py); for (const next of [p - 1, p + 1, p - width, p + width]) { if (next < 0 || next >= width * height || visited[next]) continue; if ((next === p - 1 && px === 0) || (next === p + 1 && px === width - 1)) continue; visited[next] = 1; queue[tail++] = next; } }
    mctx.putImageData(out, 0, 0); setSelection({ x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1, mask });
  };

  return <div className={`page-canvas tool-${activeTool}`} ref={host} aria-label={`${page.label} canvas`} onPointerDown={onCanvasDown} onPointerMove={onCanvasMove} onPointerUp={onCanvasUp} onPointerCancel={onCanvasUp}>
    {scale > 0 && panels.filter((panel) => panel.placement?.page === page.id).map((panel) => {
      const [x, y, width, height] = panel.placement!.frame;
      const position = dragged?.id === panel.id ? dragged : { x, y };
      return <button
        key={panel.id}
        type="button"
        className="page-panel-handle"
        aria-label={`Move panel ${panel.label.page}.${panel.label.panel}`}
        title="Drag to reposition panel"
        style={{ left: left + position.x * scale, top: top + position.y * scale, width: width * scale, height: height * scale }}
        onPointerDown={(event) => startDrag(event, panel.id, position.x, position.y)}
        onPointerMove={(event) => updateDrag(event, panel.id, width, height)}
        onPointerUp={() => finishDrag(panel.id, x, y, onMovePanel)}
        onPointerCancel={() => finishDrag(panel.id, x, y, onMovePanel)}
        onKeyDown={(event) => {
          const step = event.shiftKey ? 50 : 10;
          const offsets: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
          const offset = offsets[event.key];
          if (!offset) return;
          event.preventDefault();
          onMovePanel?.(panel.id, Math.max(0, Math.min(1200 - width, x + offset[0])), Math.max(0, Math.min(1800 - height, y + offset[1])));
        }}
      ><span>Panel {panel.label.page}.{panel.label.panel}</span></button>;
    })}
    {scale > 0 && objects.map((object) => {
      const position = dragged?.id === object.id ? dragged : object;
      return <button
        key={object.id}
        type="button"
        className="page-object-handle"
        aria-label={`Move ${object.kind.replace('-', ' ')}${object.text ? `: ${object.text}` : ''}`}
        title="Drag to reposition"
        style={{ left: left + position.x * scale, top: top + position.y * scale, width: object.width * scale, height: object.height * scale }}
        onPointerDown={(event) => startDrag(event, object.id, position.x, position.y)}
        onPointerMove={(event) => updateDrag(event, object.id, object.width, object.height)}
        onPointerUp={() => finishDrag(object.id, object.x, object.y, onMoveObject)}
        onPointerCancel={() => finishDrag(object.id, object.x, object.y, onMoveObject)}
        onKeyDown={(event) => {
          const step = event.shiftKey ? 50 : 10;
          const offsets: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
          const offset = offsets[event.key];
          if (!offset) return;
          event.preventDefault();
          onMoveObject?.(object.id, Math.max(0, Math.min(1200 - object.width, object.x + offset[0])), Math.max(0, Math.min(1800 - object.height, object.y + offset[1])));
        }}
      ><span>{object.kind.replace('-', ' ')}</span></button>;
    })}
  </div>;
}
