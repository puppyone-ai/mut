#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
MUT="python3 -m mut"
WORKDIR="/tmp/mut-test-$$"
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
cd "$WORKDIR"

export PYTHONPATH="$PROJECT_ROOT"

echo "══════════════════════════════════════════"
echo "  Mut — End-to-End Test (layered architecture)"
echo "  workdir: $WORKDIR"
echo "  project: $PROJECT_ROOT"
echo "══════════════════════════════════════════"

# ── init ──────────────────────────────────────
echo ""
echo "▸ mut init"
$MUT init

# ── 创建文件（模拟 Agent A 的工作区）─────────
mkdir -p src docs
echo '{"name": "my-project", "version": "1.0"}' > config.json
echo 'def main():
    print("hello mut")

if __name__ == "__main__":
    main()' > src/main.py
echo '# API Docs
## Endpoints
- GET /health' > docs/api.md

# ── commit 1 ──────────────────────────────────
echo ""
echo "▸ mut commit (Agent A: initial setup)"
$MUT commit -m "Initial project setup" -w agent-A

# ── log ───────────────────────────────────────
echo ""
echo "▸ mut log"
$MUT log

# ── tree ──────────────────────────────────────
echo ""
echo "▸ mut tree 1"
$MUT tree 1

# ── status (should be clean) ──────────────────
echo ""
echo "▸ mut status (should be clean)"
$MUT status

# ── Agent A 修改一个文件 ──────────────────────
echo '{"name": "my-project", "version": "1.1", "description": "Mut demo"}' > config.json

echo ""
echo "▸ mut status (after modifying config.json)"
$MUT status

# ── commit 2 ──────────────────────────────────
echo ""
echo "▸ mut commit (Agent A: bump version)"
$MUT commit -m "Bump version to 1.1" -w agent-A

# ── Agent B 添加新文件 + 修改文件 ─────────────
echo '# Contributing Guide
1. Fork the repo
2. Make changes
3. Submit PR' > docs/contributing.md

echo 'def main():
    print("hello mut v2")
    run_server()

def run_server():
    print("server started")

if __name__ == "__main__":
    main()' > src/main.py

echo ""
echo "▸ mut status (after Agent B's changes)"
$MUT status

# ── commit 3 ──────────────────────────────────
echo ""
echo "▸ mut commit (Agent B: add docs + update main)"
$MUT commit -m "Add contributing guide and update main" -w agent-B

# ── Agent C 删除一个文件 ──────────────────────
rm docs/api.md

echo ""
echo "▸ mut commit (Agent C: remove old api docs)"
$MUT commit -m "Remove deprecated api docs" -w agent-C

# ── 完整 log ──────────────────────────────────
echo ""
echo "▸ mut log (full history)"
$MUT log

# ── diff 1→4 ──────────────────────────────────
echo ""
echo "▸ mut diff 1 4 (all changes from start to now)"
$MUT diff 1 4

# ── show 文件内容 ─────────────────────────────
echo ""
echo "▸ mut show 1:src/main.py (original version)"
$MUT show 1:src/main.py

echo ""
echo "▸ mut show 3:src/main.py (after Agent B's edit)"
$MUT show 3:src/main.py

# ── tree 对比 ─────────────────────────────────
echo ""
echo "▸ mut tree 4 (current)"
$MUT tree 4

# ── push (stub) ───────────────────────────────
echo ""
echo "▸ mut push (stub — marks snapshots as pushed)"
$MUT push

echo ""
echo "▸ mut log (after push — all should be ✓)"
$MUT log

# ── pull (stub) ───────────────────────────────
echo ""
echo "▸ mut pull (stub — nothing to pull)"
$MUT pull

# ── checkout 回到 v1 ──────────────────────────
echo ""
echo "▸ mut checkout 1 (rollback to initial)"
$MUT checkout 1

echo ""
echo "▸ verify config.json is back to v1:"
cat config.json

# ── checkout 回到最新 ─────────────────────────
echo ""
echo "▸ mut checkout 4 (back to latest)"
$MUT checkout 4

echo ""
echo "▸ verify config.json is v1.1:"
cat config.json

# ── stats ─────────────────────────────────────
echo ""
echo "▸ mut stats"
$MUT stats

echo ""
echo "▸ .mut/ directory:"
find .mut -type f | sort | head -30

echo ""
echo "══════════════════════════════════════════"
echo "  All tests passed!"
echo "══════════════════════════════════════════"

rm -rf "$WORKDIR"
