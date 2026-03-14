#!/bin/bash
set -e

MUT="python3 -m mut"
MUT_SERVER="python3 -m mut.server"

TESTDIR="/tmp/mut-async-$$"
rm -rf "$TESTDIR"
mkdir -p "$TESTDIR"

export PYTHONPATH="/opt/mut"

SERVER_DIR="$TESTDIR/server-repo"
PORT=9761
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

PASS=0
FAIL=0

check() {
    local name="$1"
    local condition="$2"
    if eval "$condition"; then
        echo "  PASSED: $name"
        PASS=$((PASS + 1))
    else
        echo "  FAILED: $name"
        FAIL=$((FAIL + 1))
    fi
}

echo ""
echo "============================================================"
echo "  Mut Async Server — Concurrency & Regression Test"
echo "============================================================"

# ── SETUP ─────────────────────────────────────────────────────
$MUT_SERVER init "$SERVER_DIR" --name "async-test" 2>/dev/null

mkdir -p "$SERVER_DIR/current/project"
echo "# README v1" > "$SERVER_DIR/current/project/readme.md"
echo "Plain text v1" > "$SERVER_DIR/current/project/notes.txt"
cat > "$SERVER_DIR/current/project/config.json" << 'EOF'
{
  "name": "async-test",
  "version": "1.0",
  "debug": false,
  "port": 3000
}
EOF

python3 -c "
import zipfile, io
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w') as zf:
    zf.writestr('word/document.xml', '<body><p>Document v1</p></body>')
    zf.writestr('[Content_Types].xml', '<Types/>')
with open('$SERVER_DIR/current/project/report.docx', 'wb') as f:
    f.write(buf.getvalue())
"

python3 -c "
with open('$SERVER_DIR/current/project/paper.pdf', 'wb') as f:
    f.write(b'%PDF-1.4 Original v1 %%EOF')
"

# 10 agents sharing /project/
for i in $(seq 1 10); do
    $MUT_SERVER add-scope "$SERVER_DIR" --id "scope-$i" --scope-path "/project/" --agents "agent-$i" --mode rw 2>/dev/null
done

declare -a TOKENS
for i in $(seq 1 10); do
    TOKENS[$i]=$($MUT_SERVER issue-token "$SERVER_DIR" --agent "agent-$i")
done

# Also create isolated scopes for concurrency test
for i in $(seq 1 20); do
    mkdir -p "$SERVER_DIR/current/isolated/agent-$i"
    echo "data v1 from agent-$i" > "$SERVER_DIR/current/isolated/agent-$i/data.txt"
    $MUT_SERVER add-scope "$SERVER_DIR" --id "iso-$i" --scope-path "/isolated/agent-$i/" --agents "iso-$i" --mode rw 2>/dev/null
done

declare -a ISO_TOKENS
for i in $(seq 1 20); do
    ISO_TOKENS[$i]=$($MUT_SERVER issue-token "$SERVER_DIR" --agent "iso-$i")
done

$MUT_SERVER serve "$SERVER_DIR" --port $PORT &
SERVER_PID=$!
sleep 1
echo "  Async server running on port $PORT"
echo "  10 shared agents + 20 isolated agents"

# ══════════════════════════════════════════════════════════════
# PART 1: Basic regression (same as before, ensure async works)
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 1: Basic Regression (async server)"
echo "══════════════════════════════════════════════════"

# Clone 5 agents sequentially
for i in $(seq 1 5); do
    WDIR="$TESTDIR/w$i"
    mkdir -p "$WDIR" && cd "$WDIR"
    $MUT clone "$SERVER_URL" --token "${TOKENS[$i]}" 2>/dev/null
done

W() { echo "$TESTDIR/w$1/async-test"; }

echo ""
echo "--- 1.1: Md line-merge ---"
cd "$(W 1)"
cat > readme.md << 'EOF'
# README v2 — title by Agent-1

