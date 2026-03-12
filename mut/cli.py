#!/usr/bin/env python3
"""
Mut — Managed Unified Tree.

A minimal, Git-like version management protocol designed for AI agents.
Unlike Git: centralized truth, auto-resolve conflicts, per-path access.

Usage:
    mut init                          Initialize a .mut/ repo
    mut commit -m "msg" -w agent-A    Snapshot the working directory
    mut log                           Show history
    mut status                        What changed since last commit
    mut diff <id1> <id2>              Compare two snapshots
    mut checkout <id>                 Restore working directory
    mut show <id>:<path>              Show file content at a snapshot
    mut tree <id>                     Show tree structure of a snapshot
    mut push                          Push local snapshots to server
    mut pull                          Pull new snapshots from server
    mut stats                         Repository statistics
"""

import argparse
import sys

from mut.foundation.error import MutError
from mut.ops.repo import MutRepo
from mut.ops import (
    init_op,
    clone_op,
    commit_op,
    status_op,
    log_op,
    diff_op,
    checkout_op,
    show_op,
    tree_op,
    push_op,
    pull_op,
    stats_op,
    register_op,
)


def cmd_register(args):
    result = register_op.register(args.invite_url)
    print(f"Registered with {result['server']}")
    print(f"  agent:   {result['agent_id']}")
    print(f"  project: {result['project']}")
    print(f"  scope:   {result['scope'].get('path', '/')}")
    print(f"")
    print(f"Credentials saved. You can now clone:")
    print(f"  mut clone {result['server']}")


def cmd_init(args):
    init_op.init(".")
    print("Initialized empty Mut repository in .mut/")


def cmd_clone(args):
    url = args.url
    token = args.token

    if "/invite/" in url:
        result = register_op.register(url)
        print(f"Registered with {result['server']}")
        print(f"  agent: {result['agent_id']}")
        url = result["server"]
        from mut.foundation.credentials import get_credential
        cred = get_credential(url)
        token = cred["token"]

    if not token:
        from mut.foundation.credentials import get_credential
        cred = get_credential(url)
        if cred:
            token = cred["token"]
        else:
            print("fatal: no --token provided and no stored credentials for this server.", file=sys.stderr)
            print("  Run 'mut clone <invite-url>' to register, or pass --token.", file=sys.stderr)
            sys.exit(1)

    workdir = args.dir if args.dir else None
    repo = clone_op.clone(url, token, workdir)
    print(f"Cloned into {repo.workdir}")


def cmd_commit(args):
    repo = MutRepo(".")
    who = args.who if args.who else None
    snap = commit_op.commit(repo, args.message, who)
    if snap is None:
        print("nothing to commit, working directory unchanged")
    else:
        pushed_tag = "" if snap["pushed"] else " (local)"
        print(f"[{snap['who']}] snapshot #{snap['id']}{pushed_tag}: {snap['message']}")
        print(f"  root: {snap['root']}")


def cmd_log(args):
    repo = MutRepo(".")
    entries = log_op.log(repo)
    if not entries:
        print("no snapshots yet")
        return
    for s in entries:
        parent = f" ← #{s['parent']}" if s['parent'] else ""
        pushed = "✓" if s.get("pushed", True) else "○"
        print(f"  {pushed} #{s['id']}  {s['time']}  [{s['who']}]  {s['message']}{parent}")
        print(f"        root: {s['root']}")


def cmd_status(args):
    repo = MutRepo(".")
    result = status_op.status(repo)
    changes = result["changes"]
    unpushed = result["unpushed"]

    if not changes:
        print("clean — no changes since last snapshot")
    else:
        for c in changes:
            print(f"  {c['op']:>8}  {c['path']}")

    if unpushed > 0:
        print(f"\n  {unpushed} unpushed snapshot(s) — run 'mut push' to sync")


def cmd_diff(args):
    repo = MutRepo(".")
    changes = diff_op.diff(repo, args.id1, args.id2)
    if not changes:
        print("no differences")
    else:
        for c in changes:
            print(f"  {c['op']:>8}  {c['path']}")


def cmd_checkout(args):
    repo = MutRepo(".")
    snap = checkout_op.checkout(repo, args.id)
    print(f"checked out snapshot #{args.id}: {snap['message']}")


