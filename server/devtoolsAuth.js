import { createHash, randomBytes, timingSafeEqual } from "node:crypto";

const KEY_PREFIX = "lsm_";
const RAW_KEY_BYTES = 36;
const KEY_PREFIX_LENGTH = 12;
const now = () => new Date().toISOString();

function hashKey(rawKey) {
  return createHash("sha256").update(String(rawKey || ""), "utf8").digest("hex");
}

function safeEqualHex(left, right) {
  const leftBuffer = Buffer.from(String(left || ""), "hex");
  const rightBuffer = Buffer.from(String(right || ""), "hex");
  return leftBuffer.length === rightBuffer.length && timingSafeEqual(leftBuffer, rightBuffer);
}

function readRawKey(req) {
  return req.get?.("X-Devtools-Key") || req.get?.("x-devtools-key") || "";
}

export async function createDevtoolsKey({ name } = {}, { createItem }) {
  const rawKey = `${KEY_PREFIX}${randomBytes(RAW_KEY_BYTES).toString("base64url")}`;
  const record = await createItem("devtoolsApiKeys", {
    name: String(name || "DevTools key").trim() || "DevTools key",
    keyHash: hashKey(rawKey),
    keyPrefix: rawKey.slice(0, KEY_PREFIX_LENGTH),
    revokedAt: null
  });
  return { rawKey, record };
}

export async function findDevtoolsKeyByRaw(rawKey, { listCollection }) {
  if (!rawKey) return null;
  const keyHash = hashKey(rawKey);
  const keys = await listCollection("devtoolsApiKeys");
  return keys.find((key) => !key.revokedAt && safeEqualHex(key.keyHash, keyHash)) ?? null;
}

export async function revokeDevtoolsKey(id, { updateItem }) {
  return updateItem("devtoolsApiKeys", id, { revokedAt: now() });
}

export function requireDevtoolsKey({ listCollection }) {
  return async (req, res, next) => {
    const rawKey = readRawKey(req);
    if (!rawKey) return res.status(401).json({ error: "missing_api_key" });

    const apiKey = await findDevtoolsKeyByRaw(rawKey, { listCollection });
    if (!apiKey) return res.status(401).json({ error: "invalid_or_revoked_api_key" });

    req.devtoolsApiKey = apiKey;
    next();
  };
}
