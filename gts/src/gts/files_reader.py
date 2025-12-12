from __future__ import annotations

import json
import yaml
import subprocess
import tempfile
import shutil
from pathlib import Path
import os
from typing import Iterator, List, Optional, Any

from .store import GtsReader
from .entities import GtsEntity, GtsFile, DEFAULT_GTS_CONFIG, GtsConfig

import logging


EXCLUDE_LIST = ["node_modules", "dist", "build"]


class GtsFileReader(GtsReader):
    """Reads GTS entities from JSON, YAML, and TypeSpec files in directories specified by path."""

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
        self._current_file_entities: List[GtsEntity] = []
        self._current_entity_index = 0
        self._initialized = False
        self._tsp_available: Optional[bool] = None

    def _check_tsp_available(self) -> bool:
        """Check if TypeSpec compiler (tsp) is available in the system."""
        if self._tsp_available is not None:
            return self._tsp_available
        
        self._tsp_available = shutil.which("tsp") is not None
        if not self._tsp_available:
            # Also check npx tsp
            try:
                result = subprocess.run(
                    ["npx", "--yes", "@typespec/compiler", "--version"],
                    capture_output=True,
                    timeout=30
                )
                self._tsp_available = result.returncode == 0
            except (subprocess.TimeoutExpired, FileNotFoundError):
                self._tsp_available = False
        
        return self._tsp_available

    def _compile_tsp(self, file_path: Path) -> Optional[Any]:
        """
        Compile TypeSpec file to JSON Schema and return the parsed content.
        
        Args:
            file_path: Path to .tsp file
            
        Returns:
            Parsed JSON Schema content or None if compilation fails
        """
        if not self._check_tsp_available():
            logging.warning(f"TypeSpec compiler not available, skipping: {file_path}")
            return None
        
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                output_dir = Path(tmpdir) / "output"
                output_dir.mkdir()
                
                # Try using tsp directly, fall back to npx
                tsp_cmd = ["tsp"] if shutil.which("tsp") else ["npx", "--yes", "@typespec/compiler"]
                
                cmd = [
                    *tsp_cmd,
                    "compile",
                    str(file_path),
                    "--emit", "@typespec/json-schema",
                    "--output-dir", str(output_dir)
                ]
                
                logging.debug(f"Compiling TypeSpec: {' '.join(cmd)}")
                
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    cwd=file_path.parent
                )
                
                if result.returncode != 0:
                    logging.warning(f"TypeSpec compilation failed for {file_path}: {result.stderr}")
                    return None
                
                # Find generated JSON Schema files
                schema_files = list(output_dir.rglob("*.json"))
                if not schema_files:
                    logging.warning(f"No JSON Schema generated for {file_path}")
                    return None
                
                # If multiple schemas generated, return as array
                schemas = []
                for schema_file in schema_files:
                    with schema_file.open("r", encoding="utf-8") as f:
                        schemas.append(json.load(f))
                
                return schemas if len(schemas) > 1 else schemas[0] if schemas else None
                
        except subprocess.TimeoutExpired:
            logging.warning(f"TypeSpec compilation timed out for {file_path}")
            return None
        except Exception as e:
            logging.warning(f"Error compiling TypeSpec {file_path}: {e}")
            return None

    def _collect_files(self) -> None:
        """Collect all JSON, YAML, and TypeSpec files from the specified paths, following symlinks."""
        valid_extensions = {'.json', '.jsonc', '.gts', '.yaml', '.yml', '.tsp'}
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

    def _load_file(self, file_path: Path) -> Any:
        """Load content from JSON, YAML, or TypeSpec file."""
        suffix = file_path.suffix.lower()
        
        if suffix == '.tsp':
            return self._compile_tsp(file_path)
        elif suffix in {'.yaml', '.yml'}:
            with file_path.open("r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        else:
            with file_path.open("r", encoding="utf-8") as f:
                return json.load(f)

    def _process_file(self, file_path: Path) -> List[GtsEntity]:
        """Process a single JSON, YAML, or TypeSpec file and return list of GtsEntity objects."""
        entities: List[GtsEntity] = []

        try:
            content = self._load_file(file_path)
            if content is None:
                return entities
                
            json_file = GtsFile(
                path=str(file_path),
                name=file_path.name,
                content=content
            )

            # Handle both single objects and arrays
            if isinstance(content, list):
                for idx, item in enumerate(content):
                    entity = GtsEntity(
                        file=json_file,
                        list_sequence=idx,
                        content=item,
                        cfg=self.cfg
                    )
                    if entity.gts_id:
                        logging.debug(f"- discovered entity: {entity.gts_id.id}")
                        entities.append(entity)
            else:
                entity = GtsEntity(
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

    def __iter__(self) -> Iterator[GtsEntity]:
        """Iterate over all GtsEntity objects from all files."""
        if not self._initialized:
            self._collect_files()
            self._initialized = True

        logging.debug(f"Processing {len(self._files)} files from {self.paths}")
        for file_path in self._files:
            entities = self._process_file(file_path)
            for entity in entities:
                yield entity

    def read_by_id(self, entity_id: str) -> Optional[GtsEntity]:
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
