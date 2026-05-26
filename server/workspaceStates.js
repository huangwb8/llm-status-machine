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

export async function createStateFromDirectory({ folderPath, name, description, createItem }) {
  const normalizedPath = String(folderPath || "").trim();
  if (!normalizedPath) throw routeError(400, "Workspace folder path is required.");
  if (!path.isAbsolute(normalizedPath)) throw routeError(400, "Workspace folder path must be absolute.");

  const resolvedPath = path.resolve(normalizedPath);
  let stats;
  try {
    stats = await fs.stat(resolvedPath);
  } catch {
    throw routeError(400, "Workspace folder does not exist.");
  }

  if (!stats.isDirectory()) throw routeError(400, "Workspace path must point to a directory.");

  return createItem("states", {
    name: String(name || "").trim() || workspaceNameFromPath(resolvedPath),
    path: resolvedPath,
    description: String(description || "").trim()
  });
}
