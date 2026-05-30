import fs from "node:fs/promises";
import path from "node:path";
import { mapHostWorkspacePath, workspacePathMappingFromEnv } from "./workspacePathMapping.js";

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

  const mapping = workspacePathMappingFromEnv();
  const resolvedFolders = [];
  for (const folder of folders) {
    if (!path.isAbsolute(folder)) throw routeError(400, "Workspace folder paths must be absolute.");

    const resolvedPath = path.resolve(folder);
    const candidatePaths = [...new Set([resolvedPath, mapHostWorkspacePath(resolvedPath, mapping)])];
    let stats;
    let visiblePath = resolvedPath;
    for (const candidatePath of candidatePaths) {
      try {
        stats = await fs.stat(candidatePath);
        visiblePath = candidatePath;
        break;
      } catch {
        stats = null;
      }
    }

    if (!stats) {
      const mappedPath = candidatePaths.find((candidatePath) => candidatePath !== resolvedPath);
      const message = mappedPath
        ? `Workspace folder does not exist: ${resolvedPath} (mapped to ${mappedPath})`
        : `Workspace folder does not exist: ${resolvedPath}`;
      throw routeError(400, message);
    }

    if (!stats.isDirectory()) throw routeError(400, `Workspace path must point to a directory: ${visiblePath}`);
    resolvedFolders.push(visiblePath);
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
