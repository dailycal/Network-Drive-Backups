#!/usr/bin/env python3
"""Tests for scripts/backup.py's filtering, skip-log handling, command-building, and CLI wiring."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import backup


def _touch(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _workspace(tmp: str) -> tuple[Path, Path, Path]:
    """A fresh source dir + skip-log path + files-from path, all scoped to this test's own temp dir."""
    tmp_path = Path(tmp)
    root = tmp_path / "source"
    root.mkdir()
    skip_log = tmp_path / "skipped.txt"
    files_from = tmp_path / "files_from.txt"
    return root, skip_log, files_from


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


class IterBackupFilesTests(unittest.TestCase):
    def _build_sample_tree(self, root: Path) -> None:
        _touch(root / "News" / "story.indd", "story")
        _touch(root / "News" / "story.idlk", "lock")
        _touch(root / "Accounting" / "ledger.QBW", "ledger")
        _touch(root / "Accounting" / "ledger.QBW.TLG", "log")
        _touch(root / ".DS_Store", "")
        _touch(root / "Firefox.app" / "Contents" / "MacOS" / "firefox", "binary")
        _touch(
            root / "QuickBooks Premier - Nonprofit Edition" / "bin" / "qbupdate.exe",
            "installer",
        )

    def test_keeps_real_content_and_skips_junk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, skip_log, files_from = _workspace(tmp)
            self._build_sample_tree(root)

            kept_count = backup.iter_backup_files(root, skip_log, files_from)
            kept = _read_lines(files_from)
            logged = _read_lines(skip_log)

        self.assertEqual(kept_count, len(kept))
        self.assertEqual(sorted(kept), sorted(["News/story.indd", "Accounting/ledger.QBW"]))
        self.assertIn("News/story.idlk", logged)
        self.assertIn("Accounting/ledger.QBW.TLG", logged)
        self.assertIn(".DS_Store", logged)

    def test_prunes_application_folders_instead_of_just_filtering_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, skip_log, files_from = _workspace(tmp)
            self._build_sample_tree(root)

            backup.iter_backup_files(root, skip_log, files_from)
            kept = _read_lines(files_from)
            logged = _read_lines(skip_log)

        # The app folders themselves are recorded as skipped...
        self.assertIn("Firefox.app/", logged)
        self.assertIn("QuickBooks Premier - Nonprofit Edition/", logged)
        # ...but os.walk should never have descended into them, so nothing
        # *inside* those folders shows up anywhere (proves pruning, not
        # post-filtering, which would still have listed the nested files).
        all_seen = kept + logged
        self.assertFalse(any("contents/macos" in entry.lower() for entry in all_seen))
        self.assertFalse(any("qbupdate" in entry.lower() for entry in all_seen))

    def test_empty_directory_yields_nothing_and_no_log_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, skip_log, files_from = _workspace(tmp)
            kept_count = backup.iter_backup_files(root, skip_log, files_from)

        self.assertEqual(kept_count, 0)
        self.assertEqual(_read_lines(files_from), [])
        self.assertFalse(skip_log.exists())


class SkipLogDedupeAndResumeTests(unittest.TestCase):
    def test_rerun_does_not_duplicate_already_logged_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, skip_log, files_from = _workspace(tmp)
            _touch(root / "News" / "story.idlk", "lock")

            backup.iter_backup_files(root, skip_log, files_from)
            backup.iter_backup_files(root, skip_log, files_from)  # simulate a resumed run

            logged = _read_lines(skip_log)

        self.assertEqual(logged.count("News/story.idlk"), 1)

    def test_rerun_only_appends_genuinely_new_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, skip_log, files_from = _workspace(tmp)
            _touch(root / "News" / "story.idlk", "lock")

            backup.iter_backup_files(root, skip_log, files_from)
            _touch(root / "News" / "another.idlk", "lock")
            backup.iter_backup_files(root, skip_log, files_from)

            logged = _read_lines(skip_log)

        self.assertEqual(sorted(logged), sorted(["News/story.idlk", "News/another.idlk"]))

    def test_pre_existing_log_entries_survive_and_are_not_duplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, skip_log, files_from = _workspace(tmp)
            _touch(root / "News" / "story.idlk", "lock")
            skip_log.write_text("Some/Old/Entry.bak\n", encoding="utf-8")

            backup.iter_backup_files(root, skip_log, files_from)

            logged = _read_lines(skip_log)

        self.assertEqual(sorted(logged), sorted(["Some/Old/Entry.bak", "News/story.idlk"]))


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
                Path("/src"), "gdrive:dest", Path("/tmp/list.txt"),
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
                Path("/src"), "gdrive:dest", Path("/tmp/list.txt"),
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
                Path("/src"), "gdrive:dest", Path("/tmp/list.txt"),
                transfers=8, checkers=16, checksum=False, dry_run=False, extra_args=[],
            )

        self.assertEqual(exit_code, 7)

    def test_quiet_mode_swaps_progress_for_json_logging_and_does_not_use_subprocess_call(self):
        with patch("scripts.backup.subprocess.call") as mock_call, \
                patch("scripts.backup._run_rclone_quiet", return_value=0) as mock_quiet:
            exit_code = backup.run_rclone(
                Path("/src"), "gdrive:dest", Path("/tmp/list.txt"),
                transfers=8, checkers=16, checksum=False, dry_run=False, extra_args=[],
                quiet=True,
            )

        self.assertEqual(exit_code, 0)
        mock_call.assert_not_called()
        command = mock_quiet.call_args[0][0]
        self.assertIn("--use-json-log", command)
        self.assertIn("--stats-log-level", command)
        self.assertNotIn("--progress", command)


