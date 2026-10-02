# Speaker Notes

A floating speaker-notes overlay for Windows that is **completely invisible to screen capture** — Teams, Meet, Zoom, OBS, all of them. No admin rights. No drivers. No DLL injection. Just a Python window that hides itself using a single Win32 API call.

---

## Origin

This started with Netflix.

Like most streaming services, Netflix blocks screen recording — try to capture it and you get a black rectangle where the video should be. I got curious about how that worked. After digging into it, I found that Windows exposes a per-window flag called `SetWindowDisplayAffinity`. When you set it to `WDA_EXCLUDEFROMCAPTURE`, the window is rendered normally on your screen but the capture pipeline sees only a transparent void.

Netflix's player sets this flag on its video surface. That's the whole trick.

The obvious next thought: *what if I set that flag on a window I own?* A window full of presenter notes, floating over everything, visible only to me — invisible to whoever is watching my screen share.

That is exactly what this project is.

---

## What it does

- Displays your speaker notes as slide cards in a borderless, always-on-top window
- The window is **excluded from all screen capture** at the OS level — it simply does not exist as far as Teams, Meet, Zoom, or any recording software is concerned
- Notes sync live from your phone via Firebase or a local WebSocket — type on your phone, see it on your laptop instantly
- Three-card layout shows previous slide (faded), current slide (active), and next slide (preview) simultaneously
- Supports rich formatting: headings, subheadings, bullets, bold, and speaker-prompt callout boxes

---

## How the cloaking works

Windows has a display stack with multiple layers. Applications like Netflix render into a protected layer that the capture APIs cannot read. The mechanism is a single function:

```
SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
```

The critical constraint: **the calling process must own the window**. You cannot cloak someone else's window — Windows enforces this. This is why Netflix's protection cannot be trivially bypassed by an external tool, and it is also why self-cloaking works perfectly: our Python process creates its own tkinter window and immediately cloaks it.

There is one subtle gotcha: `tkinter.winfo_id()` returns an inner child HWND, not the top-level window handle. `SetWindowDisplayAffinity` rejects child windows with error 87 (invalid parameter). The fix is `GetParent(winfo_id())` to reach the real top-level HWND.

```python
inner = window.winfo_id()
hwnd  = ctypes.windll.user32.GetParent(inner) or inner
ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x00000011)
```

No admin. No elevated privileges. No side effects on the rest of the system.

---

## Features

- **Screen-capture invisible** — works against Teams, Meet, Zoom, OBS, Windows Game Bar, PrintScreen
- **Three-card viewport** — prev / active / next slide always visible; click to navigate
- **Live sync via Firebase** — type notes on your phone, updates appear on your laptop in under a second
- **Live sync via local WebSocket** — same-network mode with no internet dependency
- **Rich note formatting** — `#` headings, `##` subheadings, `**bold**`, `- bullets`, `> speaker prompts`
- **Speaker prompt boxes** — `>` lines render as a distinct highlighted callout box, separate from the main notes
- **Teleprompter mode** — auto-scroll with live word highlight, comfortable reading pace
- **Slide switcher** — sidebar logs panel lists all slide titles; click to jump
- **Resize + drag** — 8-direction resize handles, drag from title bar
- **Opacity control** — adjustable from the settings popup
- **Meeting timer** — counts up from 00:00:00 on launch
- **Font size control** — A− / A+ buttons in the status bar
- **No admin, no DLL injection, no external drivers**

---

## AI Copilot mode (100% on-device)

A built-in live interview copilot — like Parakeet, but nothing leaves your machine. Click the **✦ COPILOT** sidebar tab to arm it. It listens to the *other side's* audio (the call output, not your mic), transcribes it locally, and when you press **F9** it streams an AI-generated answer straight into the same cloaked card — invisible to the screen share.

