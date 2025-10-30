from __future__ import annotations

import argparse
import logging
import json
import sys
from typing import Any, List

from .ops import GtsOps
from .server import GtsHttpServer


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gts", description="GTS helpers CLI (demo)")
    p.add_argument("--verbose", "-v", action="count", default=0)
    p.add_argument("--config", help="Path to optional GTS config JSON to override defaults")
    p.add_argument("--path", help="Path to json and schema files or directories (global default)")
    sub = p.add_subparsers(dest="op", required=True)

    s = sub.add_parser("validate-id", help="Validate a GTS ID format")
    s.add_argument("--gts-id", required=True)

    s = sub.add_parser("parse-id", help="Parse a GTS ID into its components")
    s.add_argument("--gts-id", required=True)

    s = sub.add_parser("match-id-pattern", help="Match a GTS ID against a pattern")
    s.add_argument("--pattern", required=True)
    s.add_argument("--candidate", required=True)

    s = sub.add_parser("uuid", help="Generate UUID from a GTS ID")
    s.add_argument("--gts-id", required=True)
    s.add_argument("--scope", choices=["major", "full"], default="major")

    s = sub.add_parser("validate-instance", help="Validate an instance against its schema")
    s.add_argument("--gts-id", required=True, help="GTS ID of the object")

    s = sub.add_parser("resolve-relationships", help="Resolve relationships for an entity")
    s.add_argument("--gts-id", required=True, help="GTS ID of the entity")

    s = sub.add_parser("compatibility", help="Check compatibility between two schemas")
    s.add_argument("--old-schema-id", required=True, help="GTS ID of old schema")
    s.add_argument("--new-schema-id", required=True, help="GTS ID of new schema")

    s = sub.add_parser("cast", help="Cast an instance or schema to a target schema")
    s.add_argument("--from-id", required=True, help="GTS ID of instance or schema to be casted")
    s.add_argument("--to-schema-id", required=True, help="GTS ID of target schema")

    s = sub.add_parser("query", help="Query entities using an expression")
    s.add_argument("--expr", required=True, help="Query expression")
    s.add_argument("--limit", type=int, default=100, help="Maximum number of entities to return (default: 100)")

    s = sub.add_parser("attr", help="Get attribute value from a GTS entity")
    s.add_argument("--gts-with-path", required=True, help="GTS ID with attribute path (e.g., gts.a.b.c.d.v1~@field.subfield)")

    s = sub.add_parser("list", help="List all entities")
    s.add_argument("--limit", type=int, default=100, help="Maximum number of entities to return (default: 100)")

    s = sub.add_parser("server", help="Start the GTS HTTP server")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)

    s = sub.add_parser("openapi-spec", help="Generate OpenAPI specification")
    s.add_argument("--out", required=True, help="Destination file path for OpenAPI spec JSON")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)

    return p


def main(argv: List[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.verbose == 0 else (logging.DEBUG if args.verbose >= 2 else logging.INFO),
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    try:
        # Helper to create GtsOps with common arguments
        ops = GtsOps(path=args.path, config=args.config, verbose=args.verbose)

        if args.op == "server":
            server = GtsHttpServer(ops=ops)
            # Print URL JSON to stdout before starting server (compute from args)
            _host = getattr(args, "host", "127.0.0.1")
            _port = getattr(args, "port", 8000)
            print(f"starting the server @ http://{_host}:{_port}")
            if args.verbose == 0:
                print("use --verbose to see server logs")
            import uvicorn
            uvicorn.run(
                server.app,
                host=_host,
                port=_port,
                log_level=("info" if args.verbose else "warning"),
            )
            return
        elif args.op == "openapi-spec":
            server = GtsHttpServer(ops=ops)
            spec = server.app.openapi()
            with open(getattr(args, "out"), "w", encoding="utf-8") as f:
                json.dump(spec, f, ensure_ascii=False, indent=2)
            out = {"ok": True, "out": getattr(args, "out")}
            json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
            return
        elif args.op == "validate-id":
            out = ops.validate_id(args.gts_id).to_dict()
        elif args.op == "parse-id":
            out = ops.parse_id(args.gts_id).to_dict()
        elif args.op == "match-id-pattern":
            out = ops.match_id_pattern(args.candidate, args.pattern).to_dict()
        elif args.op == "uuid":
            out = ops.uuid(args.gts_id).to_dict()
        elif args.op == "validate-instance":
            out = ops.validate_instance(args.gts_id).to_dict()
        elif args.op == "resolve-relationships":
            out = ops.schema_graph(args.gts_id).to_dict()
        elif args.op == "compatibility":
            out = ops.compatibility(args.old_schema_id, args.new_schema_id).to_dict()
        elif args.op == "cast":
            out = ops.cast(args.from_id, args.to_schema_id).to_dict()
        elif args.op == "query":
            out = ops.query(args.expr, args.limit).to_dict()
        elif args.op == "attr":
            out = ops.attr(args.gts_with_path).to_dict()
        elif args.op == "list":
            out = ops.get_entities(limit=args.limit).to_dict()
        else:
            raise SystemExit("Unknown op")
        json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        raise


if __name__ == "__main__":
    main()
