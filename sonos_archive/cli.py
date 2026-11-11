"""``python3 -m sonos_archive`` entry point.

Commands: capture, status, verify, export. Exit codes:

  0  clean run / budget exhausted
  1  usage error
  2  config error
  3  a host denied access (persisted, never retried around)
  4  transient / schema / integrity failure
  5  publish failure
"""

import argparse
import base64
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

from .crawl import Crawler
from .errors import ArchiveError
from .forum import FixtureTransport, LiveTransport
from . import publish
from .store import Store


def _load_config(args):
    try:
        return publish.load_config(args.config)
    except (OSError, ValueError) as e:
        raise SystemExit(f"config error: {e}")


def _remote(args, config):
    mode = getattr(args, "remote", "auto")
    if mode == "local":
        return publish.LocalRemote(args.archive_dir)
    if mode == "github" and not (
            os.environ.get("GITHUB_TOKEN") and os.environ.get("GITHUB_REPOSITORY")):
        raise SystemExit("--remote github requires GITHUB_TOKEN and GITHUB_REPOSITORY")
    return publish.remote_from_env(config, args.archive_dir)


def _fixture_transport(path):
    data = json.loads(Path(path).read_text())
    responses = {}
    for entry in data.get("responses", []):
        body = entry.get("body", "")
        if "body_base64" in entry:
            body = base64.b64decode(entry["body_base64"])
        responses.setdefault((entry["method"].upper(), entry["url"]), []).append(
            (entry.get("status", 200), entry.get("headers") or {}, body))
    fixture = FixtureTransport(responses)
    return lambda _host: fixture


def cmd_capture(args):
    config = _load_config(args)
    hosts = args.host or config["hosts"]
    unknown = [h for h in hosts if h not in config["hosts"]]
    if unknown:
        print(f"error: unknown hosts {unknown}", file=sys.stderr)
        return 2

    remote = _remote(args, config)
    work = Path(args.archive_dir) / "work"
    shutil.rmtree(work, ignore_errors=True)

    # A capture only needs the latest durable index and new raw captures.
    # Historical raw files remain available through the immutable release
    # lineage and can be hydrated by a full restore when needed.
    # Never silently reset on a restore failure: that would fork the archive.
    try:
        manifest_prev, since_seq = remote.restore(work, include_historical_raw=False)
        work.mkdir(parents=True, exist_ok=True)
        store = Store(work)
    except (ArchiveError, ValueError, OSError) as e:
        print(f"cannot restore archive safely: {e}", file=sys.stderr)
        return 4

    published = []
    publish_error = [None]
    prev_tag = [manifest_prev["tag"] if manifest_prev else None]
    last_seq = [since_seq if since_seq >= 0 else -1]
    last_publish = [time.time()]
    publish_lock = threading.Lock()
    publish_thread = [None]

    def do_publish():
        """Stage + publish an immutable snapshot of everything captured so
        far. Runs in a background thread so the crawl never waits on the
        DB backup, tar builds or the upload. Uses its own SQLite connection:
        connection objects are thread-bound."""
        if not publish_lock.acquire(blocking=False):
            return  # a publish is already in flight
        reader = None
        try:
            stage = Path(tempfile.mkdtemp(prefix=".staging-", dir=args.archive_dir))
            try:
                reader = Store(work)  # separate connection to the same file (WAL)
                stage, manifest = publish.build_snapshot(
                    reader, hosts, stage, prev_tag[0], last_seq[0], crawler.run_id)
                manifest = remote.publish(stage, manifest)
                prev_tag[0] = manifest["tag"]
                last_seq[0] = manifest["capture_cursor"]
                last_publish[0] = time.time()
                published.append(manifest["tag"])
                print(f"published {manifest['tag']}", file=sys.stderr)
            except Exception as e:
                publish_error[0] = str(e)
                print(f"publish failed: {e}", file=sys.stderr)
                shutil.rmtree(stage, ignore_errors=True)
        finally:
            if reader is not None:
                reader.close()
            publish_lock.release()

    def after_task():
        if not args.no_publish and \
                time.time() - last_publish[0] >= args.checkpoint_seconds:
            t = threading.Thread(target=do_publish, daemon=True)
            publish_thread[0] = t
            t.start()
            last_publish[0] = time.time()  # timer resets on dispatch, not completion

    transport_factory = (
        _fixture_transport(args.fixtures) if args.fixtures
        else (lambda _host: LiveTransport(timeout=config["timeout_seconds"])))
    crawler = Crawler(store, config, transport_factory,
                      run_id=args.run_id, log=lambda m: print(m, file=sys.stderr),
                      after_task=after_task)
    crawler.run(hosts)

    # Final publish: wait for any in-flight thread, then publish whatever was
    # captured since the last checkpoint and wait for that too.
    if not args.no_publish:
        if publish_thread[0] is not None:
            publish_thread[0].join()
        if last_seq[0] < store.last_capture_seq():
            do_publish()
        # do_publish runs inline when the lock is free, so just wait for any
        # thread it may have spawned via after_task bookkeeping.
        if publish_thread[0] is not None and publish_thread[0].is_alive():
            publish_thread[0].join()

    store.close()
    report = crawler.report.to_dict()
    report["published"] = published
    report["publish_error"] = publish_error[0]
    report["restore_previous"] = manifest_prev["tag"] if manifest_prev else None
    print(json.dumps(report, indent=2, sort_keys=True))
    if publish_error[0]:
        return 5
    if crawler.report.errors:
        return 4
    if crawler.report.denials:
        return 3
    return 0


