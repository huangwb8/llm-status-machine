import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createFileStore } from "../server/storage/fileStore.js";
import { createPostgresStore } from "../server/storage/postgresStore.js";

function createFakeDocumentPool({ runs = [], sessions = [], events = [] } = {}) {
  const documents = new Map();
  const keyFor = (collection, id) => `${collection}:${id}`;

  return {
    async query(text, params = []) {
      const normalized = text.replace(/\s+/g, " ").trim();

      if (normalized.startsWith("select body from documents where collection = $1 and id = $2")) {
        const body = documents.get(keyFor(params[0], params[1]));
        return { rows: body ? [{ body }] : [] };
      }

      if (normalized.startsWith("select body from documents where collection = $1 order by")) {
        const rows = [...documents.entries()]
          .filter(([key]) => key.startsWith(`${params[0]}:`))
          .map(([, body]) => ({ body }));
        return { rows };
      }

      if (normalized.startsWith("update documents set body = $3::jsonb")) {
        const key = keyFor(params[0], params[1]);
        if (!documents.has(key)) return { rowCount: 0, rows: [] };
        documents.set(key, JSON.parse(params[2]));
        return { rowCount: 1, rows: [] };
      }

      if (normalized.startsWith("insert into documents")) {
        documents.set(keyFor(params[0], params[1]), JSON.parse(params[2]));
        return { rowCount: 1, rows: [] };
      }

      if (normalized.startsWith("select id, body from runs where id = $1")) {
        return { rows: runs.filter((run) => run.id === params[0]).map((run) => ({ id: run.id, body: run.body })) };
      }

      if (normalized.startsWith("select id, body from runs")) {
        return { rows: runs.map((run) => ({ id: run.id, body: run.body })) };
      }

      if (normalized.startsWith("select id, run_id, body from sessions")) {
        const runIds = new Set(params[0] || []);
        return {
          rows: sessions
            .filter((session) => runIds.has(session.runId))
            .map((session) => ({ id: session.id, run_id: session.runId, body: session.body }))
        };
      }

      if (normalized.startsWith("select run_id, session_id, body from session_events")) {
        const runIds = new Set(params[0] || []);
        return {
          rows: events
            .filter((event) => runIds.has(event.runId))
            .map((event) => ({ run_id: event.runId, session_id: event.sessionId, body: event.body }))
        };
      }

      if (normalized.startsWith("select body from runs")) {
        return { rows: [] };
      }

      throw new Error(`Unexpected fake query: ${normalized}`);
    },
    async connect() {
      throw new Error("Fake pool does not support transactions in this test");
    }
  };
}

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

test("postgres storage persists updates to seed-backed environments", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "llm-status-postgres-seed-"));
  const store = createPostgresStore({ root, pool: createFakeDocumentPool() });

  const updated = await store.updateItem("environments", "env-codex", {
    model: "gpt-debug-save",
    reasoningEffort: "high",
    timeoutMs: 123456
  });

  assert.equal(updated.model, "gpt-debug-save");

  const persisted = await store.getItem("environments", "env-codex");
  assert.equal(persisted.model, "gpt-debug-save");
  assert.equal(persisted.reasoningEffort, "high");
  assert.equal(persisted.timeoutMs, 123456);

  const environments = await store.listCollection("environments");
  assert.deepEqual(
    environments.map((environment) => environment.id).sort(),
    ["env-claude", "env-codex", "env-dry-run"]
  );

  assert.equal(await store.deleteItem("environments", "env-codex"), true);
  assert.equal(await store.getItem("environments", "env-codex"), null);
  assert.deepEqual(
    (await store.listCollection("environments")).map((environment) => environment.id).sort(),
    ["env-claude", "env-dry-run"]
  );
});

test("postgres aggregate store honors tombstoned seed documents", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "llm-status-postgres-tombstone-store-"));
  const store = createPostgresStore({ root, pool: createFakeDocumentPool() });

  await store.deleteItem("environments", "env-codex");
  await store.deleteItem("environments", "env-claude");
  await store.deleteItem("environments", "env-dry-run");

  assert.deepEqual(await store.listCollection("environments"), []);
  assert.deepEqual((await store.readStore()).environments, []);
});

test("postgres storage lists runs with hydrated sessions and events", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "llm-status-postgres-runs-"));
  const store = createPostgresStore({
    root,
    pool: createFakeDocumentPool({
      runs: [
        {
          id: "run-1",
          body: {
            id: "run-1",
            name: "Hydrated run",
            status: "completed",
            sessions: []
          }
        }
      ],
      sessions: [
        {
          id: "session-1",
          runId: "run-1",
          body: {
            id: "session-1",
            runId: "run-1",
            status: "completed",
            events: []
          }
        }
      ],
      events: [
        {
          runId: "run-1",
          sessionId: "session-1",
          body: {
            id: "event-1",
            type: "session_finished",
            payload: { changed: true }
          }
        }
      ]
    })
  });

  const runs = await store.listCollection("runs");

  assert.equal(runs.length, 1);
  assert.equal(runs[0].sessions.length, 1);
  assert.equal(runs[0].sessions[0].status, "completed");
  assert.deepEqual(runs[0].sessions[0].events.map((event) => event.type), ["session_finished"]);
});
