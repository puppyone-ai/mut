#!/usr/bin/env node
/**
 * mut CLI wrapper — runs the Python mut package via python3.
 *
 * This allows `npx mut init`, `npx mut commit -m "msg"`, etc.
 * Requires Python 3.9+ to be available on the system (pre-installed
 * on macOS and most Linux distributions).
 *
 * The Python source is bundled inside this npm package, so no
 * `pip install` is needed.
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
    "error: mut requires Python 3.9+\n" +
    "  macOS:  Python 3 is pre-installed\n" +
    "  Linux:  sudo apt install python3  (or equivalent)\n" +
    "  Windows: https://www.python.org/downloads/\n"
  );
  process.exit(1);
}

const args = ["-m", "mut", ...process.argv.slice(2)];
const env = { ...process.env, PYTHONPATH: ROOT + (process.env.PYTHONPATH ? path.delimiter + process.env.PYTHONPATH : "") };

const result = spawnSync(python, args, {
  stdio: "inherit",
  env,
  cwd: process.cwd(),
});

process.exit(result.status ?? 1);
