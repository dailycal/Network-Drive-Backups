#!/usr/bin/env python3
"""Back up a directory tree to a remote via rclone, skipping files should_back_up rejects.

Usage:
    python scripts/backup.py <source> <destination> [rclone options...]

Example (a local path or mapped drive):
    python scripts/backup.py "/Volumes/Q" "gdrive:Backups/Q-Drive" --transfers 12

Example (a native rclone remote, e.g. an SMB share configured with
`rclone config` — no OS-level mounting/drive-mapping needed at all):
    python scripts/backup.py "smb-finance:Q" "gdrive:Backups/Q-Drive"

source and destination are both plain rclone paths — either works anywhere
rclone does (local disk, a mapped/mounted network drive, or a remote defined
in `rclone config`, such as an "smb" remote pointing straight at an SMB
server). The script itself never touches the filesystem directly: listing
goes through `rclone lsjson` and transferring through `rclone copy`, so
attaching to the source is entirely rclone's job, configured once via
`rclone config` rather than something this script has to do.

Resuming: just rerun the same command. rclone lists what is already at the
destination and compares it against the source (by size and modification
time, or by checksum with --checksum), then only transfers what is new or
changed. A stopped or interrupted run picks up where it left off with no
extra bookkeeping, and files added since the last run are picked up too.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_file import should_back_up


def load_skip_log(skip_log_path: Path) -> set[str]:
    if not skip_log_path.exists():
        return set()
    return {line for line in skip_log_path.read_text(encoding="utf-8").splitlines() if line}


def list_source(source: str) -> list[dict]:
    """Run `rclone lsjson <source> --recursive` and return the parsed entries.

    Works identically whether source is a local path, a mapped/mounted drive,
    or a remote:path defined in `rclone config` (e.g. an SMB share) — rclone
    handles the protocol, this just gets back a flat list of every file and
    directory under source with its Path (posix-style, relative to source),
    Size, and IsDir.
    """
    result = subprocess.run(
        ["rclone", "lsjson", source, "--recursive"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.exit(f"Failed to list {source!r}:\n{result.stderr.strip()}")
    return json.loads(result.stdout)


def iter_backup_files(source: str, skip_log_path: Path, files_from_path: Path) -> tuple[int, int]:
    """Filter source's file listing through should_back_up into files_from_path.

    Entries are processed shallowest-first so that once a directory is found
    to be prunable (an excluded app-copy folder, say), every entry beneath it
    is skipped as a no-op rather than being individually checked and logged —
    the same "never even look inside" behavior as the old os.walk-based
    pruning, just implemented over a flat listing instead of a live walk.

    Returns (files kept, total bytes of files kept). That byte total is the
    ground truth for progress reporting later: rclone only learns the true
    total for a job gradually, as its checkers work through the file list, so
    early in a run its own "total files/bytes" figures are a partial,
    still-growing count rather than the real job size.
    """
    already_logged = load_skip_log(skip_log_path)
    new_skips: list[str] = []

    def record_skip(relative: str) -> None:
        if relative not in already_logged:
            already_logged.add(relative)
            new_skips.append(relative)

    entries = list_source(source)
    entries.sort(key=lambda e: e["Path"].count("/"))  # parents before their children

    pruned_prefixes: list[str] = []

    def is_pruned(path: str) -> bool:
        return any(path == prefix or path.startswith(prefix + "/") for prefix in pruned_prefixes)

    kept_count = 0
    kept_bytes = 0

    with open(files_from_path, "w", encoding="utf-8") as files_from:
        for entry in entries:
            path = str(entry["Path"]).replace("\\", "/")
            if is_pruned(path):
                continue  # already covered by an ancestor directory's single skip-log line

            if entry.get("IsDir"):
                if not should_back_up(path + "/"):
                    record_skip(path + "/")
                    pruned_prefixes.append(path)
                continue

            if should_back_up(path):
                files_from.write(path + "\n")
                kept_count += 1
                kept_bytes += entry.get("Size") or 0
            else:
                record_skip(path)

    if new_skips:
        with open(skip_log_path, "a", encoding="utf-8") as f:
            for skip_entry in new_skips:
                f.write(skip_entry + "\n")

    return kept_count, kept_bytes


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


def _run_rclone_quiet(command: list[str], total_files: int, total_bytes: int) -> int:
    """Run rclone with JSON logging and print one summary line per 1% of progress.

    Meant for a job whose output is being redirected to a log file: rclone's
    own --progress bar repaints a line in place with carriage returns, which
    turns into unreadable noise in a log file. This reads rclone's periodic
    JSON stats off stderr instead and only echoes a line when the percentage
    complete has actually moved, so a log file gets a clean, appendable trail.

    Percent, "files remaining", and ETA are all computed against total_files
    and total_bytes rather than rclone's own totalTransfers/totalBytes stats.
    rclone only discovers the true job total gradually, as its checkers work
    through the file list, so those fields start out as a small, still-growing
    partial count rather than the real total — misleading in a log nobody is
    watching live to see stabilize. total_files/total_bytes come from our own
    scan up front, so they're correct from the very first line.
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

            done_bytes = stats.get("bytes") or 0
            percent = int(done_bytes * 100 / total_bytes) if total_bytes else 0
            if percent == last_percent:
                continue
            last_percent = percent

            transfers_done = stats.get("transfers") or 0
            remaining = max(total_files - transfers_done, 0)
            speed = stats.get("speed") or 0
            remaining_bytes = max(total_bytes - done_bytes, 0)
            eta_text = f"{remaining_bytes / speed:.0f}s" if speed > 0 else "-"
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(
                f"[{timestamp}] {percent}% - {transfers_done:,}/{total_files:,} files "
                f"({remaining:,} remaining) - {_human_size(done_bytes)}/{_human_size(total_bytes)} "
                f"transferred - {_human_size(speed)}/s - ETA {eta_text}",
                flush=True,
            )
    finally:
        process.wait()

    return process.returncode


def run_rclone(
    source: str,
    destination: str,
    files_from: Path,
    transfers: int,
    checkers: int,
    checksum: bool,
    dry_run: bool,
    extra_args: list[str],
    quiet: bool = False,
    total_files: int = 0,
    total_bytes: int = 0,
) -> int:
    command = [
        "rclone", "copy", source, destination,
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
        return _run_rclone_quiet(command, total_files, total_bytes)

    command += ["--progress", "--stats", "5s"]
    command.extend(extra_args)
    print("Running:", " ".join(command))
    return subprocess.call(command)


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up a directory to a remote via rclone, skipping junk files.")
    parser.add_argument(
        "source",
        help="What to back up: a local path, a mapped/mounted drive, or an rclone remote:path (e.g. an SMB share configured with `rclone config`)",
    )
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

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        files_from = Path(f.name)

    try:
        print(f"Listing {args.source} via rclone (this can take a while for a large tree) ...")
        kept_count, kept_bytes = iter_backup_files(args.source, args.skip_log, files_from)
        print(
            f"Found {kept_count:,} files ({_human_size(kept_bytes)}) to back up. "
            f"Skipped paths are recorded in {args.skip_log}"
        )

        if kept_count == 0:
            print("Nothing to back up.")
            return

        exit_code = run_rclone(
            args.source,
            args.destination,
            files_from,
            args.transfers,
            args.checkers,
            args.checksum,
            args.dry_run,
            extra_args,
            args.quiet,
            kept_count,
            kept_bytes,
        )
    finally:
        files_from.unlink(missing_ok=True)

    if exit_code == 0:
        alphabetize_skip_log(args.skip_log)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
