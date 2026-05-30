import path from "node:path";

function normalizeAbsolute(value) {
  const text = String(value || "").trim();
  if (!text || !path.isAbsolute(text)) return "";
  return path.resolve(text);
}

function isSameOrChildPath(candidate, root) {
  if (candidate === root) return true;
  const relative = path.relative(root, candidate);
  return Boolean(relative) && !relative.startsWith("..") && !path.isAbsolute(relative);
}

export function workspacePathMappingFromEnv(env = process.env) {
  const hostRoot = normalizeAbsolute(env.WORKSPACES_MOUNT);
  const containerRoot = normalizeAbsolute(env.WORKSPACES_TARGET);
  if (!hostRoot || !containerRoot || hostRoot === containerRoot) return null;
  return { hostRoot, containerRoot };
}

export function mapHostWorkspacePath(folderPath, mapping = workspacePathMappingFromEnv()) {
  const resolvedPath = normalizeAbsolute(folderPath);
  if (!resolvedPath || !mapping) return resolvedPath || String(folderPath || "").trim();
  if (!isSameOrChildPath(resolvedPath, mapping.hostRoot)) return resolvedPath;

  const relativePath = path.relative(mapping.hostRoot, resolvedPath);
  return path.resolve(mapping.containerRoot, relativePath);
}
