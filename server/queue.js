const QUEUE_NAME = process.env.RUN_QUEUE_NAME || "llm-status-machine-runs";

export function createRunQueue({ driver = process.env.QUEUE_DRIVER || "inline", redisUrl = process.env.REDIS_URL } = {}) {
  let queuePromise = null;
  let worker = null;

  function connectionOptions() {
    const connection = redisUrl ? parseRedisUrl(redisUrl) : { host: "127.0.0.1", port: 6379 };
    return {
      connection: {
        ...connection,
        maxRetriesPerRequest: null,
        enableReadyCheck: false
      }
    };
  }

  async function getQueue() {
    if (!queuePromise) {
      queuePromise = import("bullmq").then(({ Queue }) => new Queue(QUEUE_NAME, connectionOptions()));
    }
    return queuePromise;
  }

  async function enqueueRunJob(job, { processor } = {}) {
    if (driver === "inline") {
      if (!processor) throw new Error("Inline queue requires a processor");
      setImmediate(async () => {
        try {
          await processor(job);
        } catch (error) {
          console.error("Inline run job failed", error);
        }
      });
      return { driver, status: "queued", job };
    }

    if (driver !== "redis") throw new Error(`Unsupported QUEUE_DRIVER: ${driver}`);
    const queue = await getQueue();
    const added = await queue.add("run.execute", job, {
      jobId: job.runId,
      attempts: 1,
      removeOnComplete: 100,
      removeOnFail: 100
    });
    return { driver, status: "queued", id: added.id };
  }

  async function startWorker(processor) {
    if (driver !== "redis") {
      throw new Error("QUEUE_DRIVER=redis is required for the standalone worker");
    }
    const { Worker } = await import("bullmq");
    worker = new Worker(
      QUEUE_NAME,
      async (job) => processor(job.data),
      connectionOptions()
    );
    worker.on("failed", (job, error) => {
      console.error(`Run job ${job?.id || "unknown"} failed`, error);
    });
    return worker;
  }

  async function close() {
    if (worker) await worker.close();
    if (queuePromise) {
      const queue = await queuePromise;
      await queue.close();
    }
  }

  return { driver, enqueueRunJob, startWorker, close };
}

function parseRedisUrl(value) {
  const url = new URL(value);
  const db = url.pathname && url.pathname !== "/" ? Number(url.pathname.slice(1)) : undefined;
  return {
    host: url.hostname,
    port: Number(url.port || 6379),
    username: decodeURIComponent(url.username || ""),
    password: url.password ? decodeURIComponent(url.password) : undefined,
    db: Number.isFinite(db) ? db : undefined,
    tls: url.protocol === "rediss:" ? {} : undefined
  };
}

export const runQueue = createRunQueue();
export const enqueueRunJob = runQueue.enqueueRunJob;
export const startRunWorker = runQueue.startWorker;
