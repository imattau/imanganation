import type { MangaPanel } from './project';

interface CastEntry { name: string; aliases: string[]; notes: string; }
export interface ParsedCanonicalScript { panels: MangaPanel[]; cast: CastEntry[]; locations: Array<{ name: string; notes: string }>; problems: string[]; }

const PAGE = /^\s*PAGE\s+(\d+)\s*$/i;
const PANEL = /^\s*PANEL\s+(\d+)\s*$/i;
const HEADER = /^\s*\[\s*([a-z ]+?)\s*(?::\s*(.*?))?\s*\]\s*$/i;
const COVER = /^\s*COVER\s*$/i;

export async function parseCanonicalScript(text: string): Promise<ParsedCanonicalScript> {
  const lines = text.split(/\r?\n/);
  const panels: MangaPanel[] = [];
  const cast: CastEntry[] = [];
  const locations: Array<{ name: string; notes: string }> = [];
  const problems: string[] = [];
  let pageNumber = 1;
  let sceneHeading = '';
  let panel: Partial<MangaPanel> | undefined;
  let cover = false;
  let section = '';
  let dialogueKind = 'speech';
  let dialogue: Array<{ speaker: string; text: string; kind: string }> = [];
  let sfx: string[] = [];
  let actionLines: string[] = [];

  const finishPanel = async () => {
    if (!panel) return;
    const action = actionLines.join('\n').trim();
    const fingerprint = await panelFingerprint({
      scene_heading: panel.scene_heading || sceneHeading,
      location: panel.location || '',
      characters: panel.characters || [], action,
      camera: panel.camera || '', dialogue, sfx,
      aspect_ratio: panel.aspect_ratio || '', size: panel.size || '',
    });
    panels.push({
      id: `pnl_${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`,
      label: panel.label ?? { page: cover ? 0 : pageNumber, panel: panels.length + 1 },
      scene_heading: panel.scene_heading || sceneHeading,
      location: panel.location || '',
      characters: panel.characters ?? inferSpeakers(dialogue),
      action,
      camera: panel.camera || '',
      expressions: panel.expressions ?? {},
      dialogue: [...dialogue], sfx: [...sfx], notes: panel.notes || '',
      flashback: Boolean(panel.flashback),
      ...(cover ? { cover: true } : {}),
      ...(panel.aspect_ratio ? { aspect_ratio: panel.aspect_ratio } : {}),
      ...(panel.size ? { size: panel.size } : {}),
      source: fingerprint,
      status: 'unplaced', placement: null, takes: [], active_take: null,
    });
    panel = undefined; dialogue = []; sfx = []; actionLines = []; section = '';
  };

  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    const trimmed = line.trim();
    const lineNumber = index + 1;
    const pageMatch = PAGE.exec(line);
    const panelMatch = PANEL.exec(line);
    if (COVER.test(line)) { await finishPanel(); cover = true; panel = newPanel(0, 1); section = ''; continue; }
    if (pageMatch) { await finishPanel(); cover = false; pageNumber = Number(pageMatch[1]); sceneHeading = ''; section = ''; continue; }
    if (panelMatch) { await finishPanel(); cover = false; panel = newPanel(pageNumber, Number(panelMatch[1])); section = ''; continue; }
    if (!trimmed) { if (section === 'action' && panel) actionLines.push(''); continue; }

    const header = HEADER.exec(line);
    if (header) {
      const name = header[1].toUpperCase().replace(/\s+/g, ' ');
      const value = header[2]?.trim() ?? '';
      if (name === 'CHARACTERS' && !panel) {
        let cursor = index + 1;
        while (cursor < lines.length && !PAGE.test(lines[cursor]) && !PANEL.test(lines[cursor]) && !COVER.test(lines[cursor])) {
          const entry = lines[cursor];
          if (entry.trim() && !entry.startsWith(' ') && !entry.startsWith('\t')) {
            const match = /^(.+?)(?:\s*\(\s*(?:aka|a\.k\.a\.?)\s+([^)]*)\))?\s*:\s*(.*)$/i.exec(entry);
            if (match) cast.push({ name: canonicalName(match[1]), aliases: (match[2] ?? '').split(',').map((part) => canonicalName(part.trim())).filter(Boolean), notes: match[3].trim() });
            else problems.push(`Line ${cursor + 1}: expected a character entry in NAME: description format.`);
          }
          cursor++;
        }
        index = cursor - 1;
        continue;
      }
      if (name === 'LOCATIONS' && !panel) {
        let cursor = index + 1;
        while (cursor < lines.length && !PAGE.test(lines[cursor]) && !PANEL.test(lines[cursor]) && !COVER.test(lines[cursor])) {
          const entry = lines[cursor];
          if (entry.trim() && !entry.startsWith(' ') && !entry.startsWith('\t')) {
            const match = /^([^:]+?)\s*:\s*(.*)$/.exec(entry);
            if (match) locations.push({ name: canonicalName(match[1].split(/\s+[—–-]\s+/)[0]), notes: match[2].trim() });
            else problems.push(`Line ${cursor + 1}: expected a location entry in NAME: description format.`);
          }
          cursor++;
        }
        index = cursor - 1;
        continue;
      }
      if (name === 'SCENE') { sceneHeading = value; if (panel) panel.scene_heading = value; section = ''; continue; }
      if (name === 'FLASHBACK START') { if (panel) panel.flashback = true; section = ''; continue; }
      if (name === 'FLASHBACK END') { if (panel) panel.flashback = false; section = ''; continue; }
      if (!panel) { problems.push(`Line ${lineNumber}: ${name} must appear inside a PANEL or COVER.`); continue; }
      if (name === 'SHOT') { panel.camera = value; section = ''; continue; }
      if (name === 'CHARACTERS') { panel.characters = splitNames(value).map((character) => ({ name: resolveName(character, cast), version: null })); section = ''; continue; }
      if (name === 'LOCATION') { panel.location = value; section = ''; continue; }
      if (name === 'EXPRESSIONS') { panel.expressions = parseExpressions(value, cast); section = ''; continue; }
      if (name === 'FRAME') {
        const parts = value.toLowerCase().split(/[ ,]+/).filter(Boolean);
        const shape = parts.find((part) => ['wide', 'tall', 'square', '1:1', '2:1', '1:2', '3:2'].includes(part));
        const size = parts.find((part) => ['small', 'large', 'splash'].includes(part));
        if (shape) panel.aspect_ratio = ({ wide: '2:1', tall: '1:2', square: '1:1' } as Record<string, string>)[shape] ?? shape;
        if (size) panel.size = size;
        section = ''; continue;
      }
      if (name === 'ACTION' || name === 'DIALOGUE' || name === 'DIALOG' || name === 'SFX' || name === 'NOTES' || name === 'NOTE') {
        section = name === 'DIALOG' ? 'DIALOGUE' : name === 'NOTE' ? 'NOTES' : name;
        if (value) consumeLine(value, lineNumber);
        continue;
      }
      if (name.startsWith('END ')) { section = ''; continue; }
      problems.push(`Line ${lineNumber}: unrecognized script heading [${header[1]}].`);
      continue;
    }
    if (panel) consumeLine(trimmed, lineNumber);
    else if (PAGE.test(line) || PANEL.test(line)) continue;
    else if (!trimmed.startsWith('#')) problems.push(`Line ${lineNumber}: add this text inside a PANEL section.`);
  }
  await finishPanel();
  if (!panels.length) problems.push('No PAGE/PANEL or COVER sections were found. Prose scripts need a local language model parser.');
  return { panels, cast, locations, problems };

  function consumeLine(value: string, lineNumber: number) {
    if (section === 'DIALOGUE') {
      const match = /^([^:]+?)\s*:\s*(.+)$/.exec(value);
      if (!match) { problems.push(`Line ${lineNumber}: dialogue should be written SPEAKER: words.`); return; }
      const voice = /^(.*?)\s*\(\s*(thought|whisper|shout|narration)\s*\)$/i.exec(match[1]);
      dialogue.push({ speaker: canonicalName((voice?.[1] ?? match[1]).trim()), text: match[2].trim(), kind: voice?.[2]?.toLowerCase() ?? dialogueKind });
      return;
    }
    if (section === 'SFX') { sfx.push(value); return; }
    if (section === 'ACTION' || section === 'NOTES') {
      if (section === 'NOTES' && panel) panel.notes = [String(panel.notes ?? ''), value].filter(Boolean).join('\n');
      else actionLines.push(value);
      return;
    }
    if (!section && panel) actionLines.push(value);
  }
}

