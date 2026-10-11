export interface MangaProject {
  format: 'imanganation.project';
  version: 1;
  project: {
    id: string;
    title: string;
    chapter?: string;
    reading_order: 'rtl' | 'ltr';
    default_color_mode?: 'inherit' | 'color' | 'bw';
    created: string;
    modified: string;
    [key: string]: unknown;
  };
  panels: MangaPanel[];
  pages: MangaPage[];
  takes: Record<string, MangaTake>;
  cast: Array<{ name: string; aliases?: string[]; notes?: string; [key: string]: unknown }>;
  locations?: Array<{ name: string; notes?: string; [key: string]: unknown }>;
  props?: Array<{ name: string; [key: string]: unknown }>;
  cursor: Record<string, unknown>;
  [key: string]: unknown;
}

export interface MangaPanel {
  id: string;
  label: { page: number; panel: number };
  action: string;
  scene_heading?: string;
  location?: string;
  camera?: string;
  characters?: Array<{ name: string; version?: string | null }>;
  dialogue?: Array<{ speaker: string; text: string; kind?: string }>;
  sfx?: string[];
  status?: 'unplaced' | 'placed' | 'orphaned';
  placement?: { page: string; frame: [number, number, number, number] } | null;
  takes?: string[];
  active_take?: string | null;
  [key: string]: unknown;
}

export interface MangaPage {
  id: string;
  label: string;
  file: string;
  mobile_objects?: Array<PageObject>;
  /** Editable brush and eraser strokes authored in the mobile editor, in page pixels. */
  mobile_strokes?: PageStroke[];
  /** Editor-only visibility and non-destructive GEGL-like effect state keyed by ORA layer src. */
  layer_state?: Record<string, PageLayerState>;
  /** Editable stack hierarchy. Layer node IDs are their OpenRaster src paths. */
  layer_stack?: LayerStackNode[];
  [key: string]: unknown;
}

export interface PageLayerState {
  visible?: boolean;
  opacity?: number;
  effects?: Array<{ id: string; kind: 'grayscale' | 'contrast'; enabled: boolean; amount: number }>;
  transform?: { x: number; y: number; scaleX: number; scaleY: number; rotation: number };
  mask?: { data: string; enabled: boolean; inverted: boolean };
}

export interface LayerStackNode {
  id: string;
  type: 'layer' | 'group';
  name: string;
  visible?: boolean;
  collapsed?: boolean;
  children?: LayerStackNode[];
}

export interface PageStroke {
  id: string;
  source: string;
  points: Array<[number, number]>;
  color: string;
  width: number;
  mode: 'paint' | 'erase';
}

export interface PageObject {
  id: string;
  kind: 'bubble' | 'sfx' | 'screentone' | 'speed-lines' | 'impact-burst';
  text?: string;
  x: number;
  y: number;
  width: number;
  height: number;
  options?: Record<string, number | string | boolean>;
}

export interface MangaTake {
  panel: string;
  file: string;
  width: number;
  height: number;
  kind: string;
  parent: string | null;
  origin: string;
  created: string;
  engine?: Record<string, unknown>;
  [key: string]: unknown;
}

export function isMangaProject(value: unknown): value is MangaProject {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<MangaProject>;
  return candidate.format === 'imanganation.project'
    && candidate.version === 1
    && typeof candidate.project?.id === 'string'
    && typeof candidate.project?.title === 'string'
    && Array.isArray(candidate.panels)
    && Array.isArray(candidate.pages)
    && !!candidate.takes && typeof candidate.takes === 'object'
    && Array.isArray(candidate.cast)
    && !!candidate.cursor && typeof candidate.cursor === 'object';
}

export function createProject(title: string): MangaProject {
  const now = new Date().toISOString();
  const id = `prj_${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`;
  return {
    format: 'imanganation.project', version: 1,
    project: { id, title: title.trim() || 'Untitled manga', reading_order: 'rtl', created: now, modified: now },
    script: { file: 'script/story.md', format: 'canonical' },
    panels: [], pages: [], takes: {}, cast: [], locations: [], props: [], cursor: {},
  };
}

export function touchProject(project: MangaProject): MangaProject {
  return { ...project, project: { ...project.project, modified: new Date().toISOString() } };
}
