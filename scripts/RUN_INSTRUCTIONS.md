# Running this on Linux (headless server / VM)

Full setup for a Linux box with no GUI — install everything, authenticate both
Google Drive and the SMB share, and kick the backup off in `tmux` so it keeps
running after you disconnect. Assumes a Debian/Ubuntu-based system (`apt`);
substitute your distro's package manager if it's something else.

## 1. Install prerequisites

```
sudo apt update
sudo apt install -y git python3 python3-pip tmux
```

Install rclone with its official installer (gets the latest version — the
version in `apt` is often quite old):
```
sudo -v
curl https://rclone.org/install.sh | sudo bash
```

Verify everything landed:
```
git --version
python3 --version
tmux -V
rclone version
```

## 2. Get the repo

```
git clone <this-repo's-url>
cd network-drive-backups
```
If it's already here, just `git pull` to get the latest scripts.

## 3. Authenticate Google Drive

This machine has no browser, so `rclone config`'s normal "open a browser"
flow won't work — use the two-machine flow instead (authorize from your own
laptop, paste the result back here).

```
rclone config
```
Walk through:
1. `n` — new remote
2. name: `gdrive`
3. Storage: type `drive` to filter the list, pick **Google Drive**
4. `client_id` / `client_secret`: as of 2026, rclone's shared client_id is being retired and it will warn you about this. For a quick start you can accept the warning (`y`, "continue using the shared client_id anyway") and leave both fields blank — but for anything you're relying on long-term, follow rclone's guide to make your own instead: https://rclone.org/drive/#making-your-own-client-id
5. `scope`: `1` (full access) unless you specifically want read-only
6. `root_folder_id`, `service_account_file`: leave blank
7. Edit advanced config: `n`
8. **Use auto config: `n`** — this is the "does this machine have a browser" question; say no
9. rclone prints something like:
   ```
   Execute the following on the machine with the web browser (same rclone
   version recommended):

       rclone authorize "drive" "eyJzY29wZSI6ImRyaXZlIn0"

   Then paste the result.
   ```
   On your **own machine** (with a browser and rclone installed), run that
   **exact** command it printed — the second argument encodes the scope you
   picked, so copy it exactly rather than typing `rclone authorize "drive"`
   from memory. Log in with the Google account you want backed up to,
   approve access, and it'll print a block of JSON. Copy that whole block
   and paste it into the waiting prompt back on the server, then press enter.
10. Confirm the remote looks right, `y` to keep it, `q` to quit config.

Verify:
```
rclone lsd gdrive:
```
Should list the folders at the root of that Drive account with no errors.

## 4. Authenticate the SMB share

No OAuth needed here — just host/credentials.

```
rclone config
```
1. `n` — new remote
2. name: `finance-smb` (or whatever — you'll use this name as the backup source)
3. Storage: type `smb` to filter, pick **smb**
4. `host`: the server's IP or hostname (e.g. `192.168.234.30`)
5. `user`: the SMB username (defaults to your current Linux username if left blank — override it)
6. `port`: leave blank for the default (445) unless your server uses something else
7. `pass`: `y` to enter one, then type it — rclone obscures it automatically before storing it
8. `domain`: leave blank for the default (`WORKGROUP`) unless your server needs a real one
9. Edit advanced config: `n`
10. `y` to keep it, `q` to quit config

Verify:
```
rclone lsd finance-smb:
```
Should list the shares on the server (e.g. `Q`, `P`).

## 5. Run the tests (optional but recommended)

```
python3 -m unittest tests.test_check_file tests.test_backup -v
```
All 33 should report `ok`.

## 6. Kick off the backup in tmux

A 4 TB backup can take days, especially once Google's ~750 GB/day upload cap
on personal accounts kicks in. `tmux` keeps it running after you log out.

Start it **already detached** — no need to attach and then manually detach:
```
tmux new-session -d -s backup "python3 scripts/backup.py finance-smb:Q gdrive:Backups/Q-Drive --quiet > backup.log 2>&1"
```
- Swap `finance-smb:Q` for whichever share/remote you're backing up (`finance-smb:P` for the other drive, etc.), and `gdrive:Backups/Q-Drive` for wherever you want it to land.
- `-s backup` names the session `backup` so you can find it again later.
- `--quiet` makes the output log-friendly — one line per 1% of progress instead of a live-redrawing bar that turns into garbage in a log file (see `scripts/README.md` for details).
- Everything the script and rclone print goes into `backup.log` in the current directory.

## 7. Checking on it later

- Watch it live: `tmux attach -t backup` — detach again without stopping it with `Ctrl+b` then `d`.
- Peek without attaching: `tail -f backup.log`
- Peek at the current screen without attaching at all: `tmux capture-pane -t backup -p`
- List all sessions: `tmux ls`
- Confirm the process is actually alive: `ps aux | grep rclone`

Note the session disappears from `tmux ls` on its own once the backup command finishes (there's no shell left running inside it) — that's expected, not a crash. Check `backup.log`'s last lines for how it actually ended.

## 8. If it stops (SSH drop, reboot, anything)

Just rerun the exact same `tmux new-session ...` command from step 6. Both
the script and rclone are built to resume: rclone only transfers what's
missing or changed at the destination, and the skip log is deduplicated
across runs — rerunning never re-uploads or double-logs anything.

## 9. Cleaning up

Once `backup.log` shows the job finished, the tmux session will already be
gone on its own (see the note in step 7). If you ever need to stop it
early: `tmux kill-session -t backup`.