- **Fully local** — system audio is captured via WASAPI loopback, transcribed on-device with faster-whisper, and answered by a local LLM running in [Ollama](https://ollama.com). The transcript and the answer never touch the network.
- **Manual trigger** — you decide when to answer (F9); nothing is sent automatically.
- **Your voice** — drop a short résumé summary in `copilot_profile.md` (git-ignored) and answers come back in first person, grounded in your background.

**Setup:** install [Ollama](https://ollama.com) and `ollama pull qwen3:8b`, then `pip install -r requirements.txt` (first run downloads the Whisper model). See `CLAUDE.md` for env overrides and GPU notes.

### Privacy & security notes

- **On-device guarantee:** the copilot only ever talks to `http://localhost:11434` (Ollama). No audio, transcript, or answer is uploaded anywhere. Model weights are downloaded once from Hugging Face and then run offline.
- **While copilot mode is active**, two system-level things happen, and stop the moment you leave the tab or close the app: (1) a **global F9 hotkey** is registered so the trigger works even when the meeting window is focused, and (2) **system output audio is captured** (loopback). Both are torn down on exit.
- The existing **Firebase / Local-WebSocket sync** (for notes) does send your *notes* off-machine by design — that is separate from the copilot, which is never routed into those sync paths. The Local WebSocket server is unauthenticated and LAN-exposed; use it on trusted networks.

---

## Web UI (phone_ui/index.html)

A single HTML file — no build step, no framework. Open it directly in your phone browser or deploy it to GitHub Pages / Vercel.

- Per-slide editor with syntax highlighting overlay
- Smart paste: copies from Word or Google Docs auto-convert to Markdown
- H1 / H2 / Bold / Bullet / Prompt toolbar buttons
- Dark and light theme
- Collapsible connection panel (collapses automatically after connecting)
- Firebase and Local WebSocket modes

---

## Requirements

- Windows 10 version 2004+ or Windows 11 (earlier builds lack `WDA_EXCLUDEFROMCAPTURE`)
- Python 3.9+
- `pywin32` (usually pre-installed or available via pip)

```powershell
pip install -r requirements.txt
```

---

## Setup

**1. Clone and create a virtual environment**
```powershell
git clone https://github.com/Sid009192/speaker-notes-prompter.git
cd speaker-notes-prompter
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

**2. Configure your sync method**

Copy `.env.example` to `.env` and fill in your Firebase URL:
```
FIREBASE_URL=https://your-project-default-rtdb.firebaseio.com
```

Or leave it blank and use the `--firebase` / `--local` flags at runtime (see below).

---

## Running

```powershell
# Activate venv first
.\.venv\Scripts\activate

# Auto-connect using .env
python overlay.py

# Local text file
python overlay.py sample_notes.txt

# Firebase (URL overrides .env)
python overlay.py --firebase https://your-project-default-rtdb.firebaseio.com

# Local WebSocket (same network as phone)
python overlay.py --local
# Prints the LAN IP — enter it in the web UI

# Custom port
python overlay.py --local --port 9000
```

---

## Notes file format

```
# Slide Heading

## Subheading

Regular paragraph text. **Bold inline.**

- Bullet point one
- Bullet point two

> This is a speaker prompt — renders as a highlighted callout box.
> Visible only in the overlay, great for reminders and cues.

---

# Next Slide
```

`---` on its own line separates slides.

---

## Firebase setup (for live phone sync)

1. Go to [console.firebase.google.com](https://console.firebase.google.com) and create a project
2. Build → Realtime Database → Create Database → choose a region → **Start in test mode**
3. Copy the database URL (looks like `https://your-app-default-rtdb.firebaseio.com`)
4. Paste it into `.env` as `FIREBASE_URL=...` or enter it in the web UI

> Test mode rules expire after 30 days. Renew in Firebase Console → Rules if sync stops working.

---

## Project structure

```
overlay.py              # Main application — OverlayApp class
copilot/                # Local, on-device AI copilot (capture, transcribe, LLM)
  capture.py            # WASAPI loopback audio capture
  transcriber.py        # faster-whisper STT (RealtimeSTT + Silero VAD)
  llm.py                # Ollama streaming client (local LLM)
  engine.py             # Orchestrator: audio -> STT -> buffer -> F9 -> answer
phone_ui/
  index.html            # Web UI for phone (no build step)
sample_notes.txt        # Example notes file
requirements.txt        # requests, websockets, + copilot deps
copilot_profile.example.md  # Template for your private résumé summary
.env.example            # Config template — copy to .env
```

---

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| `←` / `→` | Previous / next slide |
| `Ctrl` + `Shift` + `` ` `` or `Ctrl` + `` ` `` | **Toggle show / hide overlay** (system-wide global hotkey + in-app) |
| `Ctrl` + `Shift` + `H` or `Ctrl` + `H` | **Toggle show / hide overlay** (alternative global hotkey) |
| `F2` or `Ctrl` + `E` | **Toggle Read-Only / Editable mode** (locks click-to-edit; enables content space drag) |
| `Alt` + `D` / `Alt` + `I` (or `Ctrl` + `[` / `]`) | **Decrease / increase window opacity** by 10% |
| `F9` | Copilot: answer the latest transcribed question (global while copilot mode is on) |
| `Ctrl` + `Shift` + `Q` | Screen capture: take screenshot of presenter's screen & sync to client |
| `Escape` | Close settings popup / slide switcher / stop copilot / exit |

---

## Limitations

- **Windows only** — `SetWindowDisplayAffinity` is a Win32 API with no equivalent on macOS or Linux
- **Requires Python** — not a standalone executable (though pyinstaller packaging is straightforward)
- Firebase test-mode rules are open read/write — suitable for personal use; add Firebase Auth for shared environments

---

## License

MIT
