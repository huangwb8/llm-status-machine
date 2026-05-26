import { processRunJob } from "./runner.js";
import { startRunWorker } from "./queue.js";

const worker = await startRunWorker(processRunJob);

console.log("LLM Status Machine worker listening for run jobs.");

async function shutdown(signal) {
  console.log(`Received ${signal}, closing worker.`);
  await worker.close();
  process.exit(0);
}

process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));
