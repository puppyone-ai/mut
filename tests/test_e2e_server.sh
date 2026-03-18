#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
MUT="python3 -m mut"
MUT_SERVER="python3 -m mut.server"

TESTDIR="/tmp/mut-e2e-server-$$"
rm -rf "$TESTDIR"
mkdir -p "$TESTDIR"

export PYTHONPATH="$PROJECT_ROOT"

SERVER_DIR="$TESTDIR/server-repo"
AGENT_A_DIR="$TESTDIR/workspace-A"
AGENT_B_DIR="$TESTDIR/workspace-B"
PORT=9742
SERVER_URL="http://127.0.0.1:$PORT"
SERVER_PID=""

cleanup() {
    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    rm -rf "$TESTDIR"
}
trap cleanup EXIT

echo "══════════════════════════════════════════════════"
echo "  Mut — Phase 2 End-to-End Test (Client ↔ Server)"
echo "  testdir: $TESTDIR"
echo "══════════════════════════════════════════════════"

# ══════════════════════════════════════════════════
# STEP 1: Initialize server repository
# ══════════════════════════════════════════════════
echo ""
echo "━━━ Step 1: Initialize server ━━━"

$MUT_SERVER init "$SERVER_DIR" --name "test-project"

# Seed some project files into current/
mkdir -p "$SERVER_DIR/current/src"
mkdir -p "$SERVER_DIR/current/docs"
echo 'def main(): print("hello")' > "$SERVER_DIR/current/src/main.py"
echo 'def add(a,b): return a+b' > "$SERVER_DIR/current/src/utils.py"
echo '# My Project' > "$SERVER_DIR/current/docs/readme.md"

echo "  ✓ Server repo initialized with seed files"

# ══════════════════════════════════════════════════
# STEP 2: Add scopes for two agents
# ══════════════════════════════════════════════════
echo ""
echo "━━━ Step 2: Add scopes ━━━"

$MUT_SERVER add-scope "$SERVER_DIR" --id scope-src --scope-path "/src/"
$MUT_SERVER add-scope "$SERVER_DIR" --id scope-docs --scope-path "/docs/"

echo "  ✓ Agent-A → /src/ (rw)"
echo "  ✓ Agent-B → /docs/ (rw)"

# ══════════════════════════════════════════════════
# STEP 3: Issue credentials
# ══════════════════════════════════════════════════
echo ""
echo "━━━ Step 3: Issue credentials ━━━"

CRED_A=$($MUT_SERVER issue-credential "$SERVER_DIR" --scope scope-src --agent agent-A --mode rw)
CRED_B=$($MUT_SERVER issue-credential "$SERVER_DIR" --scope scope-docs --agent agent-B --mode rw)

echo "  ✓ Cred-A: ${CRED_A:0:30}..."
echo "  ✓ Cred-B: ${CRED_B:0:30}..."

# ══════════════════════════════════════════════════
# STEP 4: Start server in background
# ══════════════════════════════════════════════════
echo ""
echo "━━━ Step 4: Start server ━━━"

$MUT_SERVER serve "$SERVER_DIR" --port $PORT &
SERVER_PID=$!
sleep 1

# Verify server is running
if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "  ✗ Server failed to start!"
    exit 1
fi
echo "  ✓ Server running on $SERVER_URL (PID: $SERVER_PID)"

# ══════════════════════════════════════════════════
# STEP 5: Agent A clones /src/
# ══════════════════════════════════════════════════
echo ""
echo "━━━ Step 5: Agent-A clones /src/ ━━━"

mkdir -p "$AGENT_A_DIR"
cd "$AGENT_A_DIR"
$MUT clone "$SERVER_URL" --credential "$CRED_A"

echo "  ✓ Cloned. Files in workspace:"
ls -la "$AGENT_A_DIR"
echo "  ✓ main.py content:"
cat "$AGENT_A_DIR/main.py"

# ══════════════════════════════════════════════════
# STEP 6: Agent B clones /docs/
# ══════════════════════════════════════════════════
echo ""
echo "━━━ Step 6: Agent-B clones /docs/ ━━━"

mkdir -p "$AGENT_B_DIR"
cd "$AGENT_B_DIR"
$MUT clone "$SERVER_URL" --credential "$CRED_B"

echo "  ✓ Cloned. Files in workspace:"
ls -la "$AGENT_B_DIR"
echo "  ✓ readme.md content:"
cat "$AGENT_B_DIR/readme.md"

# ══════════════════════════════════════════════════
# STEP 7: Agent A makes changes, commits locally, pushes
# ══════════════════════════════════════════════════
echo ""
echo "━━━ Step 7: Agent-A works → commit → push ━━━"

cd "$AGENT_A_DIR"
echo 'def main(): print("hello v2")' > main.py

echo ""
echo "▸ mut status"
$MUT status

echo ""
echo "▸ mut commit"
$MUT commit -m "update main to v2" -w agent-A

echo ""
echo "▸ mut log"
$MUT log

echo ""
echo "▸ mut push"
$MUT push

echo ""
echo "▸ mut log (after push)"
$MUT log

# ══════════════════════════════════════════════════
# STEP 8: Agent B makes changes, commits, pushes
# ══════════════════════════════════════════════════
echo ""
echo "━━━ Step 8: Agent-B works → commit → push ━━━"

cd "$AGENT_B_DIR"
echo '# My Project v2
## Overview
This is a Mut-managed project.' > readme.md

echo ""
echo "▸ mut commit"
$MUT commit -m "update readme" -w agent-B

echo ""
echo "▸ mut push"
$MUT push

# ══════════════════════════════════════════════════
# STEP 9: Agent A pulls Agent B's changes (no overlap)
# ══════════════════════════════════════════════════
echo ""
echo "━━━ Step 9: Agent-A pulls (should get nothing — different scopes) ━━━"

cd "$AGENT_A_DIR"
echo ""
echo "▸ mut pull"
$MUT pull

# ══════════════════════════════════════════════════
# STEP 10: Verify server has everything
# ══════════════════════════════════════════════════
echo ""
echo "━━━ Step 10: Verify server state ━━━"

echo "  Server current/ files:"
find "$SERVER_DIR/current" -type f | sort

echo ""
echo "  Server src/main.py content:"
cat "$SERVER_DIR/current/src/main.py"

echo ""
echo "  Server docs/readme.md content:"
cat "$SERVER_DIR/current/docs/readme.md"

echo ""
echo "  Server history/latest:"
cat "$SERVER_DIR/.mut-server/history/latest"

# ══════════════════════════════════════════════════
# STEP 11: Agent A makes another commit + push
# ══════════════════════════════════════════════════
echo ""
echo "━━━ Step 11: Agent-A second push ━━━"

cd "$AGENT_A_DIR"
echo 'def sub(a,b): return a-b' > utils.py

$MUT commit -m "modify utils" -w agent-A
$MUT push

echo "  ✓ Second push complete"

echo ""
echo "  Server src/utils.py now:"
cat "$SERVER_DIR/current/src/utils.py"

# ══════════════════════════════════════════════════
# DONE
# ══════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  All Phase 2 tests passed!"
echo "══════════════════════════════════════════════════"
