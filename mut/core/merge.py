"""Three-way merge engine with pluggable conflict resolution strategies.

Strategy stack (tried in order by DefaultResolver):
  1. Identical — both sides made the same change → trivial
  2. One-side-only — only one side changed → take that side
  3. Line-level merge — both changed different lines → auto-merge
  4. JSON merge — both changed different keys → auto-merge
  5. LWW (Last-Writer-Wins) — take the incoming change, log the loss

The strategy chain is configurable: create a ConflictResolver with a
custom list of MergeStrategy instances to change the order or inject
domain-specific strategies (e.g. LLM-assisted merge).
"""

from __future__ import annotations

import abc
import json
from dataclasses import dataclass, field

from mut.foundation.hash import hash_bytes


# ── Data types ─────────────────────────────────

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
    conflicts: list[ConflictRecord] = field(default_factory=list)
    strategy: str = "identical"


# ── Strategy interface ─────────────────────────

class MergeStrategy(abc.ABC):
    """A single merge strategy. Returns MergeResult or None to pass."""

    name: str = "base"

    @abc.abstractmethod
    def try_merge(self, base: bytes, ours: bytes, theirs: bytes,
                  path: str) -> MergeResult | None:
        """Attempt to merge. Return None to defer to the next strategy."""


# ── Built-in strategies ───────────────────────

class IdenticalStrategy(MergeStrategy):
    name = "identical"

    def try_merge(self, base: bytes, ours: bytes, theirs: bytes,
                  path: str) -> MergeResult | None:
        if ours == theirs:
            return MergeResult(content=ours, strategy="identical")
        return None


class OneSideOnlyStrategy(MergeStrategy):
    name = "one_side_only"

    def try_merge(self, base: bytes, ours: bytes, theirs: bytes,
                  path: str) -> MergeResult | None:
        if base == ours:
            return MergeResult(content=theirs, strategy="theirs_only")
        if base == theirs:
            return MergeResult(content=ours, strategy="ours_only")
        return None


class LineMergeStrategy(MergeStrategy):
    name = "line_merge"

    def try_merge(self, base: bytes, ours: bytes, theirs: bytes,
                  path: str) -> MergeResult | None:
        return _try_line_merge(base, ours, theirs, path)


class JsonMergeStrategy(MergeStrategy):
    name = "json_merge"

    def try_merge(self, base: bytes, ours: bytes, theirs: bytes,
                  path: str) -> MergeResult | None:
        if path.endswith(".json"):
            return _try_json_merge(base, ours, theirs, path)
        return None


class LWWStrategy(MergeStrategy):
    """Last-Writer-Wins fallback — incoming push always wins."""
    name = "lww"

    def try_merge(self, base: bytes, ours: bytes, theirs: bytes,
                  path: str) -> MergeResult | None:
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


# ── Default strategy order ─────────────────────
DEFAULT_STRATEGIES: list[MergeStrategy] = [
    IdenticalStrategy(),
    OneSideOnlyStrategy(),
    JsonMergeStrategy(),
    LineMergeStrategy(),
    LWWStrategy(),
]


# ── Resolver (strategy chain) ─────────────────

class ConflictResolver:
    """Runs a chain of MergeStrategy instances until one succeeds.

    The default chain matches the original five-layer strategy.
    Pass a custom strategies list to change the order or inject
    domain-specific strategies.
    """

    def __init__(self, strategies: list[MergeStrategy] | None = None):
        self.strategies = strategies if strategies is not None else list(DEFAULT_STRATEGIES)

    def resolve(self, base: bytes, ours: bytes, theirs: bytes,
                path: str = "") -> MergeResult:
        for strategy in self.strategies:
            result = strategy.try_merge(base, ours, theirs, path)
            if result is not None:
                return result
        # Should never reach here because LWW always succeeds,
        # but guard against misconfigured chains.
        return LWWStrategy().try_merge(base, ours, theirs, path)


# Module-level default resolver
_default_resolver = ConflictResolver()


# ── Public API (backward-compatible) ──────────

def three_way_merge(base: bytes, ours: bytes, theirs: bytes,
                    path: str = "",
                    resolver: ConflictResolver | None = None) -> MergeResult:
    """Merge two versions against a common base. Never fails — LWW is the fallback."""
    r = resolver or _default_resolver
    return r.resolve(base, ours, theirs, path)


def merge_file_sets(base_files: dict, our_files: dict, their_files: dict,
                    resolver: ConflictResolver | None = None) -> tuple:
    """Merge two sets of files against a common base.

    Args:
        base_files:  {path: bytes} at the common ancestor version
        our_files:   {path: bytes} currently on server
        their_files: {path: bytes} incoming from agent push
        resolver:    optional custom ConflictResolver

    Returns:
        (merged_files: {path: bytes}, all_conflicts: [ConflictRecord])
    """
    merged: dict[str, bytes] = {}
    all_conflicts: list[ConflictRecord] = []
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

        result = three_way_merge(base, ours, theirs, path, resolver)
        merged[path] = result.content
        all_conflicts.extend(result.conflicts)

    return merged, all_conflicts


# ── Private helpers (line / JSON merge) ────────

def _try_line_merge(base: bytes, ours: bytes, theirs: bytes,
                    path: str) -> MergeResult | None:
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
                    path: str) -> MergeResult | None:
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
    conflicts: list[ConflictRecord] = []

    for key in set(base) | set(ours) | set(theirs):
        b_val, o_val, t_val = base.get(key), ours.get(key), theirs.get(key)
        action, conflict = _merge_key(b_val, o_val, t_val, key, path)

        if action == "delete":
            merged.pop(key, None)
        elif action is not None:
            merged[key] = action

        if conflict is not None:
            conflicts.extend(conflict) if isinstance(conflict, list) else conflicts.append(conflict)

    return merged, conflicts


def _merge_key(b_val, o_val, t_val, key: str, path: str):
    """Resolve a single key in a dict merge.

    Returns (value_or_action, conflict_or_none).
    value_or_action: the merged value, "delete" to remove the key, or None for no-op.
    """
    # Both sides agree
    if o_val == t_val:
        if o_val is None:
            return "delete", None
        return o_val, None

    # Only theirs changed
    if b_val == o_val:
        return ("delete" if t_val is None else t_val), None

    # Only ours changed
    if b_val == t_val:
        return ("delete" if o_val is None else o_val), None

    # Both changed — try recursive dict merge
    if all(isinstance(v, dict) for v in (b_val, o_val, t_val)):
        sub_merged, sub_conflicts = _merge_dicts(b_val, o_val, t_val, f"{path}/{key}")
        return sub_merged, sub_conflicts

    # LWW at key level: theirs wins
    winner = t_val if t_val is not None else o_val
    lost_val = json.dumps(o_val)
    conflict = ConflictRecord(
        path=f"{path}#{key}",
        strategy="json_lww",
        detail=f"both modified key '{key}'",
        kept="theirs",
        lost_content=lost_val[:500],
        lost_hash=hash_bytes(lost_val.encode()) if o_val != t_val else "",
    )
    return winner, conflict
