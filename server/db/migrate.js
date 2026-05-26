import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import pg from "pg";

const { Pool } = pg;
const schemaPath = path.join(path.dirname(fileURLToPath(import.meta.url)), "schema.sql");
const schemaVersion = "2026-05-26-001";

if (!process.env.DATABASE_URL) {
  console.error("DATABASE_URL is required to run migrations.");
  process.exit(1);
}

const lockId = 431701;
const pool = new Pool({ connectionString: process.env.DATABASE_URL, max: 1 });
const client = await pool.connect();

try {
  await client.query("select pg_advisory_lock($1)", [lockId]);
  const schema = await fs.readFile(schemaPath, "utf8");
  await client.query(schema);
  await client.query(
    "insert into schema_migrations (version) values ($1) on conflict (version) do nothing",
    [schemaVersion]
  );
  console.log("Database migration complete.");
} finally {
  await client.query("select pg_advisory_unlock($1)", [lockId]).catch(() => {});
  client.release();
  await pool.end();
}
