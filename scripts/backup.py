#!/usr/bin/env python3
"""Back up a directory tree to a remote via rclone, skipping files should_back_up rejects.

Usage:
    python scripts/backup.py <root> <destination> [rclone options...]

Example:
    python scripts/backup.py "/Volumes/Q" "gdrive:Backups/Q-Drive" --transfers 12

Resuming: just rerun the same command. rclone lists what is already at the
destination and compares it against the source (by size and modification
time, or by checksum with --checksum), then only transfers what is new or
changed. A stopped or interrupted run picks up where it left off with no
extra bookkeeping, and files added since the last run are picked up too.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_file import should_back_up


def load_skip_log(skip_log_path: Path) -> set[str]:
    if not skip_log_path.exists():
        return set()
    return {line for line in skip_log_path.read_text(encoding="utf-8").splitlines() if line}


def iter_backup_files(root: Path, skip_log_path: Path, files_from_path: Path) -> int:
    """Stream every path should_back_up keeps straight into files_from_path.

    The tree can hold millions of entries, so this never builds an in-memory
    list of them: each kept path is written to files_from_path as it's found,
    and only the (much smaller) set of already-logged skips is held in memory.
    Returns how many files were kept.
    """
    already_logged = load_skip_log(skip_log_path)
    new_skips: list[str] = []

    def record_skip(relative: str) -> None:
        if relative not in already_logged:
            already_logged.add(relative)
            new_skips.append(relative)

    kept_count = 0
    skipped_count = 0
    last_report = 0.0

    def report(force: bool = False) -> None:
        nonlocal last_report
        now = time.monotonic()
        if force or now - last_report >= 0.5:
            seen = kept_count + skipped_count
            print(
                f"\rScanning... {seen:,} files seen ({kept_count:,} to back up, {skipped_count:,} skipped)",
                end="",
                flush=True,
            )
            last_report = now

    with open(files_from_path, "w", encoding="utf-8") as files_from:
        for dirpath, dirnames, filenames in os.walk(root):
            current = Path(dirpath)

            pruned_dirnames = []
            for dirname in dirnames:
                relative = (current / dirname).relative_to(root).as_posix()
                if should_back_up(relative):
                    pruned_dirnames.append(dirname)
                else:
                    record_skip(relative + "/")
            dirnames[:] = pruned_dirnames

            for filename in filenames:
                relative = (current / filename).relative_to(root).as_posix()
                if should_back_up(relative):
                    files_from.write(relative + "\n")
                    kept_count += 1
                else:
                    record_skip(relative)
                    skipped_count += 1
                report()

    report(force=True)
    print()

    if new_skips:
        with open(skip_log_path, "a", encoding="utf-8") as f:
            for entry in new_skips:
                f.write(entry + "\n")

    return kept_count


def alphabetize_skip_log(skip_log_path: Path) -> None:
    """Sort the skip log in place. Only call this once the whole tree has been backed up."""
    if not skip_log_path.exists():
        return
    entries = sorted(load_skip_log(skip_log_path))
    skip_log_path.write_text("".join(entry + "\n" for entry in entries), encoding="utf-8")


def _human_size(num_bytes: float) -> str:
    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(size) < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def _run_rclone_quiet(command: list[str]) -> int:
    """Run rclone with JSON logging and print one summary line per 1% of progress.

    Meant for a job whose output is being redirected to a log file: rclone's
    own --progress bar repaints a line in place with carriage returns, which
    turns into unreadable noise in a log file. This reads rclone's periodic
    JSON stats off stderr instead and only echoes a line when the percentage
    complete has actually moved, so a log file gets a clean, appendable trail.
    """
    last_percent = -1
    process = subprocess.Popen(command, stderr=subprocess.PIPE, text=True, bufsize=1)
    try:
        for line in process.stderr:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                print(line, flush=True)
                continue

            stats = payload.get("stats")
            if stats is None:
                msg = payload.get("msg", "").strip()
                if msg:
                    print(f"[rclone {payload.get('level', 'info').upper()}] {msg}", flush=True)
                continue

            total_bytes = stats.get("totalBytes") or 0
            done_bytes = stats.get("bytes") or 0
            percent = int(done_bytes * 100 / total_bytes) if total_bytes else 0
            if percent == last_percent:
                continue
            last_percent = percent

            transfers_done = stats.get("transfers") or 0
            transfers_total = stats.get("totalTransfers") or 0
            remaining = max(transfers_total - transfers_done, 0)
            eta = stats.get("eta")
            eta_text = f"{eta:.0f}s" if isinstance(eta, (int, float)) else "-"
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(
                f"[{timestamp}] {percent}% - {transfers_done:,}/{transfers_total:,} files "
                f"({remaining:,} remaining) - {_human_size(done_bytes)}/{_human_size(total_bytes)} "
                f"transferred - {_human_size(stats.get('speed') or 0)}/s - ETA {eta_text}",
                flush=True,
            )
    finally:
        process.wait()

    return process.returncode


def run_rclone(
    root: Path,
    destination: str,
    files_from: Path,
    transfers: int,
    checkers: int,
    checksum: bool,
    dry_run: bool,
    extra_args: list[str],
    quiet: bool = False,
) -> int:
    command = [
        "rclone", "copy", str(root), destination,
        "--files-from", str(files_from),
        "--transfers", str(transfers),
        "--checkers", str(checkers),
        "--retries", "5",
        "--low-level-retries", "10",
    ]
    if checksum:
        command.append("--checksum")
    if dry_run:
        command.append("--dry-run")

    if quiet:
        command += ["--use-json-log", "--stats-log-level", "NOTICE", "--stats", "5s"]
        command.extend(extra_args)
        print("Running:", " ".join(command))
        return _run_rclone_quiet(command)

    command += ["--progress", "--stats", "5s"]
    command.extend(extra_args)
    print("Running:", " ".join(command))
    return subprocess.call(command)


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up a directory to a remote via rclone, skipping junk files.")
    parser.add_argument("root", type=Path, help="Local directory to back up")
    parser.add_argument("destination", help="rclone destination, e.g. gdrive:Backups/Q-Drive")
    parser.add_argument("--transfers", type=int, default=8, help="Concurrent file uploads (default: 8)")
    parser.add_argument("--checkers", type=int, default=16, help="Concurrent existence checks against the destination (default: 16)")
    parser.add_argument("--checksum", action="store_true", help="Compare files by checksum instead of size+modtime (slower, safer resume)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be transferred without uploading anything")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Print one summary line per 1%% of progress instead of a live-updating bar (for logging to a file)",
    )
    parser.add_argument(
        "--skip-log",
        type=Path,
        default=Path("skipped_files.txt"),
        help="File that skipped paths are recorded to, one per line (default: skipped_files.txt)",
    )
    args, extra_args = parser.parse_known_args()

    if shutil.which("rclone") is None:
        sys.exit("rclone is not installed or not on PATH. See scripts/README.md for install instructions.")

    root = args.root.resolve()
    if not root.is_dir():
        sys.exit(f"Not a directory: {root}")

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        files_from = Path(f.name)

    try:
        print(f"Scanning {root} ...")
        kept_count = iter_backup_files(root, args.skip_log, files_from)
        print(f"Found {kept_count:,} files to back up. Skipped paths are recorded in {args.skip_log}")

        if kept_count == 0:
            print("Nothing to back up.")
            return

        exit_code = run_rclone(
            root,
            args.destination,
            files_from,
            args.transfers,
            args.checkers,
            args.checksum,
            args.dry_run,
            extra_args,
            args.quiet,
        )
    finally:
        files_from.unlink(missing_ok=True)

    if exit_code == 0:
        alphabetize_skip_log(args.skip_log)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
