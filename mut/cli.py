#!/usr/bin/env python3
"""
Mut — Managed Unified Tree.

A minimal, Git-like version management protocol designed for AI agents.
Unlike Git: centralized truth, auto-resolve conflicts, per-scope access.

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
import json
import sys

from mut.foundation.error import MutError, NetworkError
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
    rollback_op,
    stats_op,
    ls_op,
    cat_op,
    daemon_op,
    link_access_op,
)


def _json_flag(args) -> bool:
    """Safely read the --json flag from parsed args."""
    return getattr(args, "json", False)


def _output(data: dict, json_mode):
    """If *json_mode*, print *data* as JSON and return True; else return False."""
    if json_mode:
        print(json.dumps(data, indent=2, default=str))
        return True
    return False  # caller handles human output


def cmd_init(_args):
    import os
    from mut.foundation.config import MUT_DIR
    was_existing = os.path.isdir(MUT_DIR)
    init_op.init(".")
    if was_existing:
        print("Reinitialized existing Mut repository in .mut/")
    else:
        print("Initialized empty Mut repository in .mut/")


def cmd_clone(args):
    url = args.url
    credential = args.credential

    if not credential:
        from mut.foundation.credentials import get_credential
        cred = get_credential(url)
        if cred:
            credential = cred["credential"]
        else:
            print(
                "fatal: no --credential provided and no stored credentials "
                "for this server.",
                file=sys.stderr,
            )
            print("  Pass --credential <key> to authenticate.", file=sys.stderr)
            sys.exit(1)

    workdir = args.dir if args.dir else None
    repo = clone_op.clone(url, credential, workdir)
    print(f"Cloned into {repo.workdir}")


def cmd_commit(args):
    repo = MutRepo(".")
    who = args.who if args.who else None
    snap = commit_op.commit(repo, args.message, who)
    if snap is None:
        print("nothing to commit, working directory unchanged")
    else:
        pushed_tag = "" if snap["pushed"] else " (local)"
        print(f"[{snap['who']}] snapshot #{snap['id']}{pushed_tag}: "
              f"{snap['message']}")
        print(f"  root: {snap['root']}")


def cmd_log(args):
    repo = MutRepo(".")
    entries = log_op.log(repo)
    if _output({"snapshots": entries}, _json_flag(args)):
        return
    if not entries:
        print("no snapshots yet")
        return
    for s in entries:
        parent = f" ← #{s['parent']}" if s['parent'] else ""
        pushed = "✓" if s.get("pushed", True) else "○"
        print(f"  {pushed} #{s['id']}  {s['time']}  [{s['who']}]  "
              f"{s['message']}{parent}")
        print(f"        root: {s['root']}")


def cmd_status(args):
    repo = MutRepo(".")
    result = status_op.status(repo)
    changes = result["changes"]
    unpushed = result["unpushed"]

    # Enrich result with config info for JSON output
    from mut.foundation.config import load_config, REMOTE_HEAD_FILE
    from mut.foundation.fs import read_text
    config = load_config(repo.mut_root)
    result["server"] = config.get("server", "")
    latest = repo.snapshots.latest()
    result["local_version"] = latest["id"] if latest else 0
    rh_path = repo.mut_root / REMOTE_HEAD_FILE
    result["remote_version"] = int(read_text(rh_path)) if rh_path.exists() else None

    if _output(result, _json_flag(args)):
        return

    server = config.get("server", "")
    if server:
        print(f"  access point: {server}")
    print(f"  local version: {result['local_version']}")
    if result["remote_version"] is not None:
        print(f"  remote version: {result['remote_version']}")

    if not changes:
        print("  clean — no changes since last snapshot")
    else:
        _OP_PREFIX = {"added": "+", "modified": "~", "deleted": "-"}
        for c in changes:
            prefix = _OP_PREFIX.get(c["op"], " ")
            print(f"  {prefix} {c['path']}")

    if unpushed > 0:
        print(f"\n  {unpushed} unpushed snapshot(s) — run 'mut push' to sync")


def cmd_diff(args):
    repo = MutRepo(".")
    changes = diff_op.diff(repo, args.id1, args.id2)
    if _output({"changes": changes}, _json_flag(args)):
        return
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
        print("usage: mut show <id>:<path>  e.g. mut show 1:src/main.py",
              file=sys.stderr)
        sys.exit(1)
    repo = MutRepo(".")
    sid, path = args.ref.split(":", 1)
    try:
        snap_id = int(sid)
    except ValueError:
        print(f"fatal: invalid snapshot id '{sid}' — must be an integer",
              file=sys.stderr)
        sys.exit(1)
    print(show_op.show(repo, snap_id, path))


def cmd_tree(args):
    repo = MutRepo(".")
    print(tree_op.tree(repo, args.id))


def cmd_remote(args):
    repo = MutRepo(".")
    from mut.foundation.config import load_config, save_config
    config = load_config(repo.mut_root)

    if args.action == "add":
        config["server"] = args.url
        save_config(repo.mut_root, config)
        print(f"Remote set to {args.url}")
    elif args.action == "remove":
        config.pop("server", None)
        save_config(repo.mut_root, config)
        print("Remote removed")
    elif args.action == "show":
        server = config.get("server", "")
        if server:
            print(f"  origin  {server}")
        else:
            print("  (no remote configured)")


def cmd_push(args):
    repo = MutRepo(".")
    try:
        result = push_op.push(repo)
    except NetworkError:
        print("fatal: push failed — cannot reach server.", file=sys.stderr)
        print("  Check your network connection and server URL.", file=sys.stderr)
        print("  You can retry with: mut push", file=sys.stderr)
        sys.exit(1)
    if _output(result, _json_flag(args)):
        return
    if result["status"] == "dirty":
        print(f"You have {result['uncommitted']} uncommitted change(s).")
        print("  Run 'mut commit -m \"...\"' first, then push.")
        print("  To see what changed: mut status")
        return
    if result["status"] == "up-to-date":
        print("Everything up-to-date")
    else:
        print(f"Pushed {result['pushed']} snapshot(s)")
        if result.get("server_version"):
            print(f"  server version: {result['server_version']}")
        if result.get("merged"):
            print(f"  auto-merged ({result.get('conflicts', 0)} "
                  "conflict(s) resolved)")
        if result.get("message"):
            print(f"  {result['message']}")


def cmd_pull(args):
    repo = MutRepo(".")
    try:
        result = pull_op.pull(repo, force=args.force)
    except NetworkError:
        print("fatal: pull failed — cannot reach server.", file=sys.stderr)
        print("  Check your network connection and try again: mut pull",
              file=sys.stderr)
        sys.exit(1)

    if _output(result, _json_flag(args)):
        return

    push_info = result.get("push")
    if push_info and push_info.get("status") == "pushed":
        print(f"Pushed {push_info['pushed']} unpushed snapshot(s) first")
        if push_info.get("merged"):
            print(f"  server auto-merged "
                  f"({push_info.get('conflicts', 0)} conflict(s) resolved)")

    if result["status"] == "up-to-date":
        print("Already up-to-date")
    else:
        print(f"Pulled {result['pulled']} file(s) from server")
        if result.get("server_version"):
            print(f"  server version: {result['server_version']}")


def cmd_rollback(args):
    repo = MutRepo(".")
    result = rollback_op.rollback(repo, args.version)
    if _output(result, _json_flag(args)):
        return
    if result["status"] == "already-at-version":
        print(f"Already at version {args.version}")
    else:
        print(f"Rolled back to v{result['target_version']}")
        print(f"  new server version: v{result['new_version']}")
        changes = result.get("changes", [])
        if changes:
            print(f"  {len(changes)} file(s) changed")


def cmd_stats(args):
    repo = MutRepo(".")
    s = stats_op.stats(repo)
    if _output(s, _json_flag(args)):
        return
    print(f"  objects:   {s['objects']}")
    print(f"  bytes:     {s['bytes']}")
    print(f"  snapshots: {s['snapshots']}")


def cmd_ls(args):
    repo = MutRepo(".")
    entries = ls_op.ls(repo, args.path)
    if _output({"entries": entries}, _json_flag(args)):
        return
    for e in entries:
        prefix = "d" if e["type"] == "dir" else "f"
        print(f"  {prefix}  {e['name']}")


def cmd_cat(args):
    repo = MutRepo(".")
    data = cat_op.cat(repo, args.path)
    if _json_flag(args):
        _output({"path": args.path, "content": data.decode(errors="replace")},
                _json_flag(args))
        return
    sys.stdout.buffer.write(data)


def cmd_daemon(args):
    repo = MutRepo(".")
    interval = args.interval if hasattr(args, "interval") else 10
    who = args.who if hasattr(args, "who") and args.who else "daemon"
    daemon_op.daemon(repo, interval=interval, who=who)


def cmd_link(args):
    if args.link_type != "access":
        print(f"fatal: unknown link type '{args.link_type}'. Use: mut link access <url>",
              file=sys.stderr)
        sys.exit(1)

    repo = MutRepo(".")
    result = link_access_op.link_access(
        repo,
        access_point_url=args.url,
        root_dir_name=args.dir_name if args.dir_name else None,
    )

    if _output(result, _json_flag(args)):
        return

    print(f"Linked to {result['server']}")
    print(f"  server version: {result['server_version']}")
    if result.get("scope_created"):
        print(f"  created scope directory: {args.dir_name}/")
    if result.get("scope_push_error"):
        print(f"  note: scope push skipped ({result['scope_push_error']})")


def main():
    parser = argparse.ArgumentParser(
        prog="mut",
        description="Mut — Managed Unified Tree. "
                    "Version management for AI agents.",
    )
    parser.add_argument("--json", action="store_true", default=False,
                        help="Output machine-readable JSON instead of text")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Initialize a .mut/ repository (idempotent)")

    p_clone = sub.add_parser("clone", help="Clone scope from server")
    p_clone.add_argument("url", help="Server URL, e.g. http://localhost:9742")
    p_clone.add_argument("--credential", default="",
                         help="Auth credential (API key, token, etc.)")
    p_clone.add_argument("--dir", default="",
                         help="Target directory (default: auto from project)")

    p_commit = sub.add_parser("commit", help="Snapshot the working directory")
    p_commit.add_argument("-m", "--message", required=True,
                          help="Commit message")
    p_commit.add_argument("-w", "--who", default="",
                          help="Agent ID (auto from config if omitted)")

    sub.add_parser("log", help="Show snapshot history")
    sub.add_parser("status", help="Show changes since last snapshot")

    p_diff = sub.add_parser("diff", help="Compare two snapshots")
    p_diff.add_argument("id1", type=int)
    p_diff.add_argument("id2", type=int)

    p_co = sub.add_parser("checkout", help="Restore to a snapshot")
    p_co.add_argument("id", type=int)

    p_show = sub.add_parser("show",
                            help="Show file at snapshot  e.g. mut show 1:main.py")
    p_show.add_argument("ref", help="<snapshot_id>:<path>")

    p_tree = sub.add_parser("tree", help="Show Merkle tree of a snapshot")
    p_tree.add_argument("id", type=int)

    p_remote = sub.add_parser("remote", help="Manage server remote")
    p_remote.add_argument("action", choices=["add", "remove", "show"],
                          help="add <url>, remove, or show")
    p_remote.add_argument("url", nargs="?", default="",
                          help="Server URL (for 'add' action)")

    sub.add_parser("push", help="Push local snapshots to server")
    p_pull = sub.add_parser("pull", help="Pull new snapshots from server")
    p_pull.add_argument("--force", action="store_true",
                        help="Overwrite uncommitted local changes")

    p_rollback = sub.add_parser("rollback",
                                help="Rollback server to a historical version")
    p_rollback.add_argument("version", type=int,
                            help="Target version number to rollback to")

    sub.add_parser("stats", help="Repository statistics")

    p_ls = sub.add_parser("ls", help="List files in latest snapshot")
    p_ls.add_argument("path", nargs="?", default="",
                      help="Path within the snapshot tree (default: root)")

    p_cat = sub.add_parser("cat", help="Show file content from latest snapshot")
    p_cat.add_argument("path", help="File path within the snapshot tree")

    p_daemon = sub.add_parser("daemon",
                               help="Watch for changes and auto-sync with server")
    p_daemon.add_argument("--interval", type=int, default=10,
                           help="Seconds between sync cycles (default: 10)")
    p_daemon.add_argument("-w", "--who", default="daemon",
                           help="Author name for auto-commits")

    p_link = sub.add_parser("link",
                             help="Link repo to a PuppyOne Access Point")
    p_link.add_argument("link_type", metavar="type",
                         help="Link type (currently only 'access')")
    p_link.add_argument("url", help="Access Point URL")
    p_link.add_argument("dir_name", nargs="?", default="",
                         help="Root directory name to create as scope (optional)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    dispatch = {
        "init": cmd_init,
        "clone": cmd_clone,
        "commit": cmd_commit,
        "log": cmd_log,
        "status": cmd_status,
        "diff": cmd_diff,
        "checkout": cmd_checkout,
        "show": cmd_show,
        "tree": cmd_tree,
        "remote": cmd_remote,
        "push": cmd_push,
        "pull": cmd_pull,
        "rollback": cmd_rollback,
        "stats": cmd_stats,
        "ls": cmd_ls,
        "cat": cmd_cat,
        "daemon": cmd_daemon,
        "link": cmd_link,
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
