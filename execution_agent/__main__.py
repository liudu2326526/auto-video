import argparse
import json
import sqlite3
import sys

from .core import dispatch
from .storage import Error


def main():
    parser = argparse.ArgumentParser(description="Supervised tool tests and evidence-backed screenwriter handoff")
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("plan", "run", "status", "observe", "reconcile", "handoff"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--root", required=True)
        cmd.add_argument("--run-id", required=True)
        if name != "status":
            cmd.add_argument("--request-id", required=True)
        if name == "plan":
            cmd.add_argument("--manifest", required=True)
        if name in ("observe", "reconcile"):
            cmd.add_argument("--step-id", required=True)
        if name == "observe":
            cmd.add_argument("--observation", dest="observation_path", required=True)
    args = vars(parser.parse_args())
    try:
        result = dispatch(**args)
    except (Error, OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"schema_version": 1, "command_ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"command_ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
