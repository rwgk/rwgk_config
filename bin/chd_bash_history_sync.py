#!/usr/bin/env python3
import argparse, shutil, time, sys
from pathlib import Path


def backup(path: Path):
    if not path.exists():
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    return bak


class Entry:
    __slots__ = ("ts", "cmd", "raw_ts_line")

    def __init__(self, ts, cmd, raw_ts_line=None):
        self.ts = ts  # int|None (epoch seconds) or None
        self.cmd = cmd  # str (may include newlines)
        self.raw_ts_line = raw_ts_line  # original "# <epoch>" line or None

    def text_key(self):
        # Exact text key for conservative duplicate detection
        if self.raw_ts_line is not None:
            return self.raw_ts_line + self.cmd
        return self.cmd


def parse_history(path: Path):
    """
    Parse bash history allowing for:
      - timestamped form: lines starting with "# <epoch>" preceding the command
      - commands that contain embedded newlines
      - non-timestamped lines (plain history)
    Returns: list[Entry]
    """
    entries = []
    if not path.exists():
        return entries

    data = path.read_text(errors="replace")
    lines = data.splitlines(keepends=True)

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("# "):
            # timestamp line: "# <epoch>\n"
            raw_ts_line = line
            try:
                ts = int(line[2:].strip())
            except ValueError:
                # Malformed; treat as part of command
                ts = None
                raw_ts_line = None
                # Fall through to plain command handling
                cmd_lines = [line]
                i += 1
                # gather until next timestamp (we'll stop at that)
                while i < len(lines) and not lines[i].startswith("# "):
                    cmd_lines.append(lines[i])
                    i += 1
                entries.append(Entry(None, "".join(cmd_lines)))
                continue

            # Gather subsequent non-# lines as the command (can include newlines)
            i += 1
            cmd_lines = []
            while i < len(lines) and not lines[i].startswith("# "):
                cmd_lines.append(lines[i])
                i += 1
            cmd = "".join(cmd_lines) if cmd_lines else ""
            entries.append(Entry(ts, cmd, raw_ts_line=raw_ts_line))
        else:
            # Plain command line (can be multi-line if history captured it that way)
            cmd_lines = [line]
            i += 1
            while i < len(lines) and not lines[i].startswith("# "):
                cmd_lines.append(lines[i])
                i += 1
            entries.append(Entry(None, "".join(cmd_lines)))
    return entries


def write_history(path: Path, entries):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="") as f:
        for e in entries:
            if e.raw_ts_line is not None:
                f.write(e.raw_ts_line)
            f.write(e.cmd)
            # Ensure commands end with newline for robustness
            if not e.cmd.endswith("\n"):
                f.write("\n")
    tmp.replace(path)


def merge_histories(chd_entries, local_entries):
    """
    Strategy:
      - If both sides are *mostly* timestamped (>=90%), stable-merge by timestamp.
      - Otherwise, keep CHD order, then append local entries whose *exact*
        text (including timestamp line if any) isn’t already present.
      - Avoid aggressive dedup; keep duplicates if they represent different times.
    """

    def pct_ts(entries):
        if not entries:
            return 0.0
        return sum(1 for e in entries if e.ts is not None) / len(entries)

    chd_ts = pct_ts(chd_entries) >= 0.9
    loc_ts = pct_ts(local_entries) >= 0.9

    if chd_ts and loc_ts:
        # Merge like logs: by ts, stable within equal timestamps
        # Build a set of exact-text keys to drop perfect duplicates
        seen = set()
        merged = []
        i = j = 0
        while i < len(chd_entries) or j < len(local_entries):
            pick_from_chd = False
            if i < len(chd_entries) and j < len(local_entries):
                if chd_entries[i].ts <= local_entries[j].ts:
                    pick_from_chd = True
            else:
                pick_from_chd = j >= len(local_entries)
            e = chd_entries[i] if pick_from_chd else local_entries[j]
            if pick_from_chd:
                i += 1
            else:
                j += 1
            key = e.text_key()
            if key in seen:
                continue
            seen.add(key)
            merged.append(e)
        return merged

    # Conservative: keep CHD as base; append any *exact-text* new entries from local
    base = list(chd_entries)
    seen = set(e.text_key() for e in base)
    for e in local_entries:
        k = e.text_key()
        if k not in seen:
            base.append(e)
            seen.add(k)
    return base


def main():
    ap = argparse.ArgumentParser(
        description="Merge Bash histories between local and CHD."
    )
    ap.add_argument(
        "--local",
        default=str(Path.home() / ".bash_history"),
        help="Local HISTFILE (default: ~/.bash_history)",
    )
    ap.add_argument(
        "--chd", required=True, help="CHD HISTFILE (e.g., /mnt/h/.bash_history)"
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write; just report what would change.",
    )
    args = ap.parse_args()

    local_path = Path(args.local).expanduser()
    chd_path = Path(args.chd).expanduser()

    # Safety: insist both dirs exist
    for p in (local_path.parent, chd_path.parent):
        p.mkdir(parents=True, exist_ok=True)

    # Read
    chd_entries = parse_history(chd_path)
    local_entries = parse_history(local_path)

    merged = merge_histories(chd_entries, local_entries)

    # Already identical?
    def as_text(entries):
        out = []
        for e in entries:
            if e.raw_ts_line is not None:
                out.append(e.raw_ts_line)
            out.append(e.cmd if e.cmd.endswith("\n") else e.cmd + "\n")
        return "".join(out)

    merged_text = as_text(merged)
    chd_text = as_text(chd_entries)
    local_text = as_text(local_entries)

    if merged_text == chd_text and merged_text == local_text:
        print("No changes needed; histories already in sync.")
        return

    print(
        f"Merged entries: {len(merged)} (CHD:{len(chd_entries)} + Local:{len(local_entries)})"
    )
    if args.dry_run:
        print("[dry-run] Would update CHD and Local histories.")
        return

    # Backups
    chd_bak = backup(chd_path)
    local_bak = backup(local_path)
    if chd_bak:
        print(f"Backed up CHD -> {chd_bak}")
    if local_bak:
        print(f"Backed up Local -> {local_bak}")

    # Write merged to both
    write_history(chd_path, merged)
    write_history(local_path, merged)

    print(f"Wrote merged history to:\n  {chd_path}\n  {local_path}\nDone.")


if __name__ == "__main__":
    # NOTE: From your shell, run `history -a` first to flush the current session.
    # You can also put that in PROMPT_COMMAND as shown above.
    main()
