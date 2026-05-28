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

test("inline runner completes a dry-run session and records workspace diff", async () => {
  process.env.DATA_DIR = await fs.mkdtemp(path.join(os.tmpdir(), "llm-status-runner-"));
  process.env.STORAGE_DRIVER = "file";
  process.env.QUEUE_DRIVER = "inline";
  process.env.EVENT_BUS = "memory";
  process.env.LLM_STATUS_MACHINE_API = "";

  const { startRun, writeAgentArtifact } = await import("../server/runner.js");
  const { readStore } = await import("../server/store.js");

  const created = await startRun({
    stateId: "state-buggy-js",
    environmentId: "env-dry-run",
    mode: "serial",
    promptRuns: [{ promptId: "prompt-review", count: 1 }]
  });

  const run = await waitForRun(readStore, created.id);
  assert.equal(run.status, "completed");
  assert.equal(run.sessions.length, 1);
  assert.equal(run.sessions[0].status, "completed");
  assert.equal(run.sessions[0].changedFiles.some((item) => item.file === "llm-simulator-notes.md"), true);

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
