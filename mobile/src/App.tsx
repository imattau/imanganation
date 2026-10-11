import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  IonApp, IonButton, IonCard, IonCardContent, IonCardHeader, IonCardSubtitle, IonCardTitle,
  IonContent, IonHeader, IonInput, IonItem, IonLabel, IonList, IonPage,
  IonTextarea, IonTitle, IonToolbar, IonToast,
} from '@ionic/react';
import { BookOpen, Boxes, Download, FolderPlus, ImagePlus, Layers3, Sparkles, Users, WandSparkles } from 'lucide-react';
import type { MangaPanel, MangaProject, PageObject } from './domain/project';
import { createProject, touchProject } from './domain/project';
import { deleteProject, listProjects, saveProject, type SavedProject } from './domain/repository';
import { composePageOra, createBlankOra, deleteProjectAssets, savePageAsset, saveProjectFile } from './domain/pageAssets';
const PageCanvas = lazy(() => import('./components/PageCanvas').then((module) => ({ default: module.PageCanvas })));
import { renderer } from './domain/renderer';
import { makeVerticalComic, saveVerticalArchive, VERTICAL_PRESETS } from './domain/verticalExport';
import { exportProjectArchive, importProjectArchive } from './domain/projectArchive';
import { parseCanonicalScript } from './domain/canonicalScript';
import { exportBook } from './domain/bookExport';

type WorkspaceTab = 'story' | 'panels' | 'pages' | 'people' | 'tools' | 'generate' | 'export';

const sections: Array<{ id: WorkspaceTab; label: string; icon: typeof BookOpen }> = [
  { id: 'story', label: 'Story', icon: BookOpen },
  { id: 'panels', label: 'Panels', icon: Boxes },
  { id: 'pages', label: 'Pages', icon: Layers3 },
  { id: 'people', label: 'People', icon: Users },
  { id: 'tools', label: 'Tools', icon: Sparkles },
  { id: 'generate', label: 'Create', icon: WandSparkles },
  { id: 'export', label: 'Export', icon: Download },
];

