import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

async function waitForRun(readStore, runId) {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    const store = await readStore();
    const run = store.runs.find((item) => item.id === runId);
    if (run && run.status !== "running") return run;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("Timed out waiting for run to finish");
}

test("core serial smoke runs the default poem prompt three times and records each result", async () => {
  process.env.DATA_DIR = await fs.mkdtemp(path.join(os.tmpdir(), "llm-status-runner-"));
  process.env.STORAGE_DRIVER = "file";
  process.env.QUEUE_DRIVER = "inline";
  process.env.EVENT_BUS = "memory";
  process.env.LLM_STATUS_MACHINE_API = "";

  const { startRun, writeAgentArtifact } = await import("../server/runner.js");
  const { readStore, writeStore } = await import("../server/store.js");

  const tmpRoot = path.join(process.cwd(), "tmp");
  await fs.mkdir(tmpRoot, { recursive: true });
  const sourceWorkspace = await fs.mkdtemp(path.join(tmpRoot, "core-smoke-workspace-"));
  await fs.writeFile(path.join(sourceWorkspace, "README.md"), "# Core smoke workspace\n");

  const now = new Date().toISOString();
  await writeStore({
    prompts: [
      {
        id: "prompt-core-poem",
        name: "核心冒烟任务：七言绝句",
        body: "请以“新中国的美人”为题写一首七言绝句。",
        tags: ["smoke"],
        createdAt: now,
        updatedAt: now
      }
    ],
    environments: [
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
        createdAt: now,
        updatedAt: now
      }
    ],
    states: [
      {
        id: "state-core-smoke",
        name: "Core Smoke Workspace",
        path: sourceWorkspace,
        folders: [sourceWorkspace],
        description: "Temporary workspace under ./tmp for the required serial smoke test.",
        createdAt: now,
        updatedAt: now
      }
    ],
    devtoolsApiKeys: [],
    devtoolsConnections: [],
    runs: []
  });

  const created = await startRun({
    stateId: "state-core-smoke",
    environmentId: "env-dry-run",
    mode: "serial",
    promptRuns: [{ promptId: "prompt-core-poem", count: 3 }]
  });

  const run = await waitForRun(readStore, created.id);
  assert.equal(run.status, "completed");
  assert.equal(run.sessions.length, 3);

  run.sessions.forEach((session, index) => {
    assert.equal(session.status, "completed");
    assert.equal(session.sequence, index + 1);
    assert.equal(session.iteration, index + 1);
    assert.equal(session.outputStateName, `state-${index + 1}`);
    assert.equal(session.changedFiles.some((item) => item.file === "llm-simulator-notes.md"), true);
    assert.match(session.diffStat, /llm-simulator-notes\.md/);
    assert.equal(session.events.some((event) => event.type === "session_started"), true);
    assert.equal(session.events.some((event) => event.type === "stdout"), true);
    assert.equal(session.events.some((event) => event.type === "session_finished"), true);
  });

  assert.deepEqual(run.sessions[0].sourceWorkspace, [sourceWorkspace]);
  assert.equal(run.sessions[1].sourceWorkspace, run.sessions[0].workspace);
  assert.equal(run.sessions[2].sourceWorkspace, run.sessions[1].workspace);

  for (const session of run.sessions) {
    const sessionDir = path.dirname(session.workspace);
    const prompt = await fs.readFile(path.join(sessionDir, "prompt.txt"), "utf8");
    const transcript = await fs.readFile(path.join(sessionDir, "transcript.ndjson"), "utf8");
    const diff = await fs.readFile(path.join(sessionDir, "diff.patch"), "utf8");
    const metadata = JSON.parse(await fs.readFile(path.join(sessionDir, "metadata.json"), "utf8"));
    assert.match(prompt, /新中国的美人/);
    assert.match(transcript, /session_finished/);
    assert.match(diff, /llm-simulator-notes\.md/);
    assert.equal(metadata.prompt.id, "prompt-core-poem");
    assert.equal(metadata.environment.model, "simulator");
  }

  const session = run.sessions[0];
  await writeAgentArtifact({
    runId: run.id,
    sessionId: session.id,
    name: "cross-process-note.md",
    content: "written without an active in-process session"
  });

  const artifactPath = path.join(path.dirname(session.workspace), "artifacts", "cross-process-note.md");
  assert.equal(await fs.readFile(artifactPath, "utf8"), "written without an active in-process session");

  const escapedPath = path.join(process.env.DATA_DIR, "..", "escaped-artifact.md");
  await assert.rejects(
    writeAgentArtifact({
      runId: "../escaped",
      sessionId: "../escaped",
      name: "escaped-artifact.md",
      content: "nope"
    }),
    /Session not found/
  );
  await assert.rejects(fs.access(escapedPath));
});

test("copyWorkspace copies multiple source folders into one isolated workspace", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "llm-status-multi-workspace-"));
  const first = path.join(root, "project-a");
  const second = path.join(root, "project-b");
  const target = path.join(root, "target");
  await fs.mkdir(first);
  await fs.mkdir(second);
  await fs.writeFile(path.join(first, "a.txt"), "alpha");
  await fs.writeFile(path.join(second, "b.txt"), "beta");
  await fs.mkdir(path.join(first, ".git"));
  await fs.writeFile(path.join(first, ".git", "config"), "ignored");

  const { copyWorkspace } = await import("../server/runner.js");
  await copyWorkspace([first, second], target);

  assert.equal(await fs.readFile(path.join(target, "project-a", "a.txt"), "utf8"), "alpha");
  assert.equal(await fs.readFile(path.join(target, "project-b", "b.txt"), "utf8"), "beta");
  await assert.rejects(fs.access(path.join(target, "project-a", ".git", "config")));
});
