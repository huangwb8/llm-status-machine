import express from "express";
import cors from "cors";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  createItem,
  deleteItem,
  getItem,
  listCollection,
  readStore,
  updateItem
} from "./store.js";
import { recordAgentEvent, startRun, subscribe, writeAgentArtifact } from "./runner.js";
import { createConnection, disconnectConnection, heartbeatConnection, requestTerminateConnection } from "./devtoolsConnections.js";
import { createDevtoolsKey, requireDevtoolsKey, revokeDevtoolsKey } from "./devtoolsAuth.js";
import { getDirectoryPickerStatus, selectDirectory } from "./systemDialog.js";
import { createStateFromDirectory } from "./workspaceStates.js";
import { isDirectoryPickerRequestAllowed } from "./localRequest.js";

const port = process.env.PORT || 4317;
const host = process.env.HOST || "127.0.0.1";
const distDir = path.join(process.cwd(), "dist");

function asyncRoute(handler) {
  return async (req, res, next) => {
    try {
      await handler(req, res, next);
    } catch (error) {
      next(error);
    }
  };
}

async function findSession(sessionId) {
  const store = await readStore();
  for (const run of store.runs) {
    const session = run.sessions.find((item) => item.id === sessionId);
    if (session) return { run, session };
  }
  return null;
}

function isDevtoolsAdminRequestAllowed(req) {
  if (/^(1|true|yes)$/i.test(String(process.env.DEVTOOLS_ADMIN_ALLOW_REMOTE || ""))) return true;
  return isDirectoryPickerRequestAllowed(req, { env: {} });
}

function publicDevtoolsKey(key) {
  return {
    id: key.id,
    name: key.name,
    keyPrefix: key.keyPrefix,
    revokedAt: key.revokedAt ?? null,
    createdAt: key.createdAt,
    updatedAt: key.updatedAt
  };
}

function publicDevtoolsConnection(connection) {
  return {
    id: connection.id,
    keyId: connection.keyId,
    keyPrefix: connection.keyPrefix,
    clientName: connection.clientName,
    clientVersion: connection.clientVersion,
    machine: connection.machine,
    workdir: connection.workdir,
    lastSeenAt: connection.lastSeenAt,
    lastError: connection.lastError,
    terminateRequestedAt: connection.terminateRequestedAt,
    terminatedAt: connection.terminatedAt,
    createdAt: connection.createdAt,
    updatedAt: connection.updatedAt
  };
}

function publicModel(environment) {
  return {
    id: environment.id,
    name: environment.name,
    client: environment.client,
    model: environment.model,
    baseUrlConfigured: Boolean(environment.baseUrl),
    reasoningEffort: environment.reasoningEffort,
    timeoutMs: environment.timeoutMs
  };
}

function publicDevtoolsContext(store) {
  return {
    prompts: store.prompts,
    models: store.environments.map(publicModel),
    workspaces: store.states.map((state) => ({
      id: state.id,
      name: state.name,
      path: state.path,
      description: state.description
    })),
    runs: store.runs
  };
}

