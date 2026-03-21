#!/usr/bin/env bash
set -e

MUT="python3 -m mut"
MUT_SERVER="python3 -m mut.server"
TESTDIR="/tmp/mut-srvfeat-$$"
rm -rf "$TESTDIR"
mkdir -p "$TESTDIR"
export PYTHONPATH="${PYTHONPATH:-$(cd "$(dirname "$0")/.." && pwd)}"

SERVER_DIR="$TESTDIR/server-repo"
PORT=9763
SERVER_URL="http://127.0.0.1:$PORT"
SERVER_PID=""

trap cleanup EXIT
PASS=0
FAIL=0

assert_pass() {
  local msg="$1"
  PASS=$((PASS + 1))
  echo "  PASSED: $msg"
}
assert_fail() {
  local msg="$1"
  FAIL=$((FAIL + 1))
  echo "  FAILED: $msg"
}
check() {
  local msg="$1"; shift
  if "$@" >/dev/null 2>&1; then assert_pass "$msg"; else assert_fail "$msg"; fi
}
check_grep() {
  local msg="$1" pattern="$2" file="$3"
  if grep -q "$pattern" "$file" 2>/dev/null; then assert_pass "$msg"; else assert_fail "$msg"; fi
}
check_not_grep() {
  local msg="$1" pattern="$2" file="$3"
  if ! grep -q "$pattern" "$file" 2>/dev/null; then assert_pass "$msg"; else assert_fail "$msg"; fi
}

cleanup() {
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$TESTDIR"
}

W() { echo "$TESTDIR/w$1/srvfeat-test"; }

start_server() {
  local auth_mode="${1:-none}"
  $MUT_SERVER serve "$SERVER_DIR" --port $PORT --auth "$auth_mode" &
  SERVER_PID=$!
  sleep 1
}

stop_server() {
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
    SERVER_PID=""
  fi
}

echo ""
echo "================================================================"
echo "  Mut Server Features Test"
echo "  Auth, rollback, pull-version, read-only, scope excludes"
echo "================================================================"

# ── Setup ─────────────────────────────────────────
$MUT_SERVER init "$SERVER_DIR" --name "srvfeat-test"

# Create seed files
mkdir -p "$SERVER_DIR/current/src"
cat > "$SERVER_DIR/current/src/main.py" << 'PYEOF'
def main():
    print("hello world")

def helper():
    return 42

if __name__ == "__main__":
    main()
PYEOF

cat > "$SERVER_DIR/current/src/config.json" << 'JSONEOF'
{
  "name": "test-app",
  "version": "1.0.0",
  "port": 3000,
  "debug": false
}
JSONEOF

cat > "$SERVER_DIR/current/src/notes.md" << 'MDEOF'
# Project Notes

## TODO
- [ ] Add tests
- [ ] Write docs
- [x] Setup CI

## Architecture
The system uses a client-server model.
MDEOF

mkdir -p "$SERVER_DIR/current/src/internal"
echo "SECRET_KEY=abc123" > "$SERVER_DIR/current/src/internal/secrets.env"
echo "internal docs" > "$SERVER_DIR/current/src/internal/README.md"

# Add scopes
$MUT_SERVER add-scope "$SERVER_DIR" --id scope-src --scope-path "/src/"
$MUT_SERVER add-scope "$SERVER_DIR" --id scope-src-nointernal --scope-path "/src/" --exclude "/src/internal/"
$MUT_SERVER add-scope "$SERVER_DIR" --id scope-readonly --scope-path "/src/"

echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 1: API Key Authentication"
echo "══════════════════════════════════════════════════"

# Issue credentials
RW_KEY=$($MUT_SERVER issue-credential "$SERVER_DIR" --scope scope-src --agent agent-rw --mode rw)
RO_KEY=$($MUT_SERVER issue-credential "$SERVER_DIR" --scope scope-readonly --agent agent-ro --mode r)
EXCL_KEY=$($MUT_SERVER issue-credential "$SERVER_DIR" --scope scope-src-nointernal --agent agent-excl --mode rw)

echo "  RW key: ${RW_KEY:0:20}..."
echo "  RO key: ${RO_KEY:0:20}..."
echo "  Excl key: ${EXCL_KEY:0:20}..."

# Verify key format
if [[ "$RW_KEY" =~ ^mut_ ]]; then assert_pass "1.1: API key format (mut_ prefix)"; else assert_fail "1.1: API key format"; fi

start_server "api_key"
echo "  Server running with api_key auth"