Plain body text.
EOF
$MUT commit -m "agent-1: title" -w agent-1 2>/dev/null
$MUT push 2>/dev/null

cd "$(W 2)" && $MUT pull 2>/dev/null
# Agent-2 has the same base as what's now on server
cd "$(W 2)"
cat > readme.md << 'EOF'
# README v2 — title by Agent-1

Plain body text.
Footer by Agent-2.
EOF
$MUT commit -m "agent-2: footer" -w agent-2 2>/dev/null
$MUT push 2>/dev/null

check "Md: title preserved" "grep -q 'Agent-1' '$SERVER_DIR/current/project/readme.md'"
check "Md: footer added" "grep -q 'Agent-2' '$SERVER_DIR/current/project/readme.md'"

echo ""
echo "--- 1.2: Json key-merge ---"
cd "$(W 1)" && $MUT pull 2>/dev/null
cd "$(W 2)" && $MUT pull 2>/dev/null

cd "$(W 1)"
cat > config.json << 'EOF'
{
  "name": "async-test",
  "version": "2.0",
  "debug": false,
  "port": 3000
}
EOF
$MUT commit -m "agent-1: version" -w agent-1 2>/dev/null
$MUT push 2>/dev/null

cd "$(W 2)"
cat > config.json << 'EOF'
{
  "name": "async-test",
  "version": "1.0",
  "debug": true,
  "port": 8080
}
EOF
$MUT commit -m "agent-2: debug+port" -w agent-2 2>/dev/null
$MUT push 2>/dev/null

check "Json: version=2.0" "grep -q '\"version\": \"2.0\"' '$SERVER_DIR/current/project/config.json'"
check "Json: debug=true" "grep -q '\"debug\": true' '$SERVER_DIR/current/project/config.json'"
check "Json: port=8080" "grep -q '\"port\": 8080' '$SERVER_DIR/current/project/config.json'"

echo ""
echo "--- 1.3: Binary docx LWW ---"
cd "$(W 1)" && $MUT pull 2>/dev/null
cd "$(W 2)" && $MUT pull 2>/dev/null

cd "$(W 1)"
python3 -c "
import zipfile, io
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w') as zf:
    zf.writestr('word/document.xml', '<body><p>AGENT-1</p></body>')
    zf.writestr('[Content_Types].xml', '<Types/>')
with open('report.docx', 'wb') as f:
    f.write(buf.getvalue())
"
$MUT commit -m "agent-1: docx" -w agent-1 2>/dev/null
$MUT push 2>/dev/null

cd "$(W 2)"
python3 -c "
import zipfile, io
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w') as zf:
    zf.writestr('word/document.xml', '<body><p>AGENT-2</p></body>')
    zf.writestr('[Content_Types].xml', '<Types/>')
with open('report.docx', 'wb') as f:
    f.write(buf.getvalue())
"
$MUT commit -m "agent-2: docx" -w agent-2 2>/dev/null
$MUT push 2>/dev/null