export function createApp({
  getDirectoryPickerStatusImpl = getDirectoryPickerStatus,
  isDirectoryPickerRequestAllowedImpl = isDirectoryPickerRequestAllowed,
  isDevtoolsAdminRequestAllowedImpl = isDevtoolsAdminRequestAllowed,
  selectDirectoryImpl = selectDirectory
} = {}) {
  const app = express();
  const devtoolsDeps = { createItem, updateItem, listCollection };
  const requireDevtools = requireDevtoolsKey({ listCollection });

  app.use(cors());
  app.use(express.json({ limit: "4mb" }));

  for (const collection of ["prompts", "environments", "states"]) {
    app.get(`/api/${collection}`, asyncRoute(async (_req, res) => {
      res.json(await listCollection(collection));
    }));

    app.post(`/api/${collection}`, asyncRoute(async (req, res) => {
      res.status(201).json(await createItem(collection, req.body));
    }));

    app.patch(`/api/${collection}/:id`, asyncRoute(async (req, res) => {
      const item = await updateItem(collection, req.params.id, req.body);
      if (!item) return res.status(404).json({ error: "Not found" });
      res.json(item);
    }));

    app.delete(`/api/${collection}/:id`, asyncRoute(async (req, res) => {
      const removed = await deleteItem(collection, req.params.id);
      res.status(removed ? 204 : 404).end();
    }));
  }

  app.get("/api/store", asyncRoute(async (_req, res) => {
    res.json(await readStore());
  }));

  app.get("/api/runs", asyncRoute(async (_req, res) => {
    res.json(await listCollection("runs"));
  }));

  app.get("/api/runs/:id", asyncRoute(async (req, res) => {
    const run = await getItem("runs", req.params.id);
    if (!run) return res.status(404).json({ error: "Not found" });
    res.json(run);
  }));

  app.post("/api/runs", asyncRoute(async (req, res) => {
    const run = await startRun(req.body);
    res.status(202).json(run);
  }));

  app.get("/api/devtools/ping", (_req, res) => {
    res.json({ ok: true, name: "llm-status-machine", prefix: "/api/devtools" });
  });

  app.get("/api/devtools/admin", asyncRoute(async (req, res) => {
    if (!isDevtoolsAdminRequestAllowedImpl(req)) return res.status(403).json({ error: "forbidden" });
    res.json({
      baseUrl: "/api/devtools",
      authHeader: "X-Devtools-Key",
      keys: (await listCollection("devtoolsApiKeys")).map(publicDevtoolsKey),
      connections: (await listCollection("devtoolsConnections")).map(publicDevtoolsConnection)
    });
  }));

  app.post("/api/devtools/admin/keys", asyncRoute(async (req, res) => {
    if (!isDevtoolsAdminRequestAllowedImpl(req)) return res.status(403).json({ error: "forbidden" });
    const created = await createDevtoolsKey(req.body, devtoolsDeps);
    res.status(201).json({ rawKey: created.rawKey, key: publicDevtoolsKey(created.record) });
  }));

  app.post("/api/devtools/admin/keys/:id/revoke", asyncRoute(async (req, res) => {
    if (!isDevtoolsAdminRequestAllowedImpl(req)) return res.status(403).json({ error: "forbidden" });
    const key = await revokeDevtoolsKey(req.params.id, devtoolsDeps);
    if (!key) return res.status(404).json({ error: "Not found" });
    res.json(publicDevtoolsKey(key));
  }));

  app.post("/api/devtools/admin/connections/:id/terminate", asyncRoute(async (req, res) => {
    if (!isDevtoolsAdminRequestAllowedImpl(req)) return res.status(403).json({ error: "forbidden" });
    res.json(await requestTerminateConnection(req.params.id, devtoolsDeps));
  }));

  app.get("/api/devtools/context", requireDevtools, asyncRoute(async (_req, res) => {
    res.json(publicDevtoolsContext(await readStore()));
  }));

  app.get("/api/devtools/prompts", requireDevtools, asyncRoute(async (_req, res) => {
    res.json(await listCollection("prompts"));
  }));

  app.get("/api/devtools/models", requireDevtools, asyncRoute(async (_req, res) => {
    const environments = await listCollection("environments");
    res.json(environments.map(publicModel));
  }));

  app.get("/api/devtools/workspaces", requireDevtools, asyncRoute(async (_req, res) => {
    const states = await listCollection("states");
    res.json(states.map((state) => ({ id: state.id, name: state.name, path: state.path, description: state.description })));
  }));

  app.get("/api/devtools/runs", requireDevtools, asyncRoute(async (_req, res) => {
    res.json(await listCollection("runs"));
  }));

  app.get("/api/devtools/runs/:id", requireDevtools, asyncRoute(async (req, res) => {
    const run = await getItem("runs", req.params.id);
    if (!run) return res.status(404).json({ error: "Not found" });
    res.json(run);
  }));

  app.post("/api/devtools/runs", requireDevtools, asyncRoute(async (req, res) => {
    const run = await startRun({
      ...req.body,
      source: "devtools",
      devtools: {
        connectionId: req.body?.connectionId || null,
        keyPrefix: req.devtoolsApiKey?.keyPrefix
      }
    });
    res.status(202).json(run);
  }));

  app.post("/api/devtools/connect", requireDevtools, asyncRoute(async (req, res) => {
    const connection = await createConnection(req.devtoolsApiKey, req.body, devtoolsDeps);
    res.status(201).json(connection);
  }));

  app.post("/api/devtools/heartbeat", requireDevtools, asyncRoute(async (req, res) => {
    res.json(await heartbeatConnection(req.devtoolsApiKey, req.body, devtoolsDeps));
  }));

  app.post("/api/devtools/disconnect", requireDevtools, asyncRoute(async (req, res) => {
    res.json(await disconnectConnection(req.devtoolsApiKey, req.body, devtoolsDeps));
  }));

  app.get("/api/devtools/sessions/:id/diff", requireDevtools, asyncRoute(async (req, res) => {
    const match = await findSession(req.params.id);
    if (!match) return res.status(404).json({ error: "Not found" });
    const diffPath = path.join(path.dirname(match.session.workspace), "diff.patch");
    const diff = await fs.readFile(diffPath, "utf8").catch(() => "");
    res.type("text/plain").send(diff);
  }));

  app.get("/api/devtools/sessions/:id/transcript", requireDevtools, asyncRoute(async (req, res) => {
    const match = await findSession(req.params.id);
    if (!match) return res.status(404).json({ error: "Not found" });
    const transcriptPath = path.join(path.dirname(match.session.workspace), "transcript.ndjson");
    const transcript = await fs.readFile(transcriptPath, "utf8").catch(() => "");
    res.type("application/x-ndjson").send(transcript);
  }));

  app.get("/api/devtools/sessions/:id/artifacts/:name", requireDevtools, asyncRoute(async (req, res) => {
    const match = await findSession(req.params.id);
    if (!match) return res.status(404).json({ error: "Not found" });
    const safeName = path.basename(req.params.name);
    const artifactPath = path.join(path.dirname(match.session.workspace), "artifacts", safeName);
    const exists = await fs.access(artifactPath).then(() => true).catch(() => false);
    if (!exists) return res.status(404).json({ error: "Artifact not found" });
    res.sendFile(artifactPath);
  }));

  app.post("/api/devtools/sessions/:id/events", requireDevtools, asyncRoute(async (req, res) => {
    const match = await findSession(req.params.id);
    if (!match) return res.status(404).json({ error: "Not found" });
    const event = await recordAgentEvent({
      runId: match.run.id,
      sessionId: match.session.id,
      type: req.body?.type,
      payload: req.body?.payload
    });
    res.status(201).json(event);
  }));

  app.post("/api/devtools/sessions/:id/artifacts", requireDevtools, asyncRoute(async (req, res) => {
    const match = await findSession(req.params.id);
    if (!match) return res.status(404).json({ error: "Not found" });
    const artifact = await writeAgentArtifact({
      runId: match.run.id,
      sessionId: match.session.id,
      name: req.body?.name,
      content: req.body?.content,
      encoding: req.body?.encoding
    });
    res.status(201).json(artifact);
  }));

  app.get("/api/system/directory-picker", asyncRoute(async (_req, res) => {
    res.json(await getDirectoryPickerStatusImpl());
  }));

  app.post("/api/system/select-directory", asyncRoute(async (req, res) => {
    if (!isDirectoryPickerRequestAllowedImpl(req)) {
      return res.status(403).json({
        error: `Directory picker is only available from this machine. Open the app at http://localhost:${port} or set DIRECTORY_PICKER_ALLOW_REMOTE=1 to allow remote browser sessions.`
      });
    }

    const status = await getDirectoryPickerStatusImpl();
    if (!status.available) {
      return res.status(409).json({ error: status.message, picker: status });
    }

    res.json(await selectDirectoryImpl({ title: req.body?.title || "Select workspace folder" }));
  }));

  app.post("/api/states/from-directory", asyncRoute(async (req, res) => {
    const item = await createStateFromDirectory({
      folderPath: req.body?.path,
      name: req.body?.name,
      description: req.body?.description,
      createItem
    });
    res.status(201).json(item);
  }));

  app.get("/api/sessions/:id/diff", asyncRoute(async (req, res) => {
    const match = await findSession(req.params.id);
    if (!match) return res.status(404).json({ error: "Not found" });
    const { session } = match;
    const diffPath = path.join(path.dirname(session.workspace), "diff.patch");
    const diff = await fs.readFile(diffPath, "utf8").catch(() => "");
    res.type("text/plain").send(diff);
  }));

  app.get("/api/sessions/:id/transcript", asyncRoute(async (req, res) => {
    const match = await findSession(req.params.id);
    if (!match) return res.status(404).json({ error: "Not found" });
    const transcriptPath = path.join(path.dirname(match.session.workspace), "transcript.ndjson");
    const transcript = await fs.readFile(transcriptPath, "utf8").catch(() => "");
    res.type("application/x-ndjson").send(transcript);
  }));

  app.get("/api/sessions/:id/artifacts/:name", asyncRoute(async (req, res) => {
    const match = await findSession(req.params.id);
    if (!match) return res.status(404).json({ error: "Not found" });
    const safeName = path.basename(req.params.name);
    const artifactPath = path.join(path.dirname(match.session.workspace), "artifacts", safeName);
    const exists = await fs.access(artifactPath).then(() => true).catch(() => false);
    if (!exists) return res.status(404).json({ error: "Artifact not found" });
    res.sendFile(artifactPath);
  }));

  app.post("/api/agent/events", asyncRoute(async (req, res) => {
    const event = await recordAgentEvent(req.body);
    res.status(201).json(event);
  }));

  app.post("/api/agent/artifacts", asyncRoute(async (req, res) => {
    const artifact = await writeAgentArtifact(req.body);
    res.status(201).json(artifact);
  }));

  app.get("/api/events", (req, res) => {
    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");
    res.flushHeaders?.();

    const send = (event) => {
      res.write(`data: ${JSON.stringify(event)}\n\n`);
    };
    const unsubscribe = subscribe(send);
    req.on("close", unsubscribe);
  });

  app.get("/api/health", (_req, res) => {
    res.json({
      ok: true,
      name: "llm-status-machine",
      storage: process.env.STORAGE_DRIVER || "file",
      queue: process.env.QUEUE_DRIVER || "inline",
      eventBus: process.env.EVENT_BUS || "memory"
    });
  });

  app.use(express.static(distDir));
  app.get(/.*/, async (_req, res, next) => {
    try {
      await fs.access(path.join(distDir, "index.html"));
      res.sendFile(path.join(distDir, "index.html"));
    } catch (error) {
      next();
    }
  });

  app.use((error, _req, res, _next) => {
    console.error(error);
    const status = error.status || error.statusCode || 500;
    const payload = { error: error.message || "Internal server error" };
    if (error.picker) payload.picker = error.picker;
    res.status(status).json(payload);
  });

  return app;
}

export const app = createApp();

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  app.listen(port, host, () => {
    console.log(`LLM Status Machine API listening on http://${host}:${port}`);
  });
}
