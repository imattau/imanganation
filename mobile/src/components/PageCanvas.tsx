import { useEffect, useRef, useState } from 'react';
import { Application, Container, Graphics, Sprite, Texture } from 'pixi.js';
import type { MangaPage, MangaPanel, PageObject } from '../domain/project';
import { loadOraPreview } from '../domain/pageAssets';

interface Props {
  projectId: string;
  page: MangaPage;
  panels: MangaPanel[];
  onMoveObject?: (objectId: string, x: number, y: number) => void;
}

export function PageCanvas({ projectId, page, panels, onMoveObject }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const [surface, setSurface] = useState({ width: 0, height: 0 });
  const [dragged, setDragged] = useState<{ id: string; x: number; y: number } | null>(null);
  const dragStart = useRef<{ id: string; x: number; y: number; pointerX: number; pointerY: number; scale: number } | null>(null);

  useEffect(() => {
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
      app.stage.addChild(pageLayer);
      const draw = async () => {
        if (!host.current || disposed) return;
        const w = Math.max(1, host.current.clientWidth);
        const h = Math.max(1, host.current.clientHeight);
        setSurface({ width: w, height: h });
        app.renderer.resize(w, h);
        pageLayer.removeChildren().forEach((child) => child.destroy({ children: true }));
        const pageWidth = 1200;
        const pageHeight = 1800;
        const scale = Math.min((w - 36) / pageWidth, (h - 36) / pageHeight);
        const x = (w - pageWidth * scale) / 2;
        const y = (h - pageHeight * scale) / 2;
        const paper = new Graphics().rect(0, 0, pageWidth, pageHeight).fill('#ffffff').stroke({ color: '#d5d9df', width: 5 });
        pageLayer.addChild(paper);

        const preview = page.file.toLowerCase().endsWith('.ora') ? await loadOraPreview(projectId, page.id) : null;
        if (preview && !disposed) {
          const bitmap = await createImageBitmap(preview);
          const sprite = new Sprite(Texture.from(bitmap));
          sprite.width = pageWidth;
          sprite.height = pageHeight;
          pageLayer.addChild(sprite);
        }
        pageLayer.scale.set(scale);
        pageLayer.position.set(x, y);
      };
      await draw();
      resize = new ResizeObserver(() => { void draw(); });
      resize.observe(host.current);
    };

    void mount().catch((error: unknown) => {
      if (host.current) host.current.textContent = error instanceof Error ? error.message : 'Canvas could not be initialized.';
    });
    return () => {
      disposed = true;
      resize?.disconnect();
      application?.destroy(true, { children: true, texture: true });
    };
  }, [projectId, page.id, page.file, page.mobile_objects, panels]);

  const scale = surface.width && surface.height ? Math.min((surface.width - 36) / 1200, (surface.height - 36) / 1800) : 0;
  const pageWidth = 1200 * scale;
  const pageHeight = 1800 * scale;
  const left = (surface.width - pageWidth) / 2;
  const top = (surface.height - pageHeight) / 2;
  const objects = page.mobile_objects ?? [];
  const moveObject = (event: React.PointerEvent<HTMLButtonElement>, object: PageObject) => {
    if (!dragStart.current || dragStart.current.id !== object.id) return;
    const deltaX = (event.clientX - dragStart.current.pointerX) / dragStart.current.scale;
    const deltaY = (event.clientY - dragStart.current.pointerY) / dragStart.current.scale;
    setDragged({ id: object.id, x: Math.max(0, Math.min(1200 - object.width, dragStart.current.x + deltaX)), y: Math.max(0, Math.min(1800 - object.height, dragStart.current.y + deltaY)) });
  };
  const finishDrag = (object: PageObject) => {
    const current = dragged?.id === object.id ? dragged : null;
    dragStart.current = null;
    if (current && (current.x !== object.x || current.y !== object.y)) onMoveObject?.(object.id, current.x, current.y);
    setDragged(null);
  };

  return <div className="page-canvas" ref={host} aria-label={`${page.label} canvas`}>
    {scale > 0 && objects.map((object) => {
      const position = dragged?.id === object.id ? dragged : object;
      return <button
        key={object.id}
        type="button"
        className="page-object-handle"
        aria-label={`Move ${object.kind.replace('-', ' ')}${object.text ? `: ${object.text}` : ''}`}
        title="Drag to reposition"
        style={{ left: left + position.x * scale, top: top + position.y * scale, width: object.width * scale, height: object.height * scale }}
        onPointerDown={(event) => {
          event.preventDefault();
          event.currentTarget.setPointerCapture(event.pointerId);
          dragStart.current = { id: object.id, x: position.x, y: position.y, pointerX: event.clientX, pointerY: event.clientY, scale };
        }}
        onPointerMove={(event) => moveObject(event, object)}
        onPointerUp={() => finishDrag(object)}
        onPointerCancel={() => finishDrag(object)}
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
