"""Three-way merge engine with layered conflict resolution.

Strategy stack (tried in order):
  1. Identical — both sides made the same change → trivial
  2. One-side-only — only one side changed → take that side
  3. Line-level merge — both changed different lines → auto-merge
  4. JSON merge — both changed different keys → auto-merge
  5. LWW (Last-Writer-Wins) — take the incoming change, log the loss

Every merge produces a MergeResult with the merged content and
a list of conflict records for audit.
"""

import json
from dataclasses import dataclass, field

from mut.foundation.hash import hash_bytes


@dataclass
class ConflictRecord:
    path: str
    strategy: str           # "line_merge", "json_merge", "lww", etc.
    detail: str = ""
    kept: str = ""          # which side was kept ("ours", "theirs", "merged")
    lost_content: str = ""  # preview of overwritten content (for display)
    lost_hash: str = ""     # hash of full lost content in object store (for recovery)


@dataclass
class MergeResult:
    content: bytes
    conflicts: list = field(default_factory=list)  # list[ConflictRecord]
    strategy: str = "identical"


def three_way_merge(base: bytes, ours: bytes, theirs: bytes,
                    path: str = "") -> MergeResult:
    """Merge two versions against a common base. Never fails — LWW is the fallback."""

    if ours == theirs:
        return MergeResult(content=ours, strategy="identical")

    if base == ours:
        return MergeResult(content=theirs, strategy="theirs_only")

    if base == theirs:
        return MergeResult(content=ours, strategy="ours_only")

    # Both sides changed — try structured merge
    if path.endswith(".json"):
        result = _try_json_merge(base, ours, theirs, path)
        if result is not None:
            return result

    result = _try_line_merge(base, ours, theirs, path)
    if result is not None:
        return result

    # Fallback: Last-Writer-Wins (theirs = incoming push wins)
    ours_hash = hash_bytes(ours)
    ours_preview = ours.decode(errors="replace")[:500]
    return MergeResult(
        content=theirs,
        strategy="lww",
        conflicts=[ConflictRecord(
            path=path,
            strategy="lww",
            detail="both sides modified, theirs (incoming push) wins",
            kept="theirs",
            lost_content=ours_preview,
            lost_hash=ours_hash,
        )],
    )


def _try_line_merge(base: bytes, ours: bytes, theirs: bytes,
                    path: str) -> MergeResult:
    """Line-level three-way merge using LCS-based diff. Returns None on conflict."""
    try:
        base_lines = base.decode().splitlines(keepends=True)
        ours_lines = ours.decode().splitlines(keepends=True)
        theirs_lines = theirs.decode().splitlines(keepends=True)
    except UnicodeDecodeError:
        return None

    our_hunks = _diff_hunks(base_lines, ours_lines)
    their_hunks = _diff_hunks(base_lines, theirs_lines)

    if _hunks_overlap(our_hunks, their_hunks):
        return None

    merged_lines = _apply_hunks(base_lines, our_hunks, their_hunks)

    total_changes = len(our_hunks) + len(their_hunks)
    merged_text = "".join(merged_lines)
    return MergeResult(
        content=merged_text.encode(),
        strategy="line_merge",
        conflicts=[ConflictRecord(
            path=path,
            strategy="line_merge",
            detail=f"auto-merged {total_changes} hunk(s)",
            kept="merged",
        )] if total_changes else [],
    )


def _diff_hunks(old: list, new: list) -> list:
    """Compute edit hunks between old and new using LCS (SequenceMatcher).

    Returns list of (old_start, old_end, new_lines) tuples.
    Each hunk means: replace old[old_start:old_end] with new_lines.
    """
    from difflib import SequenceMatcher
    sm = SequenceMatcher(None, old, new, autojunk=False)
    hunks = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        hunks.append((i1, i2, new[j1:j2]))
    return hunks


def _hunks_overlap(hunks_a: list, hunks_b: list) -> bool:
    """Check if any hunks from A and B touch the same lines in the base."""
    for a_start, a_end, _ in hunks_a:
        for b_start, b_end, _ in hunks_b:
            if a_start < b_end and b_start < a_end:
                return True
    return False


