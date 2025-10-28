from __future__ import annotations

import argparse
import logging
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .gts import GtsID, GtsWildcard
from .store import (
    GtsStore,
)
from .entities import DEFAULT_GTS_CONFIG, GtsConfig
from .path_resolver import JsonPathResolver
from .files_reader import GtsFileReader
from pathlib import Path as SysPath


def _load_config(config_path: Optional[str]) -> GtsConfig:
    """Load GTS config from a JSON file or return default if missing."""
    if config_path:
        p = SysPath(config_path).expanduser()
        try:
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return GtsConfig(
                entity_id_fields=list(data.get("entity_id_fields", DEFAULT_GTS_CONFIG.entity_id_fields)),
                schema_id_fields=list(data.get("schema_id_fields", DEFAULT_GTS_CONFIG.schema_id_fields)),
            )
        except Exception:
            pass
    # Try package default gts/gts.config.json
    try:
        default_path = SysPath(__file__).resolve().parents[2] / "gts.config.json"
        with default_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return GtsConfig(
            entity_id_fields=list(data.get("entity_id_fields", DEFAULT_GTS_CONFIG.entity_id_fields)),
            schema_id_fields=list(data.get("schema_id_fields", DEFAULT_GTS_CONFIG.schema_id_fields)),
        )
    except Exception:
        return DEFAULT_GTS_CONFIG


def load_json_file(p: Path) -> Any:
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def iter_json_files(path: Path) -> List[Path]:
    if path.is_file():
        return [path]
    files: List[Path] = []
    for fp in path.rglob("*.json"):
        files.append(fp)
    return files


def op_validate_id(args: argparse.Namespace) -> Any:
    try:
        _ = GtsID(args.gts_id)
        return {"id": args.gts_id, "valid": True, "error": ""}
    except Exception as e:
        return {"id": args.gts_id, "valid": False, "error": str(e)}


def op_parse(args: argparse.Namespace) -> Any:
    ret = {"id": args.gts_id}
    try:
        segs = GtsID(args.gts_id).gts_id_segments
    except Exception as e:
        ret["error"] = str(e)
    else:
        ret["segments"] = [
            {
                "vendor": s.vendor,
                "package": s.package,
                "namespace": s.namespace,
                "type": s.type,
                "ver_major": s.ver_major,
                "ver_minor": s.ver_minor,
                "is_type": s.is_type,
            }
            for s in segs
        ]
    return ret


def op_wildcard_match(args: argparse.Namespace) -> Any:
    ret = {"candidate": args.candidate, "pattern": args.pattern, "match": False}

    try:
        candidate = GtsID(args.candidate)
    except Exception as e:
        ret["error"] = str(e)
    else:
        try:
            pattern = GtsWildcard(args.pattern)
        except Exception as e:
            ret["error"] = str(e)
        else:
            ret["match"] = candidate.wildcard_match(pattern)
    return ret


def op_uuid(args: argparse.Namespace) -> Any:
    g = GtsID(args.gts_id)
    return {"id": g.id, "uuid": str(g.to_uuid())}


def op_validate_instance(args: argparse.Namespace) -> Any:
    # Load schemas from path
    cfg = _load_config(getattr(args, "config", None))
    reader = GtsFileReader(args.path, cfg=cfg)
    store = GtsStore(reader)
    ret = {"id": args.gts_id, "ok": True}

    # Validate
    try:
        store.validate_instance(args.gts_id)
    except Exception as e:
        ret["ok"] = False
        ret["error"] = str(e)

    return ret


def op_schema_graph(args: argparse.Namespace) -> Any:
    # Load schemas from path
    cfg = _load_config(getattr(args, "config", None))
    reader = GtsFileReader(args.path, cfg=cfg)
    store = GtsStore(reader)

    # Build graph and detect cycles
    graph = store.build_schema_graph(args.gts_id)
    return graph


def op_compatibility(args: argparse.Namespace) -> Any:
    # Load schemas from path
    cfg = _load_config(getattr(args, "config", None))
    reader = GtsFileReader(args.path, cfg=cfg)
    store = GtsStore(reader)

    # Check compatibility
    return store.cast(args.old_schema_id, args.new_schema_id).to_dict()


