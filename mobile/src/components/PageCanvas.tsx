import { useEffect, useRef } from 'react';
import { Application, Container, Graphics, Sprite, Texture } from 'pixi.js';
import type { MangaPage, MangaPanel } from '../domain/project';
import { loadOraPreview } from '../domain/pageAssets';

interface Props {
  projectId: string;
  page: MangaPage;
  panels: MangaPanel[];
}

export function PageCanvas({ projectId, page, panels }: Props) {
  const host = useRef<HTMLDivElement>(null);

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
  }, [projectId, page.id, panels]);

  return <div className="page-canvas" ref={host} aria-label={`${page.label} canvas`} />;
}
