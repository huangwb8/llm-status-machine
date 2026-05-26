import { execFile } from "node:child_process";
import os from "node:os";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

export function createDirectoryPickerCommand({ platform = process.platform, title = "Select workspace folder" } = {}) {
  if (platform === "darwin") {
    return {
      command: "osascript",
      args: ["-e", `POSIX path of (choose folder with prompt "${title.replaceAll("\"", "'")}")`]
    };
  }

  if (platform === "win32") {
    const script = [
      "Add-Type -AssemblyName System.Windows.Forms",
      "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog",
      `$dialog.Description = '${title.replaceAll("'", "''")}'`,
      "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { $dialog.SelectedPath }"
    ].join("; ");

    return {
      command: "powershell.exe",
      args: ["-NoProfile", "-Sta", "-Command", script]
    };
  }

  if (platform === "linux") {
    return {
      command: "zenity",
      args: ["--file-selection", "--directory", `--title=${title}`]
    };
  }

  return null;
}

export async function selectDirectory({ title } = {}) {
  const picker = createDirectoryPickerCommand({ platform: os.platform(), title });
  if (!picker) {
    throw new Error(`Directory picker is not supported on ${os.platform()}.`);
  }

  try {
    const { stdout } = await execFileAsync(picker.command, picker.args, { timeout: 120000 });
    const selectedPath = stdout.trim();
    return selectedPath ? { path: selectedPath, cancelled: false } : { path: "", cancelled: true };
  } catch (error) {
    if (error.code === 1 || error.signal === "SIGTERM") return { path: "", cancelled: true };
    throw new Error(`Unable to open directory picker: ${error.message}`);
  }
}
