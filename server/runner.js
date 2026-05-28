import fs from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import { nanoid } from "nanoid";
import {
  RUNS_DIR,
  addSession,
  appendSessionEvent,
  createRun,
  getSession,
  getItem,
  listCollection,
  patchRun,
  patchSession,
  timestamp
} from "./store.js";
import { SNAPSHOT_BRANCH, changedFiles, commitSnapshot, diffPatch, diffStat, initRepo } from "./git.js";
import { createExecutionPlan } from "./experimentPlan.js";
import { emit, subscribe } from "./events.js";
import { enqueueRunJob } from "./queue.js";
import { workspaceFoldersFromState } from "./workspaceStates.js";

const serverDir = path.dirname(new URL(import.meta.url).pathname);
const simulatorPath = path.join(serverDir, "simulator.js");
const activeSessions = new Map();
export { subscribe };

function getSessionPaths(sessionDir) {
  return {
    transcript: path.join(sessionDir, "transcript.ndjson"),
    stdout: path.join(sessionDir, "stdout.txt"),
    stderr: path.join(sessionDir, "stderr.txt"),
    artifacts: path.join(sessionDir, "artifacts")
  };
}

async function appendLine(filePath, value) {
  await fs.appendFile(filePath, `${value}\n`);
}

async function listArtifacts(artifactsDir) {
  const entries = await fs.readdir(artifactsDir, { withFileTypes: true }).catch(() => []);
  const files = [];
  for (const entry of entries) {
    if (!entry.isFile()) continue;
    const filePath = path.join(artifactsDir, entry.name);
    const stat = await fs.stat(filePath).catch(() => null);
    files.push({ name: entry.name, size: stat?.size ?? 0 });
  }
  return files.sort((a, b) => a.name.localeCompare(b.name));
}

async function findStoredSession(runId, sessionId) {
  return getSession(runId, sessionId);
}

async function resolveSessionPaths(runId, sessionId) {
  const active = activeSessions.get(sessionId);
  if (active) {
    if (active.runId !== runId) throw new Error("Session does not belong to run");
    return active.paths;
  }

  const stored = await findStoredSession(runId, sessionId);
  if (!stored) throw new Error("Session not found");
  return getSessionPaths(path.dirname(stored.session.workspace));
}

function uniqueFolderName(usedNames, sourcePath) {
  const baseName = path.basename(path.resolve(sourcePath)) || "workspace";
  let name = baseName;
  let suffix = 2;
  while (usedNames.has(name)) {
    name = `${baseName}-${suffix}`;
    suffix += 1;
  }
  usedNames.add(name);
  return name;
}

export async function copyWorkspace(source, target) {
  await fs.mkdir(target, { recursive: true });
  const sources = Array.isArray(source) ? source.filter(Boolean) : [source];
  if (sources.length <= 1) {
    await fs.cp(sources[0], target, {
      recursive: true,
      force: true,
      filter: (item) => !item.includes(`${path.sep}.git${path.sep}`) && !item.endsWith(`${path.sep}.git`)
    });
    return;
  }

  const usedNames = new Set();
  for (const sourcePath of sources) {
    const destination = path.join(target, uniqueFolderName(usedNames, sourcePath));
    await fs.cp(sourcePath, destination, {
      recursive: true,
      force: true,
      filter: (item) => !item.includes(`${path.sep}.git${path.sep}`) && !item.endsWith(`${path.sep}.git`)
    });
  }
}

function shellEscape(value) {
  const text = String(value ?? "");
  return `'${text.replace(/'/g, "'\\''")}'`;
}

function renderCommand(template, context) {
  return template.replace(/\{(prompt|promptRaw|promptFile|model|baseUrl|reasoningEffort|workspace|simulator)\}/g, (_, key) => {
    if (key === "promptRaw") return context.prompt;
    return shellEscape(context[key]);
  });
}

function runCommand(command, cwd, env, timeoutMs, onEvent) {
  return new Promise((resolve) => {
    const child = spawn(command, {
      cwd,
      env: { ...process.env, ...env },
      shell: true,
      stdio: ["ignore", "pipe", "pipe"]
    });
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGTERM");
      setTimeout(() => child.kill("SIGKILL"), 3000).unref();
    }, timeoutMs || 600000);

    child.stdout.on("data", (chunk) => onEvent("stdout", chunk.toString()));
    child.stderr.on("data", (chunk) => onEvent("stderr", chunk.toString()));
    child.on("error", (error) => onEvent("error", error.message));
    child.on("close", (code, signal) => {
      clearTimeout(timer);
      resolve({ code, signal, timedOut });
    });
  });
}

async function persistEvent(runId, sessionId, event) {
  await appendSessionEvent(runId, sessionId, event);
}

async function recordSessionEvent(runId, sessionId, event) {
  const paths = await resolveSessionPaths(runId, sessionId);
  await appendLine(paths.transcript, JSON.stringify(event));
  if (event.type === "stdout") await fs.appendFile(paths.stdout, String(event.payload));
  if (event.type === "stderr") await fs.appendFile(paths.stderr, String(event.payload));
  await persistEvent(runId, sessionId, event);
}

