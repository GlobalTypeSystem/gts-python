from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from .entities import DEFAULT_GTS_CONFIG, GtsConfig, GtsEntity, GtsFile
from .store import GtsReader

# Default directory names skipped during recursive scanning. The CLI --exclude
# option overrides this per invocation.
DEFAULT_EXCLUDE_LIST = ["node_modules", "dist", "build", ".git", "target"]

logger = logging.getLogger(__name__)


class GtsFileReader(GtsReader):
    """Reads GTS entities from JSON and YAML files in directories specified by path."""

    def __init__(
        self,
        path: str | list[str],
        cfg: GtsConfig | None = None,
        exclude: list[str] | None = None,
    ) -> None:
        """
        Initialize FileReader with one or more paths.

        Args:
            path: Single path string or list of paths (files or directories)
            cfg: GtsConfig for entity ID extraction (defaults to DEFAULT_GTS_CONFIG)
            exclude: Directory names to skip while scanning (defaults to
                DEFAULT_EXCLUDE_LIST)
        """
        self.paths: list[Path] = []
        if isinstance(path, str):
            self.paths = [Path(os.path.expanduser(path))]
        else:
            self.paths = [Path(os.path.expanduser(p)) for p in path]

        self.cfg = cfg or DEFAULT_GTS_CONFIG
        self.exclude = list(exclude) if exclude else list(DEFAULT_EXCLUDE_LIST)
        self._files: list[Path] = []
        self._current_index = 0
        self._current_file_entities: list[GtsEntity] = []
        self._current_entity_index = 0
        self._initialized = False

    def _collect_files(self) -> None:
        """Collect all JSON and YAML files from the specified paths, following symlinks."""
        valid_extensions = {".json", ".jsonc", ".gts", ".yaml", ".yml"}
        seen: set[str] = set()
        seen_dirs: set[tuple[int, int]] = set()
        collected: list[Path] = []

        for path in self.paths:
            # Resolve symlinks and make absolute (non-strict to allow non-existing paths to be handled gracefully)
            resolved_path = path.expanduser().resolve(strict=False)

            if resolved_path.is_file():
                if resolved_path.suffix.lower() in valid_extensions:
                    rp = str(resolved_path)
                    if rp not in seen:
                        seen.add(rp)
                        logger.debug(f"- discovered file: {resolved_path}")
                        collected.append(resolved_path)
            elif resolved_path.is_dir():
                # Recursively scan for all valid file types, following symlinks
                for root, dirs, files in os.walk(resolved_path, followlinks=True):
                    # Prevent symlink cycles by tracking visited directory identities
                    root_stat = os.stat(root)
                    dir_id = (root_stat.st_dev, root_stat.st_ino)
                    if dir_id in seen_dirs:
                        dirs.clear()
                        continue
                    seen_dirs.add(dir_id)

                    dirs[:] = [d for d in dirs if d not in self.exclude]
                    for fname in files:
                        ext = os.path.splitext(fname)[1].lower()
                        if ext in valid_extensions:
                            fpath = Path(root) / fname
                            rp = str(fpath.resolve(strict=False))
                            if rp not in seen:
                                seen.add(rp)
                                logger.debug(f"- discovered file: {fpath}")
                                collected.append(Path(rp))

        self._files = collected

    def _load_file(self, file_path: Path) -> Any:
        """Load content from JSON or YAML file."""
        with file_path.open("r", encoding="utf-8") as f:
            if file_path.suffix.lower() in {".yaml", ".yml"}:
                return yaml.safe_load(f)
            else:
                return json.load(f)

    def _process_file(self, file_path: Path) -> list[GtsEntity]:
        """Process a single JSON or YAML file and return list of GtsEntity objects."""
        entities: list[GtsEntity] = []

        try:
            content = self._load_file(file_path)
            json_file = GtsFile(
                path=str(file_path), name=file_path.name, content=content
            )

            # Handle both single objects and arrays
            if isinstance(content, list):
                for idx, item in enumerate(content):
                    entity = GtsEntity(
                        file=json_file, list_sequence=idx, content=item, cfg=self.cfg
                    )
                    if entity.gts_id:
                        logger.debug(f"- discovered entity: {entity.gts_id.id}")
                        entities.append(entity)
            else:
                entity = GtsEntity(
                    file=json_file, list_sequence=None, content=content, cfg=self.cfg
                )
                if entity.gts_id:
                    logger.debug(f"- discovered entity: {entity.gts_id.id}")
                    entities.append(entity)
        except Exception:
            logger.debug("skipping unparsable file: %s", file_path, exc_info=True)

        return entities

    def __iter__(self) -> Iterator[GtsEntity]:
        """Iterate over all GtsEntity objects from all files."""
        if not self._initialized:
            self._collect_files()
            self._initialized = True

        logger.debug(f"Processing {len(self._files)} files from {self.paths}")
        for file_path in self._files:
            yield from self._process_file(file_path)

    def read_by_id(self, entity_id: str) -> GtsEntity | None:
        """
        Read a GtsEntity by its ID.
        For FileReader, this returns None as we don't support random access by ID.
        """
        return None

    def reset(self) -> None:
        """Reset the iterator to start from the beginning."""
        self._current_index = 0
        self._current_file_entities = []
        self._current_entity_index = 0
        self._initialized = False
