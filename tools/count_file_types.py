#!/usr/bin/env python3
"""Count file extensions from a drive-list txt file into a CSV.

Usage: count_file_types.py <drive-list.txt> <output.csv>
"""

import csv
import os
import sys


def get_extension(path: str) -> str:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in name:
        return "(no extension)"
    return name.rsplit(".", 1)[-1].lower()


def get_parent(path: str) -> str | None:
    normalized = path.rstrip("\\/")
    if "\\" in normalized:
        return normalized.rsplit("\\", 1)[0]
    if "/" in normalized:
        return normalized.rsplit("/", 1)[0]
    return None


def find_directories(txt_path: str) -> set[str]:
    directories: set[str] = set()
    with open(txt_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            path = line.strip()
            if not path:
                continue
            parent = get_parent(path)
            if parent:
                directories.add(parent)
    return directories


def load_counts(csv_path: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not os.path.exists(csv_path):
        return counts
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            counts[row["file_type"]] = int(row["count"])
    return counts


def save_counts(csv_path: str, counts: dict[str, int]) -> None:
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["file_type", "count"])
        for file_type, count in sorted(counts.items(), key=lambda item: item[1], reverse=True):
            writer.writerow([file_type, count])


def main() -> None:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <drive-list.txt> <output.csv>", file=sys.stderr)
        sys.exit(1)

    txt_path, csv_path = sys.argv[1], sys.argv[2]

    counts = load_counts(csv_path)
    directories = find_directories(txt_path)

    with open(txt_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            path = line.strip()
            if not path:
                continue
            if path in directories:
                counts["DIRECTORY"] = counts.get("DIRECTORY", 0) + 1
            else:
                ext = get_extension(path)
                counts[ext] = counts.get(ext, 0) + 1

    save_counts(csv_path, counts)


if __name__ == "__main__":
    main()