export async function runSession({ run, prompt, environment, state, iteration, sequence, sourceWorkspace, outputLabel }) {
  const sessionId = nanoid(12);
  const sessionDir = outputLabel ? path.join(RUNS_DIR, run.id, outputLabel) : path.join(RUNS_DIR, run.id, sessionId);
  const workspace = path.join(sessionDir, "workspace");
  const paths = getSessionPaths(sessionDir);
  await fs.mkdir(sessionDir, { recursive: true });
  await fs.mkdir(paths.artifacts, { recursive: true });
  await fs.writeFile(paths.transcript, "");
  await fs.writeFile(paths.stdout, "");
  await fs.writeFile(paths.stderr, "");

  const session = {
    id: sessionId,
    runId: run.id,
    promptId: prompt.id,
    promptName: prompt.name,
    iteration,
    sequence,
    inputStateName: sequence === 1 ? state.name : `state-${sequence - 1}`,
    outputStateName: outputLabel,
    status: "running",
    branch: SNAPSHOT_BRANCH,
    sourceWorkspace,
    workspace,
    command: "",
    startedAt: timestamp(),
    endedAt: null,
    exitCode: null,
    signal: null,
    timedOut: false,
    initialCommit: null,
    finalCommit: null,
    diffStat: "",
    changedFiles: [],
    artifacts: [],
    events: []
  };
  activeSessions.set(sessionId, { runId: run.id, sessionDir, workspace, paths });

  await addSession(run.id, session);

  const remember = async (type, payload) => {
    const event = await emit(run.id, sessionId, type, payload);
    await recordSessionEvent(run.id, sessionId, event);
  };

  try {
    await copyWorkspace(sourceWorkspace || state.path, workspace);
    session.initialCommit = await initRepo(workspace);
    const promptFile = path.join(sessionDir, "prompt.txt");
    await fs.writeFile(promptFile, prompt.body);

    const command = renderCommand(environment.commandTemplate, {
      prompt: prompt.body,
      promptFile,
      model: environment.model,
      baseUrl: environment.baseUrl,
      reasoningEffort: environment.reasoningEffort,
      workspace,
      simulator: simulatorPath
    });

    const env = {
      ...(environment.baseUrl ? { OPENAI_BASE_URL: environment.baseUrl, ANTHROPIC_BASE_URL: environment.baseUrl } : {}),
      ...(environment.reasoningEffort ? { LLM_REASONING_EFFORT: environment.reasoningEffort } : {}),
      LLM_STATUS_MACHINE_API: process.env.LLM_STATUS_MACHINE_API || "http://localhost:4317",
      LLM_STATUS_MACHINE_RUN_ID: run.id,
      LLM_STATUS_MACHINE_SESSION_ID: sessionId,
      LLM_STATUS_MACHINE_BRANCH: SNAPSHOT_BRANCH,
      LLM_STATUS_MACHINE_ARTIFACTS_DIR: paths.artifacts,
      LLM_STATUS_MACHINE_WORKSPACE: workspace,
      ...(environment.envVars || {})
    };

    await patchSession(run.id, sessionId, (storedSession) => {
      Object.assign(storedSession, { command, branch: SNAPSHOT_BRANCH, initialCommit: session.initialCommit });
    });

    await remember("session_started", { command, workspace, branch: SNAPSHOT_BRANCH });
    const result = await runCommand(command, workspace, env, environment.timeoutMs, remember);
    const snapshot = await commitSnapshot(workspace, "LLM session result");
    const patch = snapshot.changed ? await diffPatch(workspace) : "";
    const files = snapshot.changed ? await changedFiles(workspace) : [];
    const stat = snapshot.changed ? await diffStat(workspace) : "";
    await fs.writeFile(path.join(sessionDir, "diff.patch"), patch);
    await fs.writeFile(
      path.join(sessionDir, "metadata.json"),
      JSON.stringify({ prompt, environment, state, branch: SNAPSHOT_BRANCH, sourceWorkspace: sourceWorkspace || state.path, outputLabel }, null, 2)
    );
    const artifacts = await listArtifacts(paths.artifacts);

    await patchSession(run.id, sessionId, (storedSession) => {
      Object.assign(storedSession, {
        status: result.code === 0 && !result.timedOut ? "completed" : "failed",
        endedAt: timestamp(),
        exitCode: result.code,
        signal: result.signal,
        timedOut: result.timedOut,
        finalCommit: snapshot.commit,
        diffStat: stat,
        changedFiles: files,
        artifacts
      });
    });
    await remember("session_finished", { ...result, changed: snapshot.changed, changedFiles: files, artifacts });
  } catch (error) {
    await patchSession(run.id, sessionId, (storedSession) => {
      storedSession.status = "failed";
      storedSession.endedAt = timestamp();
    });
    await remember("error", error.stack || error.message);
  } finally {
    activeSessions.delete(sessionId);
  }
}

