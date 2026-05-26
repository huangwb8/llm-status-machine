import express from "express";
import cors from "cors";
import fs from "node:fs/promises";
import path from "node:path";
import {
  createItem,
  deleteItem,
  getItem,
  listCollection,
  readStore,
  updateItem
} from "./store.js";
import { recordAgentEvent, startRun, subscribe, writeAgentArtifact } from "./runner.js";
import { selectDirectory } from "./systemDialog.js";
import { createStateFromDirectory } from "./workspaceStates.js";
import { isDirectoryPickerRequestAllowed } from "./localRequest.js";

const app = express();
const port = process.env.PORT || 4317;
const host = process.env.HOST || "127.0.0.1";
const distDir = path.join(process.cwd(), "dist");

app.use(cors());
app.use(express.json({ limit: "4mb" }));

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

app.post("/api/system/select-directory", asyncRoute(async (req, res) => {
  if (!isDirectoryPickerRequestAllowed(req)) {
    return res.status(403).json({
      error: `Directory picker is only available from this machine. Open the app at http://localhost:${port} or set DIRECTORY_PICKER_ALLOW_REMOTE=1 to allow remote browser sessions.`
    });
  }
  res.json(await selectDirectory({ title: req.body?.title || "Select workspace folder" }));
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
  res.status(status).json({ error: error.message || "Internal server error" });
});

app.listen(port, host, () => {
  console.log(`LLM Status Machine API listening on http://${host}:${port}`);
});
