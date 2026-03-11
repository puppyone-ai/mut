"""Manifest: a flat {filename: hash} map for fast local status checks."""

from pathlib import Path

from mut.foundation.fs import read_json, write_json
from mut.foundation.hash import hash_file
from mut.foundation.config import MANIFEST_FILE
from mut.core.ignore import IgnoreRules


def generate(workdir: Path, ignore: IgnoreRules, prefix: str = "") -> dict:
    """Scan a real directory and return {relative_path: sha256_hash}."""
    result = {}
    for child in sorted(workdir.iterdir()):
        if ignore.should_ignore(child.name):
            continue
        rel = f"{prefix}/{child.name}" if prefix else child.name
        if child.is_file():
            result[rel] = hash_file(child)
        elif child.is_dir():
            result.update(generate(child, ignore, rel))
    return result


def load(mut_root: Path) -> dict:
    path = mut_root / MANIFEST_FILE
    if not path.exists():
        return {}
    return read_json(path)


def save(mut_root: Path, manifest: dict):
    write_json(mut_root / MANIFEST_FILE, manifest)
