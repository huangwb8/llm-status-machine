import fs from "node:fs/promises";
import path from "node:path";

function routeError(status, message) {
  const error = new Error(message);
  error.status = status;
  return error;
}

export function workspaceNameFromPath(folderPath) {
  const resolved = path.resolve(folderPath);
  return path.basename(resolved) || resolved;
}

export function normalizeWorkspaceFolders(value) {
  const rawFolders = Array.isArray(value?.folders) ? value.folders : [];
  const candidates = [...rawFolders, value?.path, value?.folderPath]
    .map((folder) => String(folder || "").trim())
    .filter(Boolean);
  return [...new Set(candidates)];
}

export async function validateWorkspaceFolders(value) {
  const folders = normalizeWorkspaceFolders(value);
  if (!folders.length) throw routeError(400, "At least one workspace folder path is required.");

  const resolvedFolders = [];
  for (const folder of folders) {
    if (!path.isAbsolute(folder)) throw routeError(400, "Workspace folder paths must be absolute.");

    const resolvedPath = path.resolve(folder);
    let stats;
    try {
      stats = await fs.stat(resolvedPath);
    } catch {
      throw routeError(400, `Workspace folder does not exist: ${resolvedPath}`);
    }

    if (!stats.isDirectory()) throw routeError(400, `Workspace path must point to a directory: ${resolvedPath}`);
    resolvedFolders.push(resolvedPath);
  }

  return [...new Set(resolvedFolders)];
}

export function workspaceFoldersFromState(state) {
  return normalizeWorkspaceFolders(state);
}

export async function normalizeWorkspaceState(attrs = {}, existing = {}) {
  const folders = await validateWorkspaceFolders({ ...existing, ...attrs });
  const fallbackName = folders.length === 1 ? workspaceNameFromPath(folders[0]) : `${workspaceNameFromPath(folders[0])} + ${folders.length - 1}`;

  return {
    ...attrs,
    name: String(attrs.name ?? existing.name ?? "").trim() || fallbackName,
    path: folders[0],
    folders,
    description: String(attrs.description ?? existing.description ?? "").trim()
  };
}

export async function createStateFromDirectory({ folderPath, folderPaths, name, description, createItem }) {
  const folders = Array.isArray(folderPaths) ? folderPaths : [folderPath];
  return createItem(
    "states",
    await normalizeWorkspaceState({
      name,
      path: folders[0],
      folders,
      description
    })
  );
}
