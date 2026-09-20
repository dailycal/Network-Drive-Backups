#!/usr/bin/env python3
"""Decide whether a file found on the network drives is worth backing up."""

import re

_EXCLUDED_EXTENSIONS = {
    "idlk",  # InDesign lock file
    "nd",  # QuickBooks network descriptor
    "tlg",  # QuickBooks transaction log
    "dsn",  # QuickBooks data source name file
    "sds",  # QuickBooks search data file
    "searchindex",  # QuickBooks search cache
    "chk",  # recovered file fragment
    "tmp",  # temporary file
    "bak",  # backup copy of a file
    "old",  # old renamed copy
    "download",  # incomplete browser download
    "crdownload",  # incomplete Chrome download
    "lnk",  # Windows shortcut pointer
    "webloc",  # Mac internet shortcut
    "thumbnail",  # cached image thumbnail
    "thumb",  # cached image thumbnail
}

_EXCLUDED_FILENAMES = {
    ".ds_store",  # Finder folder metadata cache
    "assert.dmp",  # application crash dump
    "debug.log",  # application debug log
}

_ALIAS_EXTENSION_RE = re.compile(r"alias(\s+\d+)?$")

_EXCLUDED_APP_PATH_SEGMENTS = {
    "firefox.app",  # copied Firefox browser install
    "quickbooks premier - nonprofit edition",  # copied QuickBooks installer
    "rapid",  # copied RAPID payroll software
}


def _extension(name: str) -> str:
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def should_back_up(name_or_path: str) -> bool:
    """Return False for junk/cache files, lock files, and copies of installed applications."""
    segments = [s.lower() for s in re.split(r"[\\/]+", name_or_path) if s]
    if any(segment in _EXCLUDED_APP_PATH_SEGMENTS for segment in segments):
        return False

    name = segments[-1] if segments else name_or_path.lower()
    if name in _EXCLUDED_FILENAMES:
        return False

    extension = _extension(name)
    if extension in _EXCLUDED_EXTENSIONS or _ALIAS_EXTENSION_RE.search(extension):
        return False

    return True
