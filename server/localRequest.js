import os from "node:os";

export function normalizeAddress(address = "") {
  return String(address)
    .trim()
    .replace(/^\[|\]$/g, "")
    .replace(/^::ffff:/, "")
    .split("%")[0];
}

export function hostNameFromHeader(host = "") {
  const value = String(host).trim();
  if (!value) return "";
  if (value.startsWith("[")) {
    const end = value.indexOf("]");
    return end === -1 ? value.slice(1) : value.slice(1, end);
  }
  return value.split(":")[0];
}

export function isLoopbackAddress(address = "") {
  const normalized = normalizeAddress(address).toLowerCase();
  return normalized === "localhost"
    || normalized === "::1"
    || normalized === "0:0:0:0:0:0:0:1"
    || normalized === "0.0.0.0"
    || normalized === "::"
    || normalized === "127.0.0.1"
    || /^127\./.test(normalized);
}

export function localInterfaceAddresses(networkInterfaces = os.networkInterfaces()) {
  const addresses = new Set();
  for (const entries of Object.values(networkInterfaces)) {
    for (const entry of entries || []) {
      if (entry?.address) addresses.add(normalizeAddress(entry.address).toLowerCase());
    }
  }
  return addresses;
}

export function isLocalMachineAddress(address = "", networkInterfaces = os.networkInterfaces()) {
  const normalized = normalizeAddress(address).toLowerCase();
  return isLoopbackAddress(normalized) || localInterfaceAddresses(networkInterfaces).has(normalized);
}

export function isDirectoryPickerRequestAllowed(req, {
  env = process.env,
  networkInterfaces = os.networkInterfaces()
} = {}) {
  if (/^(1|true|yes)$/i.test(String(env.DIRECTORY_PICKER_ALLOW_REMOTE || ""))) return true;
  if (isLocalMachineAddress(req.socket?.remoteAddress, networkInterfaces)) return true;
  return isLoopbackAddress(hostNameFromHeader(req.headers?.host));
}
