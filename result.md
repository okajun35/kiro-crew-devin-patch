English | [日本語](result.ja.md)

# Verification results

**Agent and Bot operation are both confirmed.**

| Area | Result | What was checked and the basis |
| --- | --- | --- |
| Agent | Confirmed | Confirmed in the user's environment (self-reported, 2026-11-08). In an isolated environment: ACP handshake, model list, prompt, and resume |
| Bot | Confirmed | Confirmed in the user's environment (self-reported, 2026-11-08). In an isolated environment: selection and saving, checking the saved settings, and authentication of Bot MCP and panels |
| Permissions | Confirmed | Per-call approval and rejection with a real Devin and AcpClient. A rejected shell command creates no file |
| Session resume | Confirmed | Resume after a process restart, and checking of the saved Bot settings |
| Worker | Confirmed | A dedicated echo server and the merged prompt in an isolated test Bot |
| Automated tests | Pass | 1,016 related backend tests and 149 Dashboard and translation tests |
| Build and static checks | Pass | Frontend build, type check of the changed Python, and docs lint |
| Patches | Confirmed | The combined patch and foundation plus Bot addition produce the same code; both reverse cleanly; checksums match |

The supported Devin CLI is `3000.12.17`, and the isolated environment ran Linux.
Other operating systems and the unsupported features listed in the README are outside this verification.
The "experimental patch" label stays because it does not mean official support or full feature coverage.

Results from the user's environment are the user's own report. Results from the isolated environment are records of the checks that were run.
This repository contains isolated test data, not records of personal Bots or MCP endpoints.
