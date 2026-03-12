#!/bin/bash
set -e

# ──────────────────────────────────────────────────
#  Mut sync test: server/ ↔ client/ full lifecycle
#
#  Layout under tests/:
#    server/seed/src/   — seed files (checked in)
#    server/repo/       — live server repo (generated)
#    client/workspace/  — agent workspace  (generated)
#
#  Flow:
#    1. Init server, copy seed files into current/
#    2. Add scope, issue token, start server
#    3. Client clone
#    4. Client modify → commit → push → verify server
#    5. Server gets new file → client pull → verify client
# ──────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
export PYTHONPATH="$PROJECT_ROOT"

MUT="python3 -m mut"
MUT_SERVER="python3 -m mut.server"

SERVER_REPO="$SCRIPT_DIR/server/repo"
CLIENT_WS="$SCRIPT_DIR/client/workspace"
PORT=$(python3 -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',0)); print(s.getsockname()[1]); s.close()")
SERVER_URL="http://127.0.0.1:$PORT"
SERVER_PID=""

# ── Cleanup ───────────────────────────────────────
cleanup() {
    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    rm -rf "$SERVER_REPO" "$CLIENT_WS"
}
trap cleanup EXIT

rm -rf "$SERVER_REPO" "$CLIENT_WS"

echo "══════════════════════════════════════════"
echo "  Mut Sync Test: server/ ↔ client/"
echo "══════════════════════════════════════════"

# ── Step 1: Init server + seed files ──────────────
echo ""
echo "━━━ Step 1: Init server ━━━"

$MUT_SERVER init "$SERVER_REPO" --name demo-project
cp -r "$SCRIPT_DIR/server/seed/"* "$SERVER_REPO/current/"

echo "  ✓ Server initialized"
echo "  Files in server/repo/current/:"
find "$SERVER_REPO/current" -type f | sort | sed 's/^/    /'

# ── Step 2: Add scope + issue token + start ───────
echo ""
echo "━━━ Step 2: Add scope, issue token, start server ━━━"

$MUT_SERVER add-scope "$SERVER_REPO" --id scope-src --scope-path "/src/" --agents agent-1
TOKEN=$($MUT_SERVER issue-token "$SERVER_REPO" --agent agent-1)

$MUT_SERVER serve "$SERVER_REPO" --port $PORT &
SERVER_PID=$!
sleep 1

if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "  ✗ Server failed to start"
    exit 1
fi
echo "  ✓ Server running at $SERVER_URL (PID $SERVER_PID)"

# ── Step 3: Client clone ─────────────────────────
echo ""
echo "━━━ Step 3: Client clone ━━━"

mkdir -p "$CLIENT_WS"
cd "$CLIENT_WS"
$MUT clone "$SERVER_URL" --token "$TOKEN"

echo "  ✓ Cloned. Client files:"
ls "$CLIENT_WS" | sed 's/^/    /'
echo ""
echo "  client/workspace/app.py:"
cat "$CLIENT_WS/app.py" | sed 's/^/    /'

# ── Step 4: Client modify → commit → push ────────
echo ""
echo "━━━ Step 4: Client modify → commit → push ━━━"

cat > "$CLIENT_WS/app.py" << 'EOF'
def hello():
    print("Updated by client!")

def greet(name):
    print(f"Hi {name}!")

if __name__ == "__main__":
    hello()
EOF

echo '{"project": "demo", "version": "2.0"}' > "$CLIENT_WS/config.json"
echo 'def add(a, b): return a + b' > "$CLIENT_WS/utils.py"

cd "$CLIENT_WS"
echo ""
echo "  mut status:"
$MUT status | sed 's/^/    /'

echo ""
echo "  mut commit:"
$MUT commit -m "update app, config; add utils" -w agent-1 | sed 's/^/    /'

echo ""
echo "  mut push:"
$MUT push | sed 's/^/    /'

echo ""
echo "  ✓ Verify server received changes:"
echo "    server app.py:"
cat "$SERVER_REPO/current/src/app.py" | sed 's/^/      /'
echo "    server utils.py:"
cat "$SERVER_REPO/current/src/utils.py" | sed 's/^/      /'

if grep -q "Updated by client" "$SERVER_REPO/current/src/app.py" && [ -f "$SERVER_REPO/current/src/utils.py" ]; then
    echo "  ✓ PUSH VERIFIED: server has client's changes"
else
    echo "  ✗ PUSH FAILED"
    exit 1
fi

# ── Step 5: Server new file → client pull ─────────
echo ""
echo "━━━ Step 5: Server adds file → client pull ━━━"

echo 'class Logger:
    def log(self, msg):
        print(f"[LOG] {msg}")' > "$SERVER_REPO/current/src/logger.py"

python3 -c "
import sys; sys.path.insert(0, '$PROJECT_ROOT')
from mut.server.repo import ServerRepo
repo = ServerRepo('$SERVER_REPO')
root = repo.build_full_tree()
ver = repo.get_latest_version() + 1
repo.record_history(ver, 'agent-other', 'add logger', '/src/', [{'path':'src/logger.py','action':'add'}], root_hash=root)
repo.set_latest_version(ver)
repo.set_root_hash(root)
"
echo "  ✓ Server added logger.py (version bumped)"

cd "$CLIENT_WS"
echo ""
echo "  mut pull:"
$MUT pull | sed 's/^/    /'

echo ""
if [ -f "$CLIENT_WS/logger.py" ]; then
    echo "  ✓ PULL VERIFIED: client received logger.py"
    echo "    client/workspace/logger.py:"
    cat "$CLIENT_WS/logger.py" | sed 's/^/      /'
else
    echo "  ✗ PULL FAILED: logger.py not found in client"
    exit 1
fi

# ── Done ──────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════"
echo "  ✓ All sync tests passed!"
echo ""
echo "  Final state:"
echo "    server/repo/current/src/:"
ls "$SERVER_REPO/current/src/" | sed 's/^/      /'
echo "    client/workspace/:"
ls "$CLIENT_WS" | grep -v '\.mut' | sed 's/^/      /'
echo "══════════════════════════════════════════"
