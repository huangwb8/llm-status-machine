import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createFileStore } from "../server/storage/fileStore.js";

test("file storage preserves collection CRUD and aggregate store shape", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "llm-status-store-"));
  const store = createFileStore({ root });

  const prompt = await store.createItem("prompts", {
    id: "prompt-test",
    name: "Test prompt",
    body: "Review this"
  });

  assert.equal(prompt.id, "prompt-test");
  assert.equal(prompt.name, "Test prompt");

  const updated = await store.updateItem("prompts", "prompt-test", { name: "Updated" });
  assert.equal(updated.name, "Updated");

  const aggregate = await store.readStore();
  assert.equal(aggregate.prompts.some((item) => item.id === "prompt-test"), true);
  assert.deepEqual(aggregate.runs, []);
  assert.deepEqual(aggregate.devtoolsApiKeys, []);
  assert.deepEqual(aggregate.devtoolsConnections, []);

  const key = await store.createItem("devtoolsApiKeys", {
    id: "key-test",
    name: "Test key",
    keyHash: "hash",
    keyPrefix: "lsm_test"
  });
  assert.equal(key.id, "key-test");

  const connection = await store.createItem("devtoolsConnections", {
    id: "connection-test",
    keyId: "key-test",
    clientName: "codex"
  });
  assert.equal(connection.clientName, "codex");

  const updatedConnection = await store.updateItem("devtoolsConnections", "connection-test", { lastError: "lost" });
  assert.equal(updatedConnection.lastError, "lost");

  assert.equal(await store.deleteItem("devtoolsApiKeys", "key-test"), true);
  assert.equal(await store.getItem("devtoolsApiKeys", "key-test"), null);

  assert.equal(await store.deleteItem("prompts", "prompt-test"), true);
  assert.equal(await store.getItem("prompts", "prompt-test"), null);
});

test("file storage serializes concurrent mutations without losing updates", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "llm-status-store-concurrent-"));
  const store = createFileStore({ root });

  await Promise.all(
    Array.from({ length: 25 }, (_, index) =>
      store.mutateStore(async (state) => {
        await new Promise((resolve) => setTimeout(resolve, 5));
        state.prompts.push({
          id: `concurrent-prompt-${index}`,
          name: `Prompt ${index}`,
          body: "Concurrent write"
        });
      })
    )
  );

  const aggregate = await store.readStore();
  const created = aggregate.prompts.filter((prompt) => prompt.id.startsWith("concurrent-prompt-"));
  assert.equal(created.length, 25);
});
