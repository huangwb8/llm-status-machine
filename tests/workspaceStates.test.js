import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { createStateFromDirectory, workspaceNameFromPath } from "../server/workspaceStates.js";

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
