import test from "node:test";
import assert from "node:assert/strict";
import { createExecutionPlan } from "../server/experimentPlan.js";

const prompts = [
  { id: "prompt-a", name: "A" },
  { id: "prompt-b", name: "B" }
];

test("serial experiment chains each output workspace into the next independent session", () => {
  const plan = createExecutionPlan({
    mode: "serial",
    runId: "run-1",
    initialWorkspace: "/fixtures/state-i",
    promptRuns: [
      { promptId: "prompt-a", count: 2 },
      { promptId: "prompt-b", count: 1 }
    ],
    prompts
  });

  assert.deepEqual(
    plan.map((job) => ({
      promptId: job.prompt.id,
      iteration: job.iteration,
      sourceWorkspace: job.sourceWorkspace,
      outputLabel: job.outputLabel
    })),
    [
      { promptId: "prompt-a", iteration: 1, sourceWorkspace: "/fixtures/state-i", outputLabel: "state-1" },
      { promptId: "prompt-a", iteration: 2, sourceWorkspace: "run-1/state-1/workspace", outputLabel: "state-2" },
      { promptId: "prompt-b", iteration: 1, sourceWorkspace: "run-1/state-2/workspace", outputLabel: "state-3" }
    ]
  );
});

test("parallel experiment keeps repeated sessions independent from the initial workspace", () => {
  const plan = createExecutionPlan({
    mode: "parallel",
    runId: "run-2",
    initialWorkspace: "/fixtures/state-i",
    promptRuns: [{ promptId: "prompt-a", count: 3 }],
    prompts
  });

  assert.equal(plan.length, 3);
  assert.deepEqual(plan.map((job) => job.sourceWorkspace), [
    "/fixtures/state-i",
    "/fixtures/state-i",
    "/fixtures/state-i"
  ]);
});