export default function App() {
  const [projects, setProjects] = useState<SavedProject[]>([]);
  const [current, setCurrent] = useState<SavedProject | null>(null);
  const [tab, setTab] = useState<WorkspaceTab>('story');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [engineMessage, setEngineMessage] = useState('Checking local renderer…');
  const importInput = useRef<HTMLInputElement>(null);

  const reload = useCallback(async () => setProjects(await listProjects()), []);
  useEffect(() => { void reload(); }, [reload]);
  useEffect(() => { void renderer.capabilities().then((capabilities) => setEngineMessage(capabilities.available ? 'Local renderer ready' : capabilities.reason ?? 'Local renderer unavailable')); }, []);

  const persist = useCallback(async (manifest: MangaProject, script: string) => {
    const prior = current?.manifest.project.id === manifest.project.id ? current : null;
    const revisions = [...(prior?.revisions ?? [])];
    if (prior && prior.script !== script && prior.script.trim()) {
      const now = new Date().toISOString();
      const last = revisions.at(-1);
      if (!last || now.slice(0, 16) !== last.created.slice(0, 16)) revisions.push({ created: now, script: prior.script });
      if (revisions.length > 30) revisions.splice(0, revisions.length - 30);
    }
    const next: SavedProject = { manifest: touchProject(manifest), script, savedAt: new Date().toISOString(), revisions };
    await saveProjectFile(manifest.project.id, 'script/revisions.json', new TextEncoder().encode(JSON.stringify(revisions, null, 2)));
    await saveProject(next);
    setCurrent(next);
    await reload();
  }, [current, reload]);

  const startProject = async () => {
    const name = window.prompt('Name your manga project');
    if (!name?.trim()) return;
    const manifest = createProject(name);
    const saved = { manifest, script: '', savedAt: new Date().toISOString() };
    try {
      await saveProject(saved);
      setProjects(await listProjects());
      setCurrent(saved);
      setTab('story');
    } catch (error) { setNotice(message(error)); }
  };

  const openProject = (project: SavedProject) => { setCurrent(project); setTab('story'); };

  const removeProject = async (project: SavedProject) => {
    if (!window.confirm(`Delete “${project.manifest.project.title}” from this device?`)) return;
    await deleteProject(project.manifest.project.id);
    await deleteProjectAssets(project.manifest.project.id);
    if (current?.manifest.project.id === project.manifest.project.id) setCurrent(null);
    await reload();
  };

  const importProject = async (file?: File) => {
    if (!file) return;
    setBusy(true);
    try {
      const imported = await importProjectArchive(file);
      await reload();
      setCurrent(imported);
      setTab('story');
      setNotice(`Imported ${imported.manifest.project.title}.`);
    } catch (error) { setNotice(message(error)); }
    finally {
      setBusy(false);
      if (importInput.current) importInput.current.value = '';
    }
  };

  const addPanel = async () => {
    if (!current) return;
    const index = current.manifest.panels.length + 1;
    const newPanel: MangaPanel = {
      id: `pnl_${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`,
      label: { page: Math.max(1, current.manifest.pages.length), panel: index },
      action: 'Describe what happens in this panel.', characters: [], dialogue: [], sfx: [], status: 'unplaced', takes: [], active_take: null,
    };
    const manifest = { ...current.manifest, panels: [...current.manifest.panels, newPanel] };
    try { await persist(manifest, current.script); setNotice('Panel added.'); }
    catch (error) { setNotice(message(error)); }
  };

  const parseScript = async () => {
    if (!current || !current.script.trim()) { setNotice('Write or import a canonical PAGE / PANEL script first.'); return; }
    try {
      const parsed = await parseCanonicalScript(current.script);
      if (!parsed.panels.length) { setNotice(parsed.problems[0] ?? 'No panels were found.'); return; }
      if (parsed.problems.length && !window.confirm(`${parsed.problems.slice(0, 3).join('\n')}\n\nCreate the panels that parsed successfully?`)) return;
      const priorBySource = new Map(current.manifest.panels.filter((panel) => typeof panel.source === 'string').map((panel) => [String(panel.source), panel]));
      const reused = new Set<string>();
      const freshPanels = parsed.panels.map((candidate) => {
        const prior = priorBySource.get(String(candidate.source));
        if (prior) {
          reused.add(prior.id);
          return { ...prior, label: candidate.label };
        }
        return candidate;
      });
      const orphaned = current.manifest.panels.filter((panel) => !reused.has(panel.id)).map((panel) => ({ ...panel, status: 'orphaned' as const }));
      const cast = [...current.manifest.cast];
      for (const entry of parsed.cast) {
        const existing = cast.find((person) => person.name.toLowerCase() === entry.name.toLowerCase() || person.aliases?.some((alias) => alias.toLowerCase() === entry.name.toLowerCase()));
        if (existing) { if (!existing.notes && entry.notes) existing.notes = entry.notes; }
        else cast.push({ name: entry.name, aliases: entry.aliases, notes: entry.notes });
      }
      for (const panel of freshPanels) for (const character of panel.characters ?? []) {
        if (!cast.some((person) => person.name.toLowerCase() === character.name.toLowerCase())) cast.push({ name: character.name, aliases: [], notes: '' });
      }
      const locations = [...(current.manifest.locations ?? [])];
      for (const location of parsed.locations) if (!locations.some((entry) => entry.name.toLowerCase() === location.name.toLowerCase())) locations.push(location);
      let pages = [...current.manifest.pages];
      const pageNumbers = [...new Set(parsed.panels.map((panel) => panel.label.page))].sort((a, b) => a - b);
      for (const number of pageNumbers) {
        if (pages.some((page) => page.label === (number === 0 ? 'Cover' : `Page ${number}`))) continue;
        const id = `pg_${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`;
        const label = number === 0 ? 'Cover' : `Page ${number}`;
        const file = number === 0 ? 'pages/cover.ora' : `pages/page-${String(number).padStart(3, '0')}.ora`;
        const archive = await createBlankOra();
        await savePageAsset(current.manifest.project.id, id, archive);
        await saveProjectFile(current.manifest.project.id, file, archive);
        pages.push({ id, label, file });
      }
      const manifest = {
        ...current.manifest,
        project: { ...current.manifest.project, modified: new Date().toISOString() },
        panels: [...freshPanels, ...orphaned], pages, cast, locations,
        script: { ...((current.manifest.script as Record<string, unknown> | undefined) ?? {}), file: 'script/story.md', format: 'canonical', parsed_at: new Date().toISOString(), parser: { kind: 'mobile-canonical' } },
      };
      await persist(manifest, current.script);
      setNotice(`Parsed ${freshPanels.length} script panels${orphaned.length ? `; ${orphaned.length} earlier panels kept under needs matching` : ''}.`);
    } catch (error) { setNotice(message(error)); }
  };

  const addPage = async () => {
    if (!current) return;
    setBusy(true);
    try {
      const pageNumber = current.manifest.pages.length + 1;
      const id = `pg_${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`;
      const archive = await createBlankOra();
      await savePageAsset(current.manifest.project.id, id, archive);
      const page = { id, label: `Page ${pageNumber}`, file: `pages/page-${String(pageNumber).padStart(3, '0')}.ora` };
      await saveProjectFile(current.manifest.project.id, page.file, archive);
      const manifest = { ...current.manifest, pages: [...current.manifest.pages, page] };
      await persist(manifest, current.script);
      setTab('pages');
      setNotice('Blank layered page created.');
    } catch (error) { setNotice(message(error)); }
    finally { setBusy(false); }
  };

  const changePanel = async (panelId: string, patch: Partial<MangaPanel>) => {
    if (!current) return;
    const manifest = { ...current.manifest, panels: current.manifest.panels.map((panel) => panel.id === panelId ? { ...panel, ...patch } : panel) };
    try { await persist(manifest, current.script); }
    catch (error) { setNotice(message(error)); }
  };

  const placePanel = async (panelId: string, pageId: string) => {
    if (!current) return;
    const ids = [...current.manifest.panels.filter((panel) => panel.placement?.page === pageId && panel.id !== panelId).map((panel) => panel.id), panelId];
    const columns = ids.length > 1 ? 2 : 1;
    const rows = Math.ceil(ids.length / columns);
    const cellWidth = (1200 - 140 - 24 * (columns - 1)) / columns;
    const cellHeight = (1800 - 140 - 24 * (rows - 1)) / rows;
    const panels = current.manifest.panels.map((panel) => {
      const index = ids.indexOf(panel.id);
      if (index < 0) return panel;
      const row = Math.floor(index / columns);
      const logicalColumn = index % columns;
      const column = current.manifest.project.reading_order === 'rtl' && columns > 1 ? columns - logicalColumn - 1 : logicalColumn;
      const frame: [number, number, number, number] = [70 + column * (cellWidth + 24), 70 + row * (cellHeight + 24), cellWidth, cellHeight];
      return { ...panel, status: 'placed' as const, placement: { page: pageId, frame } };
    });
    const manifest = { ...current.manifest, panels };
    const page = manifest.pages.find((item) => item.id === pageId);
    if (!page) return;
    try {
      const archive = await composePageOra(manifest.project.id, page.id, panels, manifest.takes, page.mobile_objects ?? []);
      await savePageAsset(manifest.project.id, page.id, archive);
      await saveProjectFile(manifest.project.id, page.file, archive);
      await persist(manifest, current.script);
      setNotice('Panel placed on page.');
    } catch (error) { setNotice(message(error)); }
  };

  const addPageObject = async (pageId: string, kind: PageObject['kind']) => {
    if (!current) return;
    const page = current.manifest.pages.find((item) => item.id === pageId);
    if (!page) return;
    const text = kind === 'bubble' ? window.prompt('Dialogue text') : kind === 'sfx' || kind === 'impact-burst' ? window.prompt(kind === 'sfx' ? 'Sound effect text' : 'Burst text', 'BAM!') : undefined;
    if ((kind === 'bubble' || kind === 'sfx') && !text?.trim()) return;
    const geometry: Record<PageObject['kind'], Pick<PageObject, 'x' | 'y' | 'width' | 'height'>> = {
      bubble: { x: 290, y: 180, width: 620, height: 300 }, sfx: { x: 230, y: 520, width: 740, height: 230 },
      screentone: { x: 190, y: 280, width: 820, height: 1000 }, 'speed-lines': { x: 120, y: 180, width: 960, height: 1380 },
      'impact-burst': { x: 260, y: 380, width: 680, height: 680 },
    };
    const object: PageObject = { id: `obj_${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`, kind, text: text?.trim(), ...geometry[kind] };
    const nextPage = { ...page, mobile_objects: [...(page.mobile_objects ?? []), object] };
    const manifest = { ...current.manifest, pages: current.manifest.pages.map((item) => item.id === pageId ? nextPage : item) };
    try {
      const archive = await composePageOra(manifest.project.id, pageId, manifest.panels, manifest.takes, nextPage.mobile_objects);
      await savePageAsset(manifest.project.id, pageId, archive);
      await saveProjectFile(manifest.project.id, nextPage.file, archive);
      await persist(manifest, current.script);
      setNotice(`${kind.replace('-', ' ')} added to ${page.label}.`);
    } catch (error) { setNotice(message(error)); }
  };

  const removePageObject = async (pageId: string, objectId: string) => {
    if (!current) return;
    const page = current.manifest.pages.find((item) => item.id === pageId);
    if (!page) return;
    const objects = (page.mobile_objects ?? []).filter((item) => item.id !== objectId);
    const nextPage = { ...page, mobile_objects: objects };
    const manifest = { ...current.manifest, pages: current.manifest.pages.map((item) => item.id === pageId ? nextPage : item) };
    try {
      const archive = await composePageOra(manifest.project.id, pageId, manifest.panels, manifest.takes, objects);
      await savePageAsset(manifest.project.id, pageId, archive);
      await saveProjectFile(manifest.project.id, nextPage.file, archive);
      await persist(manifest, current.script);
    } catch (error) { setNotice(message(error)); }
  };

  const movePageObject = async (pageId: string, objectId: string, x: number, y: number) => {
    if (!current) return;
    const page = current.manifest.pages.find((item) => item.id === pageId);
    if (!page) return;
    const objects = (page.mobile_objects ?? []).map((object) => object.id === objectId ? { ...object, x, y } : object);
    const nextPage = { ...page, mobile_objects: objects };
    const manifest = { ...current.manifest, pages: current.manifest.pages.map((item) => item.id === pageId ? nextPage : item) };
    try {
      const archive = await composePageOra(manifest.project.id, pageId, manifest.panels, manifest.takes, objects);
      await savePageAsset(manifest.project.id, pageId, archive);
      await saveProjectFile(manifest.project.id, page.file, archive);
      await persist(manifest, current.script);
    } catch (error) { setNotice(message(error)); }
  };

  const movePagePanel = async (pageId: string, panelId: string, x: number, y: number) => {
    if (!current) return;
    const page = current.manifest.pages.find((item) => item.id === pageId);
    const target = current.manifest.panels.find((panel) => panel.id === panelId);
    if (!page || !target?.placement || target.placement.page !== pageId) return;
    const [, , width, height] = target.placement.frame;
    const panels = current.manifest.panels.map((panel) => panel.id === panelId
      ? { ...panel, placement: { ...panel.placement!, frame: [x, y, width, height] as [number, number, number, number] } }
      : panel);
    const manifest = { ...current.manifest, panels };
    try {
      const archive = await composePageOra(manifest.project.id, pageId, panels, manifest.takes, page.mobile_objects ?? []);
      await savePageAsset(manifest.project.id, pageId, archive);
      await saveProjectFile(manifest.project.id, page.file, archive);
      await persist(manifest, current.script);
    } catch (error) { setNotice(message(error)); }
  };

  const addCastMember = async () => {
    if (!current) return;
    const name = window.prompt('Character name');
    if (!name?.trim()) return;
    if (current.manifest.cast.some((person) => person.name.toLowerCase() === name.trim().toLowerCase())) {
      setNotice('That character is already in the cast.');
      return;
    }
    try { await persist({ ...current.manifest, cast: [...current.manifest.cast, { name: name.trim(), aliases: [], notes: '' }] }, current.script); }
    catch (error) { setNotice(message(error)); }
  };

  const addReference = (characterName: string) => {
    if (!current) return;
    const picker = document.createElement('input');
    picker.type = 'file'; picker.accept = 'image/png,image/jpeg,image/webp';
    picker.onchange = () => {
      const file = picker.files?.[0];
      if (!file || !current) return;
      void (async () => {
        try {
          const extension = file.name.split('.').pop()?.toLowerCase() || 'png';
          const safeName = characterName.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
          const path = `characters/${safeName}/references/${crypto.randomUUID()}.${extension}`;
          await saveProjectFile(current.manifest.project.id, path, new Uint8Array(await file.arrayBuffer()));
          const cast = current.manifest.cast.map((person) => {
            if (person.name !== characterName) return person;
            const references = Array.isArray(person.references) ? person.references : [];
            return { ...person, references: [...references, path] };
          });
          await persist({ ...current.manifest, cast }, current.script);
          setNotice(`Reference image added for ${characterName}.`);
        } catch (error) { setNotice(message(error)); }
      })();
    };
    picker.click();
  };

  const addCollectionEntry = async (kind: 'location' | 'prop') => {
    if (!current) return;
    const name = window.prompt(kind === 'location' ? 'Location name' : 'Prop name');
    if (!name?.trim()) return;
    const key = kind === 'location' ? 'locations' : 'props';
    const values = current.manifest[key] ?? [];
    if (values.some((item) => item.name.toLowerCase() === name.trim().toLowerCase())) {
      setNotice(`That ${kind} is already in the project.`);
      return;
    }
    try { await persist({ ...current.manifest, [key]: [...values, { name: name.trim(), notes: '' }] }, current.script); }
    catch (error) { setNotice(message(error)); }
  };

  const workspace = useMemo(() => current && <Workspace
    saved={current} tab={tab} engineMessage={engineMessage} busy={busy}
    onTab={setTab} onScript={(script) => void persist(current.manifest, script)}
    onAddPanel={() => void addPanel()} onAddPage={() => void addPage()}
    onParseScript={() => void parseScript()}
    onChangePanel={(id, patch) => void changePanel(id, patch)} onAddCast={() => void addCastMember()}
    onPlacePanel={(panelId, pageId) => void placePanel(panelId, pageId)}
    onAddPageObject={(pageId, kind) => void addPageObject(pageId, kind)}
    onRemovePageObject={(pageId, objectId) => void removePageObject(pageId, objectId)}
    onMovePageObject={(pageId, objectId, x, y) => void movePageObject(pageId, objectId, x, y)}
    onMovePagePanel={(pageId, panelId, x, y) => void movePagePanel(pageId, panelId, x, y)}
    onAddReference={(name) => addReference(name)}
    onAddCollectionEntry={(kind) => void addCollectionEntry(kind)}
    onChapter={(chapter) => void persist({ ...current.manifest, project: { ...current.manifest.project, chapter } }, current.script)}
    onRestoreRevision={(script) => { setTab('story'); void persist(current.manifest, script); }}
    onExport={async (width, sliceHeight) => {
      setBusy(true);
      try {
        const slices = await makeVerticalComic(current.manifest, width, sliceHeight);
        await saveVerticalArchive(current.manifest.project.title, slices);
        setNotice(`Vertical archive saved (${slices.length} slice${slices.length === 1 ? '' : 's'}).`);
      } catch (error) { setNotice(message(error)); }
      finally { setBusy(false); }
    }}
    onExportProject={async () => {
      setBusy(true);
      try { await exportProjectArchive(current); setNotice('Project archive exported.'); }
      catch (error) { setNotice(message(error)); }
      finally { setBusy(false); }
    }}
    onExportBook={async (format) => {
      setBusy(true);
      try { await exportBook(current.manifest, format); setNotice(`${format.toUpperCase()} export ready.`); }
      catch (error) { setNotice(message(error)); }
      finally { setBusy(false); }
    }}
  />, [current, tab, engineMessage, busy, persist]);

  return <IonApp>
    <IonPage>
      <IonHeader translucent={false}>
        <IonToolbar className="brand-toolbar">
          <IonTitle>{current ? current.manifest.project.title : 'Imanganation'}</IonTitle>
          {current && <IonButton slot="end" fill="clear" onClick={() => setCurrent(null)}>Projects</IonButton>}
        </IonToolbar>
      </IonHeader>
      <IonContent fullscreen>
        {current ? workspace : <main className="home-shell">
          <section className="hero-card">
            <span className="eyebrow">MAKE YOUR NEXT MANGA</span>
            <h1>Stories into panels.<br />Panels into worlds.</h1>
            <p>Plan stories, gather references and assemble pages in one creative workspace.</p>
            <IonButton onClick={() => void startProject()}><FolderPlus size={18} />&nbsp; New project</IonButton>
          </section>
          <div className="section-heading"><div><span className="eyebrow">ON THIS DEVICE</span><h2>Your projects</h2></div><span className="count-pill">{projects.length}</span></div>
          <input ref={importInput} className="visually-hidden" type="file" accept=".zip,.imanga,application/zip" onChange={(event) => void importProject(event.target.files?.[0])}/>
          {projects.length === 0 ? <div className="empty-state"><div className="empty-icon"><BookOpen /></div><h3>Your next story starts here</h3><p>Create a project to begin writing, organizing panels and laying out pages.</p><IonButton fill="outline" onClick={() => importInput.current?.click()} disabled={busy}>Import a project ZIP</IonButton></div> :
            <div className="project-grid">{projects.map((project) => <IonCard key={project.manifest.project.id} className="project-card" button onClick={() => openProject(project)}>
              <div className="project-art"><div className="art-sun"/><div className="art-stripe"/><span>MANGA<br/>PROJECT</span></div>
              <IonCardHeader><IonCardSubtitle>{project.manifest.project.chapter || 'Untitled chapter'} · {project.manifest.project.reading_order.toUpperCase()}</IonCardSubtitle><IonCardTitle>{project.manifest.project.title}</IonCardTitle></IonCardHeader>
              <IonCardContent><span>{project.manifest.panels.length} panels</span><span>{project.manifest.pages.length} pages</span><button className="text-action" onClick={(event) => { event.stopPropagation(); void removeProject(project); }}>Delete</button></IonCardContent>
            </IonCard>)}</div>}
          {projects.length > 0 && <IonButton className="import-button" fill="outline" onClick={() => importInput.current?.click()} disabled={busy}>Import project ZIP</IonButton>}
          <p className="local-note">Projects are stored locally on this device.</p>
        </main>}
      </IonContent>
      {current && <nav className="bottom-nav" aria-label="Project workspace">
        {sections.map(({ id, label, icon: Icon }) => <button className={tab === id ? 'nav-item active' : 'nav-item'} key={id} onClick={() => setTab(id)} aria-current={tab === id ? 'page' : undefined}><Icon size={20}/><span>{label}</span></button>)}
      </nav>}
      <IonToast isOpen={!!notice} message={notice} duration={2600} onDidDismiss={() => setNotice('')} position="bottom" />
    </IonPage>
  </IonApp>;
}

