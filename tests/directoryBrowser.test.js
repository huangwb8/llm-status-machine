import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { createApp } from "../server/index.js";
import { listWorkspaceDirectories } from "../server/directoryBrowser.js";

test("directory browser lists folders inside the configured workspace root", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "workspace-browser-"));
  await fs.mkdir(path.join(root, "project-b"));
  await fs.mkdir(path.join(root, "project-a"));
  await fs.writeFile(path.join(root, "note.txt"), "not a directory");

  const result = await listWorkspaceDirectories({
    env: { WORKSPACES_TARGET: root },
    cwd: "/unused"
  });
  const resolvedRoot = await fs.realpath(root);

  assert.equal(result.root, resolvedRoot);
  assert.equal(result.path, resolvedRoot);
  assert.equal(result.parent, null);
  assert.deepEqual(result.directories, [
    { name: "project-a", path: path.join(resolvedRoot, "project-a") },
    { name: "project-b", path: path.join(resolvedRoot, "project-b") }
  ]);
});

test("directory browser rejects paths outside the configured workspace root", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "workspace-browser-root-"));
  const outside = await fs.mkdtemp(path.join(os.tmpdir(), "workspace-browser-outside-"));

  await assert.rejects(
    () => listWorkspaceDirectories({ requestedPath: outside, env: { WORKSPACES_TARGET: root } }),
    (error) => error.status === 403 && /outside the configured workspace root/i.test(error.message)
  );
});

test("directory browser API returns a browsable fallback payload", async () => {
  const payload = {
    root: "/workspaces",
    path: "/workspaces",
    parent: null,
    directories: [{ name: "project", path: "/workspaces/project" }]
  };
  const app = createApp({
    isDirectoryPickerRequestAllowedImpl: () => true,
    listWorkspaceDirectoriesImpl: async ({ requestedPath }) => {
      assert.equal(requestedPath, "/workspaces");
      return payload;
    }
  });
  const server = app.listen(0);

  try {
    const { port } = server.address();
    const response = await fetch(`http://127.0.0.1:${port}/api/system/directories?path=${encodeURIComponent("/workspaces")}`);

    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), payload);
  } finally {
    await new Promise((resolve, reject) => server.close((error) => (error ? reject(error) : resolve())));
  }
});
