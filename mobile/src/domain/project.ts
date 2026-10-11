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
  [key: string]: unknown;
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
