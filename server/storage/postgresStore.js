import { nanoid } from "nanoid";
import { createSeed } from "./fileStore.js";

const DOCUMENT_COLLECTIONS = ["prompts", "environments", "states"];
const now = () => new Date().toISOString();

function byUpdatedDesc(a, b) {
  return String(b.updatedAt || b.createdAt || "").localeCompare(String(a.updatedAt || a.createdAt || ""));
}

export function createPostgresStore({ connectionString = process.env.DATABASE_URL, root = process.cwd(), pool } = {}) {
  let poolPromise = pool ? Promise.resolve(pool) : null;

  async function getPool() {
    if (!connectionString && !pool) throw new Error("DATABASE_URL is required when STORAGE_DRIVER=postgres");
    if (!poolPromise) {
      poolPromise = import("pg").then(({ Pool }) => new Pool({ connectionString }));
    }
    return poolPromise;
  }

  async function query(text, params = []) {
    const clientPool = await getPool();
    return clientPool.query(text, params);
  }

  async function withClient(callback) {
    const clientPool = await getPool();
    const client = await clientPool.connect();
    try {
      return await callback(client);
    } finally {
      client.release();
    }
  }

  async function readDocuments(collection) {
    const result = await query(
      "select body from documents where collection = $1 order by updated_at desc, created_at desc",
      [collection]
    );
    const rows = result.rows.map((row) => row.body);
    return rows.length ? rows : createSeed(root)[collection] ?? [];
  }

  async function hydrateRuns(runRows) {
    if (!runRows.length) return [];
    const runIds = runRows.map((row) => row.id);
    const sessionsResult = await query(
      "select id, run_id, body from sessions where run_id = any($1::text[]) order by created_at asc",
      [runIds]
    );
    const eventsResult = await query(
      "select run_id, session_id, body from session_events where run_id = any($1::text[]) order by ts asc",
      [runIds]
    );
    const sessionsByRun = new Map();
    const eventsBySession = new Map();

    for (const eventRow of eventsResult.rows) {
      const key = `${eventRow.run_id}:${eventRow.session_id}`;
      eventsBySession.set(key, [...(eventsBySession.get(key) ?? []), eventRow.body]);
    }

    for (const sessionRow of sessionsResult.rows) {
      const session = {
        ...sessionRow.body,
        events: eventsBySession.get(`${sessionRow.run_id}:${sessionRow.id}`) ?? sessionRow.body.events ?? []
      };
      sessionsByRun.set(sessionRow.run_id, [...(sessionsByRun.get(sessionRow.run_id) ?? []), session]);
    }

    return runRows.map((row) => ({
      ...row.body,
      sessions: sessionsByRun.get(row.id) ?? row.body.sessions ?? []
    }));
  }

  async function readRuns() {
    const runsResult = await query("select id, body from runs order by created_at desc");
    if (!runsResult.rows.length) return [];
    return hydrateRuns(runsResult.rows);
  }

  async function readRun(id) {
    const result = await query("select id, body from runs where id = $1", [id]);
    const [run] = await hydrateRuns(result.rows);
    return run ?? null;
  }

  async function listRuns() {
    const result = await query("select body from runs order by created_at desc");
    return result.rows.map((row) => row.body);
  }

  async function readStore() {
    const store = { prompts: [], environments: [], states: [], runs: [] };
    for (const collection of DOCUMENT_COLLECTIONS) {
      store[collection] = await readDocuments(collection);
    }
    store.runs = await readRuns();

    const seed = createSeed(root);
    for (const collection of DOCUMENT_COLLECTIONS) {
      if (!store[collection].length) store[collection] = seed[collection];
    }
    return store;
  }

  async function writeStore(store) {
    const clientPool = await getPool();
    const client = await clientPool.connect();
    try {
      await client.query("begin");

      for (const collection of DOCUMENT_COLLECTIONS) {
        await client.query("delete from documents where collection = $1", [collection]);
        for (const item of store[collection] ?? []) {
          await client.query(
            `insert into documents (collection, id, body, created_at, updated_at)
             values ($1, $2, $3::jsonb, coalesce($4::timestamptz, now()), coalesce($5::timestamptz, now()))
             on conflict (collection, id) do update
             set body = excluded.body, updated_at = excluded.updated_at`,
            [collection, item.id, JSON.stringify(item), item.createdAt, item.updatedAt]
          );
        }
      }

      await client.query("delete from session_events");
      await client.query("delete from sessions");
      await client.query("delete from runs");

      for (const run of store.runs ?? []) {
        await client.query(
          `insert into runs (id, body, status, created_at, updated_at)
           values ($1, $2::jsonb, $3, coalesce($4::timestamptz, now()), coalesce($5::timestamptz, now()))`,
          [run.id, JSON.stringify(run), run.status, run.createdAt || run.startedAt, run.updatedAt]
        );

        for (const session of run.sessions ?? []) {
          await client.query(
            `insert into sessions (id, run_id, body, status, created_at, updated_at)
             values ($1, $2, $3::jsonb, $4, coalesce($5::timestamptz, now()), coalesce($6::timestamptz, now()))`,
            [session.id, run.id, JSON.stringify(session), session.status, session.startedAt, session.endedAt || run.updatedAt]
          );

          for (const event of session.events ?? []) {
            await client.query(
              `insert into session_events (id, run_id, session_id, body, ts)
               values ($1, $2, $3, $4::jsonb, coalesce($5::timestamptz, now()))`,
              [event.id || nanoid(12), run.id, session.id, JSON.stringify(event), event.ts]
            );
          }
        }
      }

      await client.query("commit");
    } catch (error) {
      await client.query("rollback");
      throw error;
    } finally {
      client.release();
    }
  }

  async function listCollection(name) {
    if (name === "runs") return listRuns();
    if (!DOCUMENT_COLLECTIONS.includes(name)) return [];
    return [...await readDocuments(name)].sort(byUpdatedDesc);
  }

  async function getItem(collection, id) {
    if (collection === "runs") return readRun(id);
    if (!DOCUMENT_COLLECTIONS.includes(collection)) return null;
    const result = await query("select body from documents where collection = $1 and id = $2", [collection, id]);
    return result.rows[0]?.body ?? createSeed(root)[collection]?.find((item) => item.id === id) ?? null;
  }

  async function createItem(collection, attrs) {
    if (collection === "runs") return createRun(attrs);
    if (!DOCUMENT_COLLECTIONS.includes(collection)) throw new Error(`Unsupported collection: ${collection}`);
    const item = {
      id: attrs.id || nanoid(12),
      ...attrs,
      createdAt: attrs.createdAt || now(),
      updatedAt: now()
    };
    await query(
      `insert into documents (collection, id, body, created_at, updated_at)
       values ($1, $2, $3::jsonb, coalesce($4::timestamptz, now()), coalesce($5::timestamptz, now()))`,
      [collection, item.id, JSON.stringify(item), item.createdAt, item.updatedAt]
    );
    return item;
  }

  async function updateItem(collection, id, patch) {
    if (!DOCUMENT_COLLECTIONS.includes(collection)) throw new Error(`Unsupported collection: ${collection}`);
    const existing = await getItem(collection, id);
    if (!existing) return null;
    const item = { ...existing, ...patch, id, updatedAt: now() };
    await query(
      `update documents
       set body = $3::jsonb, updated_at = coalesce($4::timestamptz, now())
       where collection = $1 and id = $2`,
      [collection, id, JSON.stringify(item), item.updatedAt]
    );
    return item;
  }

  async function deleteItem(collection, id) {
    if (!DOCUMENT_COLLECTIONS.includes(collection)) throw new Error(`Unsupported collection: ${collection}`);
    const result = await query("delete from documents where collection = $1 and id = $2", [collection, id]);
    return result.rowCount > 0;
  }

  async function mutateStore(mutator) {
    const store = await readStore();
    const result = await mutator(store);
    await writeStore(store);
    return result;
  }

  async function createRun(run) {
    await query(
      `insert into runs (id, body, status, created_at, updated_at)
       values ($1, $2::jsonb, $3, coalesce($4::timestamptz, now()), coalesce($5::timestamptz, now()))`,
      [run.id, JSON.stringify(run), run.status, run.createdAt || run.startedAt, run.updatedAt]
    );
    return run;
  }

  async function patchRun(runId, patcher) {
    return withClient(async (client) => {
      await client.query("begin");
      try {
        const result = await client.query("select body from runs where id = $1 for update", [runId]);
        const run = result.rows[0]?.body;
        if (!run) {
          await client.query("commit");
          return null;
        }
        patcher(run);
        run.updatedAt = now();
        await client.query(
          "update runs set body = $2::jsonb, status = $3, updated_at = coalesce($4::timestamptz, now()) where id = $1",
          [runId, JSON.stringify(run), run.status, run.updatedAt]
        );
        await client.query("commit");
        return run;
      } catch (error) {
        await client.query("rollback");
        throw error;
      }
    });
  }

  async function getSession(runId, sessionId) {
    const result = await query("select body from sessions where run_id = $1 and id = $2", [runId, sessionId]);
    const session = result.rows[0]?.body;
    if (!session) return null;
    const runResult = await query("select body from runs where id = $1", [runId]);
    const run = runResult.rows[0]?.body;
    return run ? { run, session } : null;
  }

  async function addSession(runId, session) {
    return withClient(async (client) => {
      await client.query("begin");
      try {
        const runResult = await client.query("select body from runs where id = $1 for update", [runId]);
        const run = runResult.rows[0]?.body;
        if (!run) {
          await client.query("commit");
          return null;
        }
        run.sessions = [...(run.sessions ?? []), { ...session, events: [] }];
        run.updatedAt = now();
        await client.query(
          "update runs set body = $2::jsonb, status = $3, updated_at = coalesce($4::timestamptz, now()) where id = $1",
          [runId, JSON.stringify(run), run.status, run.updatedAt]
        );
        await client.query(
          `insert into sessions (id, run_id, body, status, created_at, updated_at)
           values ($1, $2, $3::jsonb, $4, coalesce($5::timestamptz, now()), coalesce($6::timestamptz, now()))
           on conflict (id) do update
           set body = excluded.body, status = excluded.status, updated_at = excluded.updated_at`,
          [session.id, runId, JSON.stringify(session), session.status, session.startedAt, session.endedAt || session.startedAt]
        );
        await client.query("commit");
        return run;
      } catch (error) {
        await client.query("rollback");
        throw error;
      }
    });
  }

  async function patchSession(runId, sessionId, patcher) {
    return withClient(async (client) => {
      await client.query("begin");
      try {
        const sessionResult = await client.query(
          "select body from sessions where run_id = $1 and id = $2 for update",
          [runId, sessionId]
        );
        const session = sessionResult.rows[0]?.body;
        if (!session) {
          await client.query("commit");
          return null;
        }
        patcher(session);
        const updatedAt = now();
        await client.query(
          "update sessions set body = $3::jsonb, status = $4, updated_at = coalesce($5::timestamptz, now()) where run_id = $1 and id = $2",
          [runId, sessionId, JSON.stringify(session), session.status, session.endedAt || updatedAt]
        );
        const runResult = await client.query("select body from runs where id = $1 for update", [runId]);
        const run = runResult.rows[0]?.body;
        if (run) {
          run.sessions = (run.sessions ?? []).map((item) => item.id === sessionId ? { ...session, events: item.events ?? [] } : item);
          run.updatedAt = updatedAt;
          await client.query(
            "update runs set body = $2::jsonb, status = $3, updated_at = coalesce($4::timestamptz, now()) where id = $1",
            [runId, JSON.stringify(run), run.status, run.updatedAt]
          );
        }
        await client.query("commit");
        return session;
      } catch (error) {
        await client.query("rollback");
        throw error;
      }
    });
  }

  async function appendSessionEvent(runId, sessionId, event) {
    await query(
      `insert into session_events (id, run_id, session_id, body, ts)
       values ($1, $2, $3, $4::jsonb, coalesce($5::timestamptz, now()))
       on conflict (id) do nothing`,
      [event.id || nanoid(12), runId, sessionId, JSON.stringify(event), event.ts]
    );
    return patchSession(runId, sessionId, (session) => {
      session.events = [...(session.events ?? []), event];
    });
  }

  return {
    readStore,
    writeStore,
    listCollection,
    getItem,
    createItem,
    updateItem,
    deleteItem,
    mutateStore,
    createRun,
    patchRun,
    getSession,
    addSession,
    patchSession,
    appendSessionEvent
  };
}
