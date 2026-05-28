const now = () => new Date().toISOString();

function cleanText(value, fallback = "") {
  return String(value ?? fallback).trim();
}

function publicConnection(connection) {
  return {
    id: connection.id,
    keyId: connection.keyId,
    keyPrefix: connection.keyPrefix,
    clientName: connection.clientName,
    clientVersion: connection.clientVersion,
    machine: connection.machine,
    workdir: connection.workdir,
    lastSeenAt: connection.lastSeenAt,
    lastError: connection.lastError,
    terminateRequestedAt: connection.terminateRequestedAt,
    terminatedAt: connection.terminatedAt,
    createdAt: connection.createdAt,
    updatedAt: connection.updatedAt
  };
}

async function findConnectionForKey(connectionId, apiKey, { listCollection }) {
  if (!connectionId) return null;
  const connections = await listCollection("devtoolsConnections");
  return connections.find((connection) => connection.id === connectionId && connection.keyId === apiKey.id) ?? null;
}

export async function createConnection(apiKey, payload = {}, { createItem }) {
  const timestamp = now();
  const connection = await createItem("devtoolsConnections", {
    keyId: apiKey.id,
    keyPrefix: apiKey.keyPrefix,
    clientName: cleanText(payload.clientName, "external-agent") || "external-agent",
    clientVersion: cleanText(payload.clientVersion),
    machine: cleanText(payload.machine),
    workdir: cleanText(payload.workdir),
    lastSeenAt: timestamp,
    lastError: null,
    terminateRequestedAt: null,
    terminatedAt: null
  });
  return { connection: publicConnection(connection), connectionId: connection.id, terminate: false };
}

export async function heartbeatConnection(apiKey, payload = {}, deps) {
  const existing = await findConnectionForKey(payload.connectionId, apiKey, deps);
  if (!existing) {
    const error = new Error("Connection not found");
    error.status = 404;
    throw error;
  }

  const connection = await deps.updateItem("devtoolsConnections", existing.id, {
    lastSeenAt: now(),
    lastError: payload.lastError ?? null
  });
  return {
    connection: publicConnection(connection),
    connectionId: connection.id,
    terminate: Boolean(connection.terminateRequestedAt)
  };
}

export async function disconnectConnection(apiKey, payload = {}, deps) {
  const existing = await findConnectionForKey(payload.connectionId, apiKey, deps);
  if (!existing) {
    const error = new Error("Connection not found");
    error.status = 404;
    throw error;
  }

  const connection = await deps.updateItem("devtoolsConnections", existing.id, {
    lastSeenAt: now(),
    terminatedAt: now()
  });
  return { ok: true, connection: publicConnection(connection), connectionId: connection.id };
}

export async function requestTerminateConnection(id, { updateItem }) {
  const connection = await updateItem("devtoolsConnections", id, { terminateRequestedAt: now() });
  if (!connection) {
    const error = new Error("Connection not found");
    error.status = 404;
    throw error;
  }
  return publicConnection(connection);
}
