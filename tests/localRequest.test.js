import test from "node:test";
import assert from "node:assert/strict";
import {
  hostNameFromHeader,
  isDirectoryPickerRequestAllowed,
  isLocalMachineAddress,
  isLoopbackAddress,
  normalizeAddress
} from "../server/localRequest.js";

const interfaces = {
  lo0: [{ address: "127.0.0.1" }, { address: "::1" }],
  en0: [{ address: "192.168.1.20" }]
};

function request(remoteAddress, host = "localhost:4317") {
  return { socket: { remoteAddress }, headers: { host } };
}

test("normalizeAddress handles IPv4-mapped IPv6 and bracketed hosts", () => {
  assert.equal(normalizeAddress("::ffff:127.0.0.1"), "127.0.0.1");
  assert.equal(normalizeAddress("[::1]"), "::1");
});

test("hostNameFromHeader parses host headers with ports", () => {
  assert.equal(hostNameFromHeader("localhost:4317"), "localhost");
  assert.equal(hostNameFromHeader("[::1]:4317"), "::1");
});

test("isLoopbackAddress accepts common local browser hosts", () => {
  assert.equal(isLoopbackAddress("127.0.0.1"), true);
  assert.equal(isLoopbackAddress("::ffff:127.0.0.1"), true);
  assert.equal(isLoopbackAddress("localhost"), true);
  assert.equal(isLoopbackAddress("192.168.1.10"), false);
});

test("isLocalMachineAddress accepts configured local network interfaces", () => {
  assert.equal(isLocalMachineAddress("192.168.1.20", interfaces), true);
  assert.equal(isLocalMachineAddress("192.168.1.21", interfaces), false);
});

test("directory picker allows local and Docker localhost requests by default", () => {
  assert.equal(isDirectoryPickerRequestAllowed(request("127.0.0.1"), { env: {}, networkInterfaces: interfaces }), true);
  assert.equal(isDirectoryPickerRequestAllowed(request("192.168.1.20", "192.168.1.20:4317"), { env: {}, networkInterfaces: interfaces }), true);
  assert.equal(isDirectoryPickerRequestAllowed(request("172.17.0.1", "localhost:4317"), { env: {}, networkInterfaces: interfaces }), true);
});

test("directory picker blocks remote browser sessions unless explicitly enabled", () => {
  assert.equal(isDirectoryPickerRequestAllowed(request("192.168.1.21", "192.168.1.20:4317"), { env: {}, networkInterfaces: interfaces }), false);
  assert.equal(
    isDirectoryPickerRequestAllowed(request("192.168.1.21", "192.168.1.20:4317"), {
      env: { DIRECTORY_PICKER_ALLOW_REMOTE: "1" },
      networkInterfaces: interfaces
    }),
    true
  );
});
