"""Command line interface.

Exit statuses (also on ``ArchiveError`` subclasses):
  0  clean stop: complete, idle, or bounded budget reached
  2  configuration / scope policy error
  3  a host denied access or challenged; the stop was persisted, never retried
  4  transient network, protocol, schema or integrity failure (fail closed)
  5  publish failure; previous snapshots are untouched
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional

from . import __version__, util
from .config import load_config
from .crawler import Crawler
from .errors import ArchiveError, SnapshotError
from .export import write_exports
from .net import FixtureTransport, LiveTransport
from .snapshot import (
    GithubRemote,
    LocalRemote,
    build_snapshot,
    extract_state,
    remote_from_env,
    verify_snapshot_dir,
)
from .store import Archive

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.json"


def _runner_info() -> dict:
    keys = (
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
        "GITHUB_WORKFLOW",
        "GITHUB_SHA",
        "GITHUB_SERVER_URL",
        "GITHUB_REPOSITORY",
        "GITHUB_ACTOR",
    )
    return {k.lower(): os.environ[k] for k in keys if os.environ.get(k)}


def _load_config(args):
    return load_config(Path(args.config))


def _make_remote(args, config):
    if args.remote == "local":
        return LocalRemote(args.archive_dir)
    if args.remote == "github":
        token = os.environ.get("GITHUB_TOKEN", "")
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        if not token or not repo:
            raise SnapshotError("--remote github needs GITHUB_TOKEN and GITHUB_REPOSITORY")
        return GithubRemote(
            repo,
            token,
            user_agent=config.user_agent,
            target_commitish=os.environ.get("GITHUB_SHA") or None,
        )
    return remote_from_env(config=config, archive_dir=args.archive_dir)


def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _transport_factory(args, config):
    if getattr(args, "fixtures", None):
        fixture_path = Path(args.fixtures)
        return lambda _hc: FixtureTransport(fixture_path)
    return lambda _hc: LiveTransport(timeout=config.politeness.timeout_seconds)


def _resolve_hosts(config, names: Optional[list]) -> Optional[list]:
    if not names:
        return None
    hosts = []
    for name in names:
        hc = config.host(name)
        if hc is None:
            raise ArchiveError(f"unknown host: {name}")
        hosts.append(hc.host)
    return hosts


def cmd_capture(args) -> int:
    config = _load_config(args)
    only_hosts = _resolve_hosts(config, args.host)
    remote = _make_remote(args, config)
    ref = remote.latest()
    sequence = (ref.sequence + 1) if ref else 1
    workdir = Path(tempfile.mkdtemp(prefix="sonos-archive-"))
    store: Optional[Archive] = None
    try:
        state_root = workdir / "state"
        if ref is not None:
            state_tar = workdir / "state.tar.gz"
            remote.fetch_state(ref, state_tar)
            extract_state(state_tar, state_root)
            print(f"resumed from {ref.tag} (sequence {ref.sequence})", file=sys.stderr)
        else:
            print("starting a new archive state (no previous snapshot)", file=sys.stderr)
        store = Archive(state_root)
        if args.reset_host:
            for host in args.reset_host:
                hc = config.host(host)
                if hc is None:
                    raise ArchiveError(f"unknown host: {host}")
                if store.reset_host(hc.host):
                    print(f"host reset: {hc.host}", file=sys.stderr)
        crawler = Crawler(
            store,
            config,
            transport_factory=_transport_factory(args, config),
            logger=lambda msg: print(msg, file=sys.stderr),
            run_id=_runner_info().get("github_run_id", "manual"),
        )
        report = crawler.run(
            max_requests=args.max_requests,
            max_seconds=args.max_seconds,
            only_hosts=only_hosts,
        )
        dirty = ref is None or report.requests > 0 or bool(args.reset_host)
        published = None
        manifest = None
        if args.no_publish:
            print("--no-publish: snapshot build skipped", file=sys.stderr)
        elif not dirty:
            print("no requests were needed; no new snapshot published", file=sys.stderr)
        else:
            stage = workdir / "snapshot"
            build = build_snapshot(
                store,
                stage,
                sequence=sequence,
                previous=ref,
                config=config,
                runner=_runner_info(),
            )
            manifest = build["manifest"]
            store.close()
            store = None
            published = remote.publish(stage, build)
            print(
                f"published {published.tag} "
                f"({manifest['counts']['topics']} topics, "
                f"{manifest['counts']['replies']} replies, "
                f"{manifest['counts']['captures']} captures)",
                file=sys.stderr,
            )
        summary = report.to_dict()
        summary["published"] = (
            None
            if published is None
            else {
                "tag": published.tag,
                "sequence": published.sequence,
                "manifest_sha256": published.manifest_sha256,
            }
        )
        summary["coverage"] = (
            manifest["coverage"]
            if manifest
            else (store.coverage() if store is not None else None)
        )
        _print_json(summary)
        return int(report.exit_code())
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                pass
        shutil.rmtree(workdir, ignore_errors=True)


def cmd_status(args) -> int:
    config = _load_config(args)
    remote = _make_remote(args, config)
    ref = remote.latest()
    if ref is None:
        _print_json({"snapshots": 0, "note": "no published snapshots found"})
        return 0
    _print_json(
        {
            "remote": remote.name,
            "latest": {
                "tag": ref.tag,
                "sequence": ref.sequence,
                "manifest_sha256": ref.manifest_sha256,
                "created_at": ref.manifest["created_at"],
                "previous": ref.manifest["previous"],
            },
            "counts": ref.manifest["counts"],
            "coverage": ref.manifest["coverage"],
        }
    )
    return 0


def cmd_verify(args) -> int:
    if args.snapshot_dir:
        _print_json(verify_snapshot_dir(args.snapshot_dir, deep=not args.shallow))
        return 0
    config = _load_config(args)
    remote = _make_remote(args, config)
    ref = remote.latest()
    if ref is None:
        _print_json({"ok": True, "snapshots": 0, "note": "nothing to verify"})
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="sonos-verify-"))
    try:
        remote.download_snapshot(ref, tmp)
        result = verify_snapshot_dir(tmp, deep=not args.shallow)
        result["remote"] = remote.name
        _print_json(result)
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_export(args) -> int:
    config = _load_config(args)
    out = Path(args.out)
    if args.snapshot_dir:
        tmp = Path(tempfile.mkdtemp(prefix="sonos-export-"))
        try:
            root = extract_state(Path(args.snapshot_dir) / "state.tar.gz", tmp / "state")
            store = Archive(root)
            try:
                result = write_exports(store, out)
            finally:
                store.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    elif args.archive_root:
        store = Archive(args.archive_root)
        try:
            result = write_exports(store, out)
        finally:
            store.close()
    else:
        remote = _make_remote(args, config)
        ref = remote.latest()
        if ref is None:
            raise SnapshotError("no published snapshots to export")
        tmp = Path(tempfile.mkdtemp(prefix="sonos-export-"))
        try:
            remote.download_snapshot(ref, tmp)
            root = extract_state(tmp / "state.tar.gz", tmp / "state")
            store = Archive(root)
            try:
                result = write_exports(store, out)
            finally:
                store.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    _print_json({"exported_to": str(out), **result})
    return 0


def _add_remote_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--archive-dir",
        default=os.environ.get("SONOS_ARCHIVE_DIR", "archive"),
        help="local archive root for the local remote (default: archive, or $SONOS_ARCHIVE_DIR)",
    )
    parser.add_argument(
        "--remote",
        choices=("auto", "local", "github"),
        default="auto",
        help="auto uses GitHub when GITHUB_TOKEN and GITHUB_REPOSITORY are set, else local",
    )


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="path to config.json",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m sonos_archive",
        description="Anonymous incremental archive of the EN/DE/NL Sonos community forums.",
        epilog=(
            "Exit status: 0 clean/budget, 2 config/scope, 3 denial (persisted, "
            "no retry), 4 transient/schema/integrity, 5 publish."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    capture = sub.add_parser("capture", help="run a bounded capture and publish a snapshot")
    _add_config_arg(capture)
    _add_remote_args(capture)
    capture.add_argument("--max-seconds", type=float, default=None,
                         help="time budget for this run (default: config.budget)")
    capture.add_argument("--max-requests", type=int, default=None,
                         help="request budget for this run (default: config.budget)")
    capture.add_argument(
        "--host", action="append",
        help="limit this run to one or more hosts (repeatable)",
    )
    capture.add_argument(
        "--reset-host", action="append",
        help="clear a persisted denial/stop for a host, then publish the new state",
    )
    capture.add_argument(
        "--fixtures",
        help="offline mode: replay responses from a recorded fixture JSON file",
    )
    capture.add_argument(
        "--no-publish", action="store_true",
        help="capture only; do not build or publish a snapshot",
    )
    capture.set_defaults(func=cmd_capture)

    status = sub.add_parser("status", help="show the latest published snapshot metadata")
    _add_config_arg(status)
    _add_remote_args(status)
    status.set_defaults(func=cmd_status)

    verify = sub.add_parser("verify", help="download and cryptographically verify a snapshot")
    _add_config_arg(verify)
    _add_remote_args(verify)
    verify.add_argument("--snapshot-dir", help="verify a local snapshot directory instead")
    verify.add_argument("--shallow", action="store_true", help="skip deep archive checks")
    verify.set_defaults(func=cmd_verify)

    export = sub.add_parser("export", help="write readable JSONL exports")
    _add_config_arg(export)
    _add_remote_args(export)
    export.add_argument("--archive-root", help="path to an archive state dir (index.sqlite + raw/)")
    export.add_argument("--snapshot-dir", help="export a local snapshot directory")
    export.add_argument("--out", required=True, help="output directory")
    export.set_defaults(func=cmd_export)
    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ArchiveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return int(getattr(exc, "exit_code", 1))
    except KeyboardInterrupt:
        print("interrupted; no partial snapshot was published", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
