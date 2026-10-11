import type { MangaProject } from './project';
import { isMangaProject } from './project';

const LEGACY_STORE_KEY = 'imanganation.mobile.projects.v1';
const DB_NAME = 'imanganation-mobile-projects';
const STORE = 'projects';

export interface SavedProject {
  manifest: MangaProject;
  script: string;
  savedAt: string;
  revisions?: Array<{ created: string; script: string }>;
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE, { keyPath: 'manifest.project.id' });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error('Could not open local project storage.'));
  });
}

async function readAll(db: IDBDatabase): Promise<SavedProject[]> {
  return new Promise((resolve, reject) => {
    const request = db.transaction(STORE, 'readonly').objectStore(STORE).getAll();
    request.onsuccess = () => resolve(request.result.filter(isSavedProject));
    request.onerror = () => reject(request.error ?? new Error('Could not read local projects.'));
  });
}

async function migrateLegacy(db: IDBDatabase, current: SavedProject[]): Promise<SavedProject[]> {
  if (current.length || !localStorage.getItem(LEGACY_STORE_KEY)) return current;
  let legacy: unknown;
  try { legacy = JSON.parse(localStorage.getItem(LEGACY_STORE_KEY) ?? 'null'); }
  catch { return current; }
  if (!Array.isArray(legacy)) return current;
  const projects = legacy.filter(isSavedProject);
  if (!projects.length) return current;
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    projects.forEach((project) => tx.objectStore(STORE).put(project));
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error('Could not migrate saved projects.'));
  });
  return projects;
}

export async function listProjects(): Promise<SavedProject[]> {
  const db = await openDb();
  try { return await migrateLegacy(db, await readAll(db)); }
  finally { db.close(); }
}

export async function saveProject(project: SavedProject): Promise<void> {
  const db = await openDb();
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).put(project);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error ?? new Error('Could not save the project.'));
      tx.onabort = () => reject(tx.error ?? new Error('Saving the project was interrupted.'));
    });
  } finally { db.close(); }
}

export async function deleteProject(id: string): Promise<void> {
  const db = await openDb();
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).delete(id);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error ?? new Error('Could not delete the project.'));
    });
  } finally { db.close(); }
}

function isSavedProject(value: unknown): value is SavedProject {
  if (!value || typeof value !== 'object') return false;
  const item = value as SavedProject;
  return isMangaProject(item.manifest) && typeof item.script === 'string'
    && (!item.revisions || Array.isArray(item.revisions));
}
