#!/usr/bin/env python3
"""Check, apply, or reverse the Devin patch without changing runtime settings."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def git(target, *args, capture=False):
    return subprocess.run(
        ["git", "-C", str(target), *args],
        check=True,
        text=True,
        encoding="utf-8",
        capture_output=capture,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "apply", "check-reverse", "reverse"))
    parser.add_argument("checkout", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    patch = root / manifest["patch"]
    digest = hashlib.sha256(patch.read_bytes()).hexdigest()
    if digest != manifest["patch_sha256"]:
        parser.exit(1, "Patch checksum mismatch; stopped.\n")
    target = args.checkout.resolve()
    try:
        top = Path(git(target, "rev-parse", "--show-toplevel", capture=True).stdout.strip())
        if top.resolve() != target:
            parser.exit(1, "Pass the root of the Kiro Crew checkout.\n")
        if not (target / "src/kiro_crew/agent_sdk/backends.py").is_file():
            parser.exit(1, "The checkout does not contain the expected Kiro Crew source.\n")
        head = git(target, "rev-parse", "HEAD", capture=True).stdout.strip()
        if head != manifest["base"]:
            print(f"Warning: HEAD {head} differs from tested base {manifest['base']}.")
        flags = ["--reverse"] if args.action in ("reverse", "check-reverse") else []
        git(target, "apply", "--check", *flags, str(patch))
        if args.action in ("apply", "reverse"):
            git(target, "apply", *flags, str(patch))
        print(f"{args.action}: OK")
    except subprocess.CalledProcessError as exc:
        if exc.stderr:
            print(exc.stderr, end="")
        parser.exit(1, "Git check or application failed; inspect the reported conflicts.\n")


if __name__ == "__main__":
    main()
