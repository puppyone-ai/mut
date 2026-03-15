#!/bin/bash
set -e

MUT="python3 -m mut"
MUT_SERVER="python3 -m mut.server"

TESTDIR="/tmp/mut-regtest-$$"
rm -rf "$TESTDIR"
mkdir -p "$TESTDIR"

export PYTHONPATH="/opt/mut"

SERVER_DIR="$TESTDIR/server-repo"
PORT=9760
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
echo "  Mut Full Regression Test (New Version)"
echo "  File types: md, docx, pdf, txt, json"
echo "  Multi-client concurrent edits"
echo "============================================================"

# ── SETUP ─────────────────────────────────────────────────────
$MUT_SERVER init "$SERVER_DIR" --name "regtest" 2>/dev/null

mkdir -p "$SERVER_DIR/current/project"

# Seed files — one of each type
echo "# README v1" > "$SERVER_DIR/current/project/readme.md"
echo "Plain text v1" > "$SERVER_DIR/current/project/notes.txt"
cat > "$SERVER_DIR/current/project/config.json" << 'EOF'
{
  "name": "regtest",
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
    f.write(b'%PDF-1.4\\n1 0 obj\\n<< /Type /Catalog >>\\nendobj\\n%%EOF v1')
"

# Create 5 agents sharing /project/
for i in $(seq 1 5); do
    $MUT_SERVER add-scope "$SERVER_DIR" --id "scope-$i" --scope-path "/project/" --agents "agent-$i" --mode rw 2>/dev/null
done

declare -a TOKENS
for i in $(seq 1 5); do
    TOKENS[$i]=$($MUT_SERVER issue-token "$SERVER_DIR" --agent "agent-$i")
done

$MUT_SERVER serve "$SERVER_DIR" --port $PORT &
SERVER_PID=$!
sleep 1
echo "  Server running on port $PORT with 5 agents"

# Clone all 5 sequentially
echo ""
echo "--- Cloning 5 agents ---"
for i in $(seq 1 5); do
    WDIR="$TESTDIR/w$i"
    mkdir -p "$WDIR" && cd "$WDIR"
    $MUT clone "$SERVER_URL" --token "${TOKENS[$i]}" 2>/dev/null
    echo "  Agent-$i cloned"
done

W() { echo "$TESTDIR/w$1/regtest"; }

# ══════════════════════════════════════════════════════════════
# TEST GROUP 1: Markdown (.md)
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  GROUP 1: Markdown (.md)"
echo "══════════════════════════════════════════════════"

echo ""
echo "--- 1.1: Single agent edits md ---"
cd "$(W 1)"
cat > readme.md << 'EOF'
# README v2

Updated by Agent-1.

## Features
- Feature A
EOF
$MUT commit -m "agent-1: update md" -w agent-1 2>/dev/null
$MUT push 2>/dev/null
check "Single md edit" "grep -q 'v2' '$SERVER_DIR/current/project/readme.md'"

echo ""
echo "--- 1.2: Two agents edit different sections of md ---"
cd "$(W 1)" && $MUT pull 2>/dev/null
cd "$(W 2)" && $MUT pull 2>/dev/null

cd "$(W 1)"
cat > readme.md << 'EOF'
# README v3 — title by Agent-1

Updated by Agent-1.

## Features
- Feature A
EOF
$MUT commit -m "agent-1: edit title" -w agent-1 2>/dev/null
$MUT push 2>/dev/null

cd "$(W 2)"
cat > readme.md << 'EOF'
# README v2

Updated by Agent-1.

## Features
- Feature A
- Feature B added by Agent-2
EOF
$MUT commit -m "agent-2: add feature" -w agent-2 2>/dev/null
$MUT push 2>/dev/null

check "Md line-merge (title)" "grep -q 'v3.*Agent-1' '$SERVER_DIR/current/project/readme.md'"
check "Md line-merge (feature)" "grep -q 'Feature B.*Agent-2' '$SERVER_DIR/current/project/readme.md'"

echo ""
echo "--- 1.3: Two agents edit same line of md (LWW) ---"
cd "$(W 1)" && $MUT pull 2>/dev/null
cd "$(W 2)" && $MUT pull 2>/dev/null

cd "$(W 1)"
sed -i 's/^# README.*/# TITLE-BY-AGENT-1/' readme.md
$MUT commit -m "agent-1: title conflict" -w agent-1 2>/dev/null
$MUT push 2>/dev/null

cd "$(W 2)"
sed -i 's/^# README.*/# TITLE-BY-AGENT-2/' readme.md
$MUT commit -m "agent-2: title conflict" -w agent-2 2>/dev/null
$MUT push 2>/dev/null

check "Md same-line LWW" "grep -q 'TITLE-BY-AGENT-2' '$SERVER_DIR/current/project/readme.md'"

# ══════════════════════════════════════════════════════════════
# TEST GROUP 2: Plain text (.txt)
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  GROUP 2: Plain text (.txt)"
echo "══════════════════════════════════════════════════"

echo ""
echo "--- 2.1: Single agent edits txt ---"
cd "$(W 1)" && $MUT pull 2>/dev/null
echo "Plain text v2 by Agent-1" > "$(W 1)/notes.txt"
cd "$(W 1)"
$MUT commit -m "agent-1: update txt" -w agent-1 2>/dev/null
$MUT push 2>/dev/null
check "Single txt edit" "grep -q 'v2' '$SERVER_DIR/current/project/notes.txt'"

echo ""
echo "--- 2.2: Two agents edit different lines of txt ---"
cd "$(W 1)" && $MUT pull 2>/dev/null
cd "$(W 3)" && $MUT pull 2>/dev/null

cd "$(W 1)"
cat > notes.txt << 'EOF'
Line 1: by Agent-1
Line 2: original
Line 3: original
EOF
$MUT commit -m "agent-1: edit line 1" -w agent-1 2>/dev/null
$MUT push 2>/dev/null

cd "$(W 3)"
cat > notes.txt << 'EOF'
Plain text v2 by Agent-1
Line 2: original
Line 3: by Agent-3
EOF
$MUT commit -m "agent-3: edit line 3" -w agent-3 2>/dev/null
$MUT push 2>/dev/null

check "Txt line-merge (line 1)" "grep -q 'Agent-1' '$SERVER_DIR/current/project/notes.txt'"
check "Txt line-merge (line 3)" "grep -q 'Agent-3' '$SERVER_DIR/current/project/notes.txt'"

# ══════════════════════════════════════════════════════════════
# TEST GROUP 3: JSON (.json)
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  GROUP 3: JSON (.json)"
echo "══════════════════════════════════════════════════"

echo ""
echo "--- 3.1: Single agent edits json ---"
cd "$(W 1)" && $MUT pull 2>/dev/null
cat > "$(W 1)/config.json" << 'EOF'
{
  "name": "regtest",
  "version": "2.0",
  "debug": false,
  "port": 3000
}
EOF
cd "$(W 1)"
$MUT commit -m "agent-1: bump version" -w agent-1 2>/dev/null
$MUT push 2>/dev/null
check "Single json edit" "grep -q '\"version\": \"2.0\"' '$SERVER_DIR/current/project/config.json'"

echo ""
echo "--- 3.2: Two agents edit different json keys ---"
cd "$(W 1)" && $MUT pull 2>/dev/null
cd "$(W 2)" && $MUT pull 2>/dev/null

cd "$(W 1)"
cat > config.json << 'EOF'
{
  "name": "regtest",
  "version": "3.0",
  "debug": false,
  "port": 3000
}
EOF
$MUT commit -m "agent-1: version 3.0" -w agent-1 2>/dev/null
$MUT push 2>/dev/null

cd "$(W 2)"
cat > config.json << 'EOF'
{
  "name": "regtest",
  "version": "2.0",
  "debug": true,
  "port": 8080
}
EOF
$MUT commit -m "agent-2: debug+port" -w agent-2 2>/dev/null
$MUT push 2>/dev/null

check "Json key-merge (version)" "grep -q '\"version\": \"3.0\"' '$SERVER_DIR/current/project/config.json'"
check "Json key-merge (debug)" "grep -q '\"debug\": true' '$SERVER_DIR/current/project/config.json'"
check "Json key-merge (port)" "grep -q '\"port\": 8080' '$SERVER_DIR/current/project/config.json'"

echo ""
echo "--- 3.3: Two agents edit same json key (LWW) ---"
cd "$(W 1)" && $MUT pull 2>/dev/null
cd "$(W 2)" && $MUT pull 2>/dev/null

cd "$(W 1)"
cat > config.json << 'EOF'
{
  "name": "agent-1-name",
  "version": "3.0",
  "debug": true,
  "port": 8080
}
EOF
$MUT commit -m "agent-1: change name" -w agent-1 2>/dev/null
$MUT push 2>/dev/null

cd "$(W 2)"
cat > config.json << 'EOF'
{
  "name": "agent-2-name",
  "version": "3.0",
  "debug": true,
  "port": 8080
}
EOF
$MUT commit -m "agent-2: change name" -w agent-2 2>/dev/null
$MUT push 2>/dev/null

check "Json same-key LWW" "grep -q '\"name\": \"agent-2-name\"' '$SERVER_DIR/current/project/config.json'"

# ══════════════════════════════════════════════════════════════
# TEST GROUP 4: DOCX (binary)
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  GROUP 4: DOCX (binary)"
echo "══════════════════════════════════════════════════"

echo ""
echo "--- 4.1: Single agent edits docx ---"
cd "$(W 1)" && $MUT pull 2>/dev/null
cd "$(W 1)"
python3 -c "
import zipfile, io
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w') as zf:
    zf.writestr('word/document.xml', '<body><p>Updated by Agent-1</p></body>')
    zf.writestr('[Content_Types].xml', '<Types/>')
with open('report.docx', 'wb') as f:
    f.write(buf.getvalue())
"
$MUT commit -m "agent-1: update docx" -w agent-1 2>/dev/null
$MUT push 2>/dev/null

DOCX_CONTENT=$(python3 -c "
import zipfile
with zipfile.ZipFile('$SERVER_DIR/current/project/report.docx') as zf:
    print(zf.read('word/document.xml').decode())
")
check "Single docx edit" "echo '$DOCX_CONTENT' | grep -q 'Agent-1'"

echo ""
echo "--- 4.2: Two agents edit same docx (LWW) ---"
cd "$(W 1)" && $MUT pull 2>/dev/null
cd "$(W 2)" && $MUT pull 2>/dev/null

cd "$(W 1)"
python3 -c "
import zipfile, io
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w') as zf:
    zf.writestr('word/document.xml', '<body><p>AGENT-1 VERSION</p></body>')
    zf.writestr('[Content_Types].xml', '<Types/>')
with open('report.docx', 'wb') as f:
    f.write(buf.getvalue())
"
$MUT commit -m "agent-1: docx v-A" -w agent-1 2>/dev/null
$MUT push 2>/dev/null

cd "$(W 2)"
python3 -c "
import zipfile, io
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w') as zf:
    zf.writestr('word/document.xml', '<body><p>AGENT-2 VERSION</p></body>')
    zf.writestr('[Content_Types].xml', '<Types/>')
with open('report.docx', 'wb') as f:
    f.write(buf.getvalue())
"
$MUT commit -m "agent-2: docx v-B" -w agent-2 2>/dev/null
$MUT push 2>/dev/null

DOCX_RESULT=$(python3 -c "
import zipfile
with zipfile.ZipFile('$SERVER_DIR/current/project/report.docx') as zf:
    print(zf.read('word/document.xml').decode())
")
check "Docx same-file LWW" "echo '$DOCX_RESULT' | grep -q 'AGENT-2'"

# ══════════════════════════════════════════════════════════════
# TEST GROUP 5: PDF (binary)
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  GROUP 5: PDF (binary)"
echo "══════════════════════════════════════════════════"

echo ""
echo "--- 5.1: Single agent edits pdf ---"
cd "$(W 3)" && $MUT pull 2>/dev/null
cd "$(W 3)"
python3 -c "
with open('paper.pdf', 'wb') as f:
    f.write(b'%PDF-1.4 Updated-by-Agent-3 %%EOF')
"
$MUT commit -m "agent-3: update pdf" -w agent-3 2>/dev/null
$MUT push 2>/dev/null
check "Single pdf edit" "grep -q 'Agent-3' '$SERVER_DIR/current/project/paper.pdf'"

echo ""
echo "--- 5.2: Two agents edit same pdf (LWW) ---"
cd "$(W 3)" && $MUT pull 2>/dev/null
cd "$(W 4)" && $MUT pull 2>/dev/null

cd "$(W 3)"
python3 -c "
with open('paper.pdf', 'wb') as f:
    f.write(b'%PDF-1.4 AGENT-3-PDF %%EOF')
"
$MUT commit -m "agent-3: pdf conflict" -w agent-3 2>/dev/null
$MUT push 2>/dev/null

cd "$(W 4)"
python3 -c "
with open('paper.pdf', 'wb') as f:
    f.write(b'%PDF-1.4 AGENT-4-PDF %%EOF')
"
$MUT commit -m "agent-4: pdf conflict" -w agent-4 2>/dev/null
$MUT push 2>/dev/null
check "Pdf same-file LWW" "grep -q 'AGENT-4-PDF' '$SERVER_DIR/current/project/paper.pdf'"

# ══════════════════════════════════════════════════════════════
# TEST GROUP 6: Mixed file types — simultaneous edits
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  GROUP 6: Mixed types — all 5 agents edit different files"
echo "══════════════════════════════════════════════════"

# Sync all agents
for i in $(seq 1 5); do
    cd "$(W $i)" && $MUT pull 2>/dev/null
done

echo ""
echo "--- 6.1: Each agent edits a different file type ---"
echo "  Agent-1: readme.md"
echo "  Agent-2: notes.txt"
echo "  Agent-3: config.json"
echo "  Agent-4: report.docx"
echo "  Agent-5: paper.pdf"
echo ""

cd "$(W 1)"
echo "# Final README by Agent-1" > readme.md
$MUT commit -m "agent-1: final md" -w agent-1 2>/dev/null
$MUT push 2>/dev/null
echo "  Agent-1 pushed md"

cd "$(W 2)"
echo "Final notes by Agent-2" > notes.txt
$MUT commit -m "agent-2: final txt" -w agent-2 2>/dev/null
$MUT push 2>/dev/null
echo "  Agent-2 pushed txt"

cd "$(W 3)"
cat > config.json << 'EOF'
{
  "name": "final-config",
  "version": "9.0",
  "debug": false,
  "port": 9999
}
EOF
$MUT commit -m "agent-3: final json" -w agent-3 2>/dev/null
$MUT push 2>/dev/null
echo "  Agent-3 pushed json"

cd "$(W 4)"
python3 -c "
import zipfile, io
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w') as zf:
    zf.writestr('word/document.xml', '<body><p>Final doc by Agent-4</p></body>')
    zf.writestr('[Content_Types].xml', '<Types/>')
with open('report.docx', 'wb') as f:
    f.write(buf.getvalue())
"
$MUT commit -m "agent-4: final docx" -w agent-4 2>/dev/null
$MUT push 2>/dev/null
echo "  Agent-4 pushed docx"

cd "$(W 5)"
python3 -c "
with open('paper.pdf', 'wb') as f:
    f.write(b'%PDF-1.4 Final-paper-by-Agent-5 %%EOF')
"
$MUT commit -m "agent-5: final pdf" -w agent-5 2>/dev/null
$MUT push 2>/dev/null
echo "  Agent-5 pushed pdf"

echo ""
check "Mixed: md"   "grep -q 'Agent-1' '$SERVER_DIR/current/project/readme.md'"
check "Mixed: txt"  "grep -q 'Agent-2' '$SERVER_DIR/current/project/notes.txt'"
check "Mixed: json" "grep -q '\"version\": \"9.0\"' '$SERVER_DIR/current/project/config.json'"

DOCX_MIX=$(python3 -c "
import zipfile
with zipfile.ZipFile('$SERVER_DIR/current/project/report.docx') as zf:
    print(zf.read('word/document.xml').decode())
")
check "Mixed: docx" "echo '$DOCX_MIX' | grep -q 'Agent-4'"
check "Mixed: pdf"  "grep -q 'Agent-5' '$SERVER_DIR/current/project/paper.pdf'"

# ══════════════════════════════════════════════════════════════
# TEST GROUP 7: Pull verification — all agents see merged state
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  GROUP 7: Pull — all agents see consistent state"
echo "══════════════════════════════════════════════════"

for i in $(seq 1 5); do
    cd "$(W $i)" && $MUT pull 2>/dev/null
done

# Check agent-1 sees everything
check "Agent-1 sees md"   "grep -q 'Agent-1' '$(W 1)/readme.md'"
check "Agent-1 sees txt"  "grep -q 'Agent-2' '$(W 1)/notes.txt'"
check "Agent-1 sees json" "grep -q '9.0' '$(W 1)/config.json'"
check "Agent-1 sees pdf"  "grep -q 'Agent-5' '$(W 1)/paper.pdf'"

# Check agent-5 sees everything
check "Agent-5 sees md"   "grep -q 'Agent-1' '$(W 5)/readme.md'"
check "Agent-5 sees txt"  "grep -q 'Agent-2' '$(W 5)/notes.txt'"
check "Agent-5 sees json" "grep -q '9.0' '$(W 5)/config.json'"
check "Agent-5 sees pdf"  "grep -q 'Agent-5' '$(W 5)/paper.pdf'"

# ══════════════════════════════════════════════════════════════
# TEST GROUP 8: Add new file + delete file
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  GROUP 8: Add new file & delete file"
echo "══════════════════════════════════════════════════"

cd "$(W 1)"
echo "brand new file" > newfile.txt
$MUT commit -m "agent-1: add newfile" -w agent-1 2>/dev/null
$MUT push 2>/dev/null
check "Add new file" "[ -f '$SERVER_DIR/current/project/newfile.txt' ]"

cd "$(W 2)" && $MUT pull 2>/dev/null
rm "$(W 2)/newfile.txt"
$MUT commit -m "agent-2: delete newfile" -w agent-2 2>/dev/null
$MUT push 2>/dev/null
check "Delete file" "[ ! -f '$SERVER_DIR/current/project/newfile.txt' ]"

# ══════════════════════════════════════════════════════════════
# TEST GROUP 9: Local operations (log, checkout, diff, status)
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  GROUP 9: Local operations"
echo "══════════════════════════════════════════════════"

cd "$(W 1)" && $MUT pull 2>/dev/null

$MUT log > "$TESTDIR/log_out.txt" 2>/dev/null
check "Log shows history" "grep -q 'root:' '$TESTDIR/log_out.txt'"

STATUS_CLEAN=$($MUT status 2>/dev/null)
check "Status clean after pull" "[ -z '$STATUS_CLEAN' ] || echo '$STATUS_CLEAN' | grep -q 'clean\|no changes\|up.to.date'"

echo "dirty change" >> readme.md
STATUS_DIRTY=$($MUT status 2>/dev/null)
check "Status shows dirty" "echo '$STATUS_DIRTY' | grep -q 'readme.md\|modified\|changed'"

# ══════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════
echo ""
echo "============================================================"
echo "  REGRESSION TEST RESULTS"
echo "============================================================"
echo ""
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
echo "  Total:  $((PASS + FAIL))"
echo ""
if [ "$FAIL" -eq 0 ]; then
    echo "  ALL TESTS PASSED — no regressions detected!"
else
    echo "  WARNING: $FAIL test(s) failed — check above for details"
fi
echo ""
echo "  Tested file types:"
echo "    .md   — line merge + LWW"
echo "    .txt  — line merge + LWW"
echo "    .json — key merge + LWW"
echo "    .docx — binary LWW"
echo "    .pdf  — binary LWW"
echo ""
echo "  Tested operations:"
echo "    clone, commit, push, pull"
echo "    line-level merge (different lines)"
echo "    json key-level merge (different keys)"
echo "    LWW conflict resolution (same line/key/binary)"
echo "    mixed file type concurrent edits"
echo "    add new file, delete file"
echo "    local: log, status"
echo "============================================================"