interface WorkspaceProps {
  saved: SavedProject;
  tab: WorkspaceTab;
  engineMessage: string;
  busy: boolean;
  onTab: (tab: WorkspaceTab) => void;
  onScript: (script: string) => void;
  onAddPanel: () => void;
  onAddPage: () => void;
  onParseScript: () => void;
  onChangePanel: (id: string, patch: Partial<MangaPanel>) => void;
  onAddCast: () => void;
  onPlacePanel: (panelId: string, pageId: string) => void;
  onAddPageObject: (pageId: string, kind: PageObject['kind']) => void;
  onRemovePageObject: (pageId: string, objectId: string) => void;
  onMovePageObject: (pageId: string, objectId: string, x: number, y: number) => void;
  onMovePagePanel: (pageId: string, panelId: string, x: number, y: number) => void;
  onAddReference: (characterName: string) => void;
  onAddCollectionEntry: (kind: 'location' | 'prop') => void;
  onChapter: (chapter: string) => void;
  onRestoreRevision: (script: string) => void;
  onExport: (width: number, sliceHeight: number) => Promise<void>;
  onExportProject: () => Promise<void>;
  onExportBook: (format: 'cbz' | 'pdf' | 'pages-zip') => Promise<void>;
}

function Workspace(props: WorkspaceProps) {
  const { saved, tab } = props;
  const [preset, setPreset] = useState<keyof typeof VERTICAL_PRESETS>('webtoon');
  const [exportWidth, setExportWidth] = useState<number>(VERTICAL_PRESETS.webtoon.width);
  const [sliceHeight, setSliceHeight] = useState<number>(VERTICAL_PRESETS.webtoon.sliceHeight);
  const [selectedToolPage, setSelectedToolPage] = useState(saved.manifest.pages[0]?.id ?? '');
  const [scriptDraft, setScriptDraft] = useState(saved.script);
  useEffect(() => {
    if (!saved.manifest.pages.some((page) => page.id === selectedToolPage)) setSelectedToolPage(saved.manifest.pages[0]?.id ?? '');
  }, [saved.manifest.pages, selectedToolPage]);
  useEffect(() => { setScriptDraft(saved.script); }, [saved.manifest.project.id, saved.script]);
  useEffect(() => {
    if (scriptDraft === saved.script) return;
    const timer = window.setTimeout(() => props.onScript(scriptDraft), 400);
    return () => window.clearTimeout(timer);
  }, [scriptDraft]);
  const summary = `${saved.manifest.panels.length} panels · ${saved.manifest.pages.length} pages · ${saved.manifest.cast.length} characters`;

  return <main className="workspace-shell">
    <div className="workspace-overview"><div><span className="eyebrow">MANGA WORKSPACE</span><h1>{saved.manifest.project.title}</h1><p>{summary}</p></div><div className="reading-badge">{saved.manifest.project.reading_order === 'rtl' ? '右から左 · RTL' : '左から右 · LTR'}</div></div>
    {tab === 'story' && <section className="workspace-section">
      <div className="section-heading"><div><span className="eyebrow">SCRIPT</span><h2>Story draft</h2></div><IonButton fill="outline" size="small" onClick={props.onParseScript}>Parse script</IonButton></div>
      <IonItem className="field-item"><IonInput label="Chapter" labelPlacement="stacked" value={saved.manifest.project.chapter ?? ''} placeholder="Chapter 1" onIonChange={(event) => props.onChapter(event.detail.value ?? '')} /></IonItem>
      <div className="script-editor"><IonTextarea value={scriptDraft} autoGrow={false} placeholder={'PAGE 1\n[SCENE: Rooftop — evening]\nPANEL 1\n[SHOT: wide shot]\n[CHARACTERS: Mio]\n[ACTION]\nMio watches the first stars appear above the city.'} onIonInput={(event) => setScriptDraft(event.detail.value ?? '')} rows={17} /></div>
      <p className="helper-text">Canonical script text is saved as you edit. Use PAGE and PANEL headings to organize the story.</p>
      {(saved.revisions?.length ?? 0) > 0 && <details className="revision-list"><summary>Earlier drafts ({saved.revisions!.length})</summary>{[...saved.revisions!].reverse().map((revision, index) => <div className="revision-row" key={`${revision.created}-${index}`}><time>{new Date(revision.created).toLocaleString()}</time><button onClick={() => { if (window.confirm('Restore this script revision? Your current draft will remain in revision history.')) props.onRestoreRevision(revision.script); }}>Restore</button></div>)}</details>}
      <div className="quick-actions"><IonButton fill="outline" onClick={props.onAddPanel}><ImagePlus size={17}/>&nbsp; Add a panel</IonButton><IonButton fill="outline" onClick={props.onAddPage}><Layers3 size={17}/>&nbsp; Add a page</IonButton></div>
    </section>}

    {tab === 'panels' && <section className="workspace-section">
      <div className="section-heading"><div><span className="eyebrow">PRODUCTION</span><h2>Panels</h2></div><IonButton onClick={props.onAddPanel}><ImagePlus size={17}/>&nbsp; Add</IonButton></div>
      {saved.manifest.panels.length === 0 ? <Empty title="No panels yet" detail="Add a panel and write its action, cast and dialogue."/> : <div className="panel-list">{saved.manifest.panels.map((panel) => <article className="panel-card" key={panel.id}>
        <div className="panel-card-top"><span className="panel-number">{panel.label.page}.{panel.label.panel}</span><span className="panel-status">{panel.active_take ? 'Take selected' : 'Needs artwork'}</span></div>
        <IonItem className="field-item"><IonTextarea label="Panel action" labelPlacement="stacked" value={panel.action} autoGrow onIonChange={(event) => props.onChangePanel(panel.id, { action: event.detail.value ?? '' })}/></IonItem>
        <IonItem className="field-item"><IonInput label="Location" labelPlacement="stacked" value={panel.location ?? ''} onIonChange={(event) => props.onChangePanel(panel.id, { location: event.detail.value ?? '' })}/></IonItem>
        <IonItem className="field-item"><IonInput label="Camera / shot" labelPlacement="stacked" value={panel.camera ?? ''} placeholder="Wide shot" onIonChange={(event) => props.onChangePanel(panel.id, { camera: event.detail.value ?? '' })}/></IonItem>
        <IonItem className="field-item"><IonInput label="Cast" labelPlacement="stacked" value={(panel.characters ?? []).map((person) => person.name).join(', ')} placeholder="Mio, Yuki" onIonChange={(event) => props.onChangePanel(panel.id, { characters: (event.detail.value ?? '').split(',').map((name) => name.trim()).filter(Boolean).map((name) => ({ name, version: panel.characters?.find((person) => person.name === name)?.version ?? null })) })}/></IonItem>
        {(panel.dialogue ?? []).map((line, index) => <div className="dialogue-row" key={`${panel.id}-line-${index}`}>
          <IonItem className="field-item"><IonInput label="Speaker" labelPlacement="stacked" value={line.speaker} onIonChange={(event) => props.onChangePanel(panel.id, { dialogue: (panel.dialogue ?? []).map((item, row) => row === index ? { ...item, speaker: event.detail.value ?? '' } : item) })}/></IonItem>
          <IonItem className="field-item"><IonTextarea label={line.kind === 'thought' ? 'Thought' : 'Dialogue'} labelPlacement="stacked" autoGrow value={line.text} onIonChange={(event) => props.onChangePanel(panel.id, { dialogue: (panel.dialogue ?? []).map((item, row) => row === index ? { ...item, text: event.detail.value ?? '' } : item) })}/></IonItem>
        </div>)}
        <IonItem className="field-item"><IonInput label="Sound effects (comma-separated)" labelPlacement="stacked" value={(panel.sfx ?? []).join(', ')} onIonChange={(event) => props.onChangePanel(panel.id, { sfx: (event.detail.value ?? '').split(',').map((value) => value.trim()).filter(Boolean) })}/></IonItem>
        <IonButton fill="clear" size="small" onClick={() => {
          const speaker = window.prompt('Speaker name');
          if (!speaker?.trim()) return;
          const text = window.prompt('Dialogue or thought');
          if (!text?.trim()) return;
          const kind = window.confirm('Make this a thought bubble?') ? 'thought' : 'speech';
          props.onChangePanel(panel.id, { dialogue: [...(panel.dialogue ?? []), { speaker: speaker.trim(), text: text.trim(), kind }] });
        }}>Add dialogue line</IonButton>
      </article>)}</div>}
    </section>}

    {tab === 'pages' && <section className="workspace-section">
      <div className="section-heading"><div><span className="eyebrow">PAGE LAYOUT</span><h2>Pages</h2></div><IonButton onClick={props.onAddPage}><Layers3 size={17}/>&nbsp; New page</IonButton></div>
      {saved.manifest.pages.length === 0 ? <Empty title="Your page canvas is ready" detail="Create a blank layered OpenRaster page, then arrange your panels." action={props.onAddPage}/> : <div className="page-grid">{saved.manifest.pages.map((page) => <article className="page-card" key={page.id}><div className="page-canvas-wrap">{page.file.toLowerCase().endsWith('.ora') ? <Suspense fallback={<div className="canvas-loading">Loading page preview…</div>}><PageCanvas projectId={saved.manifest.project.id} page={page} panels={saved.manifest.panels} onMoveObject={(objectId, x, y) => props.onMovePageObject(page.id, objectId, x, y)} onMovePanel={(panelId, x, y) => props.onMovePagePanel(page.id, panelId, x, y)}/></Suspense> : <div className="canvas-unavailable"><strong>{page.file.split('.').pop()?.toUpperCase() ?? 'Page'} page preserved</strong><span>Android can keep this source file, but cannot preview or edit it yet.</span></div>}</div><div className="page-card-caption"><strong>{page.label}</strong><span>{page.file.split('.').pop()?.toUpperCase()} · 1200 × 1800</span></div>
        {saved.manifest.panels.filter((panel) => !panel.placement?.page || panel.placement.page !== page.id).length > 0 && <div className="place-panel-list"><strong>Unplaced panels</strong>{saved.manifest.panels.filter((panel) => !panel.placement?.page || panel.placement.page !== page.id).map((panel) => <button key={panel.id} onClick={() => props.onPlacePanel(panel.id, page.id)}>Place panel {panel.label.page}.{panel.label.panel}<span>+</span></button>)}</div>}
      </article>)}</div>}
      <p className="helper-text">Page files use OpenRaster and can be opened in GIMP. Existing XCF page assets are left untouched.</p>
    </section>}

    {tab === 'people' && <section className="workspace-section">
      <div className="section-heading"><div><span className="eyebrow">CAST & REFERENCES</span><h2>Characters</h2></div><IonButton onClick={props.onAddCast}><Users size={17}/>&nbsp; Add</IonButton></div>
      {saved.manifest.cast.length === 0 ? <Empty title="Build your cast" detail="Add character names, then attach reference art for your local generation workflow." action={props.onAddCast}/> : <IonList className="people-list">{saved.manifest.cast.map((person) => <IonItem key={person.name}><div className="avatar-mark">{person.name.slice(0, 1).toUpperCase()}</div><IonLabel><h3>{person.name}</h3><p>{person.notes || 'No character notes yet'}</p></IonLabel><span className="reference-pill">{Array.isArray(person.references) ? person.references.length : 0} refs</span><IonButton fill="clear" size="small" onClick={() => props.onAddReference(person.name)}>Add art</IonButton></IonItem>)}</IonList>}
      <div className="secondary-library"><div className="library-heading"><h3>Locations & props</h3><div><IonButton fill="clear" size="small" onClick={() => props.onAddCollectionEntry('location')}>Add location</IonButton><IonButton fill="clear" size="small" onClick={() => props.onAddCollectionEntry('prop')}>Add prop</IonButton></div></div><p>{saved.manifest.locations?.length ?? 0} locations · {saved.manifest.props?.length ?? 0} props</p>{[...(saved.manifest.locations ?? []).map((item) => ({ ...item, kind: 'Location' })), ...(saved.manifest.props ?? []).map((item) => ({ ...item, kind: 'Prop' }))].map((item, index) => <div key={`${item.kind}-${item.name}-${index}`} className="object-row"><div><strong>{item.name}</strong><span>{item.kind}</span></div></div>)}</div>
    </section>}

    {tab === 'tools' && <section className="workspace-section">
      <div className="section-heading"><div><span className="eyebrow">LETTERING & EFFECTS</span><h2>Manga Tools</h2></div><span className="offline-pill">LAYERED</span></div>
      {saved.manifest.pages.length === 0 ? <Empty title="Create a page first" detail="Bubbles, sound effects, screentones and impact effects are added to a page layer." action={props.onAddPage}/> : <>
        <IonItem className="field-item"><IonLabel position="stacked">Target page</IonLabel><select className="native-select" value={selectedToolPage} onChange={(event) => setSelectedToolPage(event.target.value)}>{saved.manifest.pages.map((page) => <option key={page.id} value={page.id}>{page.label}</option>)}</select></IonItem>
        <div className="tool-grid">
          {([['bubble', 'Speech bubble'], ['sfx', 'Sound effect'], ['screentone', 'Screentone'], ['speed-lines', 'Speed lines'], ['impact-burst', 'Impact burst']] as const).map(([kind, label]) => <button key={kind} className="tool-card" onClick={() => props.onAddPageObject(selectedToolPage, kind)}><Sparkles size={19}/><strong>{label}</strong><span>Add to page layer</span></button>)}
        </div>
        <div className="objects-list"><h3>Page objects</h3>{(saved.manifest.pages.find((page) => page.id === selectedToolPage)?.mobile_objects ?? []).length === 0 ? <p className="helper-text">No lettering or effects yet.</p> : (saved.manifest.pages.find((page) => page.id === selectedToolPage)?.mobile_objects ?? []).map((object) => <div key={object.id} className="object-row"><div><strong>{object.kind.replace('-', ' ')}</strong><span>{object.text || 'Graphic effect'}</span></div><button onClick={() => props.onRemovePageObject(selectedToolPage, object.id)}>Remove</button></div>)}</div>
      </>}
    </section>}

    {tab === 'generate' && <section className="workspace-section">
      <div className="section-heading"><div><span className="eyebrow">LOCAL IMAGE CREATION</span><h2>Generate artwork</h2></div><span className="offline-pill">ON DEVICE</span></div>
      <div className="renderer-card"><div className="renderer-mark"><WandSparkles/></div><div><strong>Local renderer</strong><p>{props.engineMessage}</p></div></div>
      <IonItem className="field-item"><IonLabel position="stacked">Target panel</IonLabel><select className="native-select" defaultValue={saved.manifest.panels[0]?.id ?? ''}><option value="" disabled>Choose a panel</option>{saved.manifest.panels.map((panel) => <option key={panel.id} value={panel.id}>Page {panel.label.page}, panel {panel.label.panel}</option>)}</select></IonItem>
      <IonItem className="field-item"><IonTextarea label="Prompt" labelPlacement="stacked" placeholder="Describe the artwork to create…" rows={4}/></IonItem>
      <IonItem className="field-item"><IonInput label="Seed" labelPlacement="stacked" type="number" placeholder="Random"/></IonItem>
      <IonButton expand="block" disabled><WandSparkles size={17}/>&nbsp; Renderer integration pending</IonButton>
      <p className="helper-text">This build does not generate placeholder results. The UI boundary is ready for the Android native runtime.</p>
    </section>}

    {tab === 'export' && <section className="workspace-section">
      <div className="section-heading"><div><span className="eyebrow">SHARE YOUR STORY</span><h2>Vertical comic export</h2></div><span className="offline-pill">LOCAL</span></div>
      <div className="export-preview"><div className="preview-pages"><i/><i/><i/></div><div><strong>One tall reading flow</strong><p>Stack pages in the project’s reading order, add a consistent gap, then split into upload-ready images.</p></div></div>
      <IonItem className="field-item"><IonLabel position="stacked">Preset</IonLabel><select className="native-select" value={preset} onChange={(event) => { const key = event.target.value as keyof typeof VERTICAL_PRESETS; setPreset(key); setExportWidth(VERTICAL_PRESETS[key].width); setSliceHeight(VERTICAL_PRESETS[key].sliceHeight); }}>
        {Object.entries(VERTICAL_PRESETS).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}
      </select></IonItem>
      <div className="field-pair"><IonItem className="field-item"><IonInput label="Width (px)" labelPlacement="stacked" type="number" min="1" max="10000" value={exportWidth} onIonChange={(event) => setExportWidth(Number(event.detail.value) || 800)}/></IonItem><IonItem className="field-item"><IonInput label="Slice height (px)" labelPlacement="stacked" type="number" min="1" max="50000" value={sliceHeight} onIonChange={(event) => setSliceHeight(Number(event.detail.value) || 1280)}/></IonItem></div>
      <div className="export-details"><span>Pages in reading order</span><strong>{saved.manifest.pages.length}</strong><span>Format</span><strong>PNG slices in ZIP</strong></div>
      <IonButton expand="block" disabled={props.busy || saved.manifest.pages.length === 0} onClick={() => void props.onExport(exportWidth, sliceHeight)}><Download size={17}/>&nbsp; {props.busy ? 'Preparing export…' : 'Create vertical export'}</IonButton>
      <IonButton className="archive-button" expand="block" fill="outline" disabled={props.busy} onClick={() => void props.onExportProject()}>Export project archive</IonButton>
      <div className="export-format-grid">
        <IonButton fill="outline" disabled={props.busy || saved.manifest.pages.length === 0} onClick={() => void props.onExportBook('cbz')}>Export CBZ</IonButton>
        <IonButton fill="outline" disabled={props.busy || saved.manifest.pages.length === 0} onClick={() => void props.onExportBook('pdf')}>Export PDF</IonButton>
        <IonButton fill="outline" disabled={props.busy || saved.manifest.pages.length === 0} onClick={() => void props.onExportBook('pages-zip')}>Page images ZIP</IonButton>
      </div>
      {saved.manifest.pages.length === 0 && <p className="helper-text">Create at least one page first. Each page needs a local OpenRaster asset.</p>}
    </section>}
  </main>;
}

function Empty({ title, detail, action }: { title: string; detail: string; action?: () => void }) {
  return <div className="empty-state compact"><div className="empty-icon"><ImagePlus/></div><h3>{title}</h3><p>{detail}</p>{action && <IonButton fill="outline" onClick={action}>Get started</IonButton>}</div>;
}

function message(error: unknown): string { return error instanceof Error ? error.message : 'Something went wrong.'; }
