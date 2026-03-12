#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
MUT="python3 -m mut"
MUT_SERVER="python3 -m mut.server"

TESTDIR="/tmp/mut-e2e-merge-$$"
rm -rf "$TESTDIR"
mkdir -p "$TESTDIR"

export PYTHONPATH="$PROJECT_ROOT"

SERVER_DIR="$TESTDIR/server-repo"
AGENT_A_DIR="$TESTDIR/workspace-A"
AGENT_D_DIR="$TESTDIR/workspace-D"
PORT=9743
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

echo "══════════════════════════════════════════════════════"
echo "  Mut — Phase 4 Test: Conflict Detection + Auto-Merge"
echo "  testdir: $TESTDIR"
echo "══════════════════════════════════════════════════════"

# ══════════════════════════════════════════════════
# SETUP: Server with two agents sharing /src/ scope
# ══════════════════════════════════════════════════
echo ""
echo "━━━ Setup: Server + two agents on SAME scope ━━━"

$MUT_SERVER init "$SERVER_DIR" --name "merge-test"

mkdir -p "$SERVER_DIR/current/src"
cat > "$SERVER_DIR/current/src/main.py" << 'PYEOF'
def main():
    print("hello")

def helper():
    return 42

if __name__ == "__main__":
    main()
PYEOF

cat > "$SERVER_DIR/current/src/config.json" << 'JSONEOF'
{
  "name": "merge-test",
  "version": "1.0",
  "debug": false,
  "port": 8080
}
JSONEOF

$MUT_SERVER add-scope "$SERVER_DIR" --id scope-src-a --scope-path "/src/" --agents agent-A
$MUT_SERVER add-scope "$SERVER_DIR" --id scope-src-d --scope-path "/src/" --agents agent-D

TOKEN_A=$($MUT_SERVER issue-token "$SERVER_DIR" --agent agent-A)
TOKEN_D=$($MUT_SERVER issue-token "$SERVER_DIR" --agent agent-D)

$MUT_SERVER serve "$SERVER_DIR" --port $PORT &
SERVER_PID=$!
sleep 1
echo "  ✓ Server running. Two agents (A, D) share /src/"

# ══════════════════════════════════════════════════
# Both agents clone the same scope
# ══════════════════════════════════════════════════
echo ""
echo "━━━ Both agents clone /src/ ━━━"

mkdir -p "$AGENT_A_DIR"
cd "$AGENT_A_DIR"
$MUT clone "$SERVER_URL" --token "$TOKEN_A"
echo "  ✓ Agent-A cloned"

mkdir -p "$AGENT_D_DIR"
cd "$AGENT_D_DIR"
$MUT clone "$SERVER_URL" --token "$TOKEN_D"
echo "  ✓ Agent-D cloned"

echo ""
echo "  Both see the same main.py:"
cat "$AGENT_A_DIR/main.py"

# ══════════════════════════════════════════════════
# TEST 1: Non-overlapping line edits → auto-merge
# ══════════════════════════════════════════════════
echo ""
echo "━━━ Test 1: Non-overlapping edits → line merge ━━━"

# Agent-A modifies the FIRST function (line 1-2)
cd "$AGENT_A_DIR"
cat > main.py << 'PYEOF'
def main():
    print("hello v2 from A")

def helper():
    return 42

if __name__ == "__main__":
    main()
PYEOF

$MUT commit -m "A: update main greeting" -w agent-A
$MUT push
echo "  ✓ Agent-A pushed (modifies line 2)"

# Agent-D modifies the SECOND function (line 4-5) — based on OLD version
cd "$AGENT_D_DIR"
cat > main.py << 'PYEOF'
def main():
    print("hello")

def helper():
    return 99

if __name__ == "__main__":
    main()
PYEOF

$MUT commit -m "D: update helper return" -w agent-D
echo ""
echo "  Agent-D pushes (base_version behind server) — should auto-merge..."
$MUT push

echo ""
echo "  ✓ Server merged result (should have BOTH changes):"
cat "$SERVER_DIR/current/src/main.py"

echo ""
echo "  Checking server file contains both edits..."
if grep -q "hello v2 from A" "$SERVER_DIR/current/src/main.py" && grep -q "return 99" "$SERVER_DIR/current/src/main.py"; then
    echo "  ✓ TEST 1 PASSED: Line merge worked!"
else
    echo "  ✗ TEST 1 FAILED: Merge did not combine both edits"
    exit 1
fi

# ══════════════════════════════════════════════════
# TEST 2: JSON key-level merge → auto-merge
# ══════════════════════════════════════════════════
echo ""
echo "━━━ Test 2: JSON key-level merge ━━━"

# Sync Agent-A
cd "$AGENT_A_DIR"
$MUT pull