def op_cast(args: argparse.Namespace) -> Any:
    # Load schemas from path
    cfg = _load_config(getattr(args, "config", None))
    reader = GtsFileReader(args.path, cfg=cfg)
    store = GtsStore(reader)
    try:
        return store.cast(args.instance_id, args.to_schema_id).to_dict()
    except Exception as e:
        return {"error": str(e)}


def op_query(args: argparse.Namespace) -> Any:
    # Load entities from path and delegate to store.query
    cfg = _load_config(getattr(args, "config", None))
    reader = GtsFileReader(args.path, cfg=cfg)
    store = GtsStore(reader)
    return store.query(args.query)


def op_attr(args: argparse.Namespace) -> Any:
    # Load entities from path
    cfg = _load_config(getattr(args, "config", None))
    reader = GtsFileReader(args.path, cfg=cfg)
    store = GtsStore(reader)

    # Parse the gts_with_path
    gts, path = GtsID.split_at_path(args.gts_with_path)
    if path is None:
        raise SystemExit("Attribute selector requires '@path' in the identifier")

    # Get the entity
    entity = store.get(gts)
    if not entity:
        return JsonPathResolver(gts_id=gts, content=None).failure(path, f"Entity not found: {gts}").to_dict()

    # Resolve path via JsonEntityPathResolver
    return entity.resolve_path(path).to_dict()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gts", description="GTS helpers CLI (demo)")
    p.add_argument("--verbose", "-v", action="count", default=0)
    p.add_argument("--config", help="Path to optional GTS config JSON to override defaults")
    sub = p.add_subparsers(dest="op", required=True)

    s = sub.add_parser("validate-id")
    s.add_argument("--gts-id", required=True)

    s = sub.add_parser("parse")
    s.add_argument("--gts-id", required=True)

    s = sub.add_parser("wildcard-match")
    s.add_argument("--pattern", required=True)
    s.add_argument("--candidate", required=True)

    s = sub.add_parser("uuid")
    s.add_argument("--gts-id", required=True)
    s.add_argument("--scope", choices=["major", "full"], default="major")

    s = sub.add_parser("validate-instance")
    s.add_argument("--path", required=True, help="Path to json and schema files or directories")
    s.add_argument("--gts-id", required=True, help="GTS ID of the object")

    s = sub.add_parser("schema-graph")
    s.add_argument("--path", required=True, help="Path to json and schema files or directories")
    s.add_argument("--gts-id", required=True, help="GTS ID of the entity")

    s = sub.add_parser("compatibility")
    s.add_argument("--path", required=True, help="Path to json and schema files or directories")
    s.add_argument("--old-schema-id", required=True, help="GTS ID of old schema")
    s.add_argument("--new-schema-id", required=True, help="GTS ID of new schema")

    s = sub.add_parser("cast")
    s.add_argument("--path", required=True, help="Path to json and schema files or directories")
    s.add_argument("--instance-id", required=True, help="GTS ID of instance to be casted to a schema")
    s.add_argument("--to-schema-id", required=True, help="GTS ID of target schema")

    s = sub.add_parser("query")
    s.add_argument("--path", required=True, help="Path to json and schema files or directories")
    s.add_argument("--query", required=True, help="Query expression")

    s = sub.add_parser("attr")
    s.add_argument("--path", required=True, help="Path to json and schema files or directories")
    s.add_argument("--gts-with-path", required=True, help="GTS ID with attribute path (e.g., gts.a.b.c.d.v1~@field.subfield)")

    return p


def main(argv: List[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.verbose == 0 else (logging.DEBUG if args.verbose >= 2 else logging.INFO),
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    try:
        if args.op == "validate-id":
            out = op_validate_id(args)
        elif args.op == "parse":
            out = op_parse(args)
        elif args.op == "wildcard-match":
            out = op_wildcard_match(args)
        elif args.op == "uuid":
            out = op_uuid(args)
        elif args.op == "validate-instance":
            out = op_validate_instance(args)
        elif args.op == "schema-graph":
            out = op_schema_graph(args)
        elif args.op == "compatibility":
            out = op_compatibility(args)
        elif args.op == "cast":
            out = op_cast(args)
        elif args.op == "query":
            out = op_query(args)
        elif args.op == "attr":
            out = op_attr(args)
        else:
            raise SystemExit("Unknown op")
        json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        raise


if __name__ == "__main__":
    main()
