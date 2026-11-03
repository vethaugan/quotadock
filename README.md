# QuotaDock

A small desktop overview of your remaining AI subscription quota, built for Fedora KDE.

Click the system tray icon to open the overview. Pin it above other windows, resize it, or adjust its opacity. Each provider reports its own quota windows and reset times; QuotaDock does not estimate subscription quota from token counts.

**Early working version:** OpenAI has been verified against a live account. Claude and Gemini adapters have parser tests, but successful live reads still need validation with signed-in accounts.

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
| Google | Gemini CLI / Code Assist quota endpoint | Install Gemini CLI and sign in using your Google account. |

The shared OpenAI allowance covers Codex, Work, Workspace Agents and ChatGPT for Excel for the account configuration used in initial validation. Ordinary ChatGPT chat conversations are excluded, so they are not shown as consuming this same allowance. QuotaDock displays the windows returned by OpenAI instead of assuming every plan has both a five-hour and weekly limit.

Claude reads the existing access token from `~/.claude/.credentials.json`, honoring `CLAUDE_CONFIG_DIR`. When a token expires, renew your CLI sign-in and refresh. QuotaDock does not refresh or overwrite Claude's credentials.

Gemini reads the existing Google access token from `~/.gemini/oauth_creds.json`. If it has expired, open Gemini CLI to renew it and then refresh QuotaDock. Encrypted/keychain credentials, API-key authentication and Vertex AI are not supported in this version. `GOOGLE_CLOUD_PROJECT` or `GOOGLE_CLOUD_PROJECT_ID` can supply a Code Assist project; otherwise the adapter asks Code Assist for the existing project. It never onboards a project or enables billing.

Claude and Gemini's endpoints are integration surfaces used by their clients, not a guaranteed general-purpose public subscription API. They can change or reject third-party access. Missing/unsupported values remain unavailable rather than becoming 0% used or 100% remaining.

## Refresh and privacy

- Automatic polling every five minutes, with a 30-second minimum between manual attempts.
- HTTP 429 responses pause retries for at least 15 minutes, or longer when requested with a numeric `Retry-After` header.
- Failed reads preserve the last successful reading and explicitly mark it stale.
- No prompts, conversation histories, or model inference requests are sent.
- No telemetry, analytics service, or cloud backend.
- Credentials are read locally, never copied to settings, printed, or committed.
- Claude and Gemini credentials are sent only to their fixed provider endpoints. HTTP redirects are refused.
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

- Validate successful Claude and Gemini reads with signed-in accounts.
- Improve credential renewal and support Gemini's encrypted storage without interfering with CLI sign-ins.
- Add account selection, optional autostart, and packaging.
- Add automated interaction tests for tray dismissal and persistence.
- Evaluate native Wayland support and additional providers.

## References and inspiration

- [OpenAI app-server documentation](https://learn.chatgpt.com/docs/app-server): documented rate-limit reads.
- [Gemini CLI server](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/code_assist/server.ts) and [response types](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/code_assist/types.ts): quota endpoint and remaining-fraction semantics.
- [Usage Monitor for Claude](https://github.com/jens-duttke/usage-monitor-for-claude): Claude quota integration and tray behavior.
- [OpenUsage](https://github.com/robinebers/openusage): multi-provider quota presentation and account-oriented design.
- [Agent Usage Analyzer](https://github.com/zm2529/agent-usage-analyze), [agentsview](https://github.com/kenn-io/agentsview), and [Agent Monitor for Claude](https://github.com/jens-duttke/agent-monitor-for-claude): adjacent usage and activity tools reviewed for context.
- [Qt system tray](https://doc.qt.io/qt-6/qsystemtrayicon.html) and [window opacity](https://doc.qt.io/qt-6/qwidget.html#windowOpacity-prop).

QuotaDock is an independent project and is not affiliated with these providers or projects.