def _apply_hunks(base: list, hunks_a: list, hunks_b: list) -> list:
    """Apply non-overlapping hunks from both sides onto the base.

    Hunks are sorted by position and applied from end to start
    so that indices remain valid.
    """
    all_hunks = sorted(hunks_a + hunks_b, key=lambda h: (h[0], h[1]), reverse=True)
    result = list(base)
    for old_start, old_end, new_lines in all_hunks:
        result[old_start:old_end] = new_lines
    return result


def _try_json_merge(base: bytes, ours: bytes, theirs: bytes,
                    path: str) -> MergeResult:
    """JSON key-level merge. Returns None if it can't parse or conflicts."""
    try:
        base_obj = json.loads(base)
        ours_obj = json.loads(ours)
        theirs_obj = json.loads(theirs)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    if not all(isinstance(o, dict) for o in [base_obj, ours_obj, theirs_obj]):
        return None

    merged, conflicts = _merge_dicts(base_obj, ours_obj, theirs_obj, path)

    return MergeResult(
        content=json.dumps(merged, indent=2, ensure_ascii=False).encode(),
        strategy="json_merge",
        conflicts=conflicts,
    )


def _merge_dicts(base: dict, ours: dict, theirs: dict,
                 path: str) -> tuple:
    """Recursively merge two dicts against a base. Returns (merged_dict, conflicts)."""
    merged = dict(base)
    conflicts = []
    all_keys = set(base) | set(ours) | set(theirs)

    for key in all_keys:
        b_val = base.get(key)
        o_val = ours.get(key)
        t_val = theirs.get(key)

        if o_val == t_val:
            if o_val is None and key in merged:
                del merged[key]
            elif o_val is not None:
                merged[key] = o_val
            continue

        if b_val == o_val:
            if t_val is None:
                merged.pop(key, None)
            else:
                merged[key] = t_val
            continue

        if b_val == t_val:
            if o_val is None:
                merged.pop(key, None)
            else:
                merged[key] = o_val
            continue

        # Both changed this key differently
        if (isinstance(o_val, dict) and isinstance(t_val, dict)
                and isinstance(b_val, dict)):
            sub_merged, sub_conflicts = _merge_dicts(b_val, o_val, t_val,
                                                      f"{path}/{key}")
            merged[key] = sub_merged
            conflicts.extend(sub_conflicts)
        else:
            # LWW at key level: theirs wins
            merged[key] = t_val if t_val is not None else o_val
            lost_val = json.dumps(o_val)
            lost_hash = hash_bytes(lost_val.encode()) if o_val != t_val else ""
            conflicts.append(ConflictRecord(
                path=f"{path}#{key}",
                strategy="json_lww",
                detail=f"both modified key '{key}'",
                kept="theirs",
                lost_content=lost_val[:500],
                lost_hash=lost_hash,
            ))

    return merged, conflicts


def merge_file_sets(base_files: dict, our_files: dict, their_files: dict) -> tuple:
    """Merge two sets of files against a common base.

    Args:
        base_files:  {path: bytes} at the common ancestor version
        our_files:   {path: bytes} currently on server
        their_files: {path: bytes} incoming from agent push

    Returns:
        (merged_files: {path: bytes}, all_conflicts: [ConflictRecord])
    """
    merged = {}
    all_conflicts = []
    all_paths = set(base_files) | set(our_files) | set(their_files)

    for path in sorted(all_paths):
        base = base_files.get(path, b"")
        ours = our_files.get(path)
        theirs = their_files.get(path)

        if ours is None and theirs is None:
            continue  # both deleted

        if ours is None:
            # We deleted, they kept/modified
            if theirs != base:
                merged[path] = theirs
                all_conflicts.append(ConflictRecord(
                    path=path, strategy="delete_modify",
                    detail="ours deleted, theirs modified → keep theirs",
                    kept="theirs",
                ))
            # else: both effectively removed
            continue

        if theirs is None:
            # They deleted, we kept/modified
            if ours != base:
                merged[path] = ours
                all_conflicts.append(ConflictRecord(
                    path=path, strategy="modify_delete",
                    detail="theirs deleted, ours modified → keep ours",
                    kept="ours",
                ))
            # else: both effectively removed
            continue

        result = three_way_merge(base, ours, theirs, path)
        merged[path] = result.content
        all_conflicts.extend(result.conflicts)

    return merged, all_conflicts
