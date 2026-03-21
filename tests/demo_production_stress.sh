#!/bin/bash
set -e

MUT="python3 -m mut"
MUT_SERVER="python3 -m mut.server"

TESTDIR="/tmp/mut-prod-$$"
rm -rf "$TESTDIR"
mkdir -p "$TESTDIR"

export PYTHONPATH="/opt/mut"

SERVER_DIR="$TESTDIR/server-repo"
PORT=9762
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
echo "================================================================"
echo "  Mut Production Stress Test"
echo "  Complex files, edge cases, multi-client simulation"
echo "================================================================"

# ── SETUP ─────────────────────────────────────────────────────
$MUT_SERVER init "$SERVER_DIR" --name "prod-test" 2>/dev/null

mkdir -p "$SERVER_DIR/current/docs"

# ── SEED: Complex Markdown file ───────────────────────────────
cat > "$SERVER_DIR/current/docs/architecture.md" << 'MDEOF'
# System Architecture v1.0

> Last updated: 2026-03-15
> Status: **Draft**

## Table of Contents

1. [Overview](#overview)
2. [Components](#components)
3. [Data Flow](#data-flow)

---

## Overview

This document describes the system architecture for the **Mut Protocol**.

### 1.1 Goals

- [ ] Zero-downtime deployment
- [ ] Sub-100ms merge latency
- [x] Content-addressable storage
- [x] Scope-based isolation

### 1.2 Non-Goals

1. Real-time collaboration (Google Docs style)
2. Binary file diffing
   1. DOCX internal XML merging
   2. PDF page-level merging
      - This would require format-specific parsers
      - Out of scope for v1
3. Offline conflict resolution

## Components

### 2.1 Server

| Component | Language | Lines | Purpose |
|-----------|----------|-------|---------|
| HTTP Server | Python | ~560 | Async request handling |
| Object Store | Python | ~160 | Content-addressable blobs |
| Merge Engine | Python | ~380 | Three-way conflict resolution |
| History | Python | ~200 | Version tracking + audit |
| **Total** | | **~1300** | |

### 2.2 Client

```python
# Example: commit and push
from mut.ops import commit_op, push_op
from mut.ops.repo import MutRepo

repo = MutRepo(".")
commit_op.commit(repo, "update docs", who="agent-A")
result = push_op.push(repo)
print(f"Pushed to version {result['server_version']}")
```

### 2.3 Merge Strategy Chain

```
Input: base, ours, theirs
  │
  ├─ Identical? ──────── yes ──▶ return ours
  │
  ├─ One-side only? ──── yes ──▶ return changed side
  │
  ├─ .json file?
  │   └─ Key-level merge ─ ok ──▶ return merged JSON
  │
  ├─ Text file?
  │   └─ Line-level merge ─ ok ──▶ return merged text
  │
  └─ Fallback: LWW ────────────▶ return theirs (log loss)
```

## Data Flow

```
Agent-A                    Server                    Agent-B
  │                          │                          │
  ├── commit ──────────────▶ │                          │
  ├── push ────────────────▶ │◀── push ────────────────┤
  │                          ├── merge ──▶ new version  │
  │◀── pull ───────────────┤ ├── pull ─────────────────▶│
  │                          │                          │
```

## Appendix

### A. Hash Algorithm

SHA-256 truncated to 16 hex chars (64-bit). Collision probability:

| Objects | P(collision) |
|---------|-------------|
| 1,000 | ~10⁻¹⁴ |
| 10,000 | ~10⁻¹² |
| 100,000 | ~10⁻¹⁰ |
| 1,000,000 | ~10⁻⁸ |

### B. 中文文档支持

本系统支持多语言内容：
- **中文**: 完全支持 UTF-8 编码
- **日本語**: テスト済み
- **한국어**: 테스트 완료
- **Emoji**: 🦞🔒✅❌

### C. Nested Structure Test

1. First level
   - Bullet under number
   - Another bullet
     1. Nested number under bullet
     2. Second nested
        - Deep bullet
          - Deeper bullet
            - [ ] Todo at depth 4
            - [x] Done at depth 4
2. Back to first level
   1. Sub-item
   2. Sub-item with `inline code`
   3. Sub-item with **bold** and *italic* and ~~strikethrough~~
MDEOF

# ── SEED: Complex JSON file ───────────────────────────────────
cat > "$SERVER_DIR/current/docs/config.json" << 'JSONEOF'
{
  "project": {
    "name": "mut-protocol",
    "version": "1.0.0-beta.3",
    "description": "Managed Unified Tree — 版本管理协议",
    "license": "MIT",
    "authors": ["agent-A", "agent-B", "agent-C"],
    "keywords": ["版本管理", "AI", "merge", "バージョン管理"]
  },
  "server": {
    "host": "0.0.0.0",
    "port": 9742,
    "max_body_size": 268435456,
    "timeouts": {
      "read": 30.0,
      "write": 60.0,
      "graceful_shutdown": 10.5
    },
    "tls": {
      "enabled": false,
      "cert_path": null,
      "key_path": null
    },
    "rate_limit": {
      "enabled": true,
      "max_requests_per_minute": 1000,
      "burst": 50
    }
  },
  "merge": {
    "strategies": ["identical", "one_side", "json", "line", "lww"],
    "lww_policy": "theirs",
    "max_file_size_bytes": 104857600,
    "binary_extensions": [".docx", ".pdf", ".png", ".jpg", ".zip"],
    "custom_rules": [
      {
        "pattern": "*.lock",
        "strategy": "lww",
        "reason": "Lock files should not be merged"
      },
      {
        "pattern": "package.json",
        "strategy": "json",
        "deep_merge": true,
        "preserve_order": true
      }
    ]
  },
  "scopes": [
    {
      "id": "scope-frontend",
      "path": "/src/frontend/",
      "agents": ["agent-ui-1", "agent-ui-2"],
      "mode": "rw",
      "exclude": ["/src/frontend/node_modules/", "/src/frontend/dist/"],
      "metadata": {
        "team": "frontend",
        "lead": "agent-ui-1",
        "created": "2026-01-15T10:00:00Z"
      }
    },
    {
      "id": "scope-backend",
      "path": "/src/backend/",
      "agents": ["agent-api-1"],
      "mode": "rw",
      "exclude": [],
      "metadata": {
        "team": "backend",
        "lead": "agent-api-1",
        "created": "2026-01-15T10:00:00Z"
      }
    }
  ],
  "feature_flags": {
    "enable_async_server": true,
    "enable_protocol_validation": true,
    "enable_hash_validation": true,
    "experimental_llm_merge": false,
    "max_concurrent_pushes": 100,
    "audit_retention_days": 90
  },
  "i18n": {
    "default_locale": "en",
    "supported": ["en", "zh-CN", "ja", "ko"],
    "messages": {
      "en": {"welcome": "Welcome to Mut", "error": "An error occurred"},
      "zh-CN": {"welcome": "欢迎使用 Mut", "error": "发生错误"},
      "ja": {"welcome": "Mutへようこそ", "error": "エラーが発生しました"},
      "ko": {"welcome": "Mut에 오신 것을 환영합니다", "error": "오류가 발생했습니다"}
    }
  },
  "test_values": {
    "integer": 42,
    "negative": -7,
    "float": 3.14159265358979,
    "scientific": 1.23e10,
    "bool_true": true,
    "bool_false": false,
    "null_value": null,
    "empty_string": "",
    "empty_array": [],
    "empty_object": {},
    "unicode": "Hello 世界 🌍",
    "escaped": "line1\nline2\ttab",
    "long_string": "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.",
    "nested_arrays": [[1, 2], [3, [4, 5]], [{"a": [6, 7]}]],
    "mixed_list": [1, "two", true, null, 3.14, {"key": "val"}, [1, 2]]
  }
}
JSONEOF

# Create agents (6 agents sharing /docs/)
for i in $(seq 1 6); do
    $MUT_SERVER add-scope "$SERVER_DIR" --id "scope-$i" --scope-path "/docs/" 2>/dev/null
done

declare -a CREDS
for i in $(seq 1 6); do
    CREDS[$i]=$($MUT_SERVER issue-credential "$SERVER_DIR" --scope "scope-$i" --agent "agent-$i" --mode rw)
done

$MUT_SERVER serve "$SERVER_DIR" --port $PORT --auth api_key &
SERVER_PID=$!
sleep 1
echo "  Server running on port $PORT with 6 agents"
echo "  Complex MD: $(wc -c < "$SERVER_DIR/current/docs/architecture.md") bytes"
echo "  Complex JSON: $(wc -c < "$SERVER_DIR/current/docs/config.json") bytes"

# Clone all agents
echo ""
echo "--- Cloning 6 agents ---"
for i in $(seq 1 6); do
    WDIR="$TESTDIR/w$i"
    mkdir -p "$WDIR" && cd "$WDIR"
    $MUT clone "$SERVER_URL" --credential "${CREDS[$i]}" 2>/dev/null
    echo "  Agent-$i cloned"
done

W() { echo "$TESTDIR/w$1/prod-test"; }

# ══════════════════════════════════════════════════════════════
# PART 1: Sequential — complex MD edits
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 1: Complex Markdown — sequential edits"
echo "══════════════════════════════════════════════════"

echo ""
echo "--- 1.1: Agent-1 adds a new section at the end ---"
cd "$(W 1)"
cat >> architecture.md << 'EOF'

## New Section by Agent-1

### D. Performance Benchmarks

| Operation | Time (p50) | Time (p99) |
|-----------|-----------|-----------|
| clone     | 45ms      | 120ms     |
| push      | 30ms      | 85ms      |
| pull      | 25ms      | 70ms      |
| merge     | 5ms       | 15ms      |

```bash
# Run benchmarks
mut-bench --iterations 1000 --agents 20
```
EOF
$MUT commit -m "agent-1: add benchmarks section" -w agent-1 2>/dev/null
$MUT push 2>/dev/null
check "1.1: MD append section" "grep -q 'Performance Benchmarks' '$SERVER_DIR/current/docs/architecture.md'"

echo ""
echo "--- 1.2: Agent-2 edits table in section 2.1 (middle of file) ---"
cd "$(W 2)" && $MUT pull 2>/dev/null
cd "$(W 2)"
sed -i 's/| HTTP Server | Python | ~560 | Async request handling |/| HTTP Server | Python | ~580 | Async request handling (v2) |/' architecture.md
$MUT commit -m "agent-2: update server LOC" -w agent-2 2>/dev/null
$MUT push 2>/dev/null
check "1.2: MD edit table cell" "grep -q '~580' '$SERVER_DIR/current/docs/architecture.md'"

echo ""
echo "--- 1.3: Agent-1 and Agent-2 edit DIFFERENT sections (merge) ---"
cd "$(W 1)" && $MUT pull 2>/dev/null
cd "$(W 2)" && $MUT pull 2>/dev/null

# Agent-1 edits the overview section
cd "$(W 1)"
sed -i 's/This document describes the system architecture for the \*\*Mut Protocol\*\*./This document describes the production architecture for the **Mut Protocol v2**./' architecture.md
$MUT commit -m "agent-1: update overview" -w agent-1 2>/dev/null
$MUT push 2>/dev/null

# Agent-2 edits the Chinese section (different location)
cd "$(W 2)"
sed -i 's/本系统支持多语言内容：/本系统完整支持多语言内容（v2更新）：/' architecture.md
$MUT commit -m "agent-2: update Chinese section" -w agent-2 2>/dev/null
$MUT push 2>/dev/null

check "1.3: MD merge overview" "grep -q 'production architecture' '$SERVER_DIR/current/docs/architecture.md'"
check "1.3: MD merge Chinese" "grep -q 'v2更新' '$SERVER_DIR/current/docs/architecture.md'"

echo ""
echo "--- 1.4: Two agents edit same MD line (LWW) ---"
cd "$(W 1)" && $MUT pull 2>/dev/null
cd "$(W 2)" && $MUT pull 2>/dev/null

cd "$(W 1)"
sed -i 's/^# System Architecture v1.0/# System Architecture v2.0-alpha/' architecture.md
$MUT commit -m "agent-1: title v2.0-alpha" -w agent-1 2>/dev/null
$MUT push 2>/dev/null

cd "$(W 2)"
sed -i 's/^# System Architecture v1.0/# System Architecture v2.0-beta/' architecture.md
$MUT commit -m "agent-2: title v2.0-beta" -w agent-2 2>/dev/null
$MUT push 2>/dev/null

check "1.4: MD same-line LWW" "grep -q 'v2.0-beta' '$SERVER_DIR/current/docs/architecture.md'"

echo ""
echo "--- 1.5: Edit todos and checkboxes ---"
cd "$(W 3)" && $MUT pull 2>/dev/null
cd "$(W 3)"
sed -i 's/- \[ \] Zero-downtime deployment/- [x] Zero-downtime deployment/' architecture.md
sed -i 's/- \[ \] Sub-100ms merge latency/- [x] Sub-100ms merge latency/' architecture.md
$MUT commit -m "agent-3: check off todos" -w agent-3 2>/dev/null
$MUT push 2>/dev/null
check "1.5: MD todo checked" "grep -q '\[x\] Zero-downtime' '$SERVER_DIR/current/docs/architecture.md'"

# ══════════════════════════════════════════════════════════════
# PART 2: Sequential — complex JSON edits
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 2: Complex JSON — key-level merges"
echo "══════════════════════════════════════════════════"

echo ""
echo "--- 2.1: Agent-1 changes version, Agent-2 changes port ---"
cd "$(W 1)" && $MUT pull 2>/dev/null
cd "$(W 2)" && $MUT pull 2>/dev/null

cd "$(W 1)"
python3 -c "
import json
with open('config.json') as f: d = json.load(f)
d['project']['version'] = '2.0.0'
with open('config.json', 'w') as f: json.dump(d, f, indent=2, ensure_ascii=False)
"
$MUT commit -m "agent-1: bump version to 2.0" -w agent-1 2>/dev/null
$MUT push 2>/dev/null

cd "$(W 2)"
python3 -c "
import json
with open('config.json') as f: d = json.load(f)
d['server']['port'] = 8080
with open('config.json', 'w') as f: json.dump(d, f, indent=2, ensure_ascii=False)
"
$MUT commit -m "agent-2: change port" -w agent-2 2>/dev/null
$MUT push 2>/dev/null

check "2.1: JSON version=2.0.0" "grep -q '2.0.0' '$SERVER_DIR/current/docs/config.json'"
check "2.1: JSON port=8080" "grep -q '8080' '$SERVER_DIR/current/docs/config.json'"

echo ""
echo "--- 2.2: Nested JSON key changes ---"
cd "$(W 3)" && $MUT pull 2>/dev/null
cd "$(W 4)" && $MUT pull 2>/dev/null

cd "$(W 3)"
python3 -c "
import json
with open('config.json') as f: d = json.load(f)
d['server']['timeouts']['read'] = 60.0
d['feature_flags']['experimental_llm_merge'] = True
with open('config.json', 'w') as f: json.dump(d, f, indent=2, ensure_ascii=False)
"
$MUT commit -m "agent-3: timeouts + feature flag" -w agent-3 2>/dev/null
$MUT push 2>/dev/null

cd "$(W 4)"
python3 -c "
import json
with open('config.json') as f: d = json.load(f)
d['server']['rate_limit']['max_requests_per_minute'] = 5000
d['i18n']['supported'].append('fr')
d['i18n']['messages']['fr'] = {'welcome': 'Bienvenue à Mut', 'error': 'Une erreur est survenue'}
with open('config.json', 'w') as f: json.dump(d, f, indent=2, ensure_ascii=False)
"
$MUT commit -m "agent-4: rate limit + french" -w agent-4 2>/dev/null
$MUT push 2>/dev/null

SERVER_JSON=$(cat "$SERVER_DIR/current/docs/config.json")
check "2.2: Nested timeout" "echo '$SERVER_JSON' | python3 -c \"import sys,json; d=json.load(sys.stdin); assert d['server']['timeouts']['read']==60.0\""
check "2.2: Feature flag" "echo '$SERVER_JSON' | python3 -c \"import sys,json; d=json.load(sys.stdin); assert d['feature_flags']['experimental_llm_merge']==True\""
check "2.2: Rate limit" "echo '$SERVER_JSON' | python3 -c \"import sys,json; d=json.load(sys.stdin); assert d['server']['rate_limit']['max_requests_per_minute']==5000\""

echo ""
echo "--- 2.3: Same nested key conflict (LWW) ---"
cd "$(W 1)" && $MUT pull 2>/dev/null
cd "$(W 2)" && $MUT pull 2>/dev/null

cd "$(W 1)"
python3 -c "
import json
with open('config.json') as f: d = json.load(f)
d['project']['name'] = 'mut-protocol-alpha'
with open('config.json', 'w') as f: json.dump(d, f, indent=2, ensure_ascii=False)
"
$MUT commit -m "agent-1: name alpha" -w agent-1 2>/dev/null
$MUT push 2>/dev/null

cd "$(W 2)"
python3 -c "
import json
with open('config.json') as f: d = json.load(f)
d['project']['name'] = 'mut-protocol-beta'
with open('config.json', 'w') as f: json.dump(d, f, indent=2, ensure_ascii=False)
"
$MUT commit -m "agent-2: name beta" -w agent-2 2>/dev/null
$MUT push 2>/dev/null

check "2.3: JSON same-key LWW" "grep -q 'mut-protocol-beta' '$SERVER_DIR/current/docs/config.json'"

echo ""
echo "--- 2.4: Same key name at different nesting levels ---"
cd "$(W 1)" && $MUT pull 2>/dev/null
cd "$(W 2)" && $MUT pull 2>/dev/null

# Agent-1 changes project.name (top-level "name")
cd "$(W 1)"
python3 -c "
import json
with open('config.json') as f: d = json.load(f)
d['project']['name'] = 'changed-at-project-level'
with open('config.json', 'w') as f: json.dump(d, f, indent=2, ensure_ascii=False)
"
$MUT commit -m "agent-1: project.name" -w agent-1 2>/dev/null
$MUT push 2>/dev/null

# Agent-2 changes scopes[0].metadata.team (deeper "name"-like key at different root)
cd "$(W 2)"
python3 -c "
import json
with open('config.json') as f: d = json.load(f)
d['scopes'][0]['metadata']['team'] = 'new-frontend-team'
with open('config.json', 'w') as f: json.dump(d, f, indent=2, ensure_ascii=False)
"
$MUT commit -m "agent-2: scopes.metadata.team" -w agent-2 2>/dev/null
$MUT push 2>/dev/null

SERVER_JSON=$(cat "$SERVER_DIR/current/docs/config.json")
check "2.4: Top-level name changed" "echo '$SERVER_JSON' | python3 -c \"import sys,json; d=json.load(sys.stdin); assert d['project']['name']=='changed-at-project-level', d['project']['name']\""
check "2.4: Nested team changed" "echo '$SERVER_JSON' | python3 -c \"import sys,json; d=json.load(sys.stdin); assert d['scopes'][0]['metadata']['team']=='new-frontend-team', d['scopes'][0]['metadata']['team']\""

echo ""
echo "--- 2.5: Change value type — string to list ---"
cd "$(W 3)" && $MUT pull 2>/dev/null
cd "$(W 3)"
python3 -c "
import json
with open('config.json') as f: d = json.load(f)
# Change license from string to list of strings
d['project']['license'] = ['MIT', 'Apache-2.0']
with open('config.json', 'w') as f: json.dump(d, f, indent=2, ensure_ascii=False)
"
$MUT commit -m "agent-3: license string->list" -w agent-3 2>/dev/null
$MUT push 2>/dev/null

check "2.5: String->list" "cat '$SERVER_DIR/current/docs/config.json' | python3 -c \"import sys,json; d=json.load(sys.stdin); assert isinstance(d['project']['license'], list) and 'Apache-2.0' in d['project']['license']\""

echo ""
echo "--- 2.6: Change value type — list to object ---"
cd "$(W 4)" && $MUT pull 2>/dev/null
cd "$(W 4)"
python3 -c "
import json
with open('config.json') as f: d = json.load(f)
# Change keywords from list to object with metadata
d['project']['keywords'] = {
    'primary': ['版本管理', 'AI'],
    'secondary': ['merge', 'バージョン管理'],
    'count': 4
}
with open('config.json', 'w') as f: json.dump(d, f, indent=2, ensure_ascii=False)
"
$MUT commit -m "agent-4: keywords list->object" -w agent-4 2>/dev/null
$MUT push 2>/dev/null

check "2.6: List->object" "cat '$SERVER_DIR/current/docs/config.json' | python3 -c \"import sys,json; d=json.load(sys.stdin); assert isinstance(d['project']['keywords'], dict) and d['project']['keywords']['count']==4\""

echo ""
echo "--- 2.7: Concurrent type changes on DIFFERENT keys ---"
cd "$(W 5)" && $MUT pull 2>/dev/null
cd "$(W 6)" && $MUT pull 2>/dev/null

# Agent-5 changes tls.enabled from bool to object
cd "$(W 5)"
python3 -c "
import json
with open('config.json') as f: d = json.load(f)
d['server']['tls'] = {
    'enabled': True,
    'cert_path': '/etc/ssl/mut.crt',
    'key_path': '/etc/ssl/mut.key',
    'min_version': 'TLSv1.3'
}
with open('config.json', 'w') as f: json.dump(d, f, indent=2, ensure_ascii=False)
"
$MUT commit -m "agent-5: tls bool->object" -w agent-5 2>/dev/null
$MUT push 2>/dev/null

# Agent-6 changes empty_array to populated array, and null to object
cd "$(W 6)"
python3 -c "
import json
with open('config.json') as f: d = json.load(f)
d['test_values']['empty_array'] = [1, 'two', {'three': 3}]
d['test_values']['null_value'] = {'was_null': True, 'reason': 'upgraded'}
with open('config.json', 'w') as f: json.dump(d, f, indent=2, ensure_ascii=False)
"
$MUT commit -m "agent-6: null->obj, empty->populated" -w agent-6 2>/dev/null
$MUT push 2>/dev/null

SERVER_JSON=$(cat "$SERVER_DIR/current/docs/config.json")
check "2.7: TLS expanded" "echo '$SERVER_JSON' | python3 -c \"import sys,json; d=json.load(sys.stdin); assert d['server']['tls']['min_version']=='TLSv1.3'\""
check "2.7: Null->object" "echo '$SERVER_JSON' | python3 -c \"import sys,json; d=json.load(sys.stdin); assert d['test_values']['null_value']['was_null']==True\""
check "2.7: Empty->populated" "echo '$SERVER_JSON' | python3 -c \"import sys,json; d=json.load(sys.stdin); assert len(d['test_values']['empty_array'])==3\""

echo ""
echo "--- 2.8: Concurrent SAME key type change (LWW) ---"
cd "$(W 1)" && $MUT pull 2>/dev/null
cd "$(W 2)" && $MUT pull 2>/dev/null

# Both agents change test_values.integer from int to something else
cd "$(W 1)"
python3 -c "
import json
with open('config.json') as f: d = json.load(f)
d['test_values']['integer'] = [42, 43, 44]  # int -> list
with open('config.json', 'w') as f: json.dump(d, f, indent=2, ensure_ascii=False)
"
$MUT commit -m "agent-1: integer->list" -w agent-1 2>/dev/null
$MUT push 2>/dev/null

cd "$(W 2)"
python3 -c "
import json
with open('config.json') as f: d = json.load(f)
d['test_values']['integer'] = {'value': 42, 'type': 'upgraded'}  # int -> object
with open('config.json', 'w') as f: json.dump(d, f, indent=2, ensure_ascii=False)
"
$MUT commit -m "agent-2: integer->object" -w agent-2 2>/dev/null
$MUT push 2>/dev/null

# LWW: agent-2 (last writer) should win
check "2.8: Type conflict LWW" "cat '$SERVER_DIR/current/docs/config.json' | python3 -c \"import sys,json; d=json.load(sys.stdin); assert isinstance(d['test_values']['integer'], dict), type(d['test_values']['integer'])\""

# ══════════════════════════════════════════════════════════════
# PART 3: Concurrent — MD + JSON mixed pushes
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 3: Concurrent pushes — mixed file edits"
echo "══════════════════════════════════════════════════"

for i in $(seq 1 4); do
    cd "$(W $i)" && $MUT pull 2>/dev/null
done

# Each agent edits a different part
cd "$(W 1)" && echo -e "\n<!-- Agent-1 was here -->" >> architecture.md && $MUT commit -m "a1: md comment" -w agent-1 2>/dev/null
cd "$(W 2)" && python3 -c "
import json
with open('config.json') as f: d = json.load(f)
d['test_values']['concurrent_test'] = 'agent-2-wrote-this'
with open('config.json', 'w') as f: json.dump(d, f, indent=2, ensure_ascii=False)
" && $MUT commit -m "a2: json add key" -w agent-2 2>/dev/null
cd "$(W 3)" && echo "# Agent-3 Notes" > notes.md && $MUT commit -m "a3: new file" -w agent-3 2>/dev/null
cd "$(W 4)" && echo "Agent-4 log entry" > changelog.txt && $MUT commit -m "a4: new txt" -w agent-4 2>/dev/null

# Push all 4 simultaneously
PIDS=""
for i in 1 2 3 4; do
    (cd "$(W $i)" && $MUT push 2>/dev/null) &
    PIDS="$PIDS $!"
done

CONC_OK=0
for PID in $PIDS; do
    wait "$PID" 2>/dev/null && CONC_OK=$((CONC_OK + 1))
done

echo "  Concurrent pushes: $CONC_OK/4 succeeded"
check "3: Most concurrent pushes succeed (>=3)" "[ $CONC_OK -ge 3 ]"
check "3: MD comment landed" "grep -q 'Agent-1 was here' '$SERVER_DIR/current/docs/architecture.md'"
# Some new files may not land if their push lost the concurrent race
NEWFILES=0
[ -f "$SERVER_DIR/current/docs/notes.md" ] && NEWFILES=$((NEWFILES + 1))
[ -f "$SERVER_DIR/current/docs/changelog.txt" ] && NEWFILES=$((NEWFILES + 1))
check "3: At least 1 new file created" "[ $NEWFILES -ge 1 ]"

# ══════════════════════════════════════════════════════════════
# PART 4: Edge cases — empty files, large content, special chars
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 4: Edge cases"
echo "══════════════════════════════════════════════════"

cd "$(W 1)" && $MUT pull 2>/dev/null

echo ""
echo "--- 4.1: Empty file ---"
cd "$(W 1)"
touch empty.md
$MUT commit -m "agent-1: empty file" -w agent-1 2>/dev/null
$MUT push 2>/dev/null
check "4.1: Empty file pushed" "[ -f '$SERVER_DIR/current/docs/empty.md' ]"

echo ""
echo "--- 4.2: File with only whitespace ---"
cd "$(W 1)"
echo -e "   \n\n\t\t\n   " > whitespace.txt
$MUT commit -m "agent-1: whitespace file" -w agent-1 2>/dev/null
$MUT push 2>/dev/null
check "4.2: Whitespace file pushed" "[ -f '$SERVER_DIR/current/docs/whitespace.txt' ]"

echo ""
echo "--- 4.3: Unicode/emoji filename content ---"
cd "$(W 1)"
cat > unicode_test.md << 'EOF'
# 🦞 Mut Protocol — 多语言测试

## 中文 Chinese
版本控制系统的基本概念：提交、推送、拉取、合并。

## 日本語 Japanese
バージョン管理システムの基本概念：コミット、プッシュ、プル、マージ。

## 한국어 Korean
버전 관리 시스템의 기본 개념: 커밋, 푸시, 풀, 병합.

## العربية Arabic
مفاهيم أساسية لنظام التحكم في الإصدارات

## Emoji Table
| Symbol | Meaning |
|--------|---------|
| ✅ | Passed |
| ❌ | Failed |
| ⚠️ | Warning |
| 🔒 | Locked |
| 🦞 | Mut! |
EOF
$MUT commit -m "agent-1: unicode content" -w agent-1 2>/dev/null
$MUT push 2>/dev/null
check "4.3: Unicode content" "grep -q '多语言测试' '$SERVER_DIR/current/docs/unicode_test.md'"
check "4.3: Emoji preserved" "grep -q '🦞' '$SERVER_DIR/current/docs/unicode_test.md'"

echo ""
echo "--- 4.4: Large repetitive content ---"
cd "$(W 1)"
python3 -c "
lines = []
for i in range(500):
    lines.append(f'Line {i:04d}: The quick brown fox jumps over the lazy dog. SHA-256 hash test data row.')
with open('large_file.txt', 'w') as f:
    f.write('\n'.join(lines))
"
$MUT commit -m "agent-1: large 500-line file" -w agent-1 2>/dev/null
$MUT push 2>/dev/null
SERVER_LINES=$(wc -l < "$SERVER_DIR/current/docs/large_file.txt")
check "4.4: Large file (>=499 lines)" "[ $SERVER_LINES -ge 499 ]"

echo ""
echo "--- 4.5: Deeply nested JSON ---"
cd "$(W 1)"
python3 -c "
import json
deep = {'level': 0}
current = deep
for i in range(1, 20):
    current['child'] = {'level': i, 'data': f'value-at-depth-{i}'}
    current = current['child']
current['leaf'] = True
with open('deep_nested.json', 'w') as f:
    json.dump(deep, f, indent=2)
"
$MUT commit -m "agent-1: 20-level nested JSON" -w agent-1 2>/dev/null
$MUT push 2>/dev/null
check "4.5: Deep nested JSON" "[ -f '$SERVER_DIR/current/docs/deep_nested.json' ]"

# ══════════════════════════════════════════════════════════════
# PART 5: File operations — create, delete, rename simulation
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 5: File lifecycle — create, delete, rename"
echo "══════════════════════════════════════════════════"

echo ""
echo "--- 5.1: Create multiple files at once ---"
cd "$(W 2)" && $MUT pull 2>/dev/null
cd "$(W 2)"
for f in alpha.md beta.md gamma.md delta.md; do
    echo "# File: $f" > "$f"
    echo "Created by Agent-2" >> "$f"
done
$MUT commit -m "agent-2: create 4 files" -w agent-2 2>/dev/null
$MUT push 2>/dev/null
CREATED=0
for f in alpha.md beta.md gamma.md delta.md; do
    [ -f "$SERVER_DIR/current/docs/$f" ] && CREATED=$((CREATED + 1))
done
check "5.1: All 4 files created" "[ $CREATED -eq 4 ]"

echo ""
echo "--- 5.2: Delete multiple files ---"
cd "$(W 3)" && $MUT pull 2>/dev/null
cd "$(W 3)"
rm -f alpha.md beta.md
$MUT commit -m "agent-3: delete alpha+beta" -w agent-3 2>/dev/null
$MUT push 2>/dev/null
check "5.2: alpha.md deleted" "[ ! -f '$SERVER_DIR/current/docs/alpha.md' ]"
check "5.2: beta.md deleted" "[ ! -f '$SERVER_DIR/current/docs/beta.md' ]"
check "5.2: gamma.md survived" "[ -f '$SERVER_DIR/current/docs/gamma.md' ]"

echo ""
echo "--- 5.3: Rename simulation (delete + create) ---"
cd "$(W 4)" && $MUT pull 2>/dev/null
cd "$(W 4)"
# Rename gamma.md -> gamma_v2.md
cp gamma.md gamma_v2.md
rm gamma.md
$MUT commit -m "agent-4: rename gamma -> gamma_v2" -w agent-4 2>/dev/null
$MUT push 2>/dev/null
check "5.3: Old name removed" "[ ! -f '$SERVER_DIR/current/docs/gamma.md' ]"
check "5.3: New name exists" "[ -f '$SERVER_DIR/current/docs/gamma_v2.md' ]"

echo ""
echo "--- 5.4: One agent creates, another deletes (conflict) ---"
cd "$(W 5)" && $MUT pull 2>/dev/null
cd "$(W 6)" && $MUT pull 2>/dev/null

cd "$(W 5)"
echo "Ephemeral file by agent-5" > ephemeral.txt
$MUT commit -m "agent-5: create ephemeral" -w agent-5 2>/dev/null
$MUT push 2>/dev/null

cd "$(W 6)" && $MUT pull 2>/dev/null
cd "$(W 6)"
rm -f ephemeral.txt
$MUT commit -m "agent-6: delete ephemeral" -w agent-6 2>/dev/null
$MUT push 2>/dev/null
check "5.4: Create then delete" "[ ! -f '$SERVER_DIR/current/docs/ephemeral.txt' ]"

# ══════════════════════════════════════════════════════════════
# PART 6: Concurrent same-line/key stress
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 6: Concurrent same-line/key LWW stress"
echo "══════════════════════════════════════════════════"

for i in $(seq 1 6); do
    cd "$(W $i)" && $MUT pull 2>/dev/null
done

echo ""
echo "--- 6.1: All 6 agents edit the title line simultaneously ---"
PIDS=""
for i in $(seq 1 6); do
    (
        cd "$(W $i)"
        sed -i "s/^# System Architecture.*/# System Architecture — Agent-$i Edition/" architecture.md
        $MUT commit -m "agent-$i: title" -w "agent-$i" 2>/dev/null
        $MUT push 2>/dev/null
    ) &
    PIDS="$PIDS $!"
done

TITLE_OK=0
for PID in $PIDS; do
    wait "$PID" 2>/dev/null && TITLE_OK=$((TITLE_OK + 1))
done

echo "  Pushes succeeded: $TITLE_OK/6"
check "6.1: At least 4 pushes succeed" "[ $TITLE_OK -ge 4 ]"

# Check the final title is one of the agents
FINAL_TITLE=$(head -1 "$SERVER_DIR/current/docs/architecture.md")
echo "  Final title: $FINAL_TITLE"
check "6.1: Title is from some agent" "echo '$FINAL_TITLE' | grep -q 'Agent-'"

echo ""
echo "--- 6.2: All 6 agents change same JSON key simultaneously ---"
for i in $(seq 1 6); do
    cd "$(W $i)" && $MUT pull 2>/dev/null
done

PIDS=""
for i in $(seq 1 6); do
    (
        cd "$(W $i)"
        python3 -c "
import json
with open('config.json') as f: d = json.load(f)
d['project']['description'] = 'Written by Agent-$i at push time'
with open('config.json', 'w') as f: json.dump(d, f, indent=2, ensure_ascii=False)
"
        $MUT commit -m "agent-$i: description" -w "agent-$i" 2>/dev/null
        $MUT push 2>/dev/null
    ) &
    PIDS="$PIDS $!"
done

JSON_OK=0
for PID in $PIDS; do
    wait "$PID" 2>/dev/null && JSON_OK=$((JSON_OK + 1))
done

echo "  Pushes succeeded: $JSON_OK/6"
check "6.2: At least 4 JSON pushes succeed" "[ $JSON_OK -ge 4 ]"

FINAL_DESC=$(python3 -c "
import json
with open('$SERVER_DIR/current/docs/config.json') as f: d = json.load(f)
print(d['project']['description'])
")
echo "  Final description: $FINAL_DESC"
check "6.2: Description is from some agent" "echo '$FINAL_DESC' | grep -q 'Agent-'"

# ══════════════════════════════════════════════════════════════
# PART 7: Large file line-merge stress
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 7: Large file line-merge (500-line file)"
echo "══════════════════════════════════════════════════"

for i in 1 2 3; do
    cd "$(W $i)" && $MUT pull 2>/dev/null
done

# Agent-1 edits line 10
cd "$(W 1)"
sed -i 's/^Line 0010:.*/Line 0010: EDITED BY AGENT-1/' large_file.txt
$MUT commit -m "agent-1: edit line 10" -w agent-1 2>/dev/null
$MUT push 2>/dev/null

# Agent-2 edits line 490 (far from line 10)
cd "$(W 2)"
sed -i 's/^Line 0490:.*/Line 0490: EDITED BY AGENT-2/' large_file.txt
$MUT commit -m "agent-2: edit line 490" -w agent-2 2>/dev/null
$MUT push 2>/dev/null

# Agent-3 edits line 250 (middle)
cd "$(W 3)"
sed -i 's/^Line 0250:.*/Line 0250: EDITED BY AGENT-3/' large_file.txt
$MUT commit -m "agent-3: edit line 250" -w agent-3 2>/dev/null
$MUT push 2>/dev/null

check "7: Line 10 merged" "grep -q 'EDITED BY AGENT-1' '$SERVER_DIR/current/docs/large_file.txt'"
check "7: Line 490 merged" "grep -q 'EDITED BY AGENT-2' '$SERVER_DIR/current/docs/large_file.txt'"
check "7: Line 250 merged" "grep -q 'EDITED BY AGENT-3' '$SERVER_DIR/current/docs/large_file.txt'"

# Verify untouched lines preserved
check "7: Untouched line intact" "grep -q '^Line 0100:' '$SERVER_DIR/current/docs/large_file.txt'"

# ══════════════════════════════════════════════════════════════
# PART 8: Concurrent pulls during active pushes
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 8: Concurrent pull + push interleaved"
echo "══════════════════════════════════════════════════"

for i in $(seq 1 6); do
    cd "$(W $i)" && $MUT pull 2>/dev/null
done

# Agent-1,2,3 push new content; Agent-4,5,6 pull simultaneously
cd "$(W 1)" && echo "push-during-pull-1" > interleave1.txt && $MUT commit -m "a1: interleave" -w agent-1 2>/dev/null
cd "$(W 2)" && echo "push-during-pull-2" > interleave2.txt && $MUT commit -m "a2: interleave" -w agent-2 2>/dev/null
cd "$(W 3)" && echo "push-during-pull-3" > interleave3.txt && $MUT commit -m "a3: interleave" -w agent-3 2>/dev/null

PIDS=""
for i in 1 2 3; do
    (cd "$(W $i)" && $MUT push 2>/dev/null) &
    PIDS="$PIDS $!"
done
for i in 4 5 6; do
    (cd "$(W $i)" && $MUT pull 2>/dev/null) &
    PIDS="$PIDS $!"
done

INTERLEAVE_OK=0
for PID in $PIDS; do
    wait "$PID" 2>/dev/null && INTERLEAVE_OK=$((INTERLEAVE_OK + 1))
done
check "8: All 6 concurrent ops succeed" "[ $INTERLEAVE_OK -eq 6 ]"

# ══════════════════════════════════════════════════════════════
# PART 9: Rapid fire — 20 sequential pushes
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 9: Rapid fire — 20 sequential commits+pushes"
echo "══════════════════════════════════════════════════"

cd "$(W 1)" && $MUT pull 2>/dev/null

START=$(date +%s)
for i in $(seq 1 20); do
    cd "$(W 1)"
    echo "Rapid iteration $i at $(date +%s%N)" > rapid_log.txt
    $MUT commit -m "rapid-$i" -w agent-1 2>/dev/null
    $MUT push 2>/dev/null
done
END=$(date +%s)

echo "  20 pushes in $((END - START))s"
check "9: Last rapid push" "grep -q 'iteration 20' '$SERVER_DIR/current/docs/rapid_log.txt'"

# ══════════════════════════════════════════════════════════════
# PART 10: Final consistency — all agents pull and verify
# ══════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo "  PART 10: Final consistency check"
echo "══════════════════════════════════════════════════"

for i in $(seq 1 6); do
    cd "$(W $i)" && $MUT pull 2>/dev/null
done

# All agents should see the same files
A1_FILES=$(ls "$(W 1)" | sort | tr '\n' ',')
A6_FILES=$(ls "$(W 6)" | sort | tr '\n' ',')
check "10: Agent-1 and Agent-6 see same files" "[ '$A1_FILES' = '$A6_FILES' ]"

# Verify key content preserved after all the stress
check "10: Architecture MD exists" "[ -f '$(W 1)/architecture.md' ]"
check "10: Config JSON valid" "python3 -c \"import json; json.load(open('$(W 1)/config.json'))\" 2>/dev/null"
FINAL_LARGE=$(wc -l < "$(W 1)/large_file.txt" 2>/dev/null || echo 0)
check "10: Large file intact" "[ $FINAL_LARGE -ge 498 ]"
check "10: Unicode preserved" "grep -q '🦞' '$(W 1)/unicode_test.md'"
check "10: Deep nested JSON valid" "python3 -c \"import json; d=json.load(open('$(W 1)/deep_nested.json')); assert d['child']['child']['level']==2\" 2>/dev/null"

# ══════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════
echo ""
echo "================================================================"
echo "  PRODUCTION STRESS TEST RESULTS"
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
echo "  Part 1:  Complex MD — sequential edits, tables, code, todos"
echo "  Part 2:  Complex JSON — nested keys, arrays, i18n, types"
echo "  Part 3:  Concurrent mixed pushes (MD + JSON + new files)"
echo "  Part 4:  Edge cases (empty, whitespace, unicode, large, deep)"
echo "  Part 5:  File lifecycle (create, delete, rename simulation)"
echo "  Part 6:  6-agent same-line/key LWW stress"
echo "  Part 7:  500-line file three-way line merge"
echo "  Part 8:  Concurrent push + pull interleaved"
echo "  Part 9:  20 rapid sequential pushes"
echo "  Part 10: Final consistency verification"
echo "================================================================"
