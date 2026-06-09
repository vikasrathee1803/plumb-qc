// Start the Plumb backend for `npm run dev`. Uses the repo's .venv interpreter
// when present (so deps are found even without activating the venv), enables
// the local-dev auth bypass, and serves on :8000 to match the Vite proxy.
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..", "..", ".."); // web/ui/scripts -> repo root
const isWin = process.platform === "win32";
const venvPy = isWin
  ? join(repoRoot, ".venv", "Scripts", "python.exe")
  : join(repoRoot, ".venv", "bin", "python");
const py = existsSync(venvPy) ? venvPy : isWin ? "python" : "python3";

const child = spawn(
  py,
  ["-m", "uvicorn", "web.api.app:app", "--host", "127.0.0.1", "--port", "8000"],
  {
    stdio: "inherit",
    cwd: repoRoot,
    env: { ...process.env, PLUMB_DISABLE_AUTH: "1", PYTHONPATH: repoRoot },
  }
);
child.on("exit", (code) => process.exit(code ?? 0));
