import fs from "node:fs/promises";
import path from "node:path";
import { nanoid } from "nanoid";

const ROOT = process.cwd();
export const DATA_DIR = path.join(ROOT, "data");
export const RUNS_DIR = path.join(DATA_DIR, "runs");
export const STATES_DIR = path.join(DATA_DIR, "states");
const STORE_PATH = path.join(DATA_DIR, "store.json");

const now = () => new Date().toISOString();

const seed = {
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
      path: path.join(ROOT, "examples", "buggy-js"),
      description: "Small local fixture for dry-run and CLI smoke tests.",
      createdAt: now(),
      updatedAt: now()
    }
  ],
  runs: []
};

async function ensureStore() {
  await fs.mkdir(DATA_DIR, { recursive: true });
  await fs.mkdir(RUNS_DIR, { recursive: true });
  await fs.mkdir(STATES_DIR, { recursive: true });

  try {
    await fs.access(STORE_PATH);
  } catch {
    await fs.writeFile(STORE_PATH, JSON.stringify(seed, null, 2));
  }
}

export async function readStore() {
  await ensureStore();
  const raw = await fs.readFile(STORE_PATH, "utf8");
  return JSON.parse(raw);
}

export async function writeStore(store) {
  await fs.mkdir(DATA_DIR, { recursive: true });
  await fs.writeFile(STORE_PATH, JSON.stringify(store, null, 2));
}

export async function listCollection(name) {
  const store = await readStore();
  return store[name] ?? [];
}

export async function getItem(collection, id) {
  const store = await readStore();
  return store[collection]?.find((item) => item.id === id) ?? null;
}

export async function createItem(collection, attrs) {
  const store = await readStore();
  const item = {
    id: attrs.id || nanoid(12),
    ...attrs,
    createdAt: attrs.createdAt || now(),
    updatedAt: now()
  };
  store[collection] = [item, ...(store[collection] ?? [])];
  await writeStore(store);
  return item;
}

export async function updateItem(collection, id, patch) {
  const store = await readStore();
  const items = store[collection] ?? [];
  const index = items.findIndex((item) => item.id === id);
  if (index < 0) return null;

  items[index] = {
    ...items[index],
    ...patch,
    id,
    updatedAt: now()
  };
  await writeStore(store);
  return items[index];
}

export async function deleteItem(collection, id) {
  const store = await readStore();
  const before = store[collection] ?? [];
  store[collection] = before.filter((item) => item.id !== id);
  await writeStore(store);
  return before.length !== store[collection].length;
}

export async function mutateStore(mutator) {
  const store = await readStore();
  const result = await mutator(store);
  await writeStore(store);
  return result;
}

export function timestamp() {
  return now();
}
