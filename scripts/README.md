# scripts/

- `check_file.py` — exports `should_back_up(path)`, which returns `False` for junk/cache/lock files and for copies of installed applications found on the drives (Firefox, QuickBooks Premier, RAPID), and `True` for everything else.
- `backup.py` — lists a source through `rclone lsjson`, filters it through `should_back_up`, and hands the result to `rclone copy` for a resumable, concurrent upload. Both listing and copying go through rclone, so the source can be a local path, a mapped/mounted drive, *or* a native rclone remote (e.g. an SMB share configured once with `rclone config`) — the script never touches the filesystem or a network share directly.
- Tests live in `../tests/test_check_file.py` and `../tests/test_backup.py`.

This file covers Windows and Mac setup. For a headless Linux server — including authenticating Google Drive without a browser and running the job unattended in `tmux` — see [`RUN_INSTRUCTIONS.md`](./RUN_INSTRUCTIONS.md) instead.

## 1. Install prerequisites

You need Git (to get this repo), Python 3.9+ (to run the scripts), and rclone (to do the actual uploading).

### Git

**Windows**
1. Download the installer from https://git-scm.com/download/win and run it (defaults are fine).
2. Open a new terminal (PowerShell or Git Bash) and confirm with:
   ```
   git --version
   ```

**Mac**
1. Run `xcode-select --install` in Terminal and accept the prompt — this installs Apple's Command Line Tools, which include Git. (If you already use Homebrew, `brew install git` also works.)
2. Confirm with:
   ```
   git --version
   ```

### Python

**Windows**
1. Download the installer from https://www.python.org/downloads/windows/.
2. Run it, and make sure you check **"Add python.exe to PATH"** on the first screen before clicking Install.
3. Open a new terminal and confirm with:
   ```
   python --version
   ```

**Mac**
1. Download the macOS installer from https://www.python.org/downloads/macos/ and run it (or `brew install python` if you use Homebrew).
2. Confirm with:
   ```
   python3 --version
   ```

### rclone

