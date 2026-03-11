"""Ignore-pattern handling for .mutignore and built-in patterns."""

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

    def should_ignore(self, name: str) -> bool:
        if self._patterns is None:
            self._patterns = self._load()
        return name in self._patterns