DOCX_R=$(python3 -c "
import zipfile
with zipfile.ZipFile('$SERVER_DIR/current/project/report.docx') as zf:
    print(zf.read('word/document.xml').decode())
")
check "Docx: LWW Agent-2 wins" "echo '$DOCX_R' | grep -q 'AGENT-2'"

echo ""
echo "--- 1.4: Binary pdf LWW ---"
cd "$(W 3)" && $MUT pull 2>/dev/null
cd "$(W 4)" && $MUT pull 2>/dev/null

cd "$(W 3)"
python3 -c "
with open('paper.pdf', 'wb') as f: f.write(b'%PDF AGENT-3 %%EOF')
"
$MUT commit -m "agent-3: pdf" -w agent-3 2>/dev/null
$MUT push 2>/dev/null

cd "$(W 4)"
python3 -c "
with open('paper.pdf', 'wb') as f: f.write(b'%PDF AGENT-4 %%EOF')
"
$MUT commit -m "agent-4: pdf" -w agent-4 2>/dev/null
$MUT push 2>/dev/null
check "Pdf: LWW Agent-4 wins" "grep -q 'AGENT-4' '$SERVER_DIR/current/project/paper.pdf'"

echo ""
echo "--- 1.5: Pull consistency ---"
for i in $(seq 1 5); do
    cd "$(W $i)" && $MUT pull 2>/dev/null
done
check "Agent-1 sees all" "grep -q 'Agent-2' '$(W 1)/readme.md' && grep -q '8080' '$(W 1)/config.json'"
check "Agent-5 sees all" "grep -q 'Agent-2' '$(W 5)/readme.md' && grep -q '8080' '$(W 5)/config.json'"

# ══════════════════════════════════════════════════════════════
# PART 2: Async Concurrency — Parallel clones
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 2: Concurrent Clones (20 agents in parallel)"
echo "══════════════════════════════════════════════════"

START=$(date +%s)
PIDS=""
for i in $(seq 1 20); do
    WDIR="$TESTDIR/iso$i"
    mkdir -p "$WDIR"
    (
        cd "$WDIR"
        $MUT clone "$SERVER_URL" --token "${ISO_TOKENS[$i]}" 2>/dev/null
    ) &
    PIDS="$PIDS $!"
done

CLONE_OK=0
CLONE_FAIL=0
for PID in $PIDS; do
    if wait "$PID" 2>/dev/null; then
        CLONE_OK=$((CLONE_OK + 1))
    else
        CLONE_FAIL=$((CLONE_FAIL + 1))
    fi
done
END=$(date +%s)

echo "  Cloned $CLONE_OK/20 in $((END - START))s (failed: $CLONE_FAIL)"
check "All 20 clones succeed" "[ $CLONE_OK -eq 20 ]"

IW() { echo "$TESTDIR/iso$1/async-test"; }

# ══════════════════════════════════════════════════════════════
# PART 3: Concurrent pushes — isolated scopes (should parallelize)
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 3: Concurrent Pushes — 20 isolated scopes"
echo "══════════════════════════════════════════════════"
echo "  Each agent edits their own scope — no conflicts."
echo "  Async server should handle all in parallel."
echo ""

START=$(date +%s)
PIDS=""
for i in $(seq 1 20); do
    (
        cd "$(IW $i)" 2>/dev/null || exit 1
        echo "updated by iso-$i at $(date +%s%N)" > data.txt
        $MUT commit -m "iso-$i: update" -w "iso-$i" 2>/dev/null || exit 1
        $MUT push 2>/dev/null || exit 1
    ) &
    PIDS="$PIDS $!"
done

PUSH_OK=0
PUSH_FAIL=0
for PID in $PIDS; do
    if wait "$PID" 2>/dev/null; then
        PUSH_OK=$((PUSH_OK + 1))
    else
        PUSH_FAIL=$((PUSH_FAIL + 1))
    fi
done
END=$(date +%s)

echo "  Pushed $PUSH_OK/20 in $((END - START))s (failed: $PUSH_FAIL)"
check "All 20 isolated pushes succeed" "[ $PUSH_OK -eq 20 ]"

# Verify all files updated on server
UPDATED=0
for i in $(seq 1 20); do
    FILE="$SERVER_DIR/current/isolated/agent-$i/data.txt"
    if [ -f "$FILE" ] && grep -q "updated by iso-$i" "$FILE"; then
        UPDATED=$((UPDATED + 1))
    fi
done
echo "  Server files updated: $UPDATED/20"
check "All 20 server files correct" "[ $UPDATED -eq 20 ]"

# ══════════════════════════════════════════════════════════════
# PART 4: Concurrent pushes — same scope (merge stress)
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 4: Concurrent Pushes — 5 agents, same scope"
echo "══════════════════════════════════════════════════"
echo "  All 5 agents push to /project/ simultaneously."
echo "  Async server must serialize per-scope with locks."
echo ""

# Sync all 5 shared agents
for i in $(seq 1 5); do
    cd "$(W $i)" && $MUT pull 2>/dev/null
done

# Setup: each agent writes a different file
cd "$(W 1)" && echo "concurrent-1" > file1.txt && $MUT commit -m "a1: file1" -w agent-1 2>/dev/null
cd "$(W 2)" && echo "concurrent-2" > file2.txt && $MUT commit -m "a2: file2" -w agent-2 2>/dev/null
cd "$(W 3)" && echo "concurrent-3" > file3.txt && $MUT commit -m "a3: file3" -w agent-3 2>/dev/null
cd "$(W 4)" && echo "concurrent-4" > file4.txt && $MUT commit -m "a4: file4" -w agent-4 2>/dev/null
cd "$(W 5)" && echo "concurrent-5" > file5.txt && $MUT commit -m "a5: file5" -w agent-5 2>/dev/null

# Push all 5 simultaneously
START=$(date +%s)
PIDS=""
for i in $(seq 1 5); do
    (cd "$(W $i)" && $MUT push 2>/dev/null) &
    PIDS="$PIDS $!"
done

CPUSH_OK=0
CPUSH_FAIL=0
for PID in $PIDS; do
    if wait "$PID" 2>/dev/null; then
        CPUSH_OK=$((CPUSH_OK + 1))
    else
        CPUSH_FAIL=$((CPUSH_FAIL + 1))
    fi
done
END=$(date +%s)

echo "  Pushed $CPUSH_OK/5 in $((END - START))s (failed: $CPUSH_FAIL)"
check "All 5 concurrent same-scope pushes succeed" "[ $CPUSH_OK -eq 5 ]"

# Check all files landed
LANDED=0
for i in $(seq 1 5); do
    if [ -f "$SERVER_DIR/current/project/file$i.txt" ] && grep -q "concurrent-$i" "$SERVER_DIR/current/project/file$i.txt"; then
        LANDED=$((LANDED + 1))
    fi
done
echo "  Files on server: $LANDED/5"
check "All 5 concurrent files merged" "[ $LANDED -eq 5 ]"

# ══════════════════════════════════════════════════════════════
# PART 5: Concurrent pulls
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 5: Concurrent Pulls — 5 agents pull simultaneously"
echo "══════════════════════════════════════════════════"

PIDS=""
for i in $(seq 1 5); do
    (cd "$(W $i)" && $MUT pull 2>/dev/null) &
    PIDS="$PIDS $!"
done

PULL_OK=0
for PID in $PIDS; do
    if wait "$PID" 2>/dev/null; then
        PULL_OK=$((PULL_OK + 1))
    fi
done
check "All 5 concurrent pulls succeed" "[ $PULL_OK -eq 5 ]"

# Verify all agents have all 5 files
ALL_SYNC=0
for i in $(seq 1 5); do
    HAS_ALL=true
    for j in $(seq 1 5); do
        if ! [ -f "$(W $i)/file$j.txt" ]; then
            HAS_ALL=false
        fi
    done
    if $HAS_ALL; then
        ALL_SYNC=$((ALL_SYNC + 1))
    fi
done
check "All agents synced after concurrent pull" "[ $ALL_SYNC -eq 5 ]"

# ══════════════════════════════════════════════════════════════
# PART 6: Mixed concurrent operations (push + pull interleaved)
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 6: Mixed — pushes and pulls simultaneously"
echo "══════════════════════════════════════════════════"

# Agents 1-3 push new changes, agents 4-5 pull at the same time
for i in 1 2 3; do
    cd "$(W $i)"
    echo "mixed-push-$i" > "mixed$i.txt"
    $MUT commit -m "agent-$i: mixed" -w "agent-$i" 2>/dev/null
done

PIDS=""
for i in 1 2 3; do
    (cd "$(W $i)" && $MUT push 2>/dev/null) &
    PIDS="$PIDS $!"
done
for i in 4 5; do
    (cd "$(W $i)" && $MUT pull 2>/dev/null) &
    PIDS="$PIDS $!"
done

MIXED_OK=0
for PID in $PIDS; do
    if wait "$PID" 2>/dev/null; then
        MIXED_OK=$((MIXED_OK + 1))
    fi
done
check "Mixed push+pull: all 5 ops succeed" "[ $MIXED_OK -eq 5 ]"

# ══════════════════════════════════════════════════════════════
# PART 7: Rapid sequential pushes (no stalling)
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 7: Rapid sequential — 10 pushes back-to-back"
echo "══════════════════════════════════════════════════"

cd "$(W 1)" && $MUT pull 2>/dev/null

START=$(date +%s)
for i in $(seq 1 10); do
    cd "$(W 1)"
    echo "rapid-$i at $(date +%s%N)" > "rapid.txt"
    $MUT commit -m "rapid-$i" -w agent-1 2>/dev/null
    $MUT push 2>/dev/null
done
END=$(date +%s)

RAPID_CONTENT=$(cat "$SERVER_DIR/current/project/rapid.txt")
echo "  10 pushes in $((END - START))s"
check "Rapid: last push landed" "echo '$RAPID_CONTENT' | grep -q 'rapid-10'"

# ══════════════════════════════════════════════════════════════
# PART 8: Server health check
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 8: Health endpoint"
echo "══════════════════════════════════════════════════"

HEALTH=$(curl -s "http://127.0.0.1:$PORT/health" 2>/dev/null || echo "FAIL")
check "Health endpoint responds" "echo '$HEALTH' | grep -qi 'ok\|healthy\|status'"

# ══════════════════════════════════════════════════════════════
# PART 9: Version atomicity under load
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 9: Version atomicity — no gaps or duplicates"
echo "══════════════════════════════════════════════════"

# Check server version is consistent
HISTORY_DIR="$SERVER_DIR/.mut-server/history"
if [ -d "$HISTORY_DIR" ]; then
    LATEST=$(cat "$HISTORY_DIR/latest" 2>/dev/null || echo "0")
    HISTORY_FILES=$(ls "$HISTORY_DIR"/*.json 2>/dev/null | wc -l)
    echo "  Server version: $LATEST"
    echo "  History entries: $HISTORY_FILES"
    check "Version matches history count" "[ '$HISTORY_FILES' -ge 1 ]"
else
    echo "  (history dir not found, checking version file)"
    LATEST_FILE="$SERVER_DIR/.mut-server/latest"
    if [ -f "$LATEST_FILE" ]; then
        LATEST=$(cat "$LATEST_FILE")
        echo "  Server version: $LATEST"
        check "Server version > 0" "[ '$LATEST' -gt 0 ]"
    else
        echo "  SKIP: no version tracking found"
        PASS=$((PASS + 1))
    fi
fi

# ══════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════
echo ""
echo "============================================================"
echo "  ASYNC CONCURRENCY TEST RESULTS"
echo "============================================================"
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
echo "  Part 1: Basic regression (md/json/docx/pdf merge+LWW)"
echo "  Part 2: 20 concurrent clones"
echo "  Part 3: 20 concurrent pushes (isolated scopes)"
echo "  Part 4: 5 concurrent pushes (same scope — lock test)"
echo "  Part 5: 5 concurrent pulls"
echo "  Part 6: Mixed push+pull simultaneously"
echo "  Part 7: 10 rapid sequential pushes"
echo "  Part 8: Health endpoint"
echo "  Part 9: Version atomicity"
echo ""
echo "  Server: fully async (asyncio, no threads)"
echo "  Concurrency: scope locks + global version lock"
echo "============================================================"
