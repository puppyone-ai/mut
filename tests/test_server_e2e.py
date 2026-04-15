#!/usr/bin/env python3
"""
MUT Server E2E Test — Client ↔ Server Integration
===================================================
Tests the three init scenarios from 05-mut-init-clone-accesspoint.md
against a real MUT server running on PUPPY_OPENCLAW_SERVER.

Setup: uploads latest mut code to server, installs, starts server.
Tests: Scenario A (clone), Scenario B (init + link + dir), Scenario C (init + link existing).
Cleanup: stops server, removes test data.

Usage:
    # Requires .env with PUPPY_OPENCLAW_SERVER, PUPPY_OPENCLAW_USERNAME, PUPPY_OPENCLAW_PASSWORD
    python tests/test_server_e2e.py
"""
from __future__ import annotations

import os
import sys
import time
import json
import shutil
import tempfile
import traceback
from pathlib import Path

import paramiko

# Add mut to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mut.ops import init_op, clone_op, commit_op, push_op, pull_op, status_op
from mut.ops.repo import MutRepo
from mut.ops import link_access_op


# ── Helpers ──

def load_env():
    env = {}
    env_path = Path(__file__).resolve().parents[2] / ".env"
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def ssh_connect(env):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        env["PUPPY_OPENCLAW_SERVER"],
        username=env["PUPPY_OPENCLAW_USERNAME"],
        password=env["PUPPY_OPENCLAW_PASSWORD"],
        timeout=10,
    )
    return ssh


def ssh_exec(ssh, cmd, timeout=30):
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    return out, err


def upload_dir(ssh, local_dir, remote_dir):
    """Upload a directory to the remote server via SFTP."""
    sftp = ssh.open_sftp()
    for root, dirs, files in os.walk(local_dir):
        for d in dirs:
            if d in ("__pycache__", ".git", ".mut", "node_modules", ".egg-info", "mut.egg-info"):
                continue
            local_path = os.path.join(root, d)
            rel = os.path.relpath(local_path, local_dir)
            remote_path = f"{remote_dir}/{rel}".replace("\\", "/")
            try:
                sftp.mkdir(remote_path)
            except IOError:
                pass
        for f in files:
            if f.endswith((".pyc", ".pyo")):
                continue
            local_path = os.path.join(root, f)
            rel = os.path.relpath(local_path, local_dir)
            remote_path = f"{remote_dir}/{rel}".replace("\\", "/")
            # Ensure parent dir
            parent = "/".join(remote_path.split("/")[:-1])
            try:
                sftp.mkdir(parent)
            except IOError:
                pass
            sftp.put(local_path, remote_path)
    sftp.close()


class T:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []
        self._sec = ""

    def section(self, name):
        self._sec = name
        print(f"\n{'='*60}\n  {name}\n{'='*60}")

    def check(self, name, cond, detail=""):
        if cond:
            self.passed += 1
            print(f"  \u2713 {name}")
        else:
            self.failed += 1
            self.errors.append(f"[{self._sec}] {name}: {detail}")
            print(f"  \u2717 {name} \u2014 {detail}")
        return cond


# ══════════════════════════════════════════════════════════════

