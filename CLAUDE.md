# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This App Does

**Noted** is a Windows desktop daemon that auto-detects Teams calls, records audio (mic + system loopback), transcribes via Whisper, generates structured meeting minutes using Claude, extracts and enriches action items, and surfaces everything through a system tray icon + pywebview web UI.

## Restarting the app during development

**Always use `scripts\restart_noted.ps1`** to stop and restart the daemon:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\restart_noted.ps1
```
Never use `Invoke-CimMethod`, `Win32_Process.Create`, or WMI methods to launch processes — these trigger security alerts in corporate environments (Accenture SOC monitors WMI process creation as a malware indicator).

## Entry Points & Commands

```bash
# Start the daemon (tray app + all background services)
python main.py

# CLI control of a running daemon (writes to .cli_command file for IPC)
python cli.py start
python cli.py stop

# Standalone blocking recording (no daemon needed)
python cli.py record --name "Meeting Name" [--no-minutes]

# Process existing files
python cli.py transcribe path/to/audio.wav
python cli.py minutes path/to/transcript.txt
```

**Windows setup:**
```bash
pip install -r requirements.txt
scripts\install_autostart.bat   # registers Task Scheduler entry
# to run without console: scripts\start_silent.vbs
```

**Diagnostic scripts** (not production, just dev helpers): `tools/check_audio.py`, `tools/check_teams.py`, `tools/check_onenote.py`, `tools/check_onenote_graph.py`, `tools/diagnostico.py`. GSK one-offs: `tools/gsk/`.

## Configuration

`.env` file in project root (all optional):
```
OPENAI_API_KEY=sk-...          # optional, Whisper fallback only
WHISPER_MODEL=medium           # tiny|base|small|medium|large
WHISPER_LANGUAGE=es            # omit for auto-detect
OUTPUT_DIR=C:\path\to\output   # default: project root
```

Key constants in `config.py`: `TEAMS_POLL_INTERVAL=3.0s`, `TEAMS_REQUIRED_CONFIRMATIONS=2`, `CLAUDE_MODEL=claude-sonnet-4-6`, `SAMPLE_RATE=16000Hz`.

## Pipeline & Data Flow

```
teams_detector.py  ──call detected──►  tray_app.py (pipeline queue)
                                              │
                                        popup.py (30s confirmation)
                                              │
                                   audio_recorder.py
                                   (mic + loopback mix → WAV)
                                              │
                                    transcriber.py
                                   (faster-whisper, OpenAI fallback)
                                              │
                                  minutes_generator.py
                                   (claude -p CLI invocation)
                                              │
                                   actions_parser.py
                                   (regex → action blocks)
                                              │
                                  actions_enricher.py
                                   (claude -p → project/assignee map)
                                              │
                              html_exporter.py + outlook_sender.py
                                              │
                                     app_window.py / web UI
```

**Storage tiers:**
- `recordings/` — raw WAVs + paired transcript `.txt`
- `recordings/processed/` — moved here after full pipeline completes
- `minutes/` — `<slug>.md` + `<slug>.html` + `<slug>.actions.json`
- `inbox/` — drop zone; `inbox_watcher.py` converts any audio/video via ffmpeg and feeds to pipeline

## Key Architecture Decisions

### Claude CLI Bridge
Minutes generation and action enrichment both call `claude -p` as a subprocess (not the SDK). The binary is expected in PATH. Calls use cleaned environment (no API key forwarded). If `claude` is missing, the feature degrades gracefully.

### Dual Audio Recording
`audio_recorder.py` records two streams concurrently:
1. **Mic** — via `sounddevice.InputStream` at 16 kHz mono
2. **System loopback** — via `wasapi_loopback_worker.py` subprocess communicating over stdout (8-byte header: sample_rate + channels, then raw float32 PCM)

At stop, streams are mixed with peak normalization. Loopback is discarded if peak < 0.02 or duration is too short.

### Teams Detection
`teams_detector.py` polls every 3 seconds using psutil (process names), window title keywords (`in a call`, `teams call`, etc.), and pycaw audio session state. Requires 2 consecutive confirmations before emitting a state-change callback.

### Minutes & Action Format
`minutes_generator.py` enforces a structured prompt. The output must begin with:
```
TITULO: [3–5 word title in meeting language]
```
Action blocks embedded in markdown:
- ` ~~~instruction-for-claude\n...\n~~~ ` — direct Claude Code instruction
- ` ~~~code_change\n// ARCHIVO:\n// CONTEXTO:\n// INSTRUCCION PARA CLAUDE CODE:\n~~~ `
- ` ~~~document-change\n// ARCHIVO:\n// CONTEXTO:\n// INSTRUCCION:\n~~~ `

`actions_parser.py` uses regex to extract these; `actions_enricher.py` calls Claude to assign each to a project (guessed from directory names) and an assignee.

### Web UI
`app_window.py` wraps pywebview as a singleton and exposes an `AppAPI` class (Python methods callable from JS via `window.pywebview.api.*`). Key methods: `get_meetings()`, `get_actions()`, `execute_action()`, `enrich_actions()`. The web UI (`web/index.html` + `app.js` + `app.css`) is a single-page app with a meetings sidebar and an actions manager view.

### Single Instance
`main.py` writes a `.lock` file with the current PID. On startup it checks if the PID is still alive; if so, it exits. CLI commands communicate with the running daemon by writing to a `.cli_command` file polled by a listener thread in `main.py`.

## Where to Make Changes

| What you want to change | Where |
|---|---|
| Audio mix levels, loopback threshold | `audio_recorder.py` |
| Whisper model / language remapping (Galician→Spanish etc.) | `transcriber.py`, `config.py` |
| Minutes system prompt / action block format | `minutes_generator.py` |
| Action parsing regex | `actions_parser.py` |
| Project/assignee guessing logic | `actions_parser._guess_project()`, `actions_enricher.py` |
| Email template / Outlook calendar search window | `outlook_sender.py` |
| HTML minutes styling | `html_exporter.py` |
| Tray menu items / pipeline triggers | `tray_app.py` |
| Web UI layout and filtering | `web/app.js`, `web/app.css` |
| Storage paths / retention policy | `storage.py` |

## Logging

All modules log to both `noted.log` (project root) and stdout at INFO level. To debug, increase to DEBUG in `main.py`'s `logging.basicConfig` call.
