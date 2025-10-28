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
    s.add_argument("--path", required=False, help="Path to json and schema files or directories")
    s.add_argument("--gts-id", required=True, help="GTS ID of the object")

    s = sub.add_parser("schema-graph")
    s.add_argument("--path", required=False, help="Path to json and schema files or directories")
    s.add_argument("--gts-id", required=True, help="GTS ID of the entity")

    s = sub.add_parser("compatibility")
    s.add_argument("--path", required=False, help="Path to json and schema files or directories")
    s.add_argument("--old-schema-id", required=True, help="GTS ID of old schema")
    s.add_argument("--new-schema-id", required=True, help="GTS ID of new schema")

    s = sub.add_parser("cast")
    s.add_argument("--path", required=False, help="Path to json and schema files or directories")
    s.add_argument("--instance-id", required=True, help="GTS ID of instance to be casted to a schema")
    s.add_argument("--to-schema-id", required=True, help="GTS ID of target schema")

    s = sub.add_parser("query")
    s.add_argument("--path", required=False, help="Path to json and schema files or directories")
    s.add_argument("--query", required=True, help="Query expression")

    s = sub.add_parser("attr")
    s.add_argument("--path", required=False, help="Path to json and schema files or directories")
    s.add_argument("--gts-with-path", required=True, help="GTS ID with attribute path (e.g., gts.a.b.c.d.v1~@field.subfield)")

    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--path", required=False, help="Path to json and schema files or directories to pre-populate")

    s = sub.add_parser("openapi-spec")
    s.add_argument("--out", required=True, help="Destination file path for OpenAPI spec JSON")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--path", required=False, help="Path to json and schema files or directories")

    return p


def main(argv: List[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.verbose == 0 else (logging.DEBUG if args.verbose >= 2 else logging.INFO),
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    try:
        # Determine effective path for ops that use store
        def effective_path(local: str | None) -> str | None:
            return local or getattr(args, "path", None)

        if args.op == "serve":
            ops = GtsOps(
                path=effective_path(getattr(args, "path", None)),
                config=getattr(args, "config", None),
                verbose=getattr(args, "verbose", 0),
            )
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
            ops = GtsOps(
                path=effective_path(getattr(args, "path", None)),
                config=getattr(args, "config", None),
                verbose=getattr(args, "verbose", 0),
            )
            server = GtsHttpServer(ops=ops)
            spec = server.app.openapi()
            with open(getattr(args, "out"), "w", encoding="utf-8") as f:
                json.dump(spec, f, ensure_ascii=False, indent=2)
            out = {"ok": True, "out": getattr(args, "out")}
            json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
            return
        elif args.op == "validate-id":
            ops = GtsOps(config=getattr(args, "config", None), verbose=getattr(args, "verbose", 0))
            out = ops.validate_id(args.gts_id)
        elif args.op == "parse":
            ops = GtsOps(config=getattr(args, "config", None), verbose=getattr(args, "verbose", 0))
            out = ops.parse(args.gts_id)
        elif args.op == "wildcard-match":
            ops = GtsOps(config=getattr(args, "config", None), verbose=getattr(args, "verbose", 0))
            out = ops.wildcard_match(args.candidate, args.pattern)
        elif args.op == "uuid":
            ops = GtsOps(config=getattr(args, "config", None), verbose=getattr(args, "verbose", 0))
            out = ops.uuid(args.gts_id)
        elif args.op == "validate-instance":
            ops = GtsOps(path=effective_path(getattr(args, "path", None)), config=getattr(args, "config", None), verbose=getattr(args, "verbose", 0))
            out = ops.validate_instance(args.gts_id)
        elif args.op == "schema-graph":
            ops = GtsOps(path=effective_path(getattr(args, "path", None)), config=getattr(args, "config", None), verbose=getattr(args, "verbose", 0))
            out = ops.schema_graph(args.gts_id)
        elif args.op == "compatibility":
            ops = GtsOps(path=effective_path(getattr(args, "path", None)), config=getattr(args, "config", None), verbose=getattr(args, "verbose", 0))
            out = ops.compatibility(args.old_schema_id, args.new_schema_id)
        elif args.op == "cast":
            ops = GtsOps(path=effective_path(getattr(args, "path", None)), config=getattr(args, "config", None), verbose=getattr(args, "verbose", 0))
            out = ops.cast(args.instance_id, args.to_schema_id)
        elif args.op == "query":
            ops = GtsOps(path=effective_path(getattr(args, "path", None)), config=getattr(args, "config", None), verbose=getattr(args, "verbose", 0))
            out = ops.query(args.query)
        elif args.op == "attr":
            ops = GtsOps(path=effective_path(getattr(args, "path", None)), config=getattr(args, "config", None), verbose=getattr(args, "verbose", 0))
            out = ops.attr(args.gts_with_path)
        else:
            raise SystemExit("Unknown op")
        json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        raise


if __name__ == "__main__":
    main()
