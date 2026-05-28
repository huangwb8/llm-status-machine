import { execFile } from "node:child_process";
import os from "node:os";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

function shellQuote(value) {
  return `'${String(value).replaceAll("'", "'\\''")}'`;
}

function unavailableStatus({ platform, command, reason = "missing-command" }) {
  if (reason === "unsupported-platform") {
    return {
      available: false,
      platform,
      reason,
      command: "",
      message: `Native directory picker is not supported on ${platform}.`
    };
  }

  if (reason === "no-display") {
    return {
      available: false,
      platform,
      reason,
      command,
      message: "Native directory picker is unavailable because the API host does not have a graphical display."
    };
  }

  return {
    available: false,
    platform,
    reason,
    command,
    message: `Native directory picker is unavailable because ${command} is not installed on the API host.`
  };
}

function hasLinuxGraphicalDisplay(env = process.env) {
  return Boolean(env.DISPLAY || env.WAYLAND_DISPLAY);
}

function isDisplayUnavailableError(error) {
  const detail = `${error?.stderr || ""}\n${error?.stdout || ""}\n${error?.message || ""}`.toLowerCase();
  return /cannot open display|unable to init server|no protocol specified|gtk-warning|wayland|xdg_runtime_dir/.test(detail);
}

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

export async function commandExists(command, { platform = process.platform } = {}) {
  try {
    if (platform === "win32") {
      await execFileAsync("where", [command], { timeout: 10000 });
      return true;
    }

    await execFileAsync("sh", ["-lc", `command -v ${shellQuote(command)}`], { timeout: 10000 });
    return true;
  } catch {
    return false;
  }
}

export async function getDirectoryPickerStatus({
  platform = os.platform(),
  title = "Select workspace folder",
  env = process.env,
  commandExists: commandExistsImpl = commandExists
} = {}) {
  const picker = createDirectoryPickerCommand({ platform, title });
  if (!picker) return unavailableStatus({ platform, command: "", reason: "unsupported-platform" });

  const exists = await commandExistsImpl(picker.command, { platform });
  if (!exists) return unavailableStatus({ platform, command: picker.command });

  if (platform === "linux" && !hasLinuxGraphicalDisplay(env)) {
    return unavailableStatus({ platform, command: picker.command, reason: "no-display" });
  }

  return {
    available: true,
    platform,
    command: picker.command,
    message: "Native directory picker is available."
  };
}

export async function selectDirectory({
  title = "Select workspace folder",
  platform = os.platform(),
  env = process.env,
  commandExists: commandExistsImpl = commandExists,
  execFileAsync: execFileImpl = execFileAsync
} = {}) {
  const status = await getDirectoryPickerStatus({ platform, title, env, commandExists: commandExistsImpl });
  if (!status.available) {
    const error = new Error(status.message);
    error.status = 409;
    error.picker = status;
    throw error;
  }

  const picker = createDirectoryPickerCommand({ platform, title });

  try {
    const { stdout } = await execFileImpl(picker.command, picker.args, { timeout: 120000 });
    const selectedPath = stdout.trim();
    return selectedPath ? { path: selectedPath, cancelled: false } : { path: "", cancelled: true };
  } catch (error) {
    if (platform === "linux" && isDisplayUnavailableError(error)) {
      const missingDisplay = unavailableStatus({ platform, command: picker.command, reason: "no-display" });
      const pickerError = new Error(missingDisplay.message);
      pickerError.status = 409;
      pickerError.picker = missingDisplay;
      throw pickerError;
    }
    if (error.code === 1 || error.signal === "SIGTERM") return { path: "", cancelled: true };
    if (error.code === "ENOENT") {
      const missing = unavailableStatus({ platform, command: picker.command });
      const pickerError = new Error(missing.message);
      pickerError.status = 409;
      pickerError.picker = missing;
      throw pickerError;
    }
    throw new Error(`Unable to open directory picker: ${error.message}`);
  }
}
