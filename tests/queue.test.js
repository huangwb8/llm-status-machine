import test from "node:test";
import assert from "node:assert/strict";
import { createRunQueue } from "../server/queue.js";

test("inline queue executes run jobs asynchronously", async () => {
  const seen = [];
  const queue = createRunQueue({ driver: "inline" });

  await queue.enqueueRunJob({ runId: "run-inline" }, {
    processor: async (job) => {
      seen.push(job.runId);
    }
  });

  assert.deepEqual(seen, []);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(seen, ["run-inline"]);
});
