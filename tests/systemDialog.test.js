import test from "node:test";
import assert from "node:assert/strict";
import { createDirectoryPickerCommand } from "../server/systemDialog.js";

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
