English | [日本語](README.ja.md)

# Kiro Crew Devin ACP patch

A reusable, experimental patch that lets Kiro Crew use Devin CLI.
**It supports choosing the harness for a Bot, checking saved settings, authenticating Bot operation and panel servers, and workers.**
It is maintained independently of the official Kiro Crew project, and there is no plan to propose it upstream.

**Agent and Bot operation are both confirmed.** The results from the user's own environment and from an isolated environment are in [the verification results](result.md).

## Distributed patches

| File | Purpose |
| --- | --- |
| `patches/devin.patch` | For a fresh install. Contains the Devin ACP foundation plus all Bot support and fixes |
| `patches/devin-bot.patch` | Additional fixes on top of the foundation patch first distributed in this repository |
| `patches/devin-base.patch` | An archived copy of the first foundation patch. Not for installing on its own |

This repository does not keep a copy of Kiro Crew itself. It manages only the changes.
`manifest.json` records the base commit, the supported CLI version, the SHA-256 of each patch, and the verification results.

## What it does, and its limits

- Select Devin in the Dashboard's Agent Harness and in a Bot's settings
- ACP handshake, the account's model list, prompts, and session resume
- Checks that a Bot's saved settings match what the session actually loaded
- MCP connections for Bot operation, panels, and workers
- A dedicated bridge into Crew's approval path. Persistent permissions and bypass are never handed over
- Keeps the OS sandbox and the credential mask. Bot MCP servers use process-bound IPC authentication
- Verification scripts that reject out-of-scope operations, frame replay, and tests for selection, saving, and authentication

The supported Devin CLI version is **`3000.12.17`**. Other versions stop at run time.
Workspaces that contain native project `.devin` config, hooks, and similar files are refused.
Shared runtimes, read-only Side Chat, manual compaction, and full compatibility with native slash commands are not supported.
Per-tool limits and policies that cannot be applied to Bots and workers are rejected, following the existing checks in Crew itself.
See the Devin section of `docs/system-specs/modules/harness-onboarding.md` after applying the patch for details.

Crew starts the local `devin acp` process. It does not use Devin Cloud sessions (`devin --cloud`). You still need to sign in to Devin.

## Base and prerequisites

- Kiro Crew base commit: `6de3b0aae57a8406ef30b39a40b3a5eef99321f5`
- Python 3.12 or later, and the development and build environment of Kiro Crew itself
- Devin sign-in: `devin auth login` / `devin auth status`
- Verified OS: Linux. Other operating systems are not verified

## Fresh install

```sh
python3 manage.py check /path/to/KiroCrew
python3 manage.py apply /path/to/KiroCrew
```

## If you already applied the first foundation patch

```sh
python3 manage.py check /path/to/KiroCrew --bot-only
python3 manage.py apply /path/to/KiroCrew --bot-only
```

The additional patch assumes the content of `devin-base.patch`. If your checkout has a partial Bot implementation or your own edits, sort those differences out first. Nothing here overwrites conflicts automatically.

In both cases, build Crew with its own procedure after applying and restart the gateway.
Choose Devin in a Bot's settings, then check from a new conversation or worker.
Existing conversations may keep using their original runtime.

The script checks the patch SHA-256 and whether it applies, and changes code only for `apply`.
It warns when the base commit differs, and stops on a conflict.
It does not restart the gateway or change personal settings or credential files.

## Removing it

To remove the combined patch:

```sh
python3 manage.py check-reverse /path/to/KiroCrew
python3 manage.py reverse /path/to/KiroCrew
```

To remove only the Bot addition and keep the first foundation:

```sh
python3 manage.py check-reverse /path/to/KiroCrew --bot-only
python3 manage.py reverse /path/to/KiroCrew --bot-only
```

Reversal can also stop if you edited the same places after applying.

## When upstream changes

1. Save your own changes to Crew separately.
2. Reverse this patch so it is removed.
3. Update Crew.
4. Run `check` to confirm it applies again. If it conflicts, update the patch by hand.
5. Run the related tests and the build, and confirm that permission allow and reject still work.

When you update the patch, also update the base commit, CLI version, checksums, and verification record in `manifest.json`.
There is no guarantee that it applies unconditionally to the latest upstream.

## Verification

- **1,016** related backend tests and **149** Dashboard and translation tests pass
- The frontend build, type checks of the changed Python, and the docs lint pass
- From a Bot MCP server inside a real OS sandbox, status and panel calls to an authenticated gateway succeed
- With a real Devin and `AcpClient`: saved-settings confirmation, Bot MCP authentication, per-call approval, shell rejection, and resume after a process restart
- In an isolated Bot's worker: a dedicated echo server and the merged prompt

To run a check against a real Devin after applying:

```sh
.venv/bin/python scripts/probe_devin_member.py
```

This explicit live check creates a trial session using your existing Devin sign-in.
It uses a temporary Crew home, a temporary Bot configuration, and a loopback gateway that returns synthetic data, so it does not change your personal Bot settings or a running gateway. It approves only the specified read-only tools and the worker's echo tool. `--record` saves raw transcripts; review their contents before publishing.
The normal pytest run does not connect to the external Devin service.

## Updating this repository

Change the code itself in a separate working tree. This repository stores only patches, the apply tool, and verification records.
Do not copy the whole Crew tree here or push Crew's git history to this repository.
Use a GitHub noreply address for the commit email.

Before publishing, stage the files you are adding explicitly and run the check:

```sh
python3 verify.py
```

This check covers the patch-only layout, local usernames from the machine it runs on, representative credential formats, patch checksums, and commit emails.
Also review recorded conversations and tool output yourself for personal data.

## License

The apply scripts, documentation, and original additions in this repository are MIT.
Code, documents, and context lines derived from Kiro Crew keep their original Apache-2.0 license.
The patches contain changes to them. The original license and NOTICE are included under `licenses/`.
The MIT designation does not replace the rights notices or conditions of the upstream-derived parts.