class FakeRcloneProcess:
    """Stands in for a subprocess.Popen handle streaming rclone's JSON log lines."""

    def __init__(self, lines: list[str], returncode: int = 0):
        self.stderr = iter(lines)
        self.returncode = returncode

    def wait(self) -> int:
        return self.returncode


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
                exit_code = backup._run_rclone_quiet(["rclone", "copy"])

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
                backup._run_rclone_quiet(["rclone", "copy"])

        self.assertIn("Failed to copy: permission denied", buffer.getvalue())

    def test_returns_the_process_return_code(self):
        process = FakeRcloneProcess([_stats_line(1000, 1000, 10, 10)], returncode=9)

        with patch("scripts.backup.subprocess.Popen", return_value=process):
            with redirect_stdout(io.StringIO()):
                exit_code = backup._run_rclone_quiet(["rclone", "copy"])

        self.assertEqual(exit_code, 9)


class MainGuardClauseTests(unittest.TestCase):
    def test_exits_when_root_is_not_a_directory(self):
        with patch("sys.argv", ["backup.py", "/no/such/directory", "gdrive:dest"]):
            with self.assertRaises(SystemExit) as ctx:
                backup.main()

        self.assertIn("Not a directory", str(ctx.exception))

    def test_exits_when_rclone_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("sys.argv", ["backup.py", tmp, "gdrive:dest"]), \
                 patch("scripts.backup.shutil.which", return_value=None):
                with self.assertRaises(SystemExit) as ctx:
                    backup.main()

        self.assertIn("rclone is not installed", str(ctx.exception))

    def test_returns_without_calling_rclone_when_nothing_to_back_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, skip_log, _ = _workspace(tmp)
            _touch(root / "junk.idlk", "lock")

            with patch("sys.argv", ["backup.py", str(root), "gdrive:dest", "--skip-log", str(skip_log)]), \
                 patch("scripts.backup.subprocess.call") as mock_call:
                backup.main()  # returns normally, does not sys.exit

        mock_call.assert_not_called()


class MainFullRunTests(unittest.TestCase):
    def test_scans_filters_writes_file_list_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, skip_log, _ = _workspace(tmp)
            _touch(root / "News" / "story.indd", "story")
            _touch(root / "News" / "story.idlk", "lock")

            captured = {}

            def fake_call(command):
                files_from = Path(command[command.index("--files-from") + 1])
                captured["path"] = files_from
                captured["content"] = files_from.read_text(encoding="utf-8")
                captured["command"] = command
                return 0

            with patch(
                "sys.argv",
                ["backup.py", str(root), "gdrive:dest", "--transfers", "3", "--skip-log", str(skip_log)],
            ), patch("scripts.backup.subprocess.call", side_effect=fake_call):
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
            root, skip_log, _ = _workspace(tmp)
            _touch(root / "News" / "story.idlk", "lock")
            _touch(root / "News" / "keep.indd", "story")

            with patch(
                "sys.argv", ["backup.py", str(root), "gdrive:dest", "--skip-log", str(skip_log)]
            ), patch("scripts.backup.subprocess.call", return_value=0), \
                    patch("scripts.backup.alphabetize_skip_log") as mock_alphabetize:
                with self.assertRaises(SystemExit) as ctx:
                    backup.main()

        self.assertEqual(ctx.exception.code, 0)
        mock_alphabetize.assert_called_once_with(skip_log)

    def test_skip_log_is_not_sorted_when_rclone_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, skip_log, _ = _workspace(tmp)
            _touch(root / "News" / "story.idlk", "lock")
            _touch(root / "News" / "keep.indd", "story")

            with patch(
                "sys.argv", ["backup.py", str(root), "gdrive:dest", "--skip-log", str(skip_log)]
            ), patch("scripts.backup.subprocess.call", return_value=1), \
                    patch("scripts.backup.alphabetize_skip_log") as mock_alphabetize:
                with self.assertRaises(SystemExit) as ctx:
                    backup.main()

        self.assertEqual(ctx.exception.code, 1)
        mock_alphabetize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