def cmd_restore(args):
    """Reconstruct every surviving raw capture across snapshot ancestry."""
    config = _load_config(args)
    remote = _remote(args, config)
    out = Path(args.out)
    if out.exists():
        print(f"refusing to overwrite existing destination: {out}", file=sys.stderr)
        return 4
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".restore-", dir=out.parent))
    try:
        manifest, _cursor = remote.restore(stage, include_historical_raw=True)
        if manifest is None:
            print("no published snapshots to restore", file=sys.stderr)
            return 4
        stage.rename(out)
        print(f"restored {manifest['tag']} with full raw lineage to {out}")
        return 0
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def cmd_status(args):
    config = _load_config(args)
    remote = _remote(args, config)
    if isinstance(remote, publish.LocalRemote):
        latest = remote.latest()
        if latest is None:
            print("no snapshots")
            return 0
        manifest = json.loads((latest / "manifest.json").read_text())
    else:
        latest = remote.latest()
        if latest is None:
            print("no snapshots")
            return 0
        with tempfile.TemporaryDirectory() as td:
            path = remote._download_asset(latest[1], "manifest.json", Path(td) / "m.json")
            manifest = json.loads(Path(path).read_text())
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def cmd_verify(args):
    remote = publish.LocalRemote(args.archive_dir)
    target = Path(args.snapshot_dir) if args.snapshot_dir else remote.latest()
    if target is None:
        print("no snapshots")
        return 4
    try:
        manifest = publish.verify_dir(target)
    except ArchiveError as e:
        print(f"verify FAILED: {e}", file=sys.stderr)
        return 4
    print(f"verify OK: {manifest['tag']} ({manifest['created_at']})")
    return 0


def cmd_export(args):
    src = Path(args.archive_root) if args.archive_root else \
        (Path(args.snapshot_dir) if args.snapshot_dir else None)
    if src is None:
        latest = publish.LocalRemote(args.archive_dir).latest()
        if latest is None:
            print("no snapshots", file=sys.stderr)
            return 4
        src = latest
    # A snapshot dir keeps index.sqlite inside state.tar.gz and must never be
    # opened in place (that would dirty the immutable snapshot). Extract to a
    # temp dir first.
    if (src / "state.tar.gz").exists():
        tmp = tempfile.mkdtemp(prefix="export-")
        try:
            publish.restore_into(tmp, src / "state.tar.gz", [src / "raw.tar.gz"])
            src = Path(tmp)
        except ArchiveError as e:
            print(f"cannot read snapshot: {e}", file=sys.stderr)
            return 4
    if not (src / "index.sqlite").exists():
        print(f"no index.sqlite under {src}", file=sys.stderr)
        return 4
    store = Store(src)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    publish.write_exports(store, out)
    (out / "coverage.json").write_text(
        json.dumps(publish.coverage(store, args.host or []), indent=2))
    store.close()
    print(f"exports written to {out}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="python3 -m sonos_archive",
                                description="Anonymous incremental archive of the "
                                            "EN/DE/NL Sonos community forums.")
    p.add_argument("--version", action="store_true")
    sub = p.add_subparsers(dest="command")

    def common(s):
        s.add_argument("--config", default="config.json")
        s.add_argument("--archive-dir", default="archive")
        s.add_argument("--remote", choices=["auto", "local", "github"], default="auto")

    c = sub.add_parser("capture", help="crawl until the queue drains; publish snapshots")
    common(c)
    c.add_argument("--host", action="append")
    c.add_argument("--run-id", default="manual")
    c.add_argument("--checkpoint-seconds", type=float, default=240,
                   help="publish an immutable snapshot at least this often "
                        "(default 240s); bounds how much a killed run loses")
    c.add_argument("--fixtures", help="offline replay fixture JSON")
    c.add_argument("--no-publish", action="store_true")
    c.set_defaults(func=cmd_capture)

    r = sub.add_parser("restore", help="reconstruct complete raw archive from release history")
    common(r)
    r.add_argument("--out", required=True, help="new directory for recovered archive")
    r.set_defaults(func=cmd_restore)

    s = sub.add_parser("status", help="show the latest snapshot manifest")
    common(s)
    s.set_defaults(func=cmd_status)

    v = sub.add_parser("verify", help="verify snapshot hashes")
    v.add_argument("--config", default="config.json")
    v.add_argument("--archive-dir", default="archive")
    v.add_argument("--snapshot-dir")
    v.set_defaults(func=cmd_verify)

    e = sub.add_parser("export", help="write readable JSONL exports")
    e.add_argument("--config", default="config.json")
    e.add_argument("--archive-dir", default="archive")
    e.add_argument("--archive-root")
    e.add_argument("--snapshot-dir")
    e.add_argument("--host", action="append")
    e.add_argument("--out", required=True)
    e.set_defaults(func=cmd_export)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if getattr(args, "version", False):
        from . import __version__
        print(__version__)
        return 0
    if not getattr(args, "func", None):
        build_parser().print_help()
        return 1
    try:
        return args.func(args)
    except ArchiveError as e:
        print(f"error: {e}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
