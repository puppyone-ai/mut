"""mut connect — Connect an existing local folder to a PuppyOne Access Point.

This is the **local-first** counterpart to ``mut clone``:

* ``mut clone <url>``  — server already has content, download it into a *new* dir.
* ``mut connect <url>`` — *this folder* already has content, bind it to an AP
  and upload the existing files (server merges, never overwrites silently).

A single ``mut connect`` is equivalent to:

    mut init                       # idempotent
    mut link access <url> ...      # write config + verify connection
    mut commit -m "..."            # if workdir has untracked changes
    mut push                       # if there are unpushed snapshots

Designed for the very common onboarding case where a user has already created
an Access Point in PuppyOne (cloud-side) and now wants to attach an existing
local directory — without manually wiring up the four steps above.
"""

from __future__ import annotations

from pathlib import Path

from mut.foundation.error import MutError
from mut.ops import commit_op, init_op, link_access_op, push_op
from mut.ops.repo import MutRepo


DEFAULT_CONNECT_MESSAGE = "connect: import existing folder"
DEFAULT_CONNECT_AUTHOR = "mut-connect"


def _has_user_content(repo: MutRepo) -> bool:
    """Return True iff *repo*'s workdir holds any non-ignored file.

    We deliberately use the same ignore rules as commit, so a workdir whose
    only entries are ``.mut/`` and other ignored files is treated as empty.
    Without this guard, calling ``mut connect`` on an empty folder would
    auto-commit an empty Merkle tree and push it — at best wasteful, at
    worst racy if the server already has content (the empty subtree could
    be grafted on top of real data via LWW merge).
    """
    workdir = Path(repo.workdir)
    for entry in workdir.rglob("*"):
        if not entry.is_file():
            continue
        rel = entry.relative_to(workdir).as_posix()
        if rel.startswith(".mut/") or rel == ".mut":
            continue
        if repo.ignore.should_ignore(entry.name, rel):
            continue
        return True
    return False


def connect(
    access_point_url: str,
    credential: str | None = None,
    workdir: str = ".",
    message: str = DEFAULT_CONNECT_MESSAGE,
    who: str = DEFAULT_CONNECT_AUTHOR,
) -> dict:
    """Connect ``workdir`` to *access_point_url* and perform the first sync.

    Steps (each is idempotent / fail-loud):

    1. ``init`` — create ``.mut/`` if missing (preserves existing repo state).
    2. ``link_access`` — write ``server`` + ``credential`` into config and
       reach the server once to confirm the AP is alive.
    3. ``commit`` — snapshot any untracked / modified files under a single
       "import existing folder" commit.  No-op if workdir is clean.
    4. ``push`` — upload the new snapshot(s).  The server runs its standard
       three-way merge; nothing is silently overwritten.

    Args:
        access_point_url: Full URL to the access point, e.g.
            ``https://api.puppyone.ai/api/v1/mut/ap/{access_key}``.
        credential: Explicit access key. If ``None``, the key is parsed from
            the URL (``…/ap/{access_key}``).
        workdir: Local directory to attach (default: current working dir).
        message: Commit message used when there is content to import.
        who: Author recorded on the auto-import commit.

    Returns:
        Status dict with the following keys::

            {
                "status": "connected",
                "server": "<url>",
                "server_commit_id": "<commit-id>",
                "initialized": True | False,   # whether .mut/ was created now
                "imported": True | False,      # whether we auto-committed
                "snapshot_id": int | None,     # id of the import commit
                "pushed": int,                 # number of snapshots pushed
            }

    Raises:
        MutError: if the server cannot be reached or the push is rejected.
    """
    workdir_path = Path(workdir).resolve()
    workdir_path.mkdir(parents=True, exist_ok=True)

    mut_dir = workdir_path / ".mut"
    was_existing_repo = mut_dir.exists()

    # 1. mut init (idempotent)
    repo = init_op.init(str(workdir_path))

    # 2. mut link access (writes config + verifies connection)
    link_result = link_access_op.link_access(
        repo,
        access_point_url=access_point_url,
        root_dir_name=None,
        credential_override=credential,
    )

    # Reload — link_access_op writes to disk, refresh repo handles
    repo = MutRepo(str(workdir_path))

    # 3. mut commit — only when workdir actually holds non-ignored content.
    # Skipping the commit on truly empty workdirs prevents an empty Merkle
    # tree from being pushed and racing with whatever the server already has.
    snap = None
    if _has_user_content(repo):
        snap = commit_op.commit(repo, message=message, who=who)
    imported = snap is not None

    # 4. mut push (idempotent: emits no-op if there is nothing unpushed)
    push_result: dict | None = None
    if repo.snapshots.get_unpushed():
        try:
            push_result = push_op.push(repo)
        except Exception as e:
            raise MutError(f"connect: push failed — {e}") from e

    final_commit_id = (
        (push_result or {}).get("server_commit_id")
        or link_result.get("server_commit_id", "")
    )

    return {
        "status": "connected",
        "server": access_point_url,
        "server_commit_id": final_commit_id,
        "initialized": not was_existing_repo,
        "imported": imported,
        "snapshot_id": snap["id"] if snap else None,
        "pushed": (push_result or {}).get("pushed", 0),
    }


__all__ = ["connect", "DEFAULT_CONNECT_MESSAGE", "DEFAULT_CONNECT_AUTHOR"]