def cmd_show(args):
    if ":" not in args.ref:
        print("usage: mut show <id>:<path>  e.g. mut show 1:src/main.py", file=sys.stderr)
        sys.exit(1)
    repo = MutRepo(".")
    sid, path = args.ref.split(":", 1)
    try:
        snap_id = int(sid)
    except ValueError:
        print(f"fatal: invalid snapshot id '{sid}' — must be an integer", file=sys.stderr)
        sys.exit(1)
    print(show_op.show(repo, snap_id, path))


def cmd_tree(args):
    repo = MutRepo(".")
    print(tree_op.tree(repo, args.id))


def cmd_push(args):
    repo = MutRepo(".")
    result = push_op.push(repo)
    if result["status"] == "dirty":
        print(f"You have {result['uncommitted']} uncommitted change(s).")
        print(f"  Run 'mut commit -m \"...\"' first, then push.")
        return
    if result["status"] == "up-to-date":
        print("Everything up-to-date")
    else:
        print(f"Pushed {result['pushed']} snapshot(s)")
        if result.get("server_version"):
            print(f"  server version: {result['server_version']}")
        if result.get("merged"):
            print(f"  auto-merged ({result.get('conflicts', 0)} conflict(s) resolved)")
        if result.get("message"):
            print(f"  {result['message']}")


def cmd_pull(args):
    repo = MutRepo(".")
    result = pull_op.pull(repo, force=args.force)
    if result["status"] == "up-to-date":
        print("Already up-to-date")
    else:
        print(f"Pulled {result['pulled']} file(s) from server")
        if result.get("server_version"):
            print(f"  server version: {result['server_version']}")


def cmd_stats(args):
    repo = MutRepo(".")
    s = stats_op.stats(repo)
    print(f"  objects:   {s['objects']}")
    print(f"  bytes:     {s['bytes']}")
    print(f"  snapshots: {s['snapshots']}")


def main():
    parser = argparse.ArgumentParser(
        prog="mut",
        description="Mut — Managed Unified Tree. Version management for AI agents.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Initialize a .mut/ repository")

    p_register = sub.add_parser("register", help="Register with a server using an invite URL")
    p_register.add_argument("invite_url", help="Invite URL, e.g. http://server:9742/invite/abc123")

    p_clone = sub.add_parser("clone", help="Clone scope from server")
    p_clone.add_argument("url", help="Server URL, e.g. http://localhost:9742")
    p_clone.add_argument("--token", default="", help="Auth token (auto from credentials if omitted)")
    p_clone.add_argument("dir", nargs="?", default="",
                         help="Target directory (default: auto from project name)")

    p_commit = sub.add_parser("commit", help="Snapshot the working directory")
    p_commit.add_argument("-m", "--message", required=True, help="Commit message")
    p_commit.add_argument("-w", "--who", default="", help="Agent ID (auto from config if omitted)")

    sub.add_parser("log", help="Show snapshot history")
    sub.add_parser("status", help="Show changes since last snapshot")

    p_diff = sub.add_parser("diff", help="Compare two snapshots")
    p_diff.add_argument("id1", type=int)
    p_diff.add_argument("id2", type=int)

    p_co = sub.add_parser("checkout", help="Restore to a snapshot")
    p_co.add_argument("id", type=int)

    p_show = sub.add_parser("show", help="Show file at snapshot  e.g. mut show 1:main.py")
    p_show.add_argument("ref", help="<snapshot_id>:<path>")

    p_tree = sub.add_parser("tree", help="Show Merkle tree of a snapshot")
    p_tree.add_argument("id", type=int)

    sub.add_parser("push", help="Push local snapshots to server")
    p_pull = sub.add_parser("pull", help="Pull new snapshots from server")
    p_pull.add_argument("--force", action="store_true",
                        help="Overwrite uncommitted local changes")
    sub.add_parser("stats", help="Repository statistics")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    dispatch = {
        "init": cmd_init,
        "register": cmd_register,
        "clone": cmd_clone,
        "commit": cmd_commit,
        "log": cmd_log,
        "status": cmd_status,
        "diff": cmd_diff,
        "checkout": cmd_checkout,
        "show": cmd_show,
        "tree": cmd_tree,
        "push": cmd_push,
        "pull": cmd_pull,
        "stats": cmd_stats,
    }

    try:
        dispatch[args.command](args)
    except MutError as e:
        print(f"fatal: {e}", file=sys.stderr)
        sys.exit(1)
    except FileExistsError as e:
        print(f"fatal: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
