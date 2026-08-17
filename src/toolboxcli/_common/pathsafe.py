"""Filename sanitization shared by the media-renaming scripts."""

from __future__ import annotations

import re

# The characters Windows forbids in a path component. Kavita and Jellyfin both
# run against SMB/NTFS shares in practice, so these are stripped regardless of
# the platform the script happens to run on — a name that's legal on APFS but
# not on the server is still a broken name.
ILLEGAL_CHARS_RE = re.compile(r'[<>:"/\\|?*]')


def sanitize_for_path(name: str) -> str:
    name = ILLEGAL_CHARS_RE.sub("", name)
    name = re.sub(r" +", " ", name)
    return name.strip(". ")