export async function recordAgentEvent({ runId, sessionId, type, payload }) {
  if (!runId || !sessionId) throw new Error("runId and sessionId are required");
  await resolveSessionPaths(runId, sessionId);
  const event = await emit(runId, sessionId, type || "agent_event", payload ?? null);
  await recordSessionEvent(runId, sessionId, event);
  return event;
}

export async function writeAgentArtifact({ runId, sessionId, name, content, encoding = "utf8" }) {
  if (!runId || !sessionId) throw new Error("runId and sessionId are required");
  if (!name) throw new Error("Artifact name is required");

  const paths = await resolveSessionPaths(runId, sessionId);
  const artifactsDir = path.resolve(paths.artifacts);
  await fs.mkdir(artifactsDir, { recursive: true });

  const safeName = path.basename(name).replace(/[^\w.-]/g, "_");
  if (!safeName) throw new Error("Artifact name is invalid");
  const body = encoding === "base64" ? Buffer.from(String(content || ""), "base64") : String(content ?? "");
  const artifactPath = path.resolve(artifactsDir, safeName);
  if (!artifactPath.startsWith(`${artifactsDir}${path.sep}`)) throw new Error("Artifact path is invalid");
  await fs.writeFile(artifactPath, body);

  const artifacts = await listArtifacts(artifactsDir);
  await patchSession(runId, sessionId, (session) => {
    session.artifacts = artifacts;
  });

  const size = Buffer.isBuffer(body) ? body.length : Buffer.byteLength(body);
  await recordAgentEvent({ runId, sessionId, type: "artifact_written", payload: { name: safeName, size } });
  return { name: safeName, size };
}

export async function processRunJob({ runId }) {
  const run = await getItem("runs", runId);
  if (!run) throw new Error(`Run not found: ${runId}`);

  const [states, environments, prompts] = await Promise.all([
    listCollection("states"),
    listCollection("environments"),
    listCollection("prompts")
  ]);
  const state = states.find((item) => item.id === run.stateId);
  const environment = environments.find((item) => item.id === run.environmentId);
  if (!state) throw new Error("State not found");
  if (!environment) throw new Error("Environment not found");

  const jobs = createExecutionPlan({
    mode: run.mode,
    runId: run.id,
    initialWorkspace: workspaceFoldersFromState(state),
    promptRuns: run.promptRuns,
    prompts,
    outputWorkspaceFor: ({ outputLabel }) => path.join(RUNS_DIR, run.id, outputLabel, "workspace")
  }).map((job) => ({ ...job, run, environment, state }));

  try {
    if (run.mode === "parallel") {
      await Promise.all(jobs.map((job) => runSession(job)));
    } else {
      for (const job of jobs) await runSession(job);
    }
    await patchRun(run.id, (storedRun) => {
      const failed = storedRun.sessions.some((session) => session.status === "failed");
      storedRun.status = failed ? "failed" : "completed";
      storedRun.endedAt = timestamp();
    });
    await emit(run.id, null, "run_finished", { status: "done" });
  } catch (error) {
    await patchRun(run.id, (storedRun) => {
      storedRun.status = "failed";
      storedRun.endedAt = timestamp();
    });
    await emit(run.id, null, "run_failed", error.stack || error.message);
    throw error;
  }
}

export async function startRun(config) {
  const [states, environments, storedPrompts] = await Promise.all([
    listCollection("states"),
    listCollection("environments"),
    listCollection("prompts")
  ]);
  const state = states.find((item) => item.id === config.stateId);
  const environment = environments.find((item) => item.id === config.environmentId);
  const prompts = config.promptRuns
    .map((selection) => {
      const prompt = storedPrompts.find((item) => item.id === selection.promptId);
      return prompt ? { ...selection, prompt } : null;
    })
    .filter(Boolean);

  if (!state) throw new Error("State not found");
  if (!environment) throw new Error("Environment not found");
  if (!prompts.length) throw new Error("Select at least one prompt");

  const run = {
    id: nanoid(12),
    name: config.name || `Run ${new Date().toLocaleString()}`,
    stateId: state.id,
    stateName: state.name,
    environmentId: environment.id,
    environmentName: environment.name,
    mode: config.mode === "parallel" ? "parallel" : "serial",
    promptRuns: config.promptRuns,
    source: config.source || "local",
    devtools: config.devtools || null,
    status: "running",
    startedAt: timestamp(),
    endedAt: null,
    sessions: [],
    createdAt: timestamp(),
    updatedAt: timestamp()
  };

  await createRun(run);

  try {
    await enqueueRunJob({ runId: run.id }, { processor: processRunJob });
  } catch (error) {
    await patchRun(run.id, (storedRun) => {
      storedRun.status = "failed";
      storedRun.endedAt = timestamp();
      storedRun.enqueueError = error.message;
    });
    throw error;
  }

  return run;
}
