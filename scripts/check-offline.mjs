// Runs the offline-asset check with the repository virtualenv (never a global interpreter).
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const python = [".venv/Scripts/python.exe", ".venv/bin/python"]
  .map((path) => fileURLToPath(new URL(`../${path}`, import.meta.url)))
  .find(existsSync);

if (!python) {
  console.error("Repository virtualenv not found (.venv); create it before running check:offline.");
  process.exit(1);
}
const result = spawnSync(python, ["scripts/check_offline_assets.py"], { cwd: root, stdio: "inherit" });
process.exit(result.status ?? 1);
