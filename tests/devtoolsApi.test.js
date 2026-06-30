import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { createFileStore } from "../server/storage/fileStore.js";
import {
  createDevtoolsKey,
  findDevtoolsKeyByRaw,
  revokeDevtoolsKey
} from "../server/devtoolsAuth.js";
import {
  createConnection,
  disconnectConnection,
  heartbeatConnection,
  requestTerminateConnection
} from "../server/devtoolsConnections.js";

function storeDeps(store) {
  return {
    createItem: store.createItem,
    updateItem: store.updateItem,
    listCollection: store.listCollection
  };
}

async function withServer(app, callback) {
  const server = http.createServer(app);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  try {
    await callback(`http://127.0.0.1:${port}`);
  } finally {
    await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  }
}

test("devtools key creation stores only hashed keys and rejects revoked keys", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "llm-status-devtools-auth-"));
  const store = createFileStore({ root });

  const created = await createDevtoolsKey({ name: "local-codex" }, storeDeps(store));
  assert.match(created.rawKey, /^lsm_[A-Za-z0-9_-]{48}$/);
  assert.equal(created.record.name, "local-codex");
  assert.equal(created.record.keyPrefix, created.rawKey.slice(0, 12));
  assert.equal(created.record.rawKey, undefined);

  const stored = await store.getItem("devtoolsApiKeys", created.record.id);
  assert.equal(stored.rawKey, undefined);
  assert.match(stored.keyHash, /^[a-f0-9]{64}$/);

  const found = await findDevtoolsKeyByRaw(created.rawKey, storeDeps(store));
  assert.equal(found.id, created.record.id);

  await revokeDevtoolsKey(created.record.id, storeDeps(store));
  assert.equal(await findDevtoolsKeyByRaw(created.rawKey, storeDeps(store)), null);
});

test("devtools connection lifecycle tracks heartbeat, termination, and disconnect", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "llm-status-devtools-conn-"));
  const store = createFileStore({ root });
  const { record: apiKey } = await createDevtoolsKey({ name: "local-codex" }, storeDeps(store));

  const connected = await createConnection(apiKey, {
    clientName: "codex",
    clientVersion: "1.0.0",
    machine: "devbox",
    workdir: "/tmp/project"
  }, storeDeps(store));
  assert.equal(connected.connection.clientName, "codex");
  assert.equal(connected.connection.keyId, apiKey.id);
  assert.equal(connected.terminate, false);

  const heartbeat = await heartbeatConnection(apiKey, {
    connectionId: connected.connection.id,
    lastError: "retrying"
  }, storeDeps(store));
  assert.equal(heartbeat.connection.lastError, "retrying");
  assert.equal(heartbeat.terminate, false);

  await requestTerminateConnection(connected.connection.id, storeDeps(store));
  const terminating = await heartbeatConnection(apiKey, {
    connectionId: connected.connection.id
  }, storeDeps(store));
  assert.equal(terminating.terminate, true);

  const disconnected = await disconnectConnection(apiKey, {
    connectionId: connected.connection.id
  }, storeDeps(store));
  assert.equal(Boolean(disconnected.connection.terminatedAt), true);
});

test("devtools API routes expose authenticated external agent workflow", async () => {
  process.env.DATA_DIR = await fs.mkdtemp(path.join(os.tmpdir(), "llm-status-devtools-api-"));
  process.env.STORAGE_DRIVER = "file";
  process.env.QUEUE_DRIVER = "inline";
  process.env.EVENT_BUS = "memory";
  process.env.DEVTOOLS_ADMIN_ALLOW_REMOTE = "1";

  const { createApp } = await import("../server/index.js");
  const app = createApp({
    isDevtoolsAdminRequestAllowedImpl: () => true
  });

  await withServer(app, async (baseUrl) => {
    const ping = await fetch(`${baseUrl}/api/devtools/ping`);
    assert.equal(ping.status, 200);
    assert.equal((await ping.json()).ok, true);

    const unauthenticated = await fetch(`${baseUrl}/api/devtools/context`);
    assert.equal(unauthenticated.status, 401);
    assert.equal((await unauthenticated.json()).error, "missing_api_key");

    const createdKeyResponse = await fetch(`${baseUrl}/api/devtools/admin/keys`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name: "route-test" })
    });
    assert.equal(createdKeyResponse.status, 201);
    const createdKey = await createdKeyResponse.json();
    assert.match(createdKey.rawKey, /^lsm_/);
    assert.equal(createdKey.key.keyHash, undefined);

    const headers = { "X-Devtools-Key": createdKey.rawKey };
    const context = await fetch(`${baseUrl}/api/devtools/context`, { headers });
    assert.equal(context.status, 200);
    const payload = await context.json();
    assert.equal(Array.isArray(payload.prompts), true);
    assert.equal(Array.isArray(payload.models), true);
    assert.equal(Array.isArray(payload.workspaces), true);
    assert.equal(Array.isArray(payload.runs), true);
    assert.equal("envVars" in payload.models[0], false);
    assert.equal(typeof payload.models[0].baseUrlConfigured, "boolean");

    const connectedResponse = await fetch(`${baseUrl}/api/devtools/connect`, {
      method: "POST",
      headers: { ...headers, "content-type": "application/json" },
      body: JSON.stringify({ clientName: "codex", workdir: "/tmp/project" })
    });
    assert.equal(connectedResponse.status, 201);
    const connected = await connectedResponse.json();
    assert.equal(Boolean(connected.connectionId), true);
    assert.equal(connected.terminate, false);

    const heartbeatResponse = await fetch(`${baseUrl}/api/devtools/heartbeat`, {
      method: "POST",
      headers: { ...headers, "content-type": "application/json" },
      body: JSON.stringify({ connectionId: connected.connectionId })
    });
    assert.equal(heartbeatResponse.status, 200);
    assert.equal((await heartbeatResponse.json()).terminate, false);

    const disconnectResponse = await fetch(`${baseUrl}/api/devtools/disconnect`, {
      method: "POST",
      headers: { ...headers, "content-type": "application/json" },
      body: JSON.stringify({ connectionId: connected.connectionId })
    });
    assert.equal(disconnectResponse.status, 200);
    assert.equal((await disconnectResponse.json()).ok, true);

    const admin = await fetch(`${baseUrl}/api/devtools/admin`);
    assert.equal(admin.status, 200);
    const adminPayload = await admin.json();
    assert.equal(adminPayload.keys.some((key) => key.keyHash), false);

    const storeResponse = await fetch(`${baseUrl}/api/store`);
    assert.equal(storeResponse.status, 200);
    const storePayload = await storeResponse.json();
    assert.equal(storePayload.devtoolsApiKeys.some((key) => key.keyHash), false);

    const revoke = await fetch(`${baseUrl}/api/devtools/admin/keys/${createdKey.key.id}/revoke`, {
      method: "POST"
    });
    assert.equal(revoke.status, 200);

    const revokedContext = await fetch(`${baseUrl}/api/devtools/context`, { headers });
    assert.equal(revokedContext.status, 401);
    assert.equal((await revokedContext.json()).error, "invalid_or_revoked_api_key");
  });
});
