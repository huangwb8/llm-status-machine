import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { createStateFromDirectory, normalizeWorkspaceState, workspaceNameFromPath } from "../server/workspaceStates.js";

test("workspaceNameFromPath derives the directory basename", () => {
  assert.equal(workspaceNameFromPath("/tmp/example-project/"), "example-project");
});

test("createStateFromDirectory validates and creates a workspace state", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "workspace-state-"));
  const created = [];

  const item = await createStateFromDirectory({
    folderPath: root,
    createItem: async (collection, attrs) => {
      created.push({ collection, attrs });
      return { id: "state-local", ...attrs };
    }
  });

  assert.equal(created[0].collection, "states");
  assert.equal(item.name, path.basename(root));
  assert.equal(item.path, root);
  assert.deepEqual(item.folders, [root]);
});

test("normalizeWorkspaceState accepts multiple workspace folders", async () => {
  const first = await fs.mkdtemp(path.join(os.tmpdir(), "workspace-state-a-"));
  const second = await fs.mkdtemp(path.join(os.tmpdir(), "workspace-state-b-"));

  const state = await normalizeWorkspaceState({
    folders: [first, second],
    description: "  multi root  "
  });

  assert.equal(state.name, `${path.basename(first)} + 1`);
  assert.equal(state.path, first);
  assert.deepEqual(state.folders, [first, second]);
  assert.equal(state.description, "multi root");
});

test("createStateFromDirectory rejects non-directories", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "workspace-state-file-"));
  const filePath = path.join(root, "note.txt");
  await fs.writeFile(filePath, "hello");

  await assert.rejects(
    () => createStateFromDirectory({ folderPath: filePath, createItem: async () => ({}) }),
    /Workspace path must point to a directory/
  );
});
