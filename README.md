# QuotaDock

A small desktop overview of your remaining AI subscription quota, built for Fedora KDE.

Click the system tray icon to open the overview. Pin it above other windows, resize it, or adjust its opacity. Each provider reports its own quota windows and reset times; QuotaDock does not estimate subscription quota from token counts.

**Early working version:** OpenAI, Claude and Antigravity have been verified against live accounts on Fedora KDE. Legacy Gemini CLI remains a fallback; its successful live reads have not been validated.

## Run

On Fedora with the system PySide6 package already installed:

```bash
./run.sh
```

For an isolated Python environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
./run.sh
```

Python 3.11+ and PySide6 6.8+ are required. The tested desktop uses PySide6 6.11.2.

```bash
./run.sh --demo    # clearly labeled example values; no account reads
./run.sh --hidden  # start in the system tray
```

The overview closes to the tray. Use the tray menu to quit. When unpinned, it dismisses after losing focus. The pin toggle keeps it open and above other windows. Window size, position, pinning and opacity are saved between launches.

## Providers and sign-in

| Card | Quota source | Setup |
| --- | --- | --- |
| OpenAI | Codex app-server `account/rateLimits/read` | Install Codex CLI and run `codex login` with a subscription account. |
| Anthropic | Claude OAuth usage endpoint | Install Claude Code and run `claude auth login`. |
| Google | Antigravity quota summary; legacy Gemini CLI fallback | Open Antigravity CLI (`agy`) and sign in using your Google account. |

The shared OpenAI allowance covers Codex, Work, Workspace Agents and ChatGPT for Excel for the account configuration used in initial validation. Ordinary ChatGPT chat conversations are excluded, so they are not shown as consuming this same allowance. QuotaDock displays the windows returned by OpenAI instead of assuming every plan has both a five-hour and weekly limit.

Claude reads the existing access token from `~/.claude/.credentials.json`, honoring `CLAUDE_CONFIG_DIR`. When a token expires, renew your CLI sign-in and refresh. QuotaDock does not refresh or overwrite Claude's credentials.

Antigravity CLI uses the Linux desktop Secret Service keyring. QuotaDock reads only its `service=gemini`, `username=antigravity` item using `secret-tool` (provided by Fedora’s `libsecret` package). It does not enumerate, copy or change credentials. When the access token expires, open `agy` to renew it and refresh QuotaDock. A locked or unavailable keyring is shown as an explicit connection error. File-based Antigravity credentials and enterprise sign-ins are not supported yet.

The Google card prioritizes Antigravity when its keyring credential is present. It reads `loadCodeAssist` and `retrieveUserQuotaSummary`, displaying shared pools such as Gemini and Claude/GPT separately, with only the windows the account reports. Missing or disabled buckets stay hidden; duplicate shared buckets are counted once. It does not combine Google’s Claude quota with an Anthropic subscription. Google currently requires an Antigravity-compatible User-Agent; QuotaDock identifies itself in its suffix. This compatibility identifier and the undocumented endpoint may need updates as Google changes its clients.

If no Antigravity credential is present, the existing Gemini reader remains available. An expired or unreadable Antigravity login never silently switches to another account.

Legacy Gemini reads the existing Google access token from `~/.gemini/oauth_creds.json`. If it has expired, open Gemini CLI to renew it and then refresh QuotaDock. Gemini encrypted/keychain credentials, API-key authentication and Vertex AI are not supported in this version. `GOOGLE_CLOUD_PROJECT` or `GOOGLE_CLOUD_PROJECT_ID` can supply a Code Assist project; otherwise the adapter asks Code Assist for the existing project. It never onboards a project or enables billing.

Claude and Google's endpoints are integration surfaces used by their clients, not a guaranteed general-purpose public subscription API. They can change or reject third-party access. Missing/unsupported values remain unavailable rather than becoming 0% used or 100% remaining.

## Refresh and privacy

- Automatic polling every five minutes, with a 30-second minimum between manual attempts.
- HTTP 429 responses pause retries for at least 15 minutes, or longer when requested with a numeric `Retry-After` header.
- Failed reads preserve the last successful reading and explicitly mark it stale.
- No prompts, conversation histories, or model inference requests are sent.
- No telemetry, analytics service, or cloud backend.
- Credentials are read locally, never copied to settings, printed, or committed.
- Claude and Google credentials are sent only to their fixed provider endpoints. HTTP redirects are refused.
- Codex manages its own sign-in through its app-server child process; its normal configuration and authentication behavior still apply.
- Preferences live in Qt's user configuration location (normally `~/.config/QuotaDock/`). Usage readings stay in memory.

## KDE and Wayland

QuotaDock defaults to Qt's X11 backend when `DISPLAY` is available, running through XWayland on KDE Wayland. This provides Qt's whole-window opacity and window-manager pinning. Only QuotaDock uses this backend; your desktop session stays on Wayland.

The desktop checks verified tray availability, resizing, KWin's `_NET_WM_STATE_ABOVE` and `_NET_WM_WINDOW_OPACITY` properties. Native Wayland opacity is not implemented; if you explicitly launch with `QT_QPA_PLATFORM=wayland`, the opacity control is disabled. Other operating systems and compositors have not been validated.

## Development

```bash
python3 -m unittest discover -s tests -v
```

- `quotadock/app.py`: tray, popup, controls and preferences.
- `quotadock/controller.py`: background fetching, cooldowns and stale readings.
- `quotadock/providers.py`: account quota readers.
- `quotadock/models.py`: normalized quota windows and response parsers.
- `tests/`: quota calculations, missing values, failure behavior and redirect protection.

## Next steps

- Validate the legacy Gemini fallback with a signed-in account.
- Improve credential renewal and add Antigravity file/enterprise sign-ins without interfering with existing CLI sign-ins.
- Add account selection, optional autostart, and packaging.
- Add automated interaction tests for tray dismissal and persistence.
- Evaluate native Wayland support and additional providers.

## References and inspiration

- [OpenAI app-server documentation](https://learn.chatgpt.com/docs/app-server): documented rate-limit reads.
- [Gemini CLI server](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/code_assist/server.ts) and [response types](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/code_assist/types.ts): quota endpoint and remaining-fraction semantics.
- [Usage Monitor for Claude](https://github.com/jens-duttke/usage-monitor-for-claude): Claude quota integration and tray behavior.
- [OpenUsage](https://github.com/robinebers/openusage): multi-provider quota presentation and account-oriented design; its [Antigravity usage client](https://github.com/robinebers/openusage/blob/main/Sources/OpenUsage/Providers/Antigravity/AntigravityUsageClient.swift) informed the quota-summary request protocol.
- [Antigravity usage documentation](https://antigravity.google/docs/cli/commands/usage): model quota and reset behavior. The installed CLI and local live validation established the Linux keyring credential format.
- [Agent Usage Analyzer](https://github.com/zm2529/agent-usage-analyze), [agentsview](https://github.com/kenn-io/agentsview), and [Agent Monitor for Claude](https://github.com/jens-duttke/agent-monitor-for-claude): adjacent usage and activity tools reviewed for context.
- [Qt system tray](https://doc.qt.io/qt-6/qsystemtrayicon.html) and [window opacity](https://doc.qt.io/qt-6/qwidget.html#windowOpacity-prop).

QuotaDock is an independent project and is not affiliated with these providers or projects.
