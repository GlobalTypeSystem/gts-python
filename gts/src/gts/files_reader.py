from __future__ import annotations

import json
from pathlib import Path
import os
from typing import Iterator, List, Optional, Any

from .store import GtsReader
from .entities import JsonEntity, JsonFile, DEFAULT_GTS_CONFIG, GtsConfig

import logging


EXCLUDE_LIST = ["node_modules", "dist", "build"]


class GtsFileReader(GtsReader):
    """Reads JSON entities from files and directories specified by path."""

    def __init__(self, path: str | List[str], cfg: Optional[GtsConfig] = None) -> None:
        """
        Initialize FileReader with one or more paths.

        Args:
            path: Single path string or list of paths (files or directories)
            cfg: GtsConfig for entity ID extraction (defaults to DEFAULT_GTS_CONFIG)
        """
        self.paths: List[Path] = []
        if isinstance(path, str):
            self.paths = [Path(os.path.expanduser(path))]
        else:
            self.paths = [Path(os.path.expanduser(p)) for p in path]

        self.cfg = cfg or DEFAULT_GTS_CONFIG
        self._files: List[Path] = []
        self._current_index = 0
        self._current_file_entities: List[JsonEntity] = []
        self._current_entity_index = 0
        self._initialized = False

    def _collect_files(self) -> None:
        """Collect all JSON files from the specified paths, following symlinks."""
        valid_extensions = {'.json', '.jsonc', '.gts'}
        seen: set[str] = set()
        collected: List[Path] = []

        for path in self.paths:
            # Resolve symlinks and make absolute (non-strict to allow non-existing paths to be handled gracefully)
            resolved_path = path.expanduser().resolve(strict=False)

            if resolved_path.is_file():
                if resolved_path.suffix.lower() in valid_extensions:
                    rp = str(resolved_path)
                    if rp not in seen:
                        seen.add(rp)
                        logging.debug(f"- discovered file: {resolved_path}")
                        collected.append(resolved_path)
            elif resolved_path.is_dir():
                # Recursively scan for all valid file types, following symlinks
                for root, dirs, files in os.walk(resolved_path, followlinks=True):
                    for exclude in EXCLUDE_LIST:
                        if exclude in dirs:
                            dirs.remove(exclude)
                    for fname in files:
                        ext = os.path.splitext(fname)[1].lower()
                        if ext in valid_extensions:
                            fpath = Path(root) / fname
                            rp = str(fpath.resolve(strict=False))
                            if rp not in seen:
                                seen.add(rp)
                                logging.debug(f"- discovered file: {fpath}")
                                collected.append(Path(rp))

        self._files = collected

    def _load_json_file(self, file_path: Path) -> Any:
        """Load JSON content from a file."""
        with file_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _process_file(self, file_path: Path) -> List[JsonEntity]:
        """Process a single JSON file and return list of JsonEntity objects."""
        entities: List[JsonEntity] = []

        try:
            content = self._load_json_file(file_path)
            json_file = JsonFile(
                path=str(file_path),
                name=file_path.name,
                content=content
            )

            # Handle both single objects and arrays
            if isinstance(content, list):
                for idx, item in enumerate(content):
                    entity = JsonEntity(
                        file=json_file,
                        list_sequence=idx,
                        content=item,
                        cfg=self.cfg
                    )
                    if entity.gts_id:
                        logging.debug(f"- discovered entity: {entity.gts_id.id}")
                        entities.append(entity)
            else:
                entity = JsonEntity(
                    file=json_file,
                    list_sequence=None,
                    content=content,
                    cfg=self.cfg
                )
                if entity.gts_id:
                    logging.debug(f"- discovered entity: {entity.gts_id.id}")
                    entities.append(entity)
        except Exception:
            # Skip files that can't be parsed
            pass

        return entities

    def __iter__(self) -> Iterator[JsonEntity]:
        """Iterate over all JsonEntity objects from all files."""
        if not self._initialized:
            self._collect_files()
            self._initialized = True

        logging.debug(f"Processing {len(self._files)} files from {self.paths}")
        for file_path in self._files:
            entities = self._process_file(file_path)
            for entity in entities:
                yield entity

    def read_by_id(self, entity_id: str) -> Optional[JsonEntity]:
        """
        Read a JsonEntity by its ID.
        For FileReader, this returns None as we don't support random access by ID.
        """
        return None

    def reset(self) -> None:
        """Reset the iterator to start from the beginning."""
        self._current_index = 0
        self._current_file_entities = []
        self._current_entity_index = 0
        self._initialized = False
