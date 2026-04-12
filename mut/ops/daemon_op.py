"""Daemon mode: watch for file changes, auto commit + push + pull.

This is the core of the filesystem provider integration — keeps a local
directory continuously synced with the MUT server via an access point.

Usage:
    mut daemon [--interval SECONDS]

The daemon loop:
    1. Pull latest from server
    2. Scan for local changes
    3. If changes found: commit + push
    4. Sleep for interval
    5. Repeat
"""
from __future__ import annotations

import signal
import sys
import time

from mut.foundation.error import MutError, NetworkError
from mut.ops import commit_op, pull_op, push_op, status_op
from mut.ops.repo import MutRepo


_running = True


def _handle_signal(signum, frame):
    global _running
    _running = False


def daemon(repo: MutRepo, *, interval: int = 10, who: str = "daemon") -> None:
    """Run the sync daemon loop.

    Args:
        repo: Initialized MutRepo (must have server configured).
        interval: Seconds between sync cycles (default: 10).
        who: Author name for auto-commits.
    """
    global _running
    _running = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    from mut.foundation.config import load_config
    config = load_config(repo.mut_root)
    server = config.get("server", "")

    if not server:
        raise MutError("No server configured. Clone from an access point first.")

    print(f"mut daemon started (interval={interval}s, server={server})")
    print("  Press Ctrl+C to stop.\n")

    cycle = 0
    consecutive_errors = 0

    while _running:
        cycle += 1
        try:
            # 1. Pull
            try:
                pull_result = pull_op.pull(repo, force=False)
                if pull_result["status"] == "updated":
                    print(f"  [{_ts()}] pulled {pull_result['pulled']} file(s)")
                consecutive_errors = 0
            except MutError as e:
                if "dirty" in str(e).lower() or "uncommitted" in str(e).lower():
                    pass  # will commit below
                else:
                    raise

            # 2. Check for local changes
            st = status_op.status(repo)
            changes = st.get("changes", [])

            # 3. Auto-commit if changes
            if changes:
                paths = [c["path"] for c in changes[:5]]
                summary = ", ".join(paths)
                if len(changes) > 5:
                    summary += f" (+{len(changes) - 5} more)"
                msg = f"auto: {summary}"

                snap = commit_op.commit(repo, message=msg, who=who)
                if snap:
                    print(f"  [{_ts()}] committed #{snap['id']}: {msg}")

            # 4. Push if unpushed
            st = status_op.status(repo)
            if st.get("unpushed", 0) > 0:
                try:
                    push_result = push_op.push(repo)
                    if push_result["status"] in ("ok", "pushed"):
                        print(f"  [{_ts()}] pushed {push_result.get('pushed', 0)} snapshot(s)")
                        if push_result.get("merged"):
                            print(f"    auto-merged ({push_result.get('conflicts', 0)} conflict(s))")
                except NetworkError:
                    consecutive_errors += 1
                    if consecutive_errors <= 3:
                        print(f"  [{_ts()}] push failed (network), will retry")
                    # don't raise — will retry next cycle

        except NetworkError as e:
            consecutive_errors += 1
            if consecutive_errors <= 3:
                print(f"  [{_ts()}] network error: {e}")
            elif consecutive_errors == 4:
                print(f"  [{_ts()}] suppressing further network errors...")

        except MutError as e:
            print(f"  [{_ts()}] error: {e}", file=sys.stderr)

        except Exception as e:
            print(f"  [{_ts()}] unexpected error: {e}", file=sys.stderr)

        # 5. Sleep
        for _ in range(interval):
            if not _running:
                break
            time.sleep(1)

    print(f"\nmut daemon stopped (ran {cycle} cycle(s))")


def _ts() -> str:
    return time.strftime("%H:%M:%S")
