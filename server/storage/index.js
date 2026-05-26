import path from "node:path";
import { createFileStore } from "./fileStore.js";
import { createPostgresStore } from "./postgresStore.js";

export const ROOT = process.cwd();
export const DATA_DIR = process.env.DATA_DIR
  ? path.resolve(process.env.DATA_DIR)
  : path.join(ROOT, "data");
export const RUNS_DIR = path.join(DATA_DIR, "runs");
export const STATES_DIR = path.join(DATA_DIR, "states");

export function createStorageFromEnv(env = process.env) {
  const driver = env.STORAGE_DRIVER || "file";
  if (driver === "postgres") {
    return createPostgresStore({ connectionString: env.DATABASE_URL, root: ROOT });
  }
  if (driver !== "file") throw new Error(`Unsupported STORAGE_DRIVER: ${driver}`);
  return createFileStore({ root: ROOT, dataDir: DATA_DIR });
}

export const storage = createStorageFromEnv();