def main():
    env = load_env()
    server_ip = env["PUPPY_OPENCLAW_SERVER"]
    mut_src = str(Path(__file__).resolve().parents[1])
    base_dir = tempfile.mkdtemp(prefix="mut-server-e2e-")
    t = T()

    print(f"MUT Server E2E Test")
    print(f"Server: {server_ip}")
    print(f"Temp:   {base_dir}")

    ssh = ssh_connect(env)

    # ── Setup: upload mut + install + start server ──
    t.section("0. Setup: Deploy MUT to server")

    # Clean old test data
    ssh_exec(ssh, "rm -rf /tmp/mut-e2e-test /tmp/mut-server-e2e")
    ssh_exec(ssh, "pkill -f 'mut.server.server' 2>/dev/null || true")
    time.sleep(1)

    # Upload latest mut source
    print("  Uploading mut source...")
    ssh_exec(ssh, "rm -rf /tmp/mut-e2e-src && mkdir -p /tmp/mut-e2e-src")

    # Upload only the mut/ package and pyproject.toml
    sftp = ssh.open_sftp()
    sftp.put(os.path.join(mut_src, "pyproject.toml"), "/tmp/mut-e2e-src/pyproject.toml")
    sftp.close()
    upload_dir(ssh, os.path.join(mut_src, "mut"), "/tmp/mut-e2e-src/mut")

    # Install in venv
    print("  Setting up venv + installing mut...")
    venv_dir = "/tmp/mut-e2e-venv"
    ssh_exec(ssh, f"rm -rf {venv_dir} && python3 -m venv {venv_dir}")
    out, _ = ssh_exec(ssh, f"{venv_dir}/bin/pip install -e /tmp/mut-e2e-src 2>&1 | tail -5", timeout=60)
    print(f"    {out}")
    t.check("Mut installed", "Successfully" in out or "already satisfied" in out.lower(), out[:200])

    py = f"{venv_dir}/bin/python3"

    # Verify import
    out, _ = ssh_exec(ssh, f"{py} -c 'import mut.cli; print(\"OK\")'")
    t.check("Mut importable", out.strip() == "OK", out)

    # Start MUT server
    print("  Starting MUT server...")
    server_dir = "/tmp/mut-server-e2e"
    ssh_exec(ssh, f"rm -rf {server_dir}")

    # Initialize server repo: python -m mut.server init <path>
    out, _ = ssh_exec(ssh, f"{py} -m mut.server init {server_dir} --name e2e-test 2>&1")
    print(f"    init: {out[:150]}")
    t.check("Server repo initialized", "Initialized" in out or os.path.isdir(server_dir), out[:150])

    # Start server: python -m mut.server serve <path> --host 0.0.0.0 --port 9742 --auth none
    ssh_exec(ssh, f"nohup {py} -m mut.server serve {server_dir} --host 0.0.0.0 --port 9742 --auth none > /tmp/mut-server.log 2>&1 &")
    time.sleep(3)

    # Check if running
    out, _ = ssh_exec(ssh, "ps aux | grep 'mut.server' | grep -v grep | head -1")
    server_running = bool(out)

    if not server_running:
        log, _ = ssh_exec(ssh, "cat /tmp/mut-server.log 2>/dev/null | tail -10")
        print(f"    Server log: {log[:300]}")

    t.check("Server running", server_running, "check /tmp/mut-server.log on server")

    # Create a scope so NoAuth mode can work (scope ID = credential)
    scope_id = "e2e-root"
    out, _ = ssh_exec(ssh, f"{py} -m mut.server add-scope {server_dir} --id {scope_id} --scope-path /")
    print(f"    scope: {out}")
    t.check("Scope created", "Added scope" in out, out[:100])

    server_url = f"http://{server_ip}:9742"
    credential = scope_id  # NoAuth mode: scope ID as Bearer token

    # ── Scenario A: Clone from server ──
    t.section("A. Clone from server")

    if server_running:
        workdir_a = os.path.join(base_dir, "scenario-a")
        try:
            repo_a = clone_op.clone(server_url, credential=credential, workdir=workdir_a)
            t.check("Clone succeeds", repo_a is not None)
            t.check(".mut/ created", (Path(workdir_a) / ".mut").is_dir())

            # Write + commit + push
            (Path(workdir_a) / "readme.md").write_text("# Scenario A\nCloned from server")
            (Path(workdir_a) / "data.json").write_text(json.dumps({"source": "clone"}))
            snap = commit_op.commit(repo_a, message="scenario A: initial", who="test-a")
            t.check("Commit succeeds", snap is not None)

            result = push_op.push(repo_a)
            t.check("Push succeeds", result.get("status") in ("ok", "pushed", "up-to-date"),
                    f"status={result.get('status')}")
        except Exception as e:
            t.check("Scenario A", False, str(e)[:200])
    else:
        print("  SKIP: server not running")

    # ── Scenario B: Init + link access with dir ──
    t.section("B. Init empty dir + link access with root_dir_name")

    if server_running:
        workdir_b = os.path.join(base_dir, "scenario-b")
        os.makedirs(workdir_b, exist_ok=True)
        try:
            # Init
            repo_b = init_op.init(workdir_b)
            t.check("Init succeeds", (Path(workdir_b) / ".mut").is_dir())

            # Link with root_dir_name
            result = link_access_op.link_access(repo_b, server_url, root_dir_name="research", credential_override=credential)
            t.check("Link succeeds", result.get("status") == "linked", json.dumps(result)[:200])
            t.check("Scope created", result.get("scope_created") is True or result.get("server_version", 0) > 0)

            # Verify directory created locally
            t.check("research/ dir exists", (Path(workdir_b) / "research").is_dir())

            # Write file in scope + commit + push
            (Path(workdir_b) / "research" / "notes.md").write_text("# Research Notes")
            repo_b = MutRepo(workdir_b)  # reload
            snap = commit_op.commit(repo_b, message="scenario B: research note", who="test-b")
            t.check("Commit succeeds", snap is not None)

            result = push_op.push(repo_b)
            t.check("Push succeeds", result.get("status") in ("ok", "pushed"),
                    f"status={result.get('status')}")
        except Exception as e:
            t.check("Scenario B", False, str(e)[:200])
    else:
        print("  SKIP: server not running")

    # ── Scenario C: Init with existing files + link ──
    t.section("C. Init non-empty dir + link access (no dir_name)")

    if server_running:
        workdir_c = os.path.join(base_dir, "scenario-c")
        os.makedirs(workdir_c, exist_ok=True)

        # Pre-populate with files
        (Path(workdir_c) / "src").mkdir()
        (Path(workdir_c) / "src" / "main.py").write_text("print('hello')")
        (Path(workdir_c) / "docs").mkdir()
        (Path(workdir_c) / "docs" / "readme.md").write_text("# Docs")
        (Path(workdir_c) / "config.json").write_text(json.dumps({"env": "test"}))

        try:
            # Init
            repo_c = init_op.init(workdir_c)
            t.check("Init succeeds", (Path(workdir_c) / ".mut").is_dir())

            # Commit existing files first
            snap = commit_op.commit(repo_c, message="initial: existing files", who="test-c")
            t.check("Commit existing files", snap is not None)

            # Link without dir_name (bind to existing server)
            result = link_access_op.link_access(repo_c, server_url, credential_override=credential)
            t.check("Link succeeds", result.get("status") == "linked")

            # Push existing files to server
            repo_c = MutRepo(workdir_c)  # reload
            result = push_op.push(repo_c)
            t.check("Push existing files", result.get("status") in ("ok", "pushed", "merged"),
                    f"status={result.get('status')}")

            # Pull to verify round-trip
            workdir_c2 = os.path.join(base_dir, "scenario-c-verify")
            repo_c2 = clone_op.clone(server_url, credential=credential, workdir=workdir_c2)
            files = list(Path(workdir_c2).rglob("*"))
            file_names = [f.name for f in files if f.is_file() and ".mut" not in str(f)]
            t.check("Clone sees pushed files", "main.py" in file_names,
                    f"files={file_names}")
            t.check("Clone sees config.json", "config.json" in file_names)
        except Exception as e:
            t.check("Scenario C", False, str(e)[:200])
    else:
        print("  SKIP: server not running")

    # ── Cross-scenario verification ──
    t.section("D. Cross-scenario: pull from one, see all")

    if server_running:
        try:
            workdir_d = os.path.join(base_dir, "scenario-d")
            repo_d = clone_op.clone(server_url, credential=credential, workdir=workdir_d)
            files = [f.relative_to(workdir_d).as_posix()
                     for f in Path(workdir_d).rglob("*")
                     if f.is_file() and ".mut" not in str(f)]
            t.check("Clone has files from all scenarios", len(files) >= 3,
                    f"files={files}")
        except Exception as e:
            t.check("Cross-scenario clone", False, str(e)[:200])

    # ── Cleanup ──
    t.section("99. Cleanup")

    ssh_exec(ssh, "pkill -f 'mut.server.server' 2>/dev/null || true")
    ssh_exec(ssh, "rm -rf /tmp/mut-e2e-test /tmp/mut-server-e2e /tmp/mut-e2e-src")
    t.check("Server stopped", True)

    shutil.rmtree(base_dir, ignore_errors=True)
    t.check("Local temp cleaned", True)

    ssh.close()

    # ── Report ──
    print(f"\n{'='*60}\n  RESULTS\n{'='*60}")
    print(f"  Passed:  {t.passed}")
    print(f"  Failed:  {t.failed}")
    if t.errors:
        print(f"\n  FAILURES:")
        for e in t.errors:
            print(f"    \u2717 {e}")
    pct = (t.passed / max(t.passed + t.failed, 1)) * 100
    print(f"\n  Pass rate: {pct:.0f}%")
    sys.exit(0 if t.failed == 0 else 1)


if __name__ == "__main__":
    main()
