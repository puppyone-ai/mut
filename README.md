# Mut

Version control built for AI agents. Git's Merkle tree + SVN's central authority + agent-native constraints.

Each agent sees only its own subtree. Commits never fail. The server merges everything.

## Why Not Git?

Git works great for humans. For AI agents, two problems keep coming up:

1. **Conflicts break the flow.** Multiple agents editing the same repo means frequent merge conflicts. Each conflict needs a human to step in, resolve `<<<< ====` markers, and restart the agent — shattering its context and momentum.

2. **No per-agent permissions.** You can't scope what each agent is allowed to touch. Git gives full repo access — there's no way to say "Agent A can only write to `/src/`, Agent B can only write to `/docs/`." Every agent can read and overwrite everything.

Mut solves both by design:

| | Git | Mut |
|---|---|---|
| **Conflicts** | Human resolves manually | Server auto-merges; LWW fallback — commits never fail |
| **Permissions** | Full repo access once cloned | Token-scoped, path-level read/write ACL |
| **Architecture** | Decentralized | Centralized — server is the source of truth |
| **Designed for** | Humans + branches + PRs | AI agents + scopes + auto-merge |

## Quick Start

### Server

```bash
# Initialize a project
mut-server init ./my-project --name my-project

# Create an invite link for agents
mut-server create-invite ./my-project --scope-path "/src/" --host localhost --port 9742

# Start the server
mut-server serve ./my-project --port 9742
```

### Agent

```bash
# One command: register + clone (auto-creates my-project/ directory)
mut clone http://localhost:9742/invite/<invite-id>

# Work normally
cd my-project
echo 'print("hello")' > app.py

# Commit and push — no -w flag needed, agent ID is automatic
mut commit -m "add app"
mut push

# Pull changes from other agents
mut pull
```

## How It Works

```
Agent A (scope: /src/)          Server              Agent B (scope: /docs/)
┌──────────────┐          ┌──────────────┐          ┌──────────────┐
│ src/          │  push →  │ current/     │  ← push  │ docs/        │
│   app.py      │          │   src/       │          │   readme.md  │
│   utils.py    │          │     app.py   │          │   api.md     │
│ .mut/         │  ← pull  │     utils.py │  pull →  │ .mut/        │
│   objects/    │          │   docs/      │          │   objects/   │
│   snapshots   │          │     readme.md│          │   snapshots  │
└──────────────┘          │     api.md   │          └──────────────┘
                          │ .mut-server/ │
                          │   objects/   │
                          │   scopes/    │
                          │   history/   │
                          └──────────────┘
```

**Core concepts:**

- **Content-addressable storage** — files are stored by SHA-256 hash, identical content stored once
- **Merkle tree** — directory structure is a hash tree; changing one file recomputes hashes up to the root
- **Scopes** — each agent gets a path prefix (e.g. `/src/`) with read/write permissions
- **Server-side merge** — when two agents modify the same scope, the server runs three-way merge (line-level, JSON key-level, then LWW fallback)
- **Grafting** — after a push, the server replaces the agent's subtree in the full project tree and recomputes the root hash

## Commands

### Agent CLI (`mut`)

| Command | Description |
|---|---|
| `mut clone <url>` | Clone from server (supports invite URLs) |
| `mut register <invite-url>` | Register with a server using an invite |
| `mut status` | Show uncommitted changes |
| `mut commit -m "msg"` | Snapshot the working directory |
| `mut push` | Push commits to server |
| `mut pull` | Pull changes from server |
| `mut log` | Show commit history |
| `mut diff <id1> <id2>` | Compare two snapshots |
| `mut checkout <id>` | Restore to a previous snapshot |
| `mut show <id>:<path>` | Show a file at a specific snapshot |
| `mut tree <id>` | Show Merkle tree structure |
| `mut stats` | Repository statistics |

### Server CLI (`mut-server`)

| Command | Description |
|---|---|
| `mut-server init <path>` | Initialize a server repository |
| `mut-server create-invite <path>` | Generate an invite URL for agents |
| `mut-server add-scope <path>` | Manually add a scope + assign agents |
| `mut-server issue-token <path>` | Manually issue an API key |
| `mut-server serve <path>` | Start the HTTP server |

## Project Structure

```
mut/
├── cli.py                 # Agent CLI
├── core/                  # Core data structures
│   ├── object_store.py    #   Content-addressable blob/tree storage
│   ├── tree.py            #   Merkle tree operations
│   ├── snapshot.py        #   Commit chain
│   ├── merge.py           #   Three-way merge engine
│   ├── diff.py            #   Tree/manifest comparison
│   ├── scope.py           #   Path permission checks
│   └── auth.py            #   JWT-style token signing
├── ops/                   # CLI operations
│   ├── clone_op.py        #   mut clone
│   ├── commit_op.py       #   mut commit
│   ├── push_op.py         #   mut push / pull
│   └── ...
├── foundation/            # Low-level utilities
│   ├── hash.py            #   SHA-256 hashing
│   ├── fs.py              #   Atomic writes, file locks
│   ├── transport.py       #   HTTP client (stdlib only)
│   └── credentials.py     #   Local API key storage
└── server/                # Server components
    ├── server.py           #   HTTP API
    ├── graft.py            #   Subtree grafting engine
    ├── repo.py             #   Server repository management
    └── ...
```

Four layers, dependencies go strictly downward:

```
CLI → Operations → Core → Foundation
                   Server ↗
```

## Requirements

- Python 3.9+
- No external dependencies (stdlib only)

## License

MIT
