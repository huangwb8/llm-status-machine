import { execFile } from "node:child_process";

export const SNAPSHOT_BRANCH = "main";

function runGit(cwd, args, allowFailure = false) {
  return new Promise((resolve, reject) => {
    execFile("git", args, { cwd }, (error, stdout, stderr) => {
      if (error && !allowFailure) {
        reject(new Error(stderr || error.message));
        return;
      }
      resolve({ stdout, stderr, code: error?.code ?? 0 });
    });
  });
}

export async function initRepo(cwd) {
  const init = await runGit(cwd, ["init", "--initial-branch", SNAPSHOT_BRANCH], true);
  if (init.code !== 0) await runGit(cwd, ["init"]);
  await runGit(cwd, ["symbolic-ref", "HEAD", `refs/heads/${SNAPSHOT_BRANCH}`], true);
  await runGit(cwd, ["config", "user.name", "LLM Status Machine"]);
  await runGit(cwd, ["config", "user.email", "llm-status-machine@local"]);
  await runGit(cwd, ["add", "-A"]);
  await runGit(cwd, ["commit", "--allow-empty", "-m", "Initial state"], true);
  await runGit(cwd, ["branch", "-M", SNAPSHOT_BRANCH], true);
  return currentCommit(cwd);
}

export async function commitSnapshot(cwd, message) {
  await runGit(cwd, ["add", "-A"]);
  const status = await runGit(cwd, ["status", "--porcelain"]);
  if (!status.stdout.trim()) {
    return { commit: await currentCommit(cwd), changed: false };
  }

  await runGit(cwd, ["commit", "-m", message], true);
  return { commit: await currentCommit(cwd), changed: true };
}

export async function currentCommit(cwd) {
  const result = await runGit(cwd, ["rev-parse", "--short", "HEAD"], true);
  return result.stdout.trim() || null;
}

export async function diffPatch(cwd) {
  const result = await runGit(cwd, ["diff", "HEAD~1..HEAD"], true);
  return result.stdout;
}

export async function changedFiles(cwd) {
  const result = await runGit(cwd, ["diff", "--name-status", "HEAD~1..HEAD"], true);
  return result.stdout
    .trim()
    .split("\n")
    .filter(Boolean)
    .map((line) => {
      const [status, ...fileParts] = line.split(/\s+/);
      return { status, file: fileParts.join(" ") };
    });
}

export async function diffStat(cwd) {
  const result = await runGit(cwd, ["diff", "--stat", "HEAD~1..HEAD"], true);
  return result.stdout.trim();
}
