#!/usr/bin/env python3
"""Tests for scripts/backup.py's filtering, skip-log handling, command-building, and CLI wiring."""

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import backup


def _file(path: str, size: int = 0) -> dict:
    return {"Path": path, "Size": size, "IsDir": False}


def _dir(path: str) -> dict:
    return {"Path": path, "Size": 0, "IsDir": True}


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


class IterBackupFilesTests(unittest.TestCase):
    """iter_backup_files takes list_source's output (mocked here) and applies
    should_back_up to it — no real filesystem or rclone binary involved, so
    these stay fast and hermetic regardless of what's installed."""

    SAMPLE_ENTRIES = [
        _dir("News"),
        _file("News/story.indd", size=5),
        _file("News/story.idlk", size=1),
        _dir("Accounting"),
        _file("Accounting/ledger.QBW", size=7),
        _file("Accounting/ledger.QBW.TLG", size=2),
        _file(".DS_Store", size=0),
        _dir("Firefox.app"),
        _dir("Firefox.app/Contents"),
        _dir("Firefox.app/Contents/MacOS"),
        _file("Firefox.app/Contents/MacOS/firefox", size=1000),
        _dir("QuickBooks Premier - Nonprofit Edition"),
        _dir("QuickBooks Premier - Nonprofit Edition/bin"),
        _file("QuickBooks Premier - Nonprofit Edition/bin/qbupdate.exe", size=2000),
    ]

    def test_keeps_real_content_and_skips_junk(self):
        with tempfile.TemporaryDirectory() as tmp:
            skip_log = Path(tmp) / "skipped.txt"
            files_from = Path(tmp) / "files_from.txt"

            with patch("scripts.backup.list_source", return_value=list(self.SAMPLE_ENTRIES)):
                kept_count, kept_bytes = backup.iter_backup_files("fake:source", skip_log, files_from)

            kept = _read_lines(files_from)
            logged = _read_lines(skip_log)

        self.assertEqual(kept_count, 2)
        self.assertEqual(kept_bytes, 5 + 7)
        self.assertEqual(sorted(kept), sorted(["News/story.indd", "Accounting/ledger.QBW"]))
        self.assertIn("News/story.idlk", logged)
        self.assertIn("Accounting/ledger.QBW.TLG", logged)
        self.assertIn(".DS_Store", logged)

    def test_prunes_application_folders_instead_of_just_filtering_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            skip_log = Path(tmp) / "skipped.txt"
            files_from = Path(tmp) / "files_from.txt"

            with patch("scripts.backup.list_source", return_value=list(self.SAMPLE_ENTRIES)):
                backup.iter_backup_files("fake:source", skip_log, files_from)

            kept = _read_lines(files_from)
            logged = _read_lines(skip_log)

        # The app folders themselves are recorded as skipped, once each...
        self.assertIn("Firefox.app/", logged)
        self.assertIn("QuickBooks Premier - Nonprofit Edition/", logged)
        # ...but nothing *inside* those folders shows up anywhere, proving
        # they were pruned as a whole rather than individually filtered
        # (which would have listed and logged each nested file separately).
        all_seen = kept + logged
        self.assertFalse(any("contents/macos" in entry.lower() for entry in all_seen))
        self.assertFalse(any("qbupdate" in entry.lower() for entry in all_seen))

    def test_nested_app_folder_is_pruned_wherever_it_appears(self):
        entries = [
            _dir("Accounting"),
            _dir("Accounting/RAPID"),
            _dir("Accounting/RAPID/bin"),
            _file("Accounting/RAPID/bin/BeneIn.exe", size=999),
            _file("Accounting/ledger.QBW", size=7),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            skip_log = Path(tmp) / "skipped.txt"
            files_from = Path(tmp) / "files_from.txt"

            with patch("scripts.backup.list_source", return_value=entries):
                kept_count, kept_bytes = backup.iter_backup_files("fake:source", skip_log, files_from)

            kept = _read_lines(files_from)
            logged = _read_lines(skip_log)

        self.assertEqual(kept, ["Accounting/ledger.QBW"])
        self.assertEqual(logged, ["Accounting/RAPID/"])
        self.assertEqual(kept_count, 1)
        self.assertEqual(kept_bytes, 7)

    def test_empty_listing_yields_nothing_and_no_log_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            skip_log = Path(tmp) / "skipped.txt"
            files_from = Path(tmp) / "files_from.txt"

            with patch("scripts.backup.list_source", return_value=[]):
                kept_count, kept_bytes = backup.iter_backup_files("fake:source", skip_log, files_from)

        self.assertEqual(kept_count, 0)
        self.assertEqual(kept_bytes, 0)
        self.assertEqual(_read_lines(files_from), [])
        self.assertFalse(skip_log.exists())


class SkipLogDedupeAndResumeTests(unittest.TestCase):
    def test_rerun_does_not_duplicate_already_logged_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            skip_log = Path(tmp) / "skipped.txt"
            files_from = Path(tmp) / "files_from.txt"

            with patch("scripts.backup.list_source", return_value=[_file("News/story.idlk", size=1)]):
                backup.iter_backup_files("fake:source", skip_log, files_from)
                backup.iter_backup_files("fake:source", skip_log, files_from)  # simulate a resumed run

            logged = _read_lines(skip_log)

        self.assertEqual(logged.count("News/story.idlk"), 1)

    def test_rerun_only_appends_genuinely_new_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            skip_log = Path(tmp) / "skipped.txt"
            files_from = Path(tmp) / "files_from.txt"

            with patch("scripts.backup.list_source", return_value=[_file("News/story.idlk", size=1)]):
                backup.iter_backup_files("fake:source", skip_log, files_from)

            entries = [_file("News/story.idlk", size=1), _file("News/another.idlk", size=1)]
            with patch("scripts.backup.list_source", return_value=entries):
                backup.iter_backup_files("fake:source", skip_log, files_from)

            logged = _read_lines(skip_log)

        self.assertEqual(sorted(logged), sorted(["News/story.idlk", "News/another.idlk"]))

    def test_pre_existing_log_entries_survive_and_are_not_duplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            skip_log = Path(tmp) / "skipped.txt"
            files_from = Path(tmp) / "files_from.txt"
            skip_log.write_text("Some/Old/Entry.bak\n", encoding="utf-8")

            with patch("scripts.backup.list_source", return_value=[_file("News/story.idlk", size=1)]):
                backup.iter_backup_files("fake:source", skip_log, files_from)

            logged = _read_lines(skip_log)

        self.assertEqual(sorted(logged), sorted(["Some/Old/Entry.bak", "News/story.idlk"]))


class ListSourceIntegrationTests(unittest.TestCase):
    """Checks our parsing against the real rclone binary's actual output,
    since everything else in this file mocks list_source. Skipped when
    rclone isn't installed, rather than failing the whole suite over it."""

    @unittest.skipUnless(shutil.which("rclone"), "rclone is not installed")
    def test_lists_a_real_local_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sub").mkdir()
            (root / "sub" / "file.txt").write_text("hello", encoding="utf-8")

            entries = backup.list_source(str(root))

        by_path = {str(e["Path"]).replace("\\", "/"): e for e in entries}
        self.assertIn("sub", by_path)
        self.assertTrue(by_path["sub"]["IsDir"])
        self.assertIn("sub/file.txt", by_path)
        self.assertFalse(by_path["sub/file.txt"]["IsDir"])
        self.assertEqual(by_path["sub/file.txt"]["Size"], 5)

    @unittest.skipUnless(shutil.which("rclone"), "rclone is not installed")
    def test_exits_with_a_clear_error_for_an_unreachable_source(self):
        with self.assertRaises(SystemExit) as ctx:
            backup.list_source("this-remote-does-not-exist:nope")
        self.assertIn("Failed to list", str(ctx.exception))


class AlphabetizeSkipLogTests(unittest.TestCase):
    def test_sorts_and_dedupes_existing_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            skip_log = Path(tmp) / "skipped.txt"
            skip_log.write_text("Zeta.tmp\nAlpha.tmp\nAlpha.tmp\nBeta.tmp\n", encoding="utf-8")

            backup.alphabetize_skip_log(skip_log)

            lines = skip_log.read_text(encoding="utf-8").splitlines()

        self.assertEqual(lines, ["Alpha.tmp", "Beta.tmp", "Zeta.tmp"])

    def test_missing_file_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            skip_log = Path(tmp) / "does-not-exist.txt"
            backup.alphabetize_skip_log(skip_log)  # must not raise
            self.assertFalse(skip_log.exists())


class RunRcloneCommandTests(unittest.TestCase):
    def test_builds_expected_command_with_defaults(self):
        with patch("scripts.backup.subprocess.call", return_value=0) as mock_call:
            exit_code = backup.run_rclone(
                "/src", "gdrive:dest", Path("/tmp/list.txt"),
                transfers=8, checkers=16, checksum=False, dry_run=False, extra_args=[],
            )

        self.assertEqual(exit_code, 0)
        command = mock_call.call_args[0][0]
        self.assertEqual(command[:4], ["rclone", "copy", "/src", "gdrive:dest"])
        self.assertEqual(command[command.index("--files-from") + 1], "/tmp/list.txt")
        self.assertEqual(command[command.index("--transfers") + 1], "8")
        self.assertEqual(command[command.index("--checkers") + 1], "16")
        self.assertIn("--progress", command)
        self.assertNotIn("--checksum", command)
        self.assertNotIn("--dry-run", command)

    def test_checksum_dry_run_and_extra_args_are_passed_through(self):
        with patch("scripts.backup.subprocess.call", return_value=0) as mock_call:
            backup.run_rclone(
                "/src", "gdrive:dest", Path("/tmp/list.txt"),
                transfers=4, checkers=8, checksum=True, dry_run=True,
                extra_args=["--bwlimit", "10M"],
            )

        command = mock_call.call_args[0][0]
        self.assertIn("--checksum", command)
        self.assertIn("--dry-run", command)
        self.assertEqual(command[-2:], ["--bwlimit", "10M"])

    def test_returns_rclones_own_exit_code(self):
        with patch("scripts.backup.subprocess.call", return_value=7):
            exit_code = backup.run_rclone(
                "/src", "gdrive:dest", Path("/tmp/list.txt"),
                transfers=8, checkers=16, checksum=False, dry_run=False, extra_args=[],
            )

        self.assertEqual(exit_code, 7)

    def test_quiet_mode_swaps_progress_for_json_logging_and_does_not_use_subprocess_call(self):
        with patch("scripts.backup.subprocess.call") as mock_call, \
                patch("scripts.backup._run_rclone_quiet", return_value=0) as mock_quiet:
            exit_code = backup.run_rclone(
                "/src", "gdrive:dest", Path("/tmp/list.txt"),
                transfers=8, checkers=16, checksum=False, dry_run=False, extra_args=[],
                quiet=True, total_files=42, total_bytes=99_999,
            )

        self.assertEqual(exit_code, 0)
        mock_call.assert_not_called()
        command, total_files, total_bytes = mock_quiet.call_args[0]
        self.assertIn("--use-json-log", command)
        self.assertIn("--stats-log-level", command)
        self.assertNotIn("--progress", command)
        self.assertEqual(total_files, 42)
        self.assertEqual(total_bytes, 99_999)


class FakeRcloneProcess:
    """Stands in for a subprocess.Popen handle streaming rclone's JSON log lines."""

    def __init__(self, lines: list[str], returncode: int = 0):
        self.stderr = iter(lines)
        self.returncode = returncode

    def wait(self) -> int:
        return self.returncode


class FakeCompletedProcess:
    """Stands in for a subprocess.run(...) result."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _stats_line(bytes_done: int, total_bytes: int, transfers: int, total_transfers: int, eta=None) -> str:
    return json.dumps({
        "level": "notice",
        "msg": "stats",
        "stats": {
            "bytes": bytes_done,
            "totalBytes": total_bytes,
            "transfers": transfers,
            "totalTransfers": total_transfers,
            "speed": 1234.0,
            "eta": eta,
        },
    })


class RunRcloneQuietModeTests(unittest.TestCase):
    def test_only_prints_when_the_percent_actually_changes(self):
        lines = [
            _stats_line(0, 1000, 0, 10, eta=20),      # 0% -> prints (first tick)
            _stats_line(5, 1000, 0, 10, eta=19),      # still 0% -> suppressed
            _stats_line(120, 1000, 1, 10, eta=15),    # 12% -> prints
            _stats_line(125, 1000, 1, 10, eta=15),    # still 12% -> suppressed
            _stats_line(999, 1000, 9, 10, eta=1),     # 99% -> prints
        ]
        process = FakeRcloneProcess(lines, returncode=0)

        buffer = io.StringIO()
        with patch("scripts.backup.subprocess.Popen", return_value=process):
            with redirect_stdout(buffer):
                exit_code = backup._run_rclone_quiet(["rclone", "copy"], total_files=10, total_bytes=1000)

        self.assertEqual(exit_code, 0)
        output_lines = [line for line in buffer.getvalue().splitlines() if line]
        self.assertEqual(len(output_lines), 3)
        self.assertIn("0%", output_lines[0])
        self.assertIn("12%", output_lines[1])
        self.assertIn("99%", output_lines[2])
        # Each line carries a timestamp, files transferred/remaining, and size transferred.
        self.assertRegex(output_lines[1], r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]")
        self.assertIn("1/10 files", output_lines[1])
        self.assertIn("9 remaining", output_lines[1])

    def test_non_stats_log_messages_are_always_surfaced(self):
        lines = [
            json.dumps({"level": "error", "msg": "Failed to copy: permission denied"}),
            _stats_line(500, 1000, 5, 10, eta=5),
        ]
        process = FakeRcloneProcess(lines, returncode=0)

        buffer = io.StringIO()
        with patch("scripts.backup.subprocess.Popen", return_value=process):
            with redirect_stdout(buffer):
                backup._run_rclone_quiet(["rclone", "copy"], total_files=10, total_bytes=1000)

        self.assertIn("Failed to copy: permission denied", buffer.getvalue())

    def test_returns_the_process_return_code(self):
        process = FakeRcloneProcess([_stats_line(1000, 1000, 10, 10)], returncode=9)

        with patch("scripts.backup.subprocess.Popen", return_value=process):
            with redirect_stdout(io.StringIO()):
                exit_code = backup._run_rclone_quiet(["rclone", "copy"], total_files=10, total_bytes=1000)

        self.assertEqual(exit_code, 9)

    def test_totals_are_anchored_to_our_own_scan_not_rclones_still_growing_stats(self):
        # rclone's own totalTransfers/totalBytes (10/1000 here) only reflect what
        # its checkers have discovered *so far* and grow over the course of a
        # run. The percent/remaining/ETA math must use the totals we pass in
        # (from our own upfront scan), not these still-converging fields.
        lines = [_stats_line(bytes_done=2_000, total_bytes=1_000, transfers=2, total_transfers=10, eta=5)]
        process = FakeRcloneProcess(lines, returncode=0)

        buffer = io.StringIO()
        with patch("scripts.backup.subprocess.Popen", return_value=process):
            with redirect_stdout(buffer):
                backup._run_rclone_quiet(["rclone", "copy"], total_files=25_852, total_bytes=100_000)

        output = buffer.getvalue()
        self.assertIn("2%", output)  # 2,000 / 100,000, not rclone's own (misleading) 1,000 total
        self.assertIn("2/25,852 files", output)
        self.assertIn("25,850 remaining", output)


class MainGuardClauseTests(unittest.TestCase):
    def test_exits_when_rclone_is_missing(self):
        with patch("sys.argv", ["backup.py", "some-source", "gdrive:dest"]), \
                patch("scripts.backup.shutil.which", return_value=None):
            with self.assertRaises(SystemExit) as ctx:
                backup.main()

        self.assertIn("rclone is not installed", str(ctx.exception))

    def test_exits_with_a_clear_error_when_the_source_cannot_be_listed(self):
        with patch("sys.argv", ["backup.py", "smb-bad:Share", "gdrive:dest"]), \
                patch("scripts.backup.subprocess.run", return_value=FakeCompletedProcess(1, stderr="boom")):
            with self.assertRaises(SystemExit) as ctx:
                backup.main()

        self.assertIn("Failed to list", str(ctx.exception))

    def test_returns_without_calling_rclone_when_nothing_to_back_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            skip_log = Path(tmp) / "skipped.txt"

            with patch(
                "sys.argv", ["backup.py", "fake:source", "gdrive:dest", "--skip-log", str(skip_log)]
            ), patch("scripts.backup.list_source", return_value=[_file("junk.idlk", size=1)]), \
                    patch("scripts.backup.subprocess.call") as mock_call:
                backup.main()  # returns normally, does not sys.exit

        mock_call.assert_not_called()


class MainFullRunTests(unittest.TestCase):
    def test_scans_filters_writes_file_list_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            skip_log = Path(tmp) / "skipped.txt"
            entries = [_file("News/story.indd", size=5), _file("News/story.idlk", size=1)]

            captured = {}

            def fake_call(command):
                files_from = Path(command[command.index("--files-from") + 1])
                captured["path"] = files_from
                captured["content"] = files_from.read_text(encoding="utf-8")
                captured["command"] = command
                return 0

            with patch(
                "sys.argv",
                ["backup.py", "fake:source", "gdrive:dest", "--transfers", "3", "--skip-log", str(skip_log)],
            ), patch("scripts.backup.list_source", return_value=entries), \
                    patch("scripts.backup.subprocess.call", side_effect=fake_call):
                with self.assertRaises(SystemExit) as ctx:
                    backup.main()

        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("News/story.indd", captured["content"])
        self.assertNotIn("story.idlk", captured["content"])
        self.assertEqual(captured["command"][captured["command"].index("--transfers") + 1], "3")
        # The temp files-from list is cleaned up once rclone has run.
        self.assertFalse(captured["path"].exists())

    def test_skip_log_is_sorted_only_after_a_successful_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            skip_log = Path(tmp) / "skipped.txt"
            entries = [_file("News/story.idlk", size=1), _file("News/keep.indd", size=5)]

            with patch(
                "sys.argv", ["backup.py", "fake:source", "gdrive:dest", "--skip-log", str(skip_log)]
            ), patch("scripts.backup.list_source", return_value=entries), \
                    patch("scripts.backup.subprocess.call", return_value=0), \
                    patch("scripts.backup.alphabetize_skip_log") as mock_alphabetize:
                with self.assertRaises(SystemExit) as ctx:
                    backup.main()

        self.assertEqual(ctx.exception.code, 0)
        mock_alphabetize.assert_called_once_with(skip_log)

    def test_skip_log_is_not_sorted_when_rclone_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            skip_log = Path(tmp) / "skipped.txt"
            entries = [_file("News/story.idlk", size=1), _file("News/keep.indd", size=5)]

            with patch(
                "sys.argv", ["backup.py", "fake:source", "gdrive:dest", "--skip-log", str(skip_log)]
            ), patch("scripts.backup.list_source", return_value=entries), \
                    patch("scripts.backup.subprocess.call", return_value=1), \
                    patch("scripts.backup.alphabetize_skip_log") as mock_alphabetize:
                with self.assertRaises(SystemExit) as ctx:
                    backup.main()

        self.assertEqual(ctx.exception.code, 1)
        mock_alphabetize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