function newPanel(page: number, index: number): Partial<MangaPanel> { return { label: { page, panel: index }, dialogue: [], sfx: [], expressions: {}, notes: '' }; }

function canonicalName(value: string): string {
  const name = value.trim().replace(/\s+/g, ' ');
  return name === name.toUpperCase() ? name.toLowerCase().replace(/\b\p{L}/gu, (letter) => letter.toUpperCase()) : name;
}

function splitNames(value: string): string[] {
  return value.split(/\s*(?:,|&|\band\b)\s*/i).map((name) => name.trim()).filter(Boolean);
}

function resolveName(value: string, cast: CastEntry[]): string {
  const lower = value.toLowerCase();
  return cast.find((entry) => entry.name.toLowerCase() === lower || entry.aliases.some((alias) => alias.toLowerCase() === lower))?.name ?? canonicalName(value);
}

function inferSpeakers(dialogue: Array<{ speaker: string; text: string; kind: string }>): Array<{ name: string; version: null }> {
  return [...new Set(dialogue.filter((line) => line.kind !== 'narration').map((line) => line.speaker))].map((name) => ({ name, version: null }));
}

function parseExpressions(value: string, cast: CastEntry[]): Record<string, string> {
  const result: Record<string, string> = {};
  for (const entry of value.split(';')) {
    const split = entry.indexOf(':');
    if (split > 0) result[resolveName(entry.slice(0, split).trim(), cast)] = entry.slice(split + 1).trim();
  }
  return result;
}

async function panelFingerprint(value: Record<string, unknown>): Promise<string> {
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  const hash = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(hash)].map((byte) => byte.toString(16).padStart(2, '0')).join('').slice(0, 16);
}
