import fs from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import { nanoid } from "nanoid";
import { RUNS_DIR, mutateStore, timestamp } from "./store.js";
import { SNAPSHOT_BRANCH, changedFiles, commitSnapshot, diffPatch, diffStat, initRepo } from "./git.js";
import { createExecutionPlan } from "./experimentPlan.js";

const serverDir = path.dirname(new URL(import.meta.url).pathname);
const simulatorPath = path.join(serverDir, "simulator.js");
const listeners = new Set();
const activeSessions = new Map();

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function broadcast(event) {
  for (const listener of listeners) listener(event);
}

function emit(runId, sessionId, type, payload) {
  const event = {
    id: nanoid(10),
    runId,
    sessionId,
    type,
    payload,
    ts: timestamp()
  };
  broadcast(event);
  return event;
}

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

async function copyWorkspace(source, target) {
  await fs.mkdir(target, { recursive: true });
  await fs.cp(source, target, {
    recursive: true,
    force: true,
    filter: (item) => !item.includes(`${path.sep}.git${path.sep}`) && !item.endsWith(`${path.sep}.git`)
  });
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

async function patchRun(runId, patcher) {
  return mutateStore((store) => {
    const run = store.runs.find((item) => item.id === runId);
    if (!run) return null;
    patcher(run);
    run.updatedAt = timestamp();
    return run;
  });
}

async function persistEvent(runId, sessionId, event) {
  await patchRun(runId, (run) => {
    const session = run.sessions.find((item) => item.id === sessionId);
    if (session) session.events.push(event);
  });
}

async function recordSessionEvent(runId, sessionId, event) {
  const active = activeSessions.get(sessionId);
  if (active) {
    await appendLine(active.paths.transcript, JSON.stringify(event));
    if (event.type === "stdout") await fs.appendFile(active.paths.stdout, String(event.payload));
    if (event.type === "stderr") await fs.appendFile(active.paths.stderr, String(event.payload));
  }
  await persistEvent(runId, sessionId, event);
}

async function runSession({ run, prompt, environment, state, iteration, sequence, sourceWorkspace, outputLabel }) {
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

  await patchRun(run.id, (storedRun) => {
    storedRun.sessions.push(session);
  });

  const remember = async (type, payload) => {
    const event = emit(run.id, sessionId, type, payload);
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

    await patchRun(run.id, (storedRun) => {
      const storedSession = storedRun.sessions.find((item) => item.id === sessionId);
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

    await patchRun(run.id, (storedRun) => {
      const storedSession = storedRun.sessions.find((item) => item.id === sessionId);
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
    await patchRun(run.id, (storedRun) => {
      const storedSession = storedRun.sessions.find((item) => item.id === sessionId);
      if (storedSession) {
        storedSession.status = "failed";
        storedSession.endedAt = timestamp();
      }
    });
    await remember("error", error.stack || error.message);
  } finally {
    activeSessions.delete(sessionId);
  }
}

export async function recordAgentEvent({ runId, sessionId, type, payload }) {
  if (!runId || !sessionId) throw new Error("runId and sessionId are required");
  const event = emit(runId, sessionId, type || "agent_event", payload ?? null);
  await recordSessionEvent(runId, sessionId, event);
  return event;
}

export async function writeAgentArtifact({ runId, sessionId, name, content, encoding = "utf8" }) {
  if (!runId || !sessionId) throw new Error("runId and sessionId are required");
  if (!name) throw new Error("Artifact name is required");

  const active = activeSessions.get(sessionId);
  const artifactsDir = active?.paths.artifacts ?? path.join(RUNS_DIR, runId, sessionId, "artifacts");
  await fs.mkdir(artifactsDir, { recursive: true });

  const safeName = path.basename(name).replace(/[^\w.-]/g, "_");
  if (!safeName) throw new Error("Artifact name is invalid");
  const body = encoding === "base64" ? Buffer.from(String(content || ""), "base64") : String(content ?? "");
  await fs.writeFile(path.join(artifactsDir, safeName), body);

  const artifacts = await listArtifacts(artifactsDir);
  await patchRun(runId, (storedRun) => {
    const session = storedRun.sessions.find((item) => item.id === sessionId);
    if (session) session.artifacts = artifacts;
  });

  const size = Buffer.isBuffer(body) ? body.length : Buffer.byteLength(body);
  await recordAgentEvent({ runId, sessionId, type: "artifact_written", payload: { name: safeName, size } });
  return { name: safeName, size };
}

export async function startRun(config) {
  let run;
  await mutateStore((store) => {
    const state = store.states.find((item) => item.id === config.stateId);
    const environment = store.environments.find((item) => item.id === config.environmentId);
    const prompts = config.promptRuns
      .map((selection) => {
        const prompt = store.prompts.find((item) => item.id === selection.promptId);
        return prompt ? { ...selection, prompt } : null;
      })
      .filter(Boolean);

    if (!state) throw new Error("State not found");
    if (!environment) throw new Error("Environment not found");
    if (!prompts.length) throw new Error("Select at least one prompt");

    run = {
      id: nanoid(12),
      name: config.name || `Run ${new Date().toLocaleString()}`,
      stateId: state.id,
      stateName: state.name,
      environmentId: environment.id,
      environmentName: environment.name,
      mode: config.mode === "parallel" ? "parallel" : "serial",
      promptRuns: config.promptRuns,
      status: "running",
      startedAt: timestamp(),
      endedAt: null,
      sessions: [],
      createdAt: timestamp(),
      updatedAt: timestamp()
    };
    store.runs.unshift(run);
    return run;
  });

  queueMicrotask(async () => {
    const stored = await mutateStore((store) => store);
    const state = stored.states.find((item) => item.id === config.stateId);
    const environment = stored.environments.find((item) => item.id === config.environmentId);
    const jobs = createExecutionPlan({
      mode: run.mode,
      runId: run.id,
      initialWorkspace: state.path,
      promptRuns: config.promptRuns,
      prompts: stored.prompts,
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
      emit(run.id, null, "run_finished", { status: "done" });
    } catch (error) {
      await patchRun(run.id, (storedRun) => {
        storedRun.status = "failed";
        storedRun.endedAt = timestamp();
      });
      emit(run.id, null, "run_failed", error.stack || error.message);
    }
  });

  return run;
}
