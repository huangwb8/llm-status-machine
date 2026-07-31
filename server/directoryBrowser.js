import fs from "node:fs/promises";
import path from "node:path";

function routeError(status, message) {
  const error = new Error(message);
  error.status = status;
  return error;
}

function isWithinRoot(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function configuredRoot({ env = process.env, cwd = process.cwd() } = {}) {
  const root = env.WORKSPACES_TARGET || env.WORKSPACES_MOUNT || cwd;
  return path.resolve(cwd, root);
}

async function resolveDirectory(directoryPath, label) {
  let resolved;
  try {
    resolved = await fs.realpath(directoryPath);
    const stats = await fs.stat(resolved);
    if (!stats.isDirectory()) throw routeError(400, `${label} must point to a directory.`);
  } catch (error) {
    if (error.status) throw error;
    throw routeError(400, `${label} is not accessible: ${directoryPath}`);
  }
  return resolved;
}

export async function listWorkspaceDirectories({
  requestedPath,
  env = process.env,
  cwd = process.cwd()
} = {}) {
  const root = await resolveDirectory(configuredRoot({ env, cwd }), "Configured workspace root");
  const candidate = await resolveDirectory(requestedPath || root, "Workspace directory");
  if (!isWithinRoot(root, candidate)) {
    throw routeError(403, `Workspace directory is outside the configured workspace root: ${root}`);
  }

  const entries = await fs.readdir(candidate, { withFileTypes: true });
  const directories = entries
    .filter((entry) => entry.isDirectory())
    .map((entry) => ({ name: entry.name, path: path.join(candidate, entry.name) }))
    .sort((left, right) => left.name.localeCompare(right.name));

  return {
    root,
    path: candidate,
    parent: candidate === root ? null : path.dirname(candidate),
    directories
  };
}