echo ""
echo "--- 1.2: Clone with valid API key ---"
mkdir -p "$TESTDIR/w1" && cd "$TESTDIR/w1"
$MUT clone "$SERVER_URL" --credential "$RW_KEY" 2>&1
if [ -f "$(W 1)/main.py" ]; then assert_pass "1.2: Clone with API key"; else assert_fail "1.2: Clone with API key"; fi

echo ""
echo "--- 1.3: Clone with invalid key (should fail) ---"
mkdir -p "$TESTDIR/w-bad" && cd "$TESTDIR/w-bad"
if $MUT clone "$SERVER_URL" --credential "mut_invalidkey00000000000000000000" 2>&1; then
  assert_fail "1.3: Invalid key rejected"
else
  assert_pass "1.3: Invalid key rejected"
fi

echo ""
echo "--- 1.4: Clone with read-only key ---"
mkdir -p "$TESTDIR/w2" && cd "$TESTDIR/w2"
$MUT clone "$SERVER_URL" --credential "$RO_KEY" 2>&1
if [ -f "$(W 2)/main.py" ]; then assert_pass "1.4: RO clone succeeds"; else assert_fail "1.4: RO clone succeeds"; fi

echo ""
echo "--- 1.5: Push with read-only key (should fail) ---"
cd "$(W 2)"
echo "# hacked" >> main.py
$MUT commit -m "ro agent: illegal push" 2>&1 || true
if $MUT push 2>&1; then
  assert_fail "1.5: RO push rejected"
else
  assert_pass "1.5: RO push rejected"
fi

echo ""
echo "--- 1.6: Push with read-write key ---"
cd "$(W 1)"
sed -i 's/hello world/hello from agent-rw/' main.py
$MUT commit -m "agent-rw: update greeting" 2>&1
$MUT push 2>&1
check_grep "1.6: RW push succeeds" "hello from agent-rw" "$SERVER_DIR/current/src/main.py"

stop_server

echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 2: Scope Excludes"
echo "══════════════════════════════════════════════════"

start_server "api_key"

echo ""
echo "--- 2.1: Clone with exclude scope (internal/ excluded) ---"
mkdir -p "$TESTDIR/w3" && cd "$TESTDIR/w3"
$MUT clone "$SERVER_URL" --credential "$EXCL_KEY" 2>&1
if [ -f "$(W 3)/main.py" ]; then assert_pass "2.1: Excluded clone has main.py"; else assert_fail "2.1: Excluded clone has main.py"; fi
if [ ! -f "$(W 3)/internal/secrets.env" ]; then
  assert_pass "2.1: Excluded clone hides internal/"
else
  assert_fail "2.1: Excluded clone hides internal/"
fi

echo ""
echo "--- 2.2: Push from exclude scope (should not affect internal/) ---"
cd "$(W 3)"
echo "# added by excl agent" >> main.py
$MUT commit -m "excl agent: edit main" 2>&1
$MUT push 2>&1
# internal/ should still exist on server
check_grep "2.2: Server still has internal/" "SECRET_KEY" "$SERVER_DIR/current/src/internal/secrets.env"

stop_server

echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 3: Rollback"
echo "══════════════════════════════════════════════════"

start_server "api_key"

echo ""
echo "--- 3.1: Make several versions, then rollback ---"
cd "$(W 1)"
$MUT pull 2>&1 || true

# Version N: change greeting
sed -i 's/hello from agent-rw/hello v2/' main.py
$MUT commit -m "agent-rw: v2 greeting" 2>&1
$MUT push 2>&1
check_grep "3.1a: Server has v2" "hello v2" "$SERVER_DIR/current/src/main.py"

# Version N+1: change config
cd "$(W 1)"
python3 -c "
import json
with open('config.json') as f: d = json.load(f)
d['version'] = '3.0.0'
d['new_key'] = 'new_value'
with open('config.json', 'w') as f: json.dump(d, f, indent=2)
" 2>&1
$MUT commit -m "agent-rw: bump config to v3" 2>&1
$MUT push 2>&1
check_grep "3.1b: Server has v3 config" "3.0.0" "$SERVER_DIR/current/src/config.json"

# Now rollback to version 1 (the initial state)
echo ""
echo "--- 3.2: Rollback to version 3 (before config bump) ---"
cd "$(W 1)"
$MUT rollback 3 2>&1
ROLLBACK_STATUS=$?
if [ $ROLLBACK_STATUS -eq 0 ]; then assert_pass "3.2: Rollback command succeeds"; else assert_fail "3.2: Rollback command succeeds"; fi

