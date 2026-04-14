<p align="center">
  <img src="assets/MUT.svg" alt="Mut Logo" width="200">
</p>

# Mut

Mut is version control for context - built for multi-agent collaboration.

- **Centralized context** — the server holds the single source of truth. All agents push to and pull from one place. No diverging copies, no conflicts.
- **Per-agent scopes** — each agent has its own scope (e.g. Agent A on `/src/`, Agent B on `/docs/`). Agents collaborate on the same project, but each one only sees and writes the context it's permitted to access.

## Old World vs New World

| | Old World (Git) | New World (Mut) |
|---|---|---|
| **For whom** |  Humans | AI Agents |
| **Branches** | Decentralized — every branch is equal, no single truth | Centralized — one source-of-truth branch, agents sync from it |
| **Conflicts** | Surface on merge / push — require human resolution | Auto-resolved by the server — push never fails |
| **Access** | All developers see the full repo | Each agent only sees the files within its scope |

### 1. One Source of Truth, Not Scattered Branches

**Old World (Git)**

Two agents update the same `customers.json` — one collects from calls, the other from emails. Each works on its own branch. Which branch has the full list? Neither. The data splits, conflicts follow.

**New World (Mut)**

There's one context on the server. Both agents push to the same place. The server merges automatically. No branches to choose from, no data drift.

### 2. Per-Agent Scopes, Not Full Access

**Old World (Git)**

A company runs two agents on the same project: a customer-facing chatbot that reads product docs, and an internal BI agent that reads financial reports. Both get full repo access — the chatbot can see revenue numbers, the BI agent can overwrite customer content. Sensitive data leaks across boundaries that should never be crossed.

**New World (Mut)**

Each agent gets a scope. The chatbot reads `/docs/`, the BI agent reads `/reports/`. Same project, naturally isolated.


## Quick Start

### Architecture Overview

Mut has two components that run separately:

- **Server** (`mut-server`) — the centralized source of truth. Hosts the project context and handles merges. Typically runs on a dedicated machine or cloud instance so all agents can reach it. Creates a `.mut-server/` directory to store objects, scopes, and history.
- **Client** (`mut`) — runs wherever your agent runs. Clones the context, commits changes locally, and pushes/pulls to/from the server. Creates a `.mut/` directory inside your workspace to track local state.

```
┌─────────────┐           ┌─────────────────┐
│  Agent A    │  push →   │                 │
│  (client)   │  ← pull   │     Server      │
└─────────────┘           │  (source of     │
                          │   truth)        │
┌─────────────┐           │                 │
│  Agent B    │  push →   │                 │
│  (client)   │  ← pull   │                 │
└─────────────┘           └─────────────────┘
```

### Example: Two OpenClaw Agents Sharing Context

You have two [OpenClaw](https://github.com/openclawx/openclaw) agents. Agent A handles customer conversations via WhatsApp. Agent B runs BI analysis on internal data. Each has its own workspace folder — that folder is its context.

**Without Mut:** Each OpenClaw workspace is a plain folder — no version history, no rollback. If an agent corrupts a file, it's gone. You can't see what all your agents are working on in one place. There's no way to govern or audit agent context across machines.

**With Mut:** You run `mut-server` on a VPS (or one of the machines) as the single source of truth. Every change is versioned — you can roll back anytime. All agent context is visible in one place. With scopes, Agent A can only write to `/conversations/`, Agent B can only write to `/reports/`. Version control, visibility, and governance — built in.

```
┌─────────────────┐           ┌─────────────────────┐
│  OpenClaw #1    │  push →   │  Server             │
│  workspace-1/   │  ← pull   │  ├── workspace-1/   │
│  ├── convos/    │           │  │   ├── convos/    │
│  └── reports/   │           │  │   └── reports/   │
│  └── .mut/      │           │  ├── workspace-2/   │
└─────────────────┘           │  │   ├── tasks/     │
                              │  │   └── logs/      │
┌─────────────────┐           │  └── .mut-server/   │
│  OpenClaw #2    │  push →   │                     │
│  workspace-2/   │  ← pull   │                     │
│  ├── tasks/     │           │                     │
│  └── logs/      │           │                     │
│  └── .mut/      │           │                     │
└─────────────────┘           └─────────────────────┘
```

> **Tip:** For local development or testing, you can run both `mut-server` and `mut` on the same machine — the server just uses a local folder as its store.

### 1. Install

```bash
pip install mutai
```

This installs both `mut` (client) and `mut-server` (server) commands.

### 2. Setup (One-Time)

**Server — on the machine that hosts the source of truth:**

```bash
mut-server init ./my-project --name my-project
mut-server add-scope ./my-project --id scope-src --scope-path "/src/"
mut-server issue-credential ./my-project --scope scope-src --agent agent-1 --mode rw
# → prints a credential key, save it for the agent
mut-server serve ./my-project --port 9742
```

**Client — on the machine where your agent runs:**

```bash
mut clone http://<server-host>:9742 --credential <CREDENTIAL>
```

Done. The agent now has a local copy of the context in `my-project/`.

### 3. Daily Usage

**Sync local changes to server:**

```bash
mut commit -m "update customer records"
mut push
```

**Pull latest context from server (other agents' changes):**

```bash
mut pull
```

**Check what changed locally:**

```bash
mut status
```

**View history:**

```bash
mut log
```

**Roll back to a previous version:**

```bash
mut checkout <snapshot-id>
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


## Requirements

- Python 3.9+
- No external dependencies (stdlib only)

## License

MIT
