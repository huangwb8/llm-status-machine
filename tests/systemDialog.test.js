import test from "node:test";
import assert from "node:assert/strict";
import { createApp } from "../server/index.js";
import {
  createDirectoryPickerCommand,
  getDirectoryPickerStatus,
  selectDirectory
} from "../server/systemDialog.js";

test("directory picker command uses native dialog on macOS", () => {
  const picker = createDirectoryPickerCommand({ platform: "darwin", title: "Workspace" });

  assert.equal(picker.command, "osascript");
  assert.deepEqual(picker.args, ["-e", "POSIX path of (choose folder with prompt \"Workspace\")"]);
});

test("directory picker command uses folder browser on Windows", () => {
  const picker = createDirectoryPickerCommand({ platform: "win32", title: "Workspace" });

  assert.equal(picker.command, "powershell.exe");
  assert.equal(picker.args.includes("-Sta"), true);
  assert.equal(picker.args.at(-1).includes("FolderBrowserDialog"), true);
});

test("directory picker command uses zenity on Linux", () => {
  const picker = createDirectoryPickerCommand({ platform: "linux", title: "Workspace" });

  assert.deepEqual(picker, {
    command: "zenity",
    args: ["--file-selection", "--directory", "--title=Workspace"]
  });
});

test("directory picker reports unavailable when linux command is missing", async () => {
  const result = await getDirectoryPickerStatus({
    platform: "linux",
    env: { DISPLAY: ":0" },
    commandExists: async () => false
  });

  assert.deepEqual(result, {
    available: false,
    platform: "linux",
    reason: "missing-command",
    command: "zenity",
    message: "Native directory picker is unavailable because zenity is not installed on the API host."
  });
});

test("directory picker reports unavailable when linux has no graphical display", async () => {
  const result = await getDirectoryPickerStatus({
    platform: "linux",
    env: {},
    commandExists: async () => true
  });

  assert.deepEqual(result, {
    available: false,
    platform: "linux",
    reason: "no-display",
    command: "zenity",
    message: "Native directory picker is unavailable because the API host does not have a graphical display."
  });
});

test("selectDirectory throws a clear unavailable message when picker command is missing", async () => {
  await assert.rejects(
    () =>
      selectDirectory({
        platform: "linux",
        env: { DISPLAY: ":0" },
        commandExists: async () => false
      }),
    /Native directory picker is unavailable because zenity is not installed on the API host\./
  );
});

test("selectDirectory reports unavailable when zenity cannot open a display", async () => {
  await assert.rejects(
    () =>
      selectDirectory({
        platform: "linux",
        env: { DISPLAY: ":0" },
        commandExists: async () => true,
        execFileAsync: async () => {
          const error = new Error("Command failed");
          error.code = 1;
          error.stderr = "Gtk-WARNING **: cannot open display: :0";
          throw error;
        }
      }),
    (error) => {
      assert.equal(error.status, 409);
      assert.equal(error.picker.reason, "no-display");
      assert.equal(error.message, "Native directory picker is unavailable because the API host does not have a graphical display.");
      return true;
    }
  );
});

test("selectDirectory still treats an empty zenity cancellation as cancelled", async () => {
  const result = await selectDirectory({
    platform: "linux",
    env: { DISPLAY: ":0" },
    commandExists: async () => true,
    execFileAsync: async () => {
      const error = new Error("Command failed");
      error.code = 1;
      error.stderr = "";
      throw error;
    }
  });

  assert.deepEqual(result, { path: "", cancelled: true });
});

test("directory picker status API returns current picker capability", async () => {
  const app = createApp({
    getDirectoryPickerStatusImpl: async () => ({
      available: false,
      platform: "linux",
      reason: "missing-command",
      command: "zenity",
      message: "Native directory picker is unavailable because zenity is not installed on the API host."
    })
  });
  const server = app.listen(0);

  try {
    const { port } = server.address();
    const response = await fetch(`http://127.0.0.1:${port}/api/system/directory-picker`);

    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), {
      available: false,
      platform: "linux",
      reason: "missing-command",
      command: "zenity",
      message: "Native directory picker is unavailable because zenity is not installed on the API host."
    });
  } finally {
    await new Promise((resolve, reject) => server.close((error) => (error ? reject(error) : resolve())));
  }
});

test("select directory API returns 409 when native picker is unavailable", async () => {
  const picker = {
    available: false,
    platform: "linux",
    reason: "missing-command",
    command: "zenity",
    message: "Native directory picker is unavailable because zenity is not installed on the API host."
  };
  const app = createApp({
    getDirectoryPickerStatusImpl: async () => picker,
    isDirectoryPickerRequestAllowedImpl: () => true,
    selectDirectoryImpl: async () => ({ path: "/should/not/run", cancelled: false })
  });
  const server = app.listen(0);

  try {
    const { port } = server.address();
    const response = await fetch(`http://127.0.0.1:${port}/api/system/select-directory`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "Workspace" })
    });

    assert.equal(response.status, 409);
    assert.deepEqual(await response.json(), {
      error: picker.message,
      picker
    });
  } finally {
    await new Promise((resolve, reject) => server.close((error) => (error ? reject(error) : resolve())));
  }
});