**Windows**
1. Download the Windows zip from https://rclone.org/downloads/.
2. Extract it somewhere permanent (e.g. `C:\rclone\`), then add that folder to your PATH: search "Edit the system environment variables" → Environment Variables → edit the `Path` variable under your user → add `C:\rclone\`.
3. Open a new terminal and confirm with:
   ```
   rclone version
   ```

**Mac**
1. `brew install rclone` (or download the macOS zip from https://rclone.org/downloads/ and place the binary somewhere on your PATH, e.g. `/usr/local/bin`).
2. Confirm with:
   ```
   rclone version
   ```

**Both platforms** — one-time setup of a Google Drive remote:
```
rclone config
```
Choose `n` for a new remote, name it `gdrive`, pick **Google Drive** from the storage list, and follow the browser sign-in prompt (accept the defaults for the rest of the questions unless you know you need something different). Test it worked with:
```
rclone lsd gdrive:
```

**Optional but recommended** — one-time setup of the SMB share itself as a remote too, so the script can reach it directly without you first mapping a drive letter or connecting to it in Finder:
```
rclone config
```
Choose `n`, name it something like `finance-smb`, pick **smb** from the storage list, then fill in:
- `host` — the server's address (e.g. `192.168.234.30`)
- `user` / `pass` — credentials for the share (`rclone config` will offer to obscure the password for you)
- `domain` — leave blank unless your server requires one

Test it worked with:
```
rclone lsd finance-smb:
```
That should list the shares on the server. From then on, `finance-smb:Q` (share name `Q`) is a valid source for the backup script — see the examples below.

## 2. Get the repo

```
git clone <this-repo's-url>
cd network-drive-backups
```
If you already have a copy, just `cd` into it and run `git pull` to get the latest scripts.

## 3. Run the tests

Same command on both platforms, from the repo root:

**Windows**
```
python -m unittest tests.test_check_file tests.test_backup -v
```

**Mac**
```
python3 -m unittest tests.test_check_file tests.test_backup -v
```

All 33 tests should report `ok`.

## 4. Run the backup

```
python scripts/backup.py <source> <destination>
```
- `<source>` — what to back up. Any of: a local path, a mapped/mounted network drive, or a `remote:path` you set up with `rclone config` (e.g. the `finance-smb:Q` SMB remote from step 1).
- `<destination>` — an `rclone` destination, in `remote:path` form (e.g. `gdrive:Backups/Q-Drive`).

**Recommended — a native SMB remote**, no drive-mapping or Finder-connecting needed, and it works the same from any machine that has this repo and rclone configured:
```
python scripts/backup.py finance-smb:Q gdrive:Backups/Q-Drive
```

**Alternative — a local path or a drive you've already mapped/mounted yourself:**

Windows:
```
python scripts\backup.py Q:\ gdrive:Backups/Q-Drive
```

Mac (once the share is connected, it typically shows up under `/Volumes`):
```
python3 scripts/backup.py /Volumes/Q gdrive:Backups/Q-Drive
```

The script prints how many files it found and where the skipped ones were logged, then hands the real ones to rclone, which shows a live progress bar (bytes transferred, speed, ETA, files remaining).

### Useful flags

- `--transfers N` — number of files to upload concurrently (default 8). Bump this up (e.g. `--transfers 16`) for more parallelism if your connection can take it.
- `--checkers N` — number of concurrent existence checks against the destination (default 16).
- `--checksum` — compare files by content hash instead of size + modified time before deciding to skip them. Slower on a 4 TB tree, but the strongest guarantee that "already backed up" really means identical.
- `--dry-run` — print what would be transferred without uploading anything.
- `--quiet` — for when output is being redirected to a log file instead of watched live (e.g. `python scripts/backup.py Q:\ gdrive:Q-Finance --quiet > backup.log 2>&1`). Swaps rclone's live-redrawing progress bar (unreadable once it's full of `\r` characters in a log file) for one clean line per 1% of progress, each with a timestamp, files transferred/remaining, and bytes transferred:
  ```
  [2026-09-20 16:55:49] 41% - 10,612/25,852 files (15,240 remaining) - 10.1 GiB/24.7 GiB transferred - 6.6 MiB/s - ETA 34m12s
  ```
  Real errors and warnings from rclone are still always printed in full, regardless of the 1% throttling.

  The file/byte totals in that line come from the script's own scan, not from rclone's internal counters. rclone only learns a job's true total gradually, as its checkers work through the file list — early in a run its own totals are a small, still-growing partial count (e.g. it might briefly report "76 files, 16.5 GiB" when the real job is 25,852 files and 24.7 GiB), which would be a misleading thing to leave sitting in a log nobody's watching live. The script already knows the real numbers from its own scan, so it uses those instead.
- `--skip-log path.txt` — where excluded paths are recorded, one per line (default: `skipped_files.txt` in the current directory).

### The skip log

Every excluded path — junk files and copies of installed applications alike — is written to the skip-log file so you can review what got left out. A few things make it safe across a multi-day, stop-and-resume backup:

- Paths are only ever appended if they're not already in the file, so re-scanning the same tree on a resumed run never creates duplicate lines.
- The file is written to as soon as the scan finishes, before rclone starts uploading, so it's not lost if the upload phase gets interrupted.
- It's only sorted alphabetically once a run finishes with every file successfully backed up (rclone exits with code 0). A run that fails partway through leaves the log as-is, so a later resumed run can keep appending to it safely without repeatedly re-sorting a job that isn't actually done yet.

### Stopping and resuming

It's safe to stop the script at any time (Ctrl+C). To resume, run the exact same command again: rclone lists what's already at the destination and compares it against the source, so it only uploads what's new, changed, or was never finished — nothing gets re-uploaded, and nothing gets missed. There's no separate progress file to manage; the destination itself is the record of what's done.

### A quota note for ~4 TB

Google Drive personal accounts commonly cap uploads at around 750 GB/day. At 4 TB, expect the backup to take several days regardless of concurrency — rclone will hit the daily limit, back off, and you can just leave it running or rerun the command the next day. The resume behavior above makes that safe to do without thinking about it.