# Agent-A changes "version" key
cat > config.json << 'JSONEOF'
{
  "name": "merge-test",
  "version": "2.0",
  "debug": false,
  "port": 8080
}
JSONEOF
$MUT commit -m "A: bump version to 2.0" -w agent-A
$MUT push
echo "  ✓ Agent-A pushed (changed version key)"

# Agent-D changes "debug" key (based on old version)
cd "$AGENT_D_DIR"
cat > config.json << 'JSONEOF'
{
  "name": "merge-test",
  "version": "1.0",
  "debug": true,
  "port": 8080
}
JSONEOF
$MUT commit -m "D: enable debug" -w agent-D
echo ""
echo "  Agent-D pushes (different JSON key) — should auto-merge..."
$MUT push

echo ""
echo "  ✓ Server merged JSON:"
cat "$SERVER_DIR/current/src/config.json"

echo ""
echo "  Checking JSON has both changes..."
if grep -q '"version": "2.0"' "$SERVER_DIR/current/src/config.json" && grep -q '"debug": true' "$SERVER_DIR/current/src/config.json"; then
    echo "  ✓ TEST 2 PASSED: JSON merge worked!"
else
    echo "  ✗ TEST 2 FAILED: JSON merge did not combine both edits"
    exit 1
fi

# ══════════════════════════════════════════════════
# TEST 3: Same-line conflict → LWW (incoming wins)
# ══════════════════════════════════════════════════
echo ""
echo "━━━ Test 3: Same-line conflict → LWW ━━━"

# Sync both
cd "$AGENT_A_DIR"
$MUT pull
cd "$AGENT_D_DIR"
$MUT pull

# Agent-A changes line 2 to "AAA"
cd "$AGENT_A_DIR"
cat > main.py << 'PYEOF'
def main():
    print("AAA wins")

def helper():
    return 99

if __name__ == "__main__":
    main()
PYEOF
$MUT commit -m "A: set AAA" -w agent-A
$MUT push
echo "  ✓ Agent-A pushed 'AAA wins'"

# Agent-D ALSO changes line 2 to "DDD" (based on old version)
cd "$AGENT_D_DIR"
cat > main.py << 'PYEOF'
def main():
    print("DDD wins")

def helper():
    return 99

if __name__ == "__main__":
    main()
PYEOF
$MUT commit -m "D: set DDD" -w agent-D
echo ""
echo "  Agent-D pushes (same line conflict) — LWW: incoming (D) wins..."
$MUT push

echo ""
echo "  ✓ Server result (D should win — Last Writer Wins):"
cat "$SERVER_DIR/current/src/main.py"

echo ""
if grep -q "DDD wins" "$SERVER_DIR/current/src/main.py"; then
    echo "  ✓ TEST 3 PASSED: LWW correctly chose incoming (agent-D)!"
else
    echo "  ✗ TEST 3 FAILED: LWW did not work as expected"
    exit 1
fi

# ══════════════════════════════════════════════════
# TEST 4: Check audit log exists
# ══════════════════════════════════════════════════
echo ""
echo "━━━ Test 4: Audit log ━━━"

echo "  Audit entries:"
ls "$SERVER_DIR/.mut-server/audit/" | head -10

AUDIT_COUNT=$(ls "$SERVER_DIR/.mut-server/audit/" | wc -l | tr -d ' ')
echo ""
echo "  Total audit entries: $AUDIT_COUNT"

if [ "$AUDIT_COUNT" -gt 0 ]; then
    echo "  ✓ TEST 4 PASSED: Audit log has entries"
else
    echo "  ✗ TEST 4 FAILED: No audit entries"
    exit 1
fi

# Show a conflict audit entry
echo ""
echo "  Sample conflict audit:"
CONFLICT_FILE=$(ls "$SERVER_DIR/.mut-server/audit/"*merge_conflict* 2>/dev/null | head -1)
if [ -n "$CONFLICT_FILE" ]; then
    cat "$CONFLICT_FILE"
else
    echo "  (no merge_conflict audits — line merge resolved without LWW)"
fi

# ══════════════════════════════════════════════════
# TEST 5: History has root hashes and conflict records
# ══════════════════════════════════════════════════
echo ""
echo "━━━ Test 5: History with root hashes ━━━"

echo "  Server version: $(cat "$SERVER_DIR/.mut-server/history/latest")"
echo "  Server root hash: $(cat "$SERVER_DIR/.mut-server/history/root")"

LAST_HISTORY=$(ls "$SERVER_DIR/.mut-server/history/"*.json | sort | tail -1)
echo ""
echo "  Latest history entry:"
cat "$LAST_HISTORY"

echo ""
echo ""
echo "══════════════════════════════════════════════════════"
echo "  All Phase 4+5 tests passed!"
echo "  ✓ Line-level three-way merge"
echo "  ✓ JSON key-level merge"
echo "  ✓ LWW fallback for true conflicts"
echo "  ✓ Audit logging"
echo "  ✓ Root hash tracking + history"
echo "══════════════════════════════════════════════════════"