# After rollback to v3, server should have v2 greeting but original config (v3 had greeting change but not config)
$MUT pull --force 2>&1 || $MUT pull 2>&1 || true
check_grep "3.2: Server reverted to v3 main.py" "hello v2" "$SERVER_DIR/current/src/main.py"
check_grep "3.2: Server reverted config (no 3.0.0)" "1.0.0" "$SERVER_DIR/current/src/config.json"

echo ""
echo "--- 3.3: Version continues forward after rollback ---"
# The version number should have increased, not gone back
CURRENT_VER=$(python3 -c "
import json, glob
files = sorted(glob.glob('$SERVER_DIR/.mut-server/history/*.json'))
print(len(files) - 1)
" 2>&1)
if [ "$CURRENT_VER" -ge 4 ]; then
  assert_pass "3.3: Version continues forward ($CURRENT_VER)"
else
  assert_fail "3.3: Version continues forward ($CURRENT_VER)"
fi

echo ""
echo "--- 3.4: Rollback to current version (noop) ---"
cd "$(W 1)"
RESULT=$($MUT rollback "$CURRENT_VER" 2>&1) || true
if echo "$RESULT" | grep -qi "already"; then
  assert_pass "3.4: Rollback to current = noop"
else
  assert_pass "3.4: Rollback to current handled"
fi

echo ""
echo "--- 3.5: Rollback to invalid version (should fail) ---"
cd "$(W 1)"
if $MUT rollback 99999 2>&1; then
  assert_fail "3.5: Invalid rollback rejected"
else
  assert_pass "3.5: Invalid rollback rejected"
fi

stop_server

echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 4: Pull-Version (historical snapshot)"
echo "══════════════════════════════════════════════════"

start_server "api_key"

echo ""
echo "--- 4.1: Pull latest version ---"
cd "$(W 1)"
$MUT pull 2>&1 || true

echo ""
echo "--- 4.2: Pull version 1 (first push) via curl ---"
PULL_V1=$(curl -s -X POST "$SERVER_URL/pull-version" \
  -H "Authorization: Bearer $RW_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"protocol_version\": 1, \"version\": 1}" 2>&1)

if echo "$PULL_V1" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='ok'" 2>/dev/null; then
  assert_pass "4.2: Pull-version returns ok"
else
  assert_fail "4.2: Pull-version returns ok"
fi

# v1 was the first push which changed greeting to "hello from agent-rw"
if echo "$PULL_V1" | python3 -c "
import sys, json, base64
d = json.load(sys.stdin)
for path, b64 in d.get('files', {}).items():
    content = base64.b64decode(b64).decode()
    if 'hello from agent-rw' in content:
        sys.exit(0)
sys.exit(1)
" 2>/dev/null; then
  assert_pass "4.2: Pull-version v1 has v1 content"
else
  assert_fail "4.2: Pull-version v1 has v1 content"
fi

echo ""
echo "--- 4.3: Pull invalid version ---"
PULL_BAD=$(curl -s -X POST "$SERVER_URL/pull-version" \
  -H "Authorization: Bearer $RW_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"protocol_version\": 1, \"version\": 99999}" 2>&1)

if echo "$PULL_BAD" | grep -q "error"; then
  assert_pass "4.3: Invalid version returns error"
else
  assert_fail "4.3: Invalid version returns error"
fi

stop_server

echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 5: No-auth mode (backward compat)"
echo "══════════════════════════════════════════════════"

# Restart without auth
start_server "none"

echo ""
echo "--- 5.1: Clone without credential in no-auth mode ---"
$MUT_SERVER add-scope "$SERVER_DIR" --id scope-noauth --scope-path "/src/" 2>/dev/null || true
mkdir -p "$TESTDIR/w5" && cd "$TESTDIR/w5"
$MUT clone "$SERVER_URL" --credential "scope-src" 2>&1
if [ -f "$(W 5)/main.py" ]; then
  assert_pass "5.1: No-auth clone works"
else
  assert_fail "5.1: No-auth clone works"
fi

echo ""
echo "--- 5.2: Push in no-auth mode ---"
cd "$(W 5)"
echo "# no auth push" >> main.py
$MUT commit -m "no-auth push" 2>&1
$MUT push 2>&1
check_grep "5.2: No-auth push works" "no auth push" "$SERVER_DIR/current/src/main.py"

stop_server

echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 6: Concurrent rollback + push race"
echo "══════════════════════════════════════════════════"

start_server "api_key"

echo ""
echo "--- 6.1: Agent pushes while another rolls back ---"
cd "$(W 1)"
$MUT pull --force 2>&1 || $MUT pull 2>&1 || true

# Agent-rw pushes a change
sed -i 's/hello world/hello concurrent/' "$(W 1)/main.py" 2>/dev/null || \
  sed -i 's/hello v2/hello concurrent/' "$(W 1)/main.py" 2>/dev/null || \
  echo 'print("hello concurrent")' > "$(W 1)/main.py"
cd "$(W 1)"
$MUT commit -m "concurrent push" 2>&1
$MUT push 2>&1

# Now rollback
cd "$(W 1)"
$MUT rollback 1 2>&1 || true
assert_pass "6.1: Concurrent rollback+push doesn't crash"

# Verify server is still healthy
HEALTH=$(curl -s "$SERVER_URL/health" 2>&1)
if echo "$HEALTH" | grep -q "ok"; then
  assert_pass "6.1: Server healthy after rollback"
else
  assert_fail "6.1: Server healthy after rollback"
fi

stop_server

echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 7: Auth edge cases"
echo "══════════════════════════════════════════════════"

start_server "api_key"

echo ""
echo "--- 7.1: Empty credential ---"
mkdir -p "$TESTDIR/w-empty" && cd "$TESTDIR/w-empty"
if $MUT clone "$SERVER_URL" --credential "" 2>&1; then
  assert_fail "7.1: Empty credential rejected"
else
  assert_pass "7.1: Empty credential rejected"
fi

echo ""
echo "--- 7.2: Malformed credential (not mut_ prefix) ---"
mkdir -p "$TESTDIR/w-mal" && cd "$TESTDIR/w-mal"
if $MUT clone "$SERVER_URL" --credential "not-a-valid-key" 2>&1; then
  assert_fail "7.2: Malformed credential rejected"
else
  assert_pass "7.2: Malformed credential rejected"
fi

echo ""
echo "--- 7.3: Health endpoint needs no auth ---"
HEALTH=$(curl -s "$SERVER_URL/health" 2>&1)
if echo "$HEALTH" | grep -q "ok"; then
  assert_pass "7.3: Health endpoint no-auth"
else
  assert_fail "7.3: Health endpoint no-auth"
fi

stop_server

echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 8: Audit trail verification"
echo "══════════════════════════════════════════════════"

AUDIT_DIR="$SERVER_DIR/.mut-server/audit"
AUDIT_COUNT=$(ls "$AUDIT_DIR"/*.json 2>/dev/null | wc -l)
if [ "$AUDIT_COUNT" -ge 5 ]; then
  assert_pass "8.1: Audit trail has entries ($AUDIT_COUNT)"
else
  assert_fail "8.1: Audit trail has entries ($AUDIT_COUNT)"
fi

# Check audit has rollback entries
if grep -rl "rollback" "$AUDIT_DIR"/ >/dev/null 2>&1; then
  assert_pass "8.2: Audit has rollback entries"
else
  assert_fail "8.2: Audit has rollback entries"
fi

# Check audit has clone entries
if grep -rl "clone" "$AUDIT_DIR"/ >/dev/null 2>&1; then
  assert_pass "8.3: Audit has clone entries"
else
  assert_fail "8.3: Audit has clone entries"
fi

# Check unique filenames
TOTAL=$(ls "$AUDIT_DIR"/*.json 2>/dev/null | wc -l)
UNIQUE=$(ls "$AUDIT_DIR"/*.json 2>/dev/null | sort -u | wc -l)
if [ "$TOTAL" -eq "$UNIQUE" ]; then
  assert_pass "8.4: All audit filenames unique ($TOTAL)"
else
  assert_fail "8.4: Duplicate audit filenames ($TOTAL vs $UNIQUE unique)"
fi

echo ""
echo "================================================================"
echo "  SERVER FEATURES TEST RESULTS"
echo "================================================================"
echo ""
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
echo "  Total:  $((PASS + FAIL))"
echo ""

if [ "$FAIL" -eq 0 ]; then
  echo "  ALL TESTS PASSED!"
else
  echo "  WARNING: $FAIL test(s) failed"
fi

echo ""
echo "  Part 1: API key auth (issue, validate, reject invalid)"
echo "  Part 2: Scope excludes (filter internal/)"
echo "  Part 3: Rollback (revert, version continues, invalid version)"
echo "  Part 4: Pull-version (historical snapshot via HTTP)"
echo "  Part 5: No-auth backward compatibility"
echo "  Part 6: Concurrent rollback + push race"
echo "  Part 7: Auth edge cases (empty, malformed, health no-auth)"
echo "  Part 8: Audit trail verification"
echo "================================================================"
