import fs from "node:fs/promises";
import path from "node:path";

const promptFile = process.argv[2];
const prompt = await fs.readFile(promptFile, "utf8");
const logPath = path.join(process.cwd(), "llm-simulator-notes.md");
const api = process.env.LLM_STATUS_MACHINE_API;
const runId = process.env.LLM_STATUS_MACHINE_RUN_ID;
const sessionId = process.env.LLM_STATUS_MACHINE_SESSION_ID;

const note = [
  "# Simulator response",
  "",
  `Time: ${new Date().toISOString()}`,
  "",
  "Prompt excerpt:",
  "",
  "```",
  prompt.slice(0, 1200),
  "```",
  "",
  "This dry-run client records a deterministic file change so the capture pipeline can be tested without calling an external LLM."
].join("\n");

await fs.writeFile(logPath, note);

if (api && runId && sessionId) {
  await fetch(`${api}/api/agent/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      runId,
      sessionId,
      type: "assistant_note",
      payload: "Dry-run simulator generated a deterministic workspace change."
    })
  }).catch(() => {});

  await fetch(`${api}/api/agent/artifacts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      runId,
      sessionId,
      name: "behavior-summary.md",
      content: [
        "# Behavior summary",
        "",
        "- Client: dry-run simulator",
        "- Action: wrote `llm-simulator-notes.md`",
        "- Purpose: verify transcript, artifact, diff, and git capture without an external model"
      ].join("\n")
    })
  }).catch(() => {});
}

console.log("Dry-run simulator wrote llm-simulator-notes.md");
