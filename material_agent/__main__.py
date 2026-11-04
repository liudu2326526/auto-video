"""Material library and handoffs; only the explicit download command accesses public HTTPS."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path
from .core import Library, MaterialError, read_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default='var/material-library', help='Ignored runtime library, not source control')
    sub = parser.add_subparsers(dest='command', required=True)
    plan = sub.add_parser('plan', help='Match local materials and produce explicit public search requests')
    plan.add_argument('--requests', required=True)
    plan.add_argument('--script', help='Optional writer draft; validate scene/line/asset references')
    add = sub.add_parser('add', help='Add immutable metadata; optionally copy a previously downloaded file')
    add.add_argument('--record', required=True)
    add.add_argument('--file')
    add.add_argument('--download-record', help='Required acquisition receipt if --file is supplied')
    download = sub.add_parser('download', help='Fetch one approved public Commons original, validate and import; no login or batch')
    download.add_argument('--record', required=True)
    download.add_argument('--policy', required=True, help='Trusted per-record provider/size/deadline policy')
    download.add_argument('--context', required=True, help='Intended use; downloading does not approve use')
    search = sub.add_parser('search', help='Local title/description/tag matching, including blocked candidates')
    search.add_argument('--query', required=True)
    search.add_argument('--context', required=True)
    search.add_argument('--type', choices=['image', 'video', 'audio'])
    search.add_argument('--limit', type=int, default=10)
    review = sub.add_parser('review', help='Store a real operator review bound to bytes, metadata and use')
    review.add_argument('--receipt', required=True)
    handoff = sub.add_parser('handoff', help='Export only eligible local materials into a new directory')
    handoff.add_argument('--requests', required=True)
    handoff.add_argument('--selection', required=True)
    handoff.add_argument('--output', required=True)
    verify = sub.add_parser('verify', help='Before editing, recheck handoff files, current license and human review')
    verify.add_argument('--handoff', required=True, help='Exported materials.json')
    usage = sub.add_parser('used', help='Record actual edit use; exporting a handoff is not use')
    usage.add_argument('--receipt', required=True)
    show = sub.add_parser('show', help='Display provenance, download, review and usage history')
    show.add_argument('--id', required=True)
    downloads = sub.add_parser('downloads', help='Download/reuse/failure audit, including records not imported after failure')
    downloads.add_argument('--id', required=True)
    args = parser.parse_args(argv)
    library = None
    try:
        library = Library(args.library)
        if args.command == 'plan':
            result = library.plan(read_json(args.requests), read_json(args.script) if args.script else None)
        elif args.command == 'add':
            result = library.add(read_json(args.record), args.file,
                                 read_json(args.download_record) if args.download_record else None)
        elif args.command == 'search':
            result = library.search(args.query, read_json(args.context), [args.type] if args.type else None, args.limit)
        elif args.command == 'download':
            from .download import download_record
            result = download_record(library, read_json(args.record), read_json(args.policy), read_json(args.context))
        elif args.command == 'review':
            result = library.review(read_json(args.receipt))
        elif args.command == 'handoff':
            result = library.handoff(read_json(args.requests), read_json(args.selection), args.output)
        elif args.command == 'used':
            result = library.record_usage(read_json(args.receipt))
        elif args.command == 'verify':
            result = library.verify_handoff(args.handoff)
        elif args.command == 'downloads':
            result = library.download_history(args.id)
        else:
            result = library.history(args.id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (MaterialError, ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        print('Material operation failed: ' + str(exc), file=sys.stderr)
        return 2
    finally:
        if library is not None:
            library.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
