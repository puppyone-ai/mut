#!/usr/bin/env node
/**
 * mut-server CLI wrapper — runs the Python mut.server package via python3.
 *
 * Usage:
 *   npx mut-server init ./my-repo
 *   npx mut-server add-scope ./my-repo --id scope-src --scope-path /src/ --agents agent-A
 *   npx mut-server serve ./my-repo
 */

const { spawnSync } = require("child_process");
const path = require("path");

const PYTHON_CANDIDATES = ["python3", "python"];
const ROOT = path.resolve(__dirname, "..");

function findPython() {
  for (const cmd of PYTHON_CANDIDATES) {
    const r = spawnSync(cmd, ["--version"], { stdio: "pipe" });
    if (r.status === 0) {
      const ver = (r.stdout || r.stderr).toString().trim();
      const match = ver.match(/(\d+)\.(\d+)/);
      if (match && (parseInt(match[1]) > 3 || (parseInt(match[1]) === 3 && parseInt(match[2]) >= 9))) {
        return cmd;
      }
    }
  }
  return null;
}

const python = findPython();
if (!python) {
  process.stderr.write(
    "error: mut-server requires Python 3.9+\n" +
    "  macOS:  Python 3 is pre-installed\n" +
    "  Linux:  sudo apt install python3  (or equivalent)\n" +
    "  Windows: https://www.python.org/downloads/\n"
  );
  process.exit(1);
}

const args = ["-m", "mut.server", ...process.argv.slice(2)];
const env = { ...process.env, PYTHONPATH: ROOT + (process.env.PYTHONPATH ? path.delimiter + process.env.PYTHONPATH : "") };

const result = spawnSync(python, args, {
  stdio: "inherit",
  env,
  cwd: process.cwd(),
});

process.exit(result.status ?? 1);
