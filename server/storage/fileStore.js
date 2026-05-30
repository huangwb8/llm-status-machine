import fs from "node:fs/promises";
import path from "node:path";
import { nanoid } from "nanoid";

const now = () => new Date().toISOString();

export function createSeed(root) {
  const defaultStatePath = process.env.DEFAULT_STATE_PATH || path.join(root, "examples", "buggy-js");
  return {
    prompts: [
      {
        id: "prompt-review",
        name: "代码审查",
        body: "请审查当前工作目录中的项目，找出最重要的缺陷、风险和可改进处。优先给出可验证的问题。",
        tags: ["review"],
        createdAt: now(),
        updatedAt: now()
      },
      {
        id: "prompt-fix",
        name: "修复主要问题",
        body: "请阅读当前工作目录，修复你发现的最关键问题，并尽量保留原有风格。完成后说明你改了什么。",
        tags: ["fix"],
        createdAt: now(),
        updatedAt: now()
      }
    ],
    environments: [
      {
        id: "env-codex",
        name: "Codex CLI",
        client: "codex",
        model: "gpt-5-codex",
        baseUrl: "",
        reasoningEffort: "medium",
        commandTemplate: "codex exec --model {model} --sandbox danger-full-access {prompt}",
        envVars: {},
        timeoutMs: 600000,
        createdAt: now(),
        updatedAt: now()
      },
      {
        id: "env-claude",
        name: "Claude Code",
        client: "claude",
        model: "sonnet",
        baseUrl: "",
        reasoningEffort: "default",
        commandTemplate: "claude -p {prompt} --model {model}",
        envVars: {},
        timeoutMs: 600000,
        createdAt: now(),
        updatedAt: now()
      },
      {
        id: "env-dry-run",
        name: "Dry Run",
        client: "custom",
        model: "simulator",
        baseUrl: "",
        reasoningEffort: "none",
        commandTemplate: "node {simulator} {promptFile}",
        envVars: {},
        timeoutMs: 120000,
        createdAt: now(),
        updatedAt: now()
      }
    ],
    states: [
      {
        id: "state-buggy-js",
        name: "Buggy JS Fixture",
        path: defaultStatePath,
        folders: [defaultStatePath],
        description: "Small local fixture for dry-run and CLI smoke tests.",
        createdAt: now(),
        updatedAt: now()
      }
    ],
    devtoolsApiKeys: [],
    devtoolsConnections: [],
    runs: []
  };
}

export function createFileStore({ root = process.cwd(), dataDir } = {}) {
  const resolvedDataDir = dataDir || path.join(root, "data");
  const runsDir = path.join(resolvedDataDir, "runs");
  const statesDir = path.join(resolvedDataDir, "states");
  const storePath = path.join(resolvedDataDir, "store.json");
  let writeChain = Promise.resolve();

  async function withWriteLock(action) {
    const previous = writeChain;
    let release;
    writeChain = new Promise((resolve) => {
      release = resolve;
    });
    await previous;
    try {
      return await action();
    } finally {
      release();
    }
  }

  async function ensureStore() {
    await fs.mkdir(resolvedDataDir, { recursive: true });
    await fs.mkdir(runsDir, { recursive: true });
    await fs.mkdir(statesDir, { recursive: true });

    try {
      await fs.access(storePath);
    } catch {
      await fs.writeFile(storePath, JSON.stringify(createSeed(root), null, 2));
    }
  }

  async function readStore() {
    await ensureStore();
    const raw = await fs.readFile(storePath, "utf8");
    return { ...createSeed(root), ...JSON.parse(raw) };
  }

  async function writeStoreUnlocked(store) {
    await fs.mkdir(resolvedDataDir, { recursive: true });
    const tempPath = `${storePath}.${process.pid}.${nanoid(6)}.tmp`;
    await fs.writeFile(tempPath, JSON.stringify(store, null, 2));
    await fs.rename(tempPath, storePath);
  }

  async function writeStore(store) {
    return withWriteLock(() => writeStoreUnlocked(store));
  }

  async function listCollection(name) {
    const store = await readStore();
    return store[name] ?? [];
  }

  async function getItem(collection, id) {
    const store = await readStore();
    return store[collection]?.find((item) => item.id === id) ?? null;
  }

  async function createItem(collection, attrs) {
    return mutateStore((store) => {
      const item = {
        id: attrs.id || nanoid(12),
        ...attrs,
        createdAt: attrs.createdAt || now(),
        updatedAt: now()
      };
      store[collection] = [item, ...(store[collection] ?? [])];
      return item;
    });
  }

  async function updateItem(collection, id, patch) {
    return mutateStore((store) => {
      const items = store[collection] ?? [];
      const index = items.findIndex((item) => item.id === id);
      if (index < 0) return null;

      items[index] = {
        ...items[index],
        ...patch,
        id,
        updatedAt: now()
      };
      return items[index];
    });
  }

  async function deleteItem(collection, id) {
    return mutateStore((store) => {
      const before = store[collection] ?? [];
      store[collection] = before.filter((item) => item.id !== id);
      return before.length !== store[collection].length;
    });
  }

  async function mutateStore(mutator) {
    return withWriteLock(async () => {
      const store = await readStore();
      const result = await mutator(store);
      await writeStoreUnlocked(store);
      return result;
    });
  }

  async function createRun(run) {
    await mutateStore((store) => {
      store.runs = [run, ...(store.runs ?? [])];
      return run;
    });
    return run;
  }

  async function patchRun(runId, patcher) {
    return mutateStore((store) => {
      const run = store.runs.find((item) => item.id === runId);
      if (!run) return null;
      patcher(run);
      run.updatedAt = now();
      return run;
    });
  }

  async function getSession(runId, sessionId) {
    const run = await getItem("runs", runId);
    const session = run?.sessions.find((item) => item.id === sessionId);
    return session ? { run, session } : null;
  }

  async function addSession(runId, session) {
    return patchRun(runId, (run) => {
      run.sessions.push(session);
    });
  }

  async function patchSession(runId, sessionId, patcher) {
    return patchRun(runId, (run) => {
      const session = run.sessions.find((item) => item.id === sessionId);
      if (session) patcher(session, run);
    });
  }

  async function appendSessionEvent(runId, sessionId, event) {
    return patchSession(runId, sessionId, (session) => {
      session.events.push(event);
    });
  }

  return {
    dataDir: resolvedDataDir,
    runsDir,
    statesDir,
    readStore,
    writeStore,
    listCollection,
    getItem,
    createItem,
    updateItem,
    deleteItem,
    mutateStore,
    createRun,
    patchRun,
    getSession,
    addSession,
    patchSession,
    appendSessionEvent
  };
}
