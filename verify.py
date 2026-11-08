#!/usr/bin/env python3
"""Verify this patch-only package and its publication metadata."""

import getpass
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FILES = {
    '.gitattributes', '.gitignore', 'LICENSE', 'README.md', 'README.ja.md',
    'result.md', 'result.ja.md',
    'manifest.json', 'manage.py', 'verify.py',
}


def git(*args):
    return subprocess.run(
        ['git', '-C', str(ROOT), *args], capture_output=True, text=True,
        encoding='utf-8', check=True,
    ).stdout


def allowed(name):
    path = Path(name)
    return name in FILES or (
        len(path.parts) == 2 and (
            path.parts[0] == 'licenses'
            or (path.parts[0] == 'patches' and path.suffix == '.patch')
        )
    )


def main():
    tracked = git('ls-files', '-z').split('\0')
    usernames = {getpass.getuser(), Path.home().name} - {'root', 'runner', 'user', ''}
    for name in filter(None, tracked):
        if not allowed(name):
            raise SystemExit('Unexpected source-tree file: ' + name)
        text = (ROOT / name).read_text(encoding='utf-8')
        if any(user in text for user in usernames):
            raise SystemExit('Local username found in: ' + name)
        for pattern in (
            r'gh[pousr]_[A-Za-z0-9]{20,}', r'github_pat_[A-Za-z0-9_]{20,}',
            r'AKIA[A-Z0-9]{16}', r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----',
        ):
            if re.search(pattern, text):
                raise SystemExit('Potential credential found in: ' + name)
    manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    for name, expected in (
        (manifest['patch'], manifest['patch_sha256']),
        (manifest['bot_addon']['patch'], manifest['bot_addon']['sha256']),
        (manifest['archived_base_patch'], manifest['bot_addon']['requires_base_patch_sha256']),
    ):
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise SystemExit('Patch checksum mismatch: ' + name)
    has_head = subprocess.run(
        ['git', '-C', str(ROOT), 'rev-parse', '--verify', 'HEAD'], capture_output=True,
    ).returncode == 0
    if has_head:
        for line in git('log', '--format=%ae%n%ce').splitlines():
            if not line.endswith('@users.noreply.github.com'):
                raise SystemExit('Use a GitHub noreply email for every commit')
    print('Patch-only files, privacy patterns, checksums and commit emails: OK')


if __name__ == '__main__':
    main()
