"""Ignore-pattern handling for .mutignore and built-in patterns."""

import fnmatch
from pathlib import Path

from mut.foundation.config import BUILTIN_IGNORE, IGNORE_FILE


class IgnoreRules:

    def __init__(self, workdir: Path):
        self._patterns = None
        self._workdir = workdir

    def _load(self) -> set[str]:
        patterns = set(BUILTIN_IGNORE)
        ignore_file = self._workdir / IGNORE_FILE
        if ignore_file.exists():
            for line in ignore_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.add(line)
        return patterns

    def should_ignore(self, name: str, rel_path: str = "") -> bool:
        """Check if a file/directory name should be ignored.

        Supports:
        - Exact name match: ``node_modules``
        - Glob patterns via fnmatch: ``*.pyc``, ``*.log``
        - Path patterns: ``build/`` matches any path containing ``build/``
        - Directory-only trailing slash patterns: ``dist/``
        """
        if self._patterns is None:
            self._patterns = self._load()

        for pattern in self._patterns:
            # Exact match (most common for builtins)
            if name == pattern:
                return True
            # Directory-only pattern with trailing /
            if pattern.endswith("/"):
                dir_name = pattern.rstrip("/")
                if name == dir_name:
                    return True
                # Path contains the directory
                if rel_path and (f"/{dir_name}/" in f"/{rel_path}/"):
                    return True
            # Glob matching (e.g. *.pyc, *.log)
            if fnmatch.fnmatch(name, pattern):
                return True
        return False
