import tkinter as tk
from tkinter import filedialog
import ctypes, re, sys, os, threading, json, time, asyncio

WDA_EXCLUDEFROMCAPTURE = 0x00000011


def _app_dir():
    """Directory to treat as 'next to overlay.py' — the real folder holding
    the .exe when frozen by PyInstaller (onefile extracts __file__ into a
    throwaway _MEIPASS temp dir, which is NOT where .env / vosk models live),
    otherwise the script's own folder for normal `python overlay.py` runs."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

# ── ALEXANDRIA COLOR TOKENS ──────────────────────────────────────────────────
C = {
    "bg":       "#141314",   # outer window bg
    "bar":      "#1b1c1d",   # top bar, sidebar
    "card_dim": "#191a1b",   # prev/next card background
    "card_act": "#1e2022",   # active card background
    "prompt":   "#252829",   # speaker prompt box bg
    "primary":  "#b1c5ff",   # accent bar, active icons, highlights
    "on_s":     "#e3e2e3",   # primary text
    "on_sv":    "#c3c6d5",   # secondary text
    "muted":    "#565966",   # prev/next preview text
    "outline":  "#434653",   # separators
    "tertiary": "#dcc661",   # archival gold
    "red":      "#ff5f56",
    "yellow":   "#ffbd2e",
    "green":    "#27c93f",
}

MIN_W, MIN_H = 480, 360
SERIF = "Noto Serif"
SANS  = "Inter"
LABEL = "Public Sans"


class OverlayApp:

    # Default right-side hint in the copilot transcript pane (replaced by the live
    # partial words while editing the pending question).
    _COPILOT_TR_HINT = "click to edit  ·  Enter answer  ·  F10 new chat"

    def __init__(self, root):
        self.root      = root
        self.slides    = ["# Welcome\n\nOpen a file via ⚙  or connect to sync."]
        self.current   = 0
        self.font_size = 12

        self._drag_x = self._drag_y = 0
        self._resize_data   = None
        self._sync_mode     = None
        self._sync_version  = 0          # increments to cancel old sync threads
        self._firebase_url  = ""         # last connected Firebase URL
        self._last_synced   = None       # last content seen/sent over sync (echo guard)
        self._edit_guard_until = 0       # ignore inbound sync briefly after a local edit
        self._fb_last_ok    = 0          # time of last successful Firebase poll (badge health)
        self._fb_presence_val = None     # last phone heartbeat value seen
        self._fb_presence_at  = 0        # local time that heartbeat last changed (peer-present)
        self._last_cmd_ts     = 0        # timestamp of the last processed remote command
        self._editing       = False      # active-card edit mode flag
        self._live_after    = None       # debounce id for live edit broadcasts
        self._ws_clients    = set()      # connected local-WS clients
        self._ws_loop       = None       # the asyncio loop running the WS server
        self._settings_win  = None
        self._logs_visible  = False
        self._logs_frame    = None
        self._elapsed       = 0
        self._live_state    = True

        self._imgs          = []         # live tk image refs for the active card (GC guard)
        self._hidden        = False      # overlay hidden via the global show/hide hotkey
        self._hotkey_id     = None       # id of the registered Win32 global hotkey
        self._hotkey_thread = None       # daemon thread running the hotkey message loop

        self._autoscroll_active   = False
        self._autoscroll_words    = []   # parallel: (start_idx, end_idx) tk positions
        self._word_texts          = []   # parallel: normalized word strings (voice match)
        self._autoscroll_word_idx = 0    # shared highlight pointer (auto + voice + seek)
        self._autoscroll_tick     = 0
        self._autoscroll_wpm      = 130  # teleprompter pace (words/min), slider-controlled
        self._autoscroll_after    = None # pending after() id, so speed changes apply live

        self._voice_active        = False
        self._voice_version       = 0    # increments to cancel old mic threads
        self._voice_spoken_count  = 0    # words consumed from the current partial

        # ── AI copilot (local, on-device) ──
        self._copilot           = None   # CopilotEngine instance (lazy)
        self._copilot_active    = False
        self._copilot_status    = "idle"
        self._copilot_transcript = []    # rolling transcript snapshot (list[str])
        self._copilot_question  = ""     # question currently being answered
        self._copilot_answer    = ""     # accumulated streamed answer
        self._copilot_hotkey    = False  # global F9 hook registered?
        self._copilot_hotkey_handle = None  # handle from keyboard.add_hotkey
        self._copilot_audio_rms = 0.0    # latest loopback RMS (audio-activity meter)
        self._copilot_audio_at  = 0.0    # monotonic time of last audio frame
        self._copilot_device    = ""     # captured device name (for diagnostics)
        self._copilot_last_heard = ""    # most recent transcript snippet (liveness)
        self._copilot_pane      = None   # live-transcript pane (built lazily)
        self._copilot_tr_text   = None   # the transcript tk.Text inside the pane
        self._copilot_tr_hint   = None   # right-side hint / live-words label in pane
        self._copilot_lines     = []     # finalized transcript lines (pane display)
        self._copilot_partial   = ""     # in-progress partial (live words)
        self._copilot_answer_epoch = 0   # generation id (drops stale aborted tokens)
        self._copilot_editing   = False  # editing the pending-question panel?
        self._copilot_think_open  = False  # THINKING section started this answer?
        self._copilot_answer_open = False  # ANSWER section started this answer?

        self._user32 = ctypes.WinDLL("user32", use_last_error=True)

        self._setup_window()
        self._build_ui()
        self._cloak(self.root)
        self._setup_resize_handles()
        self.render_slide()
        self._tick_timer()
        self.root.focus_force()
        self._register_global_hotkey()

    # ── WINDOW ───────────────────────────────────────────────────────────────

    def _setup_window(self):
        self.root.title("AsusServiceOLED")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.93)
        self.root.geometry("700x500+150+100")
        self.root.configure(bg=C["bg"])
        self.root.minsize(MIN_W, MIN_H)
        self._apply_stealth_style(self.root)

    def _apply_stealth_style(self, window=None):
        """Ensure the window has WS_EX_TOOLWINDOW and no WS_EX_APPWINDOW
        so it never appears on the taskbar, in the tray, or in Alt+Tab,
        and Windows Task Manager classifies it under Background Processes."""
        if window is None:
            window = self.root
        try:
            window.update_idletasks()
            inner = window.winfo_id()
            hwnd  = ctypes.windll.user32.GetParent(inner) or inner
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_APPWINDOW  = 0x00040000
            style = self._user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
            self._user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            self._user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)
        except Exception:
            pass

    def _cloak(self, window):
        window.update()
        self._apply_stealth_style(window)
        inner = window.winfo_id()
        hwnd  = ctypes.windll.user32.GetParent(inner) or inner
        ret   = self._user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        if ret == 0:
            err = ctypes.get_last_error()
            print(f"WARNING: screen-capture cloaking failed "
                  f"(HWND={hwnd:#x}, error={err}). "
                  "The overlay may be visible in screen shares.")

    def _quit(self):
        """Tear down background workers (copilot engine + global F9 hook + STT
        child processes) before destroying the window, so nothing is orphaned."""
        if getattr(self, "_quitting", False):
            return                       # idempotent — ignore a second close event
        self._quitting = True
        try:
            if self._copilot_active:
                self._stop_copilot(set_notes_tab=False)
        except Exception as e:           # noqa: BLE001 — never let cleanup block close
            print(f"[Copilot] teardown error on quit: {type(e).__name__}: {e}")
        finally:
            try:
                self.root.destroy()
            except tk.TclError:
                pass                     # already destroyed

    def _minimize(self):
        """Collapse or expand the overlay smoothly without ever touching the taskbar."""
        if getattr(self, "_collapsed", False):
            self._collapsed = False
            h = getattr(self, "_saved_height", 500)
            self.root.minsize(MIN_W, MIN_H)
            if hasattr(self, "_body"):
                self._body.pack(fill="both", expand=True)
            self.root.geometry(f"{self.root.winfo_width()}x{h}+{self.root.winfo_x()}+{self.root.winfo_y()}")
        else:
            self._collapsed = True
            self._saved_height = max(self.root.winfo_height(), MIN_H)
            if hasattr(self, "_body"):
                self._body.pack_forget()
            self.root.minsize(MIN_W, 44)
            self.root.geometry(f"{self.root.winfo_width()}x44+{self.root.winfo_x()}+{self.root.winfo_y()}")
        self._cloak(self.root)

    # ── UI BUILD ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_topbar()
        self._build_body()
        self._bind_keys()

    # ── TOP BAR ──────────────────────────────────────────────────────────────

    def _build_topbar(self):
        bar = tk.Frame(self.root, bg=C["bar"], height=44)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)
        bar.bind("<ButtonPress-1>", self._start_drag)
        bar.bind("<B1-Motion>",     self._do_drag)

        left = tk.Frame(bar, bg=C["bar"])
        left.pack(side="left", padx=(14, 0))
        left.bind("<ButtonPress-1>", self._start_drag)
        left.bind("<B1-Motion>",     self._do_drag)

        for color, symbol, cmd in [
            (C["red"],    "✕", self._quit),
            (C["yellow"], "−", self._minimize),
            (C["green"],  "●", self._minimize),
        ]:
            dot = tk.Frame(left, bg=color, width=16, height=16,
                           cursor="hand2")
            dot.pack(side="left", padx=3)
            dot.pack_propagate(False)
            lbl = tk.Label(dot, text=symbol, bg=color, fg=C["bar"],
                           font=(SANS, 8, "bold"), cursor="hand2")
            lbl.place(relx=0.5, rely=0.5, anchor="center")
            for w in (dot, lbl):
                w.bind("<Button-1>", lambda e, c=cmd: c())

        tk.Label(left, text="  📖", bg=C["bar"], fg=C["primary"],
                 font=(SANS, 13)).pack(side="left", padx=(8, 4))

        title = tk.Label(left, text="PRESENTER PRO — ALEXANDRIA",
                         bg=C["bar"], fg=C["on_sv"], font=(LABEL, 8, "bold"))
        title.pack(side="left")
        title.bind("<ButtonPress-1>", self._start_drag)
        title.bind("<B1-Motion>",     self._do_drag)

        # Right cluster
        right = tk.Frame(bar, bg=C["bar"])
        right.pack(side="right", padx=(0, 14))

        self._gear_btn = tk.Button(
            right, text="⚙", bg=C["bar"], fg=C["on_sv"],
            bd=0, font=(SANS, 13), cursor="hand2",
            activebackground=C["bar"], activeforeground=C["primary"],
            relief="flat", command=self._open_settings)
        self._gear_btn.pack(side="right", padx=(6, 0))

        tk.Frame(right, bg=C["outline"], width=1).pack(
            side="right", fill="y", pady=10, padx=8)

        self.timer_label = tk.Label(right, text="00:00:00",
                                    bg=C["bar"], fg=C["on_sv"],
                                    font=(LABEL, 9))
        self.timer_label.pack(side="right", padx=(0, 4))

        tk.Label(right, text="🕐", bg=C["bar"], fg=C["on_sv"],
                 font=(SANS, 10)).pack(side="right")

        self._live_frame = tk.Frame(right, bg=C["bar"])
        self._live_dot   = tk.Label(self._live_frame, text="●",
                                    fg=C["primary"], bg=C["bar"],
                                    font=(SANS, 7))
        self._live_dot.pack(side="left")
        self._live_label = tk.Label(self._live_frame, text="LIVE SYNCING",
                                    bg=C["bar"], fg=C["primary"],
                                    font=(LABEL, 7, "bold"))
        self._live_label.pack(side="left", padx=(3, 0))

    # ── BODY ─────────────────────────────────────────────────────────────────

    def _build_body(self):
        self._body = tk.Frame(self.root, bg=C["bg"])
        self._body.pack(fill="both", expand=True)
        self._build_sidebar(self._body)
        self._build_main_column(self._body)

    # ── SIDEBAR ──────────────────────────────────────────────────────────────

    def _build_sidebar(self, parent):
        sb = tk.Frame(parent, bg=C["bar"], width=72)
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)

        self._active_tab  = "notes"
        self._tab_widgets = {}

        def make_tab(icon, label, name, cmd):
            a    = (name == "notes")
            ibg  = C["primary"] if a else C["bar"]
            ifg  = C["bar"]     if a else C["on_sv"]
            nfg  = C["primary"] if a else C["on_sv"]
            wrap = tk.Frame(sb, bg=C["bar"], cursor="hand2")
            wrap.pack(pady=(14, 0))
            icon_f = tk.Frame(wrap, bg=ibg, width=38, height=38)
            icon_f.pack(); icon_f.pack_propagate(False)
            icon_l = tk.Label(icon_f, text=icon, bg=ibg, fg=ifg, font=(SANS, 15))
            icon_l.pack(expand=True)
            name_l = tk.Label(wrap, text=label, bg=C["bar"], fg=nfg,
                              font=(LABEL, 7, "bold"))
            name_l.pack(pady=(2, 0))
            self._tab_widgets[name] = (icon_f, icon_l, name_l)

            def click(e, n=name, c=cmd):
                self._set_active_tab(n); c()

            for w in (wrap, icon_f, icon_l, name_l):
                w.bind("<Button-1>", click)

        make_tab("✏", "NOTES",   "notes",   self._show_notes)
        make_tab("≡", "LOGS",    "logs",    self._toggle_logs_panel)
        make_tab("✦", "COPILOT", "copilot", self._toggle_copilot)

        hf = tk.Frame(sb, bg=C["bar"], cursor="hand2")
        hf.pack(side="bottom", pady=12)
        tk.Label(hf, text="?", bg=C["bar"], fg=C["on_sv"],
                 font=(LABEL, 12, "bold")).pack()

    def _set_active_tab(self, name):
        # Leaving the copilot tab tears the pipeline down (without re-grabbing
        # the tab we're in the middle of switching to).
        if name != "copilot" and self._copilot_active:
            self._stop_copilot(set_notes_tab=False)
        self._active_tab = name
        for n, (icon_f, icon_l, name_l) in self._tab_widgets.items():
            a = (n == name)
            ibg = C["primary"] if a else C["bar"]
            icon_f.config(bg=ibg)
            icon_l.config(bg=ibg, fg=C["bar"] if a else C["on_sv"])
            name_l.config(fg=C["primary"] if a else C["on_sv"])

    def _show_notes(self):
        if self._logs_visible:
            self._hide_logs()

    # ── MAIN COLUMN ──────────────────────────────────────────────────────────

    def _build_main_column(self, parent):
        col = tk.Frame(parent, bg=C["bg"])
        col.pack(side="left", fill="both", expand=True)
        self._main_col = col
        self._build_statusbar(col)
        self._build_viewport(col)
        self._build_progress(col)

    # ── STATUS BAR ───────────────────────────────────────────────────────────

    def _build_statusbar(self, parent):
        bar = tk.Frame(parent, bg=C["bar"], height=40)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        self._breadcrumb = tk.Label(bar, text="",
                                    bg=C["bar"], fg=C["outline"],
                                    font=(LABEL, 8, "bold"))
        self._breadcrumb.pack(side="left", padx=(16, 0))

        right = tk.Frame(bar, bg=C["bar"])
        right.pack(side="right", padx=8)

        _b = dict(bg=C["card_act"], fg=C["on_s"], bd=0,
                  font=(LABEL, 8, "bold"), width=3, cursor="hand2",
                  relief="flat", activebackground=C["primary"],
                  activeforeground=C["bg"], padx=5, pady=4)

        tk.Button(right, text="A−",
                  command=lambda: self._change_font(-1), **_b).pack(
                  side="left", padx=(0, 2))
        tk.Button(right, text="A+",
                  command=lambda: self._change_font(1),  **_b).pack(
                  side="left", padx=(0, 10))

        self._auto_btn = tk.Button(
            right, text="▶  AUTO", command=self._toggle_autoscroll,
            bg=C["on_s"], fg=C["bg"], bd=0, font=(LABEL, 8, "bold"),
            cursor="hand2", relief="flat",
            activebackground=C["primary"], activeforeground=C["bg"],
            padx=10, pady=4)
        self._auto_btn.pack(side="left")

        self._voice_btn = tk.Button(
            right, text="🎤  SYNC", command=self._toggle_voice,
            bg=C["on_s"], fg=C["bg"], bd=0, font=(LABEL, 8, "bold"),
            cursor="hand2", relief="flat",
            activebackground=C["primary"], activeforeground=C["bg"],
            padx=10, pady=4)
        self._voice_btn.pack(side="left", padx=(6, 0))

        # Auto-scroll speed (words per minute)
        tk.Label(right, text="SPEED", bg=C["bar"], fg=C["muted"],
                 font=(LABEL, 7, "bold")).pack(side="left", padx=(12, 4))
        self._speed_scale = tk.Scale(
            right, from_=60, to=320, orient="horizontal",
            showvalue=False, length=80, width=14, sliderlength=18,
            bg=C["bar"], troughcolor=C["card_act"], highlightthickness=0,
            bd=0, sliderrelief="raised", activebackground=C["primary"],
            cursor="hand2", command=self._on_speed_change)
        self._speed_scale.set(self._autoscroll_wpm)
        self._speed_scale.pack(side="left")
        self._speed_label = tk.Label(right, text=str(self._autoscroll_wpm),
                                     bg=C["bar"], fg=C["on_sv"],
                                     font=(LABEL, 8, "bold"), width=3, anchor="w")
        self._speed_label.pack(side="left", padx=(4, 0))

    # ── VIEWPORT — three distinct card boxes ─────────────────────────────────
    #
    # Layout uses grid so we can show/hide prev & next cards cleanly.
    # row 0 = prev card   (hidden on first slide)
    # row 1 = active card (always visible, expands to fill space)
    # row 2 = next card   (hidden on last slide)

    def _build_viewport(self, parent):
        vp = tk.Frame(parent, bg=C["bg"])
        vp.pack(fill="both", expand=True, padx=8, pady=(6, 0))
        vp.grid_rowconfigure(1, weight=1)
        vp.grid_columnconfigure(0, weight=1)
        self._vp = vp

        # ── PREV CARD ──
        pc = tk.Frame(vp, bg=C["card_dim"], cursor="hand2")
        pc.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self._prev_card = pc

        self._prev_nav  = tk.Label(pc, text="", bg=C["card_dim"],
                                   fg=C["outline"], font=(LABEL, 7, "bold"),
                                   anchor="w", cursor="hand2")
        self._prev_nav.pack(fill="x", padx=14, pady=(8, 2))

        self._prev_text = tk.Label(pc, text="", bg=C["card_dim"],
                                   fg=C["muted"],
                                   font=(SERIF, self.font_size - 2, "italic"),
                                   anchor="w", justify="left",
                                   wraplength=1,   # set dynamically
                                   cursor="hand2")
        self._prev_text.pack(fill="x", padx=14, pady=(0, 8))

        for w in (pc, self._prev_nav, self._prev_text):
            w.bind("<Button-1>", lambda e: self.navigate(-1))
            w.bind("<Enter>",    lambda e: self._prev_card.config(bg=C["card_act"]))
            w.bind("<Leave>",    lambda e: self._prev_card.config(bg=C["card_dim"]))

        # ── ACTIVE CARD ──
        ac_outer = tk.Frame(vp, bg=C["card_act"])
        ac_outer.grid(row=1, column=0, sticky="nsew", pady=(0, 4))
        ac_outer.grid_rowconfigure(0, weight=1)
        ac_outer.grid_columnconfigure(1, weight=1)
        self._active_card = ac_outer

        # left blue accent bar
        accent = tk.Frame(ac_outer, bg=C["primary"], width=3)
        accent.grid(row=0, column=0, sticky="ns")

        # inner content frame
        inner = tk.Frame(ac_outer, bg=C["card_act"])
        inner.grid(row=0, column=1, sticky="nsew")
        inner.grid_rowconfigure(1, weight=1)
        inner.grid_columnconfigure(0, weight=1)

        self._manuscript_lbl = tk.Label(
            inner, text="", bg=C["card_act"],
            fg=C["primary"], font=(LABEL, 8, "bold"), anchor="w")
        self._manuscript_lbl.grid(row=0, column=0, sticky="ew",
                                  padx=(14, 10), pady=(10, 4))

        self.text = tk.Text(
            inner,
            bg=C["card_act"], fg=C["on_s"],
            relief="flat", bd=0, state="disabled",
            wrap="word", cursor="arrow",
            font=(SANS, self.font_size),
            padx=14, pady=4,
            spacing2=4, spacing3=4,
            selectbackground=C["outline"],
            insertwidth=0)
        self.text.grid(row=1, column=0, sticky="nsew")
        self.text.bind("<Button-1>", self._on_text_click)
        self._configure_tags()

        # ── NEXT CARD ──
        nc = tk.Frame(vp, bg=C["card_dim"], cursor="hand2")
        nc.grid(row=2, column=0, sticky="ew")
        self._next_card = nc

        # dashed separator line
        sep = tk.Frame(nc, bg=C["outline"], height=1)
        sep.pack(fill="x")

        self._next_nav  = tk.Label(nc, text="", bg=C["card_dim"],
                                   fg=C["outline"], font=(LABEL, 7, "bold"),
                                   anchor="w", cursor="hand2")
        self._next_nav.pack(fill="x", padx=14, pady=(8, 2))

        self._next_text = tk.Label(nc, text="", bg=C["card_dim"],
                                   fg=C["muted"],
                                   font=(SANS, self.font_size - 2),
                                   anchor="w", justify="left",
                                   wraplength=1,
                                   cursor="hand2")
        self._next_text.pack(fill="x", padx=14, pady=(0, 8))

        for w in (nc, self._next_nav, self._next_text, sep):
            w.bind("<Button-1>", lambda e: self.navigate(1))
            w.bind("<Enter>",    lambda e: self._next_card.config(bg=C["card_act"]))
            w.bind("<Leave>",    lambda e: self._next_card.config(bg=C["card_dim"]))

        # keep wraplength in sync with window width
        vp.bind("<Configure>", self._on_vp_resize)

    def _on_vp_resize(self, event):
        wl = max(100, event.width - 56)
        self._prev_text.config(wraplength=wl)
        self._next_text.config(wraplength=wl)

    # ── PROGRESS BAR ─────────────────────────────────────────────────────────

    def _build_progress(self, parent):
        prog = tk.Frame(parent, bg=C["bar"], height=2)
        prog.pack(fill="x")
        prog.pack_propagate(False)
        self._prog_fill = tk.Frame(prog, bg=C["primary"])
        self._prog_fill.place(x=0, y=0, relheight=1, width=0)

    def _update_progress(self):
        total = len(self.slides)
        ratio = 1.0 if total <= 1 else self.current / (total - 1)
        self.root.update_idletasks()
        w = self._prog_fill.master.winfo_width()
        self._prog_fill.place(x=0, y=0, relheight=1, width=max(1, int(w * ratio)))

    # ── TEXT TAGS ────────────────────────────────────────────────────────────

    def _configure_tags(self):
        fs = self.font_size
        t  = self.text
        t.tag_configure("h1",
            font=(SERIF, fs + 10, "bold"), foreground=C["on_s"],
            spacing1=6, spacing3=4)
        t.tag_configure("h2",
            font=(SERIF, fs + 4,  "bold"), foreground=C["on_s"],
            spacing1=4, spacing3=3)
        t.tag_configure("body",
            font=(SANS, fs), foreground=C["on_s"], spacing1=2)
        t.tag_configure("bold",
            font=(SANS, fs, "bold"), foreground=C["on_s"])
        t.tag_configure("bullet",
            font=(SANS, fs), foreground=C["on_s"],
            lmargin1=24, lmargin2=24, spacing1=2)
        t.tag_configure("prompt_header",
            font=(LABEL, fs - 2, "bold"), foreground=C["primary"],
            spacing1=14, spacing3=4)
        t.tag_configure("prompt_body",
            font=(SERIF, fs, "italic"), foreground=C["on_sv"],
            lmargin1=14, lmargin2=14, rmargin=14,
            background=C["prompt"], spacing1=4, spacing3=8)
        t.tag_configure("keyword",
            font=(SANS, fs), foreground=C["primary"],
            background="#1e2a40", underline=True)
        t.tag_configure("autoscroll_hl",
            background=C["primary"], foreground=C["bg"])
        t.tag_configure("copilot_think",        # the model's live reasoning preview
            font=(SERIF, fs - 1, "italic"), foreground=C["muted"],
            lmargin1=12, lmargin2=12, rmargin=12, spacing1=1)

    # ── SLIDE RENDERING ──────────────────────────────────────────────────────

    def _extract_title(self, slide):
        for line in slide.split('\n'):
            s = line.strip()
            if s.startswith('# '):  return s[2:]
            if s.startswith('## '): return s[3:]
        return None

    def _preview_text(self, slide, max_lines=2):
        """Return plain-text preview (strips markdown, max N lines)."""
        out = []
        for line in slide.split('\n'):
            s = line.strip()
            if not s:
                continue
            for prefix in ('## ', '# ', '- ', '* ', '> '):
                if s.startswith(prefix):
                    s = s[len(prefix):]
                    break
            # Collapse ![alt](...) images to a marker so base64 never bloats previews
            s = re.sub(r'!\[[^\]]*\]\([^)]*\)', '🖼 image', s)
            # Strip **bold**
            s = re.sub(r'\*\*(.+?)\*\*', r'\1', s)
            # Strip ==highlight==
            s = re.sub(r'==(.+?)==', r'\1', s)
            if s:
                out.append(s)
            if len(out) >= max_lines:
                break
        return '  '.join(out)

    def _insert_rich(self, line, base_tag):
        """Insert one line with **bold** and ==keyword== parsing."""
        parts = re.split(r'==(.+?)==', line)
        for ki, kp in enumerate(parts):
            if ki % 2 == 1:
                self.text.insert("end", kp, "keyword")
            else:
                for bi, bp in enumerate(re.split(r'\*\*', kp)):
                    if bp:
                        self.text.insert("end", bp,
                                         "bold" if bi % 2 == 1 else base_tag)
        self.text.insert("end", "\n")

    def _render_active_content(self, slide):
        self._imgs   = []          # release refs from the previous slide (let tk GC them)
        in_prompt    = False
        blank_pend   = False
        for raw in slide.split('\n'):
            line = raw.strip()
            if not line:
                blank_pend = True
                continue
            if blank_pend:
                self.text.insert("end", "\n")
                blank_pend = False
            # A line that is only an image:  ![alt](data:...  |  local path)
            m_img = re.match(r'^!\[[^\]]*\]\((.+)\)\s*$', line)
            if m_img:
                in_prompt = False
                self._insert_image(m_img.group(1))
                continue
            if line.startswith('> '):
                if not in_prompt:
                    self.text.insert("end", "❝  SPEAKER PROMPT\n", "prompt_header")
                    in_prompt = True
                self._insert_rich(line[2:], "prompt_body")
            else:
                in_prompt = False
                if   line.startswith('# '):         self._insert_rich(line[2:],  "h1")
                elif line.startswith('## '):         self._insert_rich(line[3:],  "h2")
                elif line.startswith(('- ', '* ')): self._insert_rich('  •  ' + line[2:], "bullet")
                else:                                self._insert_rich(line,      "body")

    # ── IMAGES IN NOTES ──────────────────────────────────────────────────────

    def _load_image(self, src):
        """Turn an image markdown target into a tk image object, scaled to fit the
        active card. Accepts data: URLs (from the phone editor) and local file
        paths (desktop). Uses Pillow for smooth scaling / broad formats when it is
        installed, else falls back to tk.PhotoImage (PNG/GIF) with integer
        subsampling. Never touches the network. Returns None if it can't decode."""
        import base64, io
        # Available width inside the card; winfo_width is 1 before first layout.
        try:
            avail = self.text.winfo_width() - 44
        except Exception:                    # noqa: BLE001
            avail = 0
        if avail < 120:
            avail = 560
        max_h = 380

        data = None
        if src.startswith("data:"):
            try:
                data = base64.b64decode(src.split(",", 1)[1])
            except Exception:                # noqa: BLE001
                return None
        else:
            # local file only — do not fetch http(s), keep the overlay offline
            if os.path.exists(src):
                try:
                    with open(src, "rb") as f:
                        data = f.read()
                except Exception:            # noqa: BLE001
                    return None
            else:
                return None

        # Preferred path: Pillow (smooth resize, handles JPEG/WebP/etc.)
        try:
            from PIL import Image, ImageTk
            im = Image.open(io.BytesIO(data)); im.load()
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
            scale = min(1.0, avail / im.width, max_h / im.height)
            if scale < 1.0:
                im = im.resize((max(1, int(im.width * scale)),
                                max(1, int(im.height * scale))), Image.LANCZOS)
            return ImageTk.PhotoImage(im)
        except Exception:                    # noqa: BLE001 — Pillow absent or format unsupported
            pass

        # Fallback: built-in tk image (PNG/GIF), integer downscale only
        try:
            b64 = base64.b64encode(data).decode("ascii")
            img = tk.PhotoImage(data=b64)
            factor = 1
            while img.width() // factor > avail or img.height() // factor > max_h:
                factor += 1
            if factor > 1:
                img = img.subsample(factor, factor)
            return img
        except Exception:                    # noqa: BLE001
            return None

    def _insert_image(self, src):
        img = self._load_image(src)
        if img is None:
            self.text.insert("end", "🖼  [image unavailable]\n", "body")
            return
        self._imgs.append(img)               # hold a ref or tk garbage-collects it
        self.text.insert("end", "\n")
        self.text.image_create("end", image=img)
        self.text.insert("end", "\n")

    def render_slide(self):
        # NOTE: do NOT stop auto-scroll / voice here. render_slide() runs on every
        # remote sync update too — killing the highlight on each Firebase poll is
        # what broke "click a word and keep scrolling". Following is stopped only
        # on real navigation (see navigate / navigate_to / load_file).

        # ── PREV CARD ──
        if self.current > 0:
            prev_title   = self._extract_title(self.slides[self.current - 1]) or ""
            prev_preview = self._preview_text(self.slides[self.current - 1])
            self._prev_nav.config(
                text=f"◀  SLIDE {self.current}  ·  {prev_title.upper()}")
            self._prev_text.config(text=prev_preview)
            self._prev_card.grid()
        else:
            self._prev_card.grid_remove()

        # ── ACTIVE CARD ──
        self._manuscript_lbl.config(
            text=f"ACTIVE MANUSCRIPT  ·  SLIDE {self.current + 1}")
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self._render_active_content(self.slides[self.current])
        self.text.config(state="disabled")

        # ── NEXT CARD ──
        if self.current < len(self.slides) - 1:
            next_title   = self._extract_title(self.slides[self.current + 1]) or ""
            next_preview = self._preview_text(self.slides[self.current + 1])
            self._next_nav.config(
                text=f"▶  SLIDE {self.current + 2}  ·  {next_title.upper()}")
            self._next_text.config(text=next_preview)
            self._next_card.grid()
        else:
            self._next_card.grid_remove()

        self._update_statusbar()
        self._update_progress()
        self._build_autoscroll_words()

    def _update_statusbar(self):
        title = self._extract_title(self.slides[self.current]) or ""
        text  = f"SLIDE {self.current + 1} / {len(self.slides)}"
        if title:
            text += f"  ›  {title.upper()}"
        self._breadcrumb.config(text=text)

    # ── NAVIGATION ───────────────────────────────────────────────────────────

    def _stop_following(self):
        """Stop auto-scroll / voice and clear the highlight (used on navigation)."""
        if self._autoscroll_active:
            self._autoscroll_active = False
            if self._autoscroll_after:
                self.root.after_cancel(self._autoscroll_after)
                self._autoscroll_after = None
            self._auto_btn.config(text="▶  AUTO", bg=C["on_s"], fg=C["bg"])
        if self._voice_active:
            self._stop_voice()
        self.text.config(state="normal")
        self.text.tag_remove("autoscroll_hl", "1.0", "end")
        self.text.config(state="disabled")

    def navigate(self, delta):
        if self._editing or self._copilot_active:
            return
        new = self.current + delta
        if 0 <= new < len(self.slides):
            self._stop_following()
            self.current = new
            self.render_slide()

    def navigate_to(self, index):
        if 0 <= index < len(self.slides):
            self._stop_following()
            self.current = index
            self.render_slide()

    # ── INLINE EDITING (two-way sync) ────────────────────────────────────────

    def _on_text_click(self, event):
        """Single click on the active card.

        While following along (voice / auto-scroll) a click re-seeks the
        highlight to that word. Otherwise it drops straight into edit mode
        with the caret placed where you clicked.
        """
        if self._editing:
            return              # already editing — let default caret placement run
        if self._copilot_active:
            return "break"      # copilot owns the card; no edit/seek
        if self._voice_active or self._autoscroll_active:
            return self._seek_word(event)
        self._enter_edit_mode(event)
        return "break"          # we place the caret ourselves

    def _enter_edit_mode(self, event=None):
        """Edit the raw markdown of the current slide, caret at the click point."""
        if self._editing:
            return
        if self._autoscroll_active:
            self._toggle_autoscroll()
        if self._voice_active:
            self._stop_voice()
        self._editing = True
        self.text.config(state="normal", cursor="xterm",
                         insertwidth=2, insertbackground=C["primary"])
        self.text.delete("1.0", "end")
        for tag in ("h1", "h2", "body", "bold", "bullet",
                    "prompt_header", "prompt_body", "keyword", "autoscroll_hl"):
            self.text.tag_remove(tag, "1.0", "end")
        self.text.insert("1.0", self.slides[self.current])
        self._manuscript_lbl.config(
            text=f"✏  EDITING  ·  SLIDE {self.current + 1}  ·  Esc or click away to save")
        self.text.focus_set()
        # Place the caret at the clicked screen position within the raw text.
        if event is not None:
            try:
                self.text.mark_set("insert",
                                   self.text.index(f"@{event.x},{event.y}"))
            except tk.TclError:
                pass
        self.text.see("insert")
        self.text.bind("<FocusOut>", self._commit_edit)
        self.text.bind("<Escape>",   self._commit_edit)
        self.text.bind("<KeyRelease>", self._on_edit_key)

    def _commit_edit(self, event=None):
        if not self._editing:
            return
        self._editing = False
        if self._live_after:
            self.root.after_cancel(self._live_after)
            self._live_after = None
        new_content = self.text.get("1.0", "end-1c")
        self.text.unbind("<FocusOut>")
        self.text.unbind("<Escape>")
        self.text.unbind("<KeyRelease>")
        self.text.config(cursor="arrow", insertwidth=0)
        # An edit may introduce `---` separators — re-split the edited slide.
        pieces = [s.strip() for s in re.split(r'\n\s*---\s*\n', new_content)
                  if s.strip()]
        if not pieces:
            pieces = [""]
        self.slides[self.current:self.current + 1] = pieces
        self.render_slide()
        self._broadcast_content()
        return "break"

    # ── OUTBOUND BROADCAST ───────────────────────────────────────────────────

    def _broadcast_content(self, content=None):
        """Push content (defaults to the committed deck) out to the active sync mode."""
        if content is None:
            content = "\n---\n".join(self.slides)
        self._last_synced = content        # echo guard for inbound listeners
        self._edit_guard_until = time.time() + 1.5   # ride out the PUT round-trip
        if self._sync_mode == "firebase":
            threading.Thread(target=self._firebase_put,
                             args=(content,), daemon=True).start()
        elif self._sync_mode == "local":
            if self._ws_loop and self._ws_clients:
                self._ws_loop.call_soon_threadsafe(self._ws_broadcast, content)

    def _on_edit_key(self, event=None):
        """Keystroke while editing → schedule a debounced live broadcast."""
        if self._live_after:
            self.root.after_cancel(self._live_after)
        self._live_after = self.root.after(220, self._live_broadcast)

    def _live_broadcast(self):
        """Send the in-progress edit buffer (uncommitted) to the web UI live."""
        self._live_after = None
        if not self._editing:
            return
        buffer = self.text.get("1.0", "end-1c")
        parts  = list(self.slides)
        if 0 <= self.current < len(parts):
            parts[self.current] = buffer
        else:
            parts = [buffer]
        self._broadcast_content("\n---\n".join(parts))

    def _firebase_put(self, content):
        try:
            import requests
            requests.put(f"{self._firebase_url}/overlay/content.json",
                         data=json.dumps(content), timeout=5)
        except Exception as e:
            print(f"[Firebase] push failed: {type(e).__name__}: {e}")

    def _ws_broadcast(self, content):
        """Runs inside the WS asyncio loop — fan out to all clients."""
        for ws in list(self._ws_clients):
            asyncio.create_task(self._ws_send_safe(ws, content))

    async def _ws_send_safe(self, ws, content):
        try:
            await ws.send(content)
        except Exception:
            self._ws_clients.discard(ws)

    # ── FILE LOADING ─────────────────────────────────────────────────────────

    def load_file(self, path=None):
        if path is None:
            path = filedialog.askopenfilename(
                title="Open notes file",
                filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path or not os.path.exists(path):
            return
        # utf-8-sig strips a BOM so a Notepad-saved file's first heading isn't
        # prefixed with a stray ﻿ character.
        with open(path, encoding="utf-8-sig") as f:
            content = f.read().replace('\r\n', '\n').replace('\r', '\n')
        parsed       = [s.strip() for s in re.split(r'\n\s*---\s*\n', content)
                        if s.strip()]
        self.slides  = parsed if parsed else ["# Empty file\n\nNo slides found."]
        self.current = 0
        # Loading a deck while copilot owns the card: stash it but stay in copilot.
        if self._copilot_active:
            return
        self._stop_following()
        self.render_slide()
        self.root.focus_force()

    # ── LOGS PANEL ───────────────────────────────────────────────────────────

    def _toggle_logs_panel(self):
        if self._logs_visible: self._hide_logs()
        else:                  self._show_logs()

    def _hide_logs(self):
        self._logs_visible = False
        if self._logs_frame and self._logs_frame.winfo_exists():
            self._logs_frame.place_forget()
        self._set_active_tab("notes")

    def _show_logs(self):
        self._logs_visible = True
        if self._logs_frame and self._logs_frame.winfo_exists():
            self._logs_frame.destroy()

        frame = tk.Frame(self._main_col, bg=C["bar"])
        frame.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._logs_frame = frame

        tk.Label(frame, text="SLIDE SWITCHER",
                 bg=C["bar"], fg=C["outline"],
                 font=(LABEL, 8, "bold")).pack(pady=(14, 4))
        tk.Frame(frame, bg=C["outline"], height=1).pack(fill="x", padx=12)

        lf = tk.Frame(frame, bg=C["bar"])
        lf.pack(fill="both", expand=True, padx=10, pady=8)

        sb = tk.Scrollbar(lf)
        sb.pack(side="right", fill="y")

        lb = tk.Listbox(lf, bg=C["bar"], fg=C["on_s"],
                        selectbackground=C["primary"],
                        selectforeground=C["bg"],
                        font=(SANS, 11), bd=0, relief="flat",
                        activestyle="none", highlightthickness=0,
                        yscrollcommand=sb.set)
        lb.pack(fill="both", expand=True)
        sb.config(command=lb.yview)

        for i, slide in enumerate(self.slides):
            t = self._extract_title(slide) or f"Slide {i + 1}"
            lb.insert(tk.END, f"  {i + 1}.  {t}")

        lb.selection_set(self.current)
        lb.see(self.current)

        def go(e):
            sel = lb.curselection()
            if sel:
                self.navigate_to(sel[0])
                self._hide_logs()

        lb.bind("<Double-1>", go)
        lb.bind("<Return>",   go)

        tk.Button(frame, text="✕  Close", command=self._hide_logs,
                  bg=C["bar"], fg=C["on_sv"], bd=0, font=(LABEL, 8),
                  relief="flat", cursor="hand2").pack(pady=(0, 10))

    # ── REMOTE SYNC ──────────────────────────────────────────────────────────

    def _apply_remote_content(self, content):
        if self._editing or time.time() < self._edit_guard_until:
            return              # don't clobber an in-progress / just-finished local edit
        self._last_synced = content   # mark seen only now that we're actually applying
        content = content.replace('\r\n', '\n').replace('\r', '\n')
        parsed  = [s.strip() for s in re.split(r'\n\s*---\s*\n', content)
                   if s.strip()]
        if not parsed:
            return
        self.slides  = parsed
        self.current = min(self.current, len(parsed) - 1)
        # Copilot owns the card while active — stash the updated deck but don't
        # repaint over the streaming answer. The teleprompter is repainted by the
        # render_slide() inside _stop_copilot when the user leaves copilot mode.
        if self._copilot_active:
            return
        self.render_slide()

    # ── REMOTE COMMANDS (phone UI controls) ─────────────────────────────────

    def _handle_remote_cmd(self, data):
        if not isinstance(data, dict):
            return
        ts = data.get("ts", 0)
        if not isinstance(ts, (int, float)):
            return
        if ts and ts <= self._last_cmd_ts:
            return
        self._last_cmd_ts = ts

        cmd = data.get("cmd")
        print(f"[RemoteCmd] {cmd} | {data}")
        if cmd == "navigate":
            delta = data.get("delta", 0)
            if isinstance(delta, int) and delta:
                self.navigate(delta)
        elif cmd == "navigate_to":
            idx = data.get("index", 0)
            if isinstance(idx, int):
                self.navigate_to(idx)
        elif cmd == "scroll":
            delta = data.get("delta", 0)
            if isinstance(delta, (int, float)) and delta:
                self._scroll_text(int(delta))
        elif cmd == "scroll_to":
            frac = data.get("fraction")
            if isinstance(frac, (int, float)):
                self.text.yview_moveto(max(0.0, min(1.0, float(frac))))
        elif cmd == "opacity":
            val = data.get("value")
            if isinstance(val, (int, float)):
                self._on_opacity(str(max(20, min(100, int(val)))))
        elif cmd == "font":
            delta = data.get("delta", 0)
            if isinstance(delta, int) and delta:
                self._change_font(delta)
        elif cmd == "screenshot":
            req_id = data.get("req_id", "")
            threading.Thread(target=self._capture_and_send_screenshot,
                             args=(req_id,), daemon=True).start()

    def _scroll_text(self, delta):
        units = max(1, min(50, abs(delta)))
        if delta > 0:
            self.text.yview_scroll(units, "units")
        elif delta < 0:
            self.text.yview_scroll(-units, "units")

    def _take_screenshot_data_url(self):
        import ctypes, ctypes.wintypes, base64, io
        from PIL import Image

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        # Virtual screen metrics (covers multi-monitor or single)
        vx = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        vy = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        vw = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        vh = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
        if vw <= 0 or vh <= 0:
            vw = user32.GetSystemMetrics(0)  # SM_CXSCREEN
            vh = user32.GetSystemMetrics(1)  # SM_CYSCREEN
            vx, vy = 0, 0

        hdc = user32.GetDC(0)
        memdc = gdi32.CreateCompatibleDC(hdc)
        hbmp = gdi32.CreateCompatibleBitmap(hdc, vw, vh)
        old_bmp = gdi32.SelectObject(memdc, hbmp)

        # SRCCOPY = 0x00CC0020, CAPTUREBLT = 0x40000000
        gdi32.BitBlt(memdc, 0, 0, vw, vh, hdc, vx, vy, 0x00CC0020 | 0x40000000)

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ('biSize', ctypes.wintypes.DWORD), ('biWidth', ctypes.wintypes.LONG),
                ('biHeight', ctypes.wintypes.LONG), ('biPlanes', ctypes.wintypes.WORD),
                ('biBitCount', ctypes.wintypes.WORD), ('biCompression', ctypes.wintypes.DWORD),
                ('biSizeImage', ctypes.wintypes.DWORD), ('biXPelsPerMeter', ctypes.wintypes.LONG),
                ('biYPelsPerMeter', ctypes.wintypes.LONG), ('biClrUsed', ctypes.wintypes.DWORD),
                ('biClrImportant', ctypes.wintypes.DWORD)
            ]

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = vw
        bmi.biHeight = -vh  # top-down DIB
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0

        buf = ctypes.create_string_buffer(vw * vh * 4)
        gdi32.GetDIBits(memdc, hbmp, 0, vh, buf, ctypes.byref(bmi), 0)

        gdi32.SelectObject(memdc, old_bmp)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(0, hdc)

        im = Image.frombuffer('RGBA', (vw, vh), buf, 'raw', 'BGRA', 0, 1).convert('RGB')

        # Limit maximum dimension to 1920 to keep upload small (~50-150KB) for instant sync
        max_dim = 1920
        if vw > max_dim or vh > max_dim:
            ratio = min(max_dim / vw, max_dim / vh)
            new_w = max(1, int(vw * ratio))
            new_h = max(1, int(vh * ratio))
            im = im.resize((new_w, new_h), Image.Resampling.LANCZOS)

        bio = io.BytesIO()
        im.save(bio, format='JPEG', quality=85, optimize=True)
        b64 = base64.b64encode(bio.getvalue()).decode('ascii')
        return f"data:image/jpeg;base64,{b64}"

    def _capture_and_send_screenshot(self, req_id):
        try:
            print(f"[Screenshot] Capturing screen for req {req_id}...")
            data_url = self._take_screenshot_data_url()
            payload = {
                "req_id": req_id,
                "ts": time.time(),
                "image": data_url
            }
            if self._sync_mode == "firebase" and self._firebase_url:
                import requests
                r = requests.put(f"{self._firebase_url}/overlay/screenshot.json",
                                 data=json.dumps(payload), timeout=10)
                print(f"[Screenshot] Pushed screenshot to Firebase (HTTP {r.status_code})")
            elif self._sync_mode == "local":
                if self._ws_loop and self._ws_clients:
                    msg = json.dumps({"type": "screenshot", **payload})
                    self._ws_loop.call_soon_threadsafe(self._ws_broadcast, msg)
                    print(f"[Screenshot] Broadcasted screenshot to WS clients")
        except Exception as e:
            print(f"[Screenshot] Error: {type(e).__name__}: {e}")

    def start_firebase_sync(self, db_url):
        self._firebase_url  = db_url.rstrip("/")
        self._sync_version += 1
        self._sync_mode = "firebase"
        v = self._sync_version
        self.root.after(0, self._show_live_badge)
        threading.Thread(target=self._firebase_loop,
                         args=(self._firebase_url, v), daemon=True).start()

    def _firebase_loop(self, db_url, version):
        try:
            import requests
        except ImportError:
            return
        # Poll the parent node so one request returns both the notes content and
        # the phone's presence heartbeat (written by the web UI while it's open).
        url  = f"{db_url}/overlay.json"
        while self._sync_version == version:
            try:
                r = requests.get(url, timeout=5)
                if r.ok:
                    self._fb_last_ok = time.time()
                    data = r.json()
                    if isinstance(data, dict):
                        content  = data.get("content")
                        presence = data.get("presence")
                    elif isinstance(data, str):       # backward-compat (flat content)
                        content, presence = data, None
                    else:
                        content, presence = None, None
                    # "peer present" = heartbeat keeps changing; track when it last did
                    if presence is not None and presence != self._fb_presence_val:
                        self._fb_presence_val = presence
                        self._fb_presence_at  = time.time()
                    # NOTE: don't mark content as seen here — _apply_remote_content does
                    # that only when it actually applies, so an update that arrives
                    # mid-edit is retried (not silently dropped) once editing ends.
                    if isinstance(content, str) and content != self._last_synced:
                        self.root.after(0, lambda c=content: self._apply_remote_content(c))
                    command = data.get("command") if isinstance(data, dict) else None
                    if isinstance(command, dict) and "cmd" in command:
                        self.root.after(0, lambda d=command: self._handle_remote_cmd(d))
            except Exception as e:
                print(f"[Firebase] {type(e).__name__}: {e}")
                time.sleep(2)
                continue
            time.sleep(0.4)    # ~2.5 req/s — snappier pickup, still well under rate limits

    def start_local_ws_server(self, port=8765):
        self._sync_version += 1
        self._sync_mode = "local"
        v = self._sync_version
        self.root.after(0, self._show_live_badge)
        threading.Thread(target=self._run_ws_server,
                         args=(port, v), daemon=True).start()

    def _run_ws_server(self, port, version):
        asyncio.run(self._ws_serve(port, version))

    async def _ws_serve(self, port, version):
        try:
            import websockets
        except ImportError:
            return

        MAX_MSG = 500_000   # 500 KB — guard against oversized / malicious payloads

        self._ws_loop    = asyncio.get_running_loop()
        self._ws_clients = set()

        async def handler(ws):
            self._ws_clients.add(ws)
            try:
                async for msg in ws:
                    if isinstance(msg, str) and self._sync_version == version:
                        if len(msg) > MAX_MSG:
                            print(f"[WebSocket] message dropped: {len(msg):,} bytes exceeds limit")
                            continue
                        try:
                            data = json.loads(msg)
                            if isinstance(data, dict) and "cmd" in data:
                                self.root.after(0, lambda d=data: self._handle_remote_cmd(d))
                                continue
                        except (json.JSONDecodeError, ValueError):
                            pass
                        self.root.after(0, lambda c=msg: self._apply_remote_content(c))
            finally:
                self._ws_clients.discard(ws)

        async with websockets.serve(handler, "0.0.0.0", port):
            while self._sync_version == version:
                await asyncio.sleep(0.5)
        self._ws_clients.clear()
        self._ws_loop = None

    def stop_sync(self):
        self._sync_version += 1
        self._sync_mode = None
        self.root.after(0, self._hide_live_badge)

    # ── LIVE BADGE ───────────────────────────────────────────────────────────

    def _show_live_badge(self):
        self._live_frame.pack(side="right", padx=(0, 10),
                              before=self._gear_btn)
        self._pulse_live()

    def _hide_live_badge(self):
        self._live_frame.pack_forget()

    def _sync_health(self):
        """Return (live, label, color). `live` (a peer is actively present) → pulse."""
        if self._sync_mode == "firebase":
            if time.time() - self._fb_last_ok >= 3:
                return False, "RECONNECTING…", C["yellow"]          # can't reach Firebase
            if time.time() - self._fb_presence_at < 8:
                return True, "LIVE SYNCING", C["primary"]            # phone is on the line
            return False, "CONNECTED", C["on_sv"]                    # FB ok, no phone yet
        if self._sync_mode == "local":
            if self._ws_clients:
                return True, "LIVE SYNCING", C["primary"]
            return False, "WAITING FOR DEVICE", C["muted"]
        return False, "OFFLINE", C["muted"]

    def _pulse_live(self):
        if not self._sync_mode:
            return
        live, label, color = self._sync_health()
        self._live_state = not self._live_state
        # Pulse the dot only when a peer is live; otherwise hold it steady in `color`.
        dot_lit = self._live_state if live else True
        self._live_dot.config(fg=color if dot_lit else C["bar"])
        self._live_label.config(text=label, fg=color)
        self.root.after(700, self._pulse_live)

    # ── MEETING TIMER ────────────────────────────────────────────────────────

    def _tick_timer(self):
        if not self.root.winfo_exists():
            return
        self._elapsed += 1
        h, m, s = self._elapsed // 3600, (self._elapsed % 3600) // 60, self._elapsed % 60
        self.timer_label.config(text=f"{h:02d}:{m:02d}:{s:02d}")
        self.root.after(1000, self._tick_timer)

    # ── AUTO-SCROLL + WORD HIGHLIGHT ─────────────────────────────────────────

    def _build_autoscroll_words(self):
        self._autoscroll_words    = []
        self._word_texts          = []
        self._autoscroll_word_idx = 0
        self._autoscroll_tick     = 0
        content = self.text.get("1.0", "end")
        for m in re.finditer(r'\S+', content):
            self._autoscroll_words.append((
                f"1.0 + {m.start()} chars",
                f"1.0 + {m.end()} chars"))
            self._word_texts.append(self._normalize_word(m.group()))

    @staticmethod
    def _normalize_word(w):
        return re.sub(r'[^a-z0-9]', '', w.lower())

    def _on_speed_change(self, val):
        self._autoscroll_wpm = max(40, int(float(val)))
        if hasattr(self, "_speed_label"):
            self._speed_label.config(text=str(self._autoscroll_wpm))
        # Apply the new pace immediately instead of waiting out the current step.
        if self._autoscroll_active:
            if self._autoscroll_after:
                self.root.after_cancel(self._autoscroll_after)
            interval = int(2 * 60000 / max(40, self._autoscroll_wpm))
            self._autoscroll_after = self.root.after(interval, self._autoscroll_step)

    def _toggle_autoscroll(self):
        if self._copilot_active:
            return                           # copilot owns the card — mutually exclusive
        self._autoscroll_active = not self._autoscroll_active
        if self._autoscroll_active:
            if self._voice_active:
                self._stop_voice()           # auto + voice are mutually exclusive
            self._auto_btn.config(text="⏸  STOP", bg=C["primary"], fg=C["bg"])
            self._autoscroll_word_idx = 0
            self._autoscroll_step()
        else:
            self._auto_btn.config(text="▶  AUTO", bg=C["on_s"], fg=C["bg"])
            if self._autoscroll_after:
                self.root.after_cancel(self._autoscroll_after)
                self._autoscroll_after = None
            self.text.config(state="normal")
            self.text.tag_remove("autoscroll_hl", "1.0", "end")
            self.text.config(state="disabled")

    def _autoscroll_step(self):
        # Highlight-driven teleprompter: advance two words at a time and let the
        # viewport follow the highlight, paced by the speed slider (words/min).
        self._autoscroll_after = None
        if not self._autoscroll_active:
            return
        words = self._autoscroll_words
        idx   = self._autoscroll_word_idx
        n     = len(words)
        if not words or idx >= n:
            self._toggle_autoscroll()        # reached the end of the slide
            return
        span = min(2, n - idx)               # two words per step
        self._highlight_span(idx, span)
        self._autoscroll_word_idx = idx + span
        interval = int(span * 60000 / max(40, self._autoscroll_wpm))
        self._autoscroll_after = self.root.after(interval, self._autoscroll_step)

    def _highlight_span(self, start, count=1, scroll=True):
        """Highlight `count` words starting at index `start`; viewport follows it."""
        words = self._autoscroll_words
        if not words:
            return
        start = max(0, min(start, len(words) - 1))
        end   = min(len(words), start + max(1, count))
        self.text.config(state="normal")
        self.text.tag_remove("autoscroll_hl", "1.0", "end")
        self.text.tag_add("autoscroll_hl", words[start][0], words[end - 1][1])
        self.text.config(state="disabled")
        if scroll:
            self.text.see(words[end - 1][0])
            self.text.see(words[start][0])

    def _highlight_word(self, p, scroll=True):
        """Single-word highlight (voice sync + click-to-seek)."""
        self._highlight_span(p, 1, scroll)

    # ── VOICE SYNC (Vosk) ────────────────────────────────────────────────────

    def _toggle_voice(self):
        if self._copilot_active:
            return                           # copilot owns the card — mutually exclusive
        if self._voice_active:
            self._stop_voice()
        else:
            self._start_voice()

    def _start_voice(self):
        if self._autoscroll_active:
            self._toggle_autoscroll()        # auto + voice are mutually exclusive
        self._voice_active   = True
        self._voice_version += 1
        self._voice_btn.config(text="⏹  LISTENING", bg=C["primary"], fg=C["bg"])
        threading.Thread(target=self._voice_loop,
                         args=(self._voice_version,), daemon=True).start()

    def _stop_voice(self):
        self._voice_active   = False
        self._voice_version += 1
        self._voice_btn.config(text="🎤  SYNC", bg=C["on_s"], fg=C["bg"])
        self.text.config(state="normal")
        self.text.tag_remove("autoscroll_hl", "1.0", "end")
        self.text.config(state="disabled")

    def _voice_error(self, msg):
        self._voice_active = False
        self._voice_btn.config(text="🎤  SYNC", bg=C["on_s"], fg=C["bg"])
        print(f"[Voice] {msg}")

    def _find_vosk_model(self):
        base = _app_dir()
        candidates = []
        env = os.environ.get("VOSK_MODEL")
        if env:
            candidates.append(env)
        candidates.append(os.path.join(base, "model"))
        try:
            for name in sorted(os.listdir(base)):
                if name.startswith("vosk-model") and \
                   os.path.isdir(os.path.join(base, name)):
                    candidates.append(os.path.join(base, name))
        except OSError:
            pass
        for c in candidates:
            if c and os.path.isdir(c):
                return c
        return None

    def _voice_loop(self, version):
        try:
            import sounddevice as sd
            from vosk import Model, KaldiRecognizer, SetLogLevel
        except ImportError:
            self.root.after(0, lambda: self._voice_error(
                "vosk / sounddevice not installed — run: pip install -r requirements.txt"))
            return

        model_path = self._find_vosk_model()
        if not model_path:
            self.root.after(0, lambda: self._voice_error(
                "Vosk model not found. Download 'vosk-model-small-en-us' and unzip it "
                "next to overlay.py (as 'model/' or 'vosk-model-...'), "
                "or set VOSK_MODEL=<path>."))
            return

        try:
            SetLogLevel(-1)                  # silence Kaldi's verbose stderr
            model = Model(model_path)
            rec   = KaldiRecognizer(model, 16000)
            self._voice_spoken_count = 0
            with sd.RawInputStream(samplerate=16000, blocksize=1600,
                                   dtype="int16", channels=1) as stream:
                while self._voice_version == version:
                    data, _ = stream.read(1600)
                    buf = bytes(data)
                    if rec.AcceptWaveform(buf):
                        txt = json.loads(rec.Result()).get("text", "")
                        self._on_voice_text(txt, final=True)
                    else:
                        txt = json.loads(rec.PartialResult()).get("partial", "")
                        self._on_voice_text(txt, final=False)
        except Exception as e:
            self.root.after(0, lambda m=f"{type(e).__name__}: {e}":
                            self._voice_error(m))

    def _on_voice_text(self, text, final):
        """Runs in the mic thread — extract newly-spoken words, dispatch to UI."""
        if not self._voice_active:
            return
        words = text.split()
        if final:
            new = words[self._voice_spoken_count:]
            self._voice_spoken_count = 0
        else:
            stable = words[:-1]              # last word of a partial is unstable
            new    = stable[self._voice_spoken_count:]
            self._voice_spoken_count = len(stable)
        for w in new:
            nw = self._normalize_word(w)
            if nw:
                self.root.after(0, lambda x=nw: self._voice_advance(x))

    def _voice_advance(self, spoken):
        """Runs on the main thread — match a spoken word forward, move highlight.

        Conservative on purpose: a short lookahead and tight rules keep the
        highlight from leaping to a random later occurrence of a common word.
        """
        if not self._voice_active:
            return
        words = self._word_texts
        idx   = self._autoscroll_word_idx
        n     = len(words)
        if idx >= n:
            return
        LOOK  = 8                            # small lookahead → no far jumps
        end   = min(n, idx + LOOK)
        near  = idx + 2                      # "very close" cutoff for short words
        match = -1
        for p in range(idx, end):           # exact match first
            if words[p] == spoken:
                if len(spoken) <= 2 and p >= near:
                    continue                 # don't let "the/a/of/in" leap ahead
                match = p
                break
        if match < 0 and len(spoken) >= 5:  # fuzzy: long words only, tight & near
            for p in range(idx, min(n, idx + 5)):
                wt = words[p]
                if wt and abs(len(wt) - len(spoken)) <= 1 and \
                   self._lev(wt, spoken) <= 1:
                    match = p
                    break
        if match >= 0:
            self._highlight_word(match)
            self._autoscroll_word_idx = match + 1

    @staticmethod
    def _lev(a, b):
        """Levenshtein distance, early-exit beyond the matcher's threshold of 2."""
        if a == b:
            return 0
        if abs(len(a) - len(b)) > 2:
            return 3
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                cost = 0 if ca == cb else 1
                cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
            prev = cur
        return prev[len(b)]

    def _seek_word(self, event):
        """Click a word while following along → move the highlight pointer there."""
        if self._editing:
            return "break"
        click = self.text.index(f"@{event.x},{event.y}")
        for p, (a, b) in enumerate(self._autoscroll_words):
            if self.text.compare(click, ">=", a) and self.text.compare(click, "<", b):
                self._autoscroll_word_idx = p
                self._highlight_word(p)
                break
        return "break"

    # ── AI COPILOT (local, on-device) ────────────────────────────────────────
    #
    # A separate `copilot/` engine runs WASAPI loopback → faster-whisper → Qwen3
    # (Ollama) and streams the answer back here. All engine callbacks fire on
    # worker threads, so every one is marshalled onto the tk loop via root.after.

    def _toggle_copilot(self):
        if self._copilot_active:
            self._stop_copilot()
        else:
            self._start_copilot()

    def _start_copilot(self):
        try:
            from copilot import CopilotEngine
        except Exception as e:                       # noqa: BLE001
            print(f"[Copilot] engine import failed: {type(e).__name__}: {e}")
            self._manuscript_lbl.config(
                text="✦  COPILOT  ·  install deps: pip install -r requirements.txt")
            return

        # Copilot takes over the card — stop any teleprompter following / editing.
        self._stop_following()
        if self._editing:
            self._commit_edit()
        if self._logs_visible:
            self._hide_logs()

        self._copilot_active     = True
        self._copilot_status     = "idle"
        self._copilot_transcript = []
        self._copilot_question   = ""
        self._copilot_answer     = ""
        self._copilot_answer_epoch = 0      # drop stray tokens from an aborted stream
        self._copilot_editing    = False    # editing the pending-question panel?

        def after(fn):
            return lambda *a, **k: self.root.after(0, lambda: fn(*a, **k))

        self._copilot_last_heard = ""
        self._copilot_audio_at   = 0.0
        self._copilot_lines      = []
        self._copilot_partial    = ""
        self._copilot = CopilotEngine(
            on_status       =after(self._copilot_set_status),
            on_transcript   =after(self._copilot_on_transcript),
            on_utterance    =after(self._copilot_on_utterance),
            on_partial      =after(self._copilot_on_partial),
            on_answer_start =after(self._copilot_on_answer_start),
            on_thinking     =after(self._copilot_on_thinking),
            on_answer_token =after(self._copilot_on_answer_token),
            on_answer_done  =after(self._copilot_on_answer_done),
            on_error        =after(self._copilot_on_error),
            on_audio        =after(self._copilot_on_audio))
        self._copilot.start()
        self._register_copilot_hotkey()
        self._copilot_show_pane(True)
        self._copilot_clear_transcript()
        self._copilot_render()
        # _hide_logs() above may have reset the active tab to NOTES — reclaim the
        # COPILOT highlight (this won't re-tear-down: teardown is name != copilot).
        self._set_active_tab("copilot")

    def _stop_copilot(self, set_notes_tab=True):
        if not self._copilot_active:
            return
        if self._copilot_editing:
            self._copilot_exit_edit()        # restore the pane to a clean state
        self._copilot_active = False
        self._unregister_copilot_hotkey()
        if self._copilot is not None:
            self._copilot.stop()
            self._copilot = None
        self._copilot_show_pane(False)   # hide the live-transcript pane
        if set_notes_tab:
            self._set_active_tab("notes")
        self.render_slide()        # always restore the teleprompter card (row 0)

    # ── engine callbacks (already marshalled to the tk loop) ──────────────────
    def _copilot_set_status(self, status):
        if not self._copilot_active:
            return
        self._copilot_status = status
        self._copilot_update_header()
        # Keep the transcript-pane placeholder honest (loading vs listening vs
        # error) until the first real speech arrives.
        if not self._copilot_lines and not self._copilot_partial:
            self._copilot_render_transcript()

    def _copilot_on_transcript(self, buffer):
        # Full rolling buffer (used by the engine for LLM context). The visible
        # live transcript is appended incrementally in _copilot_on_utterance, and
        # the answer streams into self.text on F9 — so nothing to repaint here.
        if not self._copilot_active:
            return
        self._copilot_transcript = list(buffer)

    def _copilot_on_answer_start(self, question, epoch=0):
        if not self._copilot_active:
            return
        # New run owns the card now — record its epoch so late tokens from a
        # superseded (aborted/re-rolled) stream are dropped below.
        self._copilot_answer_epoch = epoch
        self._copilot_question    = question
        self._copilot_answer      = ""
        self._copilot_think_open  = False
        self._copilot_answer_open = False
        # Hold the widget "normal" for the whole stream (re-disabled in _done) so
        # tokens don't flip state on every chunk. The card has no caret
        # (insertwidth=0) and clicks are swallowed by _on_text_click during
        # copilot, so leaving it normal can't be edited by the user.
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", question.strip() + "\n", "h2")
        self.text.insert("end", "\n")
        self.text.see("1.0")        # show the top of the new answer; do NOT follow
        self._copilot_update_header()

    def _copilot_on_thinking(self, token, epoch=0):
        """Live reasoning preview — stream it (dim/italic) so you can start framing
        a reply before the polished answer is ready."""
        if not self._copilot_active or epoch != self._copilot_answer_epoch:
            return
        if not self._copilot_think_open:
            self.text.insert("end", "💭  THINKING…\n", "prompt_header")
            self._copilot_think_open = True
        self.text.insert("end", token, "copilot_think")
        # No auto-scroll — the user controls the viewport while it streams.

    def _copilot_on_answer_token(self, token, epoch=0):
        if not self._copilot_active or epoch != self._copilot_answer_epoch:
            return
        # First answer token: if reasoning was shown above, label the answer so the
        # two are visually separated; otherwise stream the answer straight in.
        if not self._copilot_answer_open:
            if self._copilot_think_open:
                self.text.insert("end", "\n\n✦  ANSWER\n", "prompt_header")
            self._copilot_answer_open = True
        self._copilot_answer += token
        self.text.insert("end", token, "body")
        # No auto-scroll while generating — let the user scroll at their own pace.

    def _copilot_on_answer_done(self, epoch=0):
        if not self._copilot_active or epoch != self._copilot_answer_epoch:
            return
        self.text.config(state="disabled")     # re-lock after the streamed answer
        self._copilot_update_header()

    def _copilot_on_error(self, msg):
        print(f"[Copilot] {msg}")
        if self._copilot_active:
            self._copilot_status = f"error — {msg}"
            self._copilot_update_header()
            if not self._copilot_lines and not self._copilot_partial:
                self._copilot_render_transcript()

    # ── trigger + rendering ───────────────────────────────────────────────────
    def _copilot_trigger(self):
        if not self._copilot_active or not self._copilot:
            return
        # While editing the question, Enter / Ctrl+Enter mean "commit + send" — not
        # "trigger on the raw pending speech". (Plain Enter is already intercepted by
        # the edit-box binding; this also covers the global Ctrl+Enter hotkey.)
        if self._copilot_editing:
            self._copilot_commit_edit()
            return
        self._copilot.trigger()

    def _copilot_reset(self):
        """F10 — start a fresh conversation: wipe the LLM memory and clear the
        on-screen transcript + answer."""
        if not self._copilot_active or not self._copilot:
            return
        if self._copilot_editing:
            self._copilot_exit_edit()        # drop any in-progress question edit
        self._copilot.reset_conversation()
        self._copilot_lines    = []
        self._copilot_partial  = ""
        self._copilot_question = ""
        self._copilot_answer   = ""
        self._copilot_render_transcript()        # transcript pane → placeholder
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", "💬  NEW CONVERSATION\n", "prompt_header")
        self.text.insert("end", "Memory cleared. Press Enter to answer the next "
                                "question.", "body")
        self.text.config(state="disabled")

    def _copilot_on_audio(self, rms, device_name):
        """Throttled (~3 Hz) audio-activity heartbeat from the capture layer."""
        if not self._copilot_active:
            return
        self._copilot_audio_rms = rms
        self._copilot_audio_at  = time.monotonic()
        if device_name:
            self._copilot_device = device_name
        # Refresh the header only while idle/listening — never interrupt a stream.
        if self._copilot_status in ("listening", "idle"):
            self._copilot_update_header()

    def _copilot_update_header(self):
        # If the global hook failed (e.g. no admin), Enter only fires while the
        # overlay is focused — tell the user instead of silently to stdout.
        f9 = "Enter" if self._copilot_hotkey else "Enter (focus overlay)"
        st = self._copilot_status
        if self._copilot_editing:
            label = "✦  COPILOT  ·  ✎ EDITING QUESTION  ·  Enter sends · Esc cancels"
        elif st == "generating":
            label = "✦  COPILOT  ·  ✦ GENERATING…"
        elif st.startswith("loading") or st == "idle":
            label = "✦  COPILOT  ·  ⏳ LOADING MODEL…"
        elif st.startswith("error"):
            label = f"✦  COPILOT  ·  ⚠ {st[8:]}"
        elif st == "listening":
            fresh   = (time.monotonic() - self._copilot_audio_at) < 0.6
            hearing = fresh and self._copilot_audio_rms > 80   # quiet-room floor
            if hearing:
                label = f"✦  COPILOT  ·  🔊 HEARING…  ·  {f9} to answer"
            elif self._copilot_audio_at:
                label = f"✦  COPILOT  ·  ● LISTENING (silence)  ·  {f9}"
            else:
                label = f"✦  COPILOT  ·  ● LISTENING (no audio yet)  ·  {f9}"
        else:
            label = f"✦  COPILOT  ·  {st}"
        self._manuscript_lbl.config(text=label)

    def _build_copilot_pane(self):
        """Live-transcript pane shown above the answer card during copilot mode.
        Reuses row 0 of the viewport grid (where the prev card normally sits)."""
        if self._copilot_pane is not None:
            return
        pane = tk.Frame(self._vp, bg=C["card_dim"])
        hdr = tk.Frame(pane, bg=C["card_dim"])
        hdr.pack(fill="x", padx=12, pady=(8, 2))
        tk.Label(hdr, text="◉  LIVE TRANSCRIPT", bg=C["card_dim"],
                 fg=C["primary"], font=(LABEL, 7, "bold")).pack(side="left")
        # Hint doubles as the live-words indicator while editing the question.
        self._copilot_tr_hint = tk.Label(
            hdr, text=self._COPILOT_TR_HINT, bg=C["card_dim"],
            fg=C["muted"], font=(LABEL, 7))
        self._copilot_tr_hint.pack(side="right")
        txt = tk.Text(pane, height=6, bg=C["card_dim"], fg=C["on_sv"],
                      relief="flat", bd=0, state="disabled", wrap="word",
                      cursor="arrow", font=(SANS, max(8, self.font_size - 1)),
                      padx=12, pady=(0), spacing3=3, insertwidth=0,
                      highlightthickness=0)
        txt.pack(fill="both", expand=True, padx=2, pady=(0, 8))
        txt.tag_configure("tr_line", foreground=C["on_sv"])
        txt.tag_configure("tr_new",  foreground=C["on_s"])
        txt.tag_configure("tr_partial", foreground=C["primary"], font=(SANS,
                          max(8, self.font_size - 1), "italic"))
        # Click a word in the pending question to correct it before sending.
        txt.bind("<Button-1>", self._copilot_tr_click)
        self._copilot_pane    = pane
        self._copilot_tr_text = txt

    def _copilot_show_pane(self, show):
        self._build_copilot_pane()
        if show:
            # Row 0 keeps weight 0 (its default) so the transcript stays a fixed
            # ~6-line band and the answer card in row 1 (weight 1) takes the rest.
            self._copilot_pane.grid(row=0, column=0, sticky="nsew", pady=(0, 4))
        else:
            self._copilot_pane.grid_remove()

    def _copilot_clear_transcript(self):
        self._copilot_lines   = []
        self._copilot_partial = ""
        self._copilot_render_transcript()

    def _copilot_set_placeholder(self):
        self._copilot_render_transcript()

    def _copilot_render_transcript(self):
        """Render the transcript pane: finalized lines + the live partial line.
        Falls back to a state-aware placeholder when nothing has been heard yet."""
        txt = self._copilot_tr_text
        if txt is None or self._copilot_editing:
            return            # never repaint over an in-progress question edit
        txt.config(state="normal")
        txt.delete("1.0", "end")
        if not self._copilot_lines and not self._copilot_partial:
            st = self._copilot_status
            if st == "listening":
                msg = "Listening… speech appears here as you speak."
            elif st.startswith("error"):
                msg = "⚠  " + st[8:]
            else:
                msg = ("⏳  Loading the speech model — first run downloads it, this "
                       "can take a minute. Transcript starts once it's ready.")
            txt.insert("end", msg + "\n", "tr_line")
        else:
            for ln in self._copilot_lines[-10:]:
                txt.insert("end", "•  " + ln + "\n", "tr_new")
            if self._copilot_partial:
                txt.insert("end", "▶  " + self._copilot_partial + "…", "tr_partial")
        txt.see("end")
        txt.config(state="disabled")

    def _copilot_on_utterance(self, text):
        """A finalized sentence → commit it as a transcript line, clear the partial."""
        if not self._copilot_active:
            return
        self._copilot_lines.append(text)
        self._copilot_lines = self._copilot_lines[-30:]
        self._copilot_partial = ""
        if self._copilot_editing:
            # Approach A: while you edit, new speech lands at the END of the edit
            # buffer without disturbing your caret — so the tail stays editable too.
            self._copilot_edit_append(text)
        else:
            self._copilot_render_transcript()

    def _copilot_on_partial(self, text):
        """Live partial words (updates many times/sec) → refresh the partial line."""
        if not self._copilot_active or not text:
            return
        self._copilot_partial = text
        if self._copilot_editing:
            # Don't inject the volatile partial into the editable text — show it as a
            # dim live indicator in the pane hint instead.
            self._copilot_tr_set_hint(
                "🔊 " + (text[:42] + "…" if len(text) > 42 else text))
        else:
            self._copilot_render_transcript()

    # ── editable question panel (click a word → fix → Enter sends) ─────────────
    def _copilot_tr_set_hint(self, text=None):
        if self._copilot_tr_hint is not None:
            self._copilot_tr_hint.config(
                text=self._COPILOT_TR_HINT if text is None else text)

    def _copilot_tr_click(self, event):
        """Single click in the transcript pane → edit the pending question.
        Only while listening/idle (never mid-answer), and only if there's a pending
        question to send. A disabled tk.Text still delivers <Button-1>."""
        if (not self._copilot_active or self._copilot_editing
                or self._copilot is None
                or self._copilot_status not in ("listening", "idle")):
            return
        lines = self._copilot.pending_lines()
        if not lines:
            return                       # nothing pending — nothing to edit/send
        self._copilot_enter_edit(lines, event)
        return "break"

    def _copilot_enter_edit(self, lines, event):
        self._copilot_editing = True
        txt = self._copilot_tr_text
        # insertbackground = caret colour; default is black → invisible on the dark
        # pane, so force a bright near-white caret while editing.
        txt.config(state="normal", insertwidth=2, cursor="xterm",
                   insertbackground=C["on_s"])
        txt.delete("1.0", "end")
        txt.insert("end", "\n".join(lines))     # one utterance per line
        try:                                     # caret near the click (tk clamps)
            txt.mark_set("insert", txt.index(f"@{event.x},{event.y}"))
        except tk.TclError:
            txt.mark_set("insert", "end")
        txt.focus_set()
        txt.see("insert")
        # Instance bindings fire before the class binding, so returning "break"
        # both sends and suppresses the newline the Text would otherwise insert.
        txt.bind("<Return>",   self._copilot_commit_edit)
        txt.bind("<KP_Enter>", self._copilot_commit_edit)
        txt.bind("<Escape>",   self._copilot_cancel_edit)
        self._copilot_tr_set_hint("✎ editing — Enter sends · Esc cancels")
        self._copilot_update_header()

    def _copilot_edit_append(self, text):
        """Append a finalized utterance to the end of the edit buffer, keeping the
        user's caret and viewport put (Approach A)."""
        txt = self._copilot_tr_text
        saved   = txt.index("insert")
        at_tail = txt.compare("insert", ">=", "end-1c")
        txt.insert("end", "\n" + text)
        txt.mark_set("insert", saved)            # don't let the append drag the caret
        if at_tail:
            self._copilot_tr_set_hint("▼ new speech added below")
        else:
            txt.see("insert")                    # stay where they're editing

    def _copilot_commit_edit(self, event=None):
        """Enter → send exactly what's in the box (collapsing it to one question),
        clearing pending so mid-edit utterances aren't double-counted."""
        if not self._copilot_editing:
            return "break"
        raw = self._copilot_tr_text.get("1.0", "end-1c")
        question = " ".join(raw.split())
        self._copilot_exit_edit()
        if question and self._copilot is not None:
            self._copilot.trigger_with(question)
        return "break"

    def _copilot_cancel_edit(self, event=None):
        """Esc → discard the edit, restore the live transcript view."""
        if not self._copilot_editing:
            return "break"
        self._copilot_exit_edit()
        self._copilot_render_transcript()
        return "break"

    def _copilot_exit_edit(self):
        self._copilot_editing = False
        txt = self._copilot_tr_text
        for seq in ("<Return>", "<KP_Enter>", "<Escape>"):
            txt.unbind(seq)
        txt.config(insertwidth=0, cursor="arrow", state="disabled")
        self._copilot_tr_set_hint(None)
        self.root.focus_set()            # hand Enter back to the global trigger
        self._copilot_update_header()

    def _copilot_render(self):
        """Paint the copilot view into the active card (idle / transcript state)."""
        self._prev_card.grid_remove()
        self._next_card.grid_remove()
        self._copilot_update_header()
        # The live transcript lives in its own pane above; self.text is the ANSWER
        # area. When no answer has streamed yet, show a short hint here.
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", "✦  ANSWER\n", "prompt_header")
        if self._copilot_device:
            self._insert_rich(
                f"Capturing from: **{self._copilot_device}**", "body")
        self.text.insert(
            "end",
            "\nWatch the live transcript above. When you hear a question, press  "
            "Enter  — the answer streams in here, invisible to screen capture.",
            "body")
        self.text.config(state="disabled")

    def _register_copilot_hotkey(self):
        # When the overlay HAS focus: plain Enter = answer, F10 = new conversation.
        self.root.bind("<Return>", lambda e: self._copilot_trigger())
        self.root.bind("<F10>",    lambda e: self._copilot_reset())
        # Global (overlay NOT focused, e.g. meeting window in front): use Ctrl+Enter
        # so it can't misfire on every plain Enter you type elsewhere. (Fn is
        # firmware-level and invisible to software, so Fn+Enter can't be hooked.)
        self._copilot_hotkey_handle = None
        try:
            import keyboard
            keyboard.add_hotkey("ctrl+enter", lambda: self.root.after(0, self._copilot_trigger))
            keyboard.add_hotkey("f10",        lambda: self.root.after(0, self._copilot_reset))
            self._copilot_hotkey = True
        except Exception as e:                       # noqa: BLE001
            self._copilot_hotkey = False
            print(f"[Copilot] global Ctrl+Enter/F10 hotkeys unavailable "
                  f"({type(e).__name__}: {e}). Plain Enter still works when the "
                  f"overlay is focused.")

    def _unregister_copilot_hotkey(self):
        self.root.unbind("<Return>")
        self.root.unbind("<F10>")
        if self._copilot_hotkey:
            # unhook_all() removes our f9 + f10 hotkeys AND tears down keyboard's
            # global low-level hook + listener thread, so the global keyboard hook
            # dies with copilot mode rather than only when the process exits.
            try:
                import keyboard
                keyboard.unhook_all()
            except Exception:                        # noqa: BLE001
                pass
            self._copilot_hotkey = False
            self._copilot_hotkey_handle = None

    # ── SETTINGS POPUP ───────────────────────────────────────────────────────

    def _open_settings(self):
        if self._settings_win and self._settings_win.winfo_exists():
            self._settings_win.destroy()
            self._settings_win = None
            return

        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=C["prompt"])

        self.root.update_idletasks()
        bx = self._gear_btn.winfo_rootx()
        by = self._gear_btn.winfo_rooty() + self._gear_btn.winfo_height() + 4
        win.geometry(f"230x280+{bx - 178}+{by}")
        self._cloak(win)

        _lbl = dict(bg=C["prompt"], fg=C["on_sv"], font=(LABEL, 8))

        # ── OPACITY ──
        tk.Label(win, text="WINDOW OPACITY", **_lbl).pack(
            anchor="w", padx=14, pady=(12, 2))

        op_row = tk.Frame(win, bg=C["prompt"])
        op_row.pack(fill="x", padx=14)
        self._op_label = tk.Label(op_row,
            text=f"{int(self.root.attributes('-alpha')*100)}%", **_lbl)
        self._op_label.pack(side="right")

        tk.Scale(win, from_=20, to=100, resolution=1, orient="horizontal",
                 showvalue=False, bg=C["prompt"], troughcolor=C["outline"],
                 highlightthickness=0, bd=0, sliderrelief="flat",
                 activebackground=C["primary"], command=self._on_opacity
                 ).pack(fill="x", padx=14)

        tk.Frame(win, bg=C["outline"], height=1).pack(fill="x", padx=14, pady=8)

        # ── SYNC MODE ──
        tk.Label(win, text="SYNC MODE", **_lbl).pack(anchor="w", padx=14)

        mode_row = tk.Frame(win, bg=C["prompt"])
        mode_row.pack(fill="x", padx=14, pady=(4, 8))

        self._mode_var   = tk.StringVar(value=self._sync_mode or "none")
        self._sync_input = None   # will hold the input frame

        def set_sync_ui(mode):
            self._mode_var.set(mode)
            # style buttons
            for m, btn in mode_btns.items():
                btn.config(bg=C["primary"] if m == mode else C["outline"],
                           fg=C["bg"]     if m == mode else C["on_sv"])
            # rebuild input area
            if self._sync_input and self._sync_input.winfo_exists():
                self._sync_input.destroy()
            f = tk.Frame(win, bg=C["prompt"])
            f.pack(fill="x", padx=14, pady=(0, 4))
            self._sync_input = f
            if mode == "firebase":
                tk.Label(f, text="Database URL", **_lbl).pack(anchor="w")
                entry = tk.Entry(f, bg=C["card_dim"], fg=C["on_s"],
                                 insertbackground=C["on_s"],
                                 relief="flat", font=(SANS, 9), bd=4)
                entry.pack(fill="x", pady=(2, 4))
                if self._sync_mode == "firebase" and self._firebase_url:
                    entry.insert(0, self._firebase_url)
                tk.Button(f, text="Connect",
                          command=lambda: self._connect_firebase(
                              entry.get(), win),
                          bg=C["primary"], fg=C["bg"], bd=0,
                          font=(LABEL, 8, "bold"), relief="flat",
                          cursor="hand2", pady=4
                          ).pack(fill="x")
            elif mode == "local":
                tk.Label(f, text="Overlay IP : Port", **_lbl).pack(anchor="w")
                import socket
                try:    default_ip = socket.gethostbyname(socket.gethostname())
                except: default_ip = "127.0.0.1"
                entry = tk.Entry(f, bg=C["card_dim"], fg=C["on_s"],
                                 insertbackground=C["on_s"],
                                 relief="flat", font=(SANS, 9), bd=4)
                entry.insert(0, f"{default_ip}:8765")
                entry.pack(fill="x", pady=(2, 4))
                tk.Button(f, text="Start Server",
                          command=lambda: self._connect_local(
                              entry.get(), win),
                          bg=C["primary"], fg=C["bg"], bd=0,
                          font=(LABEL, 8, "bold"), relief="flat",
                          cursor="hand2", pady=4
                          ).pack(fill="x")
            elif mode == "none":
                if self._sync_mode:
                    tk.Button(f, text="Disconnect",
                              command=lambda: [self.stop_sync(), win.destroy()],
                              bg=C["outline"], fg=C["on_s"], bd=0,
                              font=(LABEL, 8, "bold"), relief="flat",
                              cursor="hand2", pady=4
                              ).pack(fill="x")
            # resize popup
            win.update_idletasks()
            needed_h = win.winfo_reqheight() + 10
            win.geometry(f"230x{needed_h}+{bx - 178}+{by}")

        _mb = dict(bd=0, font=(LABEL, 7, "bold"), cursor="hand2",
                   relief="flat", padx=8, pady=4)
        mode_btns = {}
        for mode_val, mode_txt in [("firebase", "☁ Firebase"),
                                    ("local",    "📡 Local"),
                                    ("none",     "✕ Off")]:
            b = tk.Button(mode_row, text=mode_txt,
                          command=lambda m=mode_val: set_sync_ui(m),
                          bg=C["outline"], fg=C["on_sv"], **_mb)
            b.pack(side="left", padx=(0, 3))
            mode_btns[mode_val] = b

        # Highlight current mode button
        cur = self._sync_mode if self._sync_mode else "none"
        mode_btns[cur].config(bg=C["primary"], fg=C["bg"])

        tk.Frame(win, bg=C["outline"], height=1).pack(fill="x", padx=14, pady=(4, 0))

        # ── LOAD FILE ──
        tk.Button(win, text="📂  Load File",
                  command=lambda: [win.destroy(), self.load_file()],
                  bg=C["prompt"], fg=C["on_s"], bd=0,
                  font=(LABEL, 9), activebackground=C["primary"],
                  activeforeground=C["bg"], relief="flat",
                  anchor="w", cursor="hand2", padx=4, pady=8
                  ).pack(fill="x", padx=14)

        def _maybe_close():
            if not win.winfo_exists():
                return
            # Only close if focus has moved completely outside this popup
            fw = win.focus_get()
            if fw is None:
                win.destroy()
            # fw is a widget inside win → user clicked an entry/button, keep open

        win.bind("<FocusOut>", lambda e: win.after(200, _maybe_close))
        win.focus_set()
        self._settings_win = win

    def _connect_firebase(self, url, win):
        url = url.strip()
        if not url:
            return
        win.destroy()
        self.start_firebase_sync(url)

    def _connect_local(self, addr, win):
        addr = addr.strip()
        win.destroy()
        port = 8765
        if ":" in addr:
            try:
                port = int(addr.split(":")[-1])
            except ValueError:
                pass
        self.start_local_ws_server(port)
        import socket
        try:    ip = socket.gethostbyname(socket.gethostname())
        except: ip = "127.0.0.1"
        print(f"WebSocket server on {ip}:{port}")

    def _on_opacity(self, val):
        v = int(val)
        self.root.attributes("-alpha", v / 100)
        if hasattr(self, "_op_label"):
            self._op_label.config(text=f"{v}%")

    # ── RESIZE HANDLES ───────────────────────────────────────────────────────

    def _setup_resize_handles(self):
        CORNER, EDGE = 14, 6
        for cursor, kw, d in [
            ("size_ns",    dict(relx=0.5, rely=0,   anchor="n",  relwidth=1.0, height=EDGE),   "n"),
            ("size_ns",    dict(relx=0.5, rely=1.0, anchor="s",  relwidth=1.0, height=EDGE),   "s"),
            ("size_we",    dict(relx=0,   rely=0.5, anchor="w",  width=EDGE,   relheight=1.0), "w"),
            ("size_we",    dict(relx=1.0, rely=0.5, anchor="e",  width=EDGE,   relheight=1.0), "e"),
            ("size_nw_se", dict(relx=0,   rely=0,   anchor="nw", width=CORNER, height=CORNER), "nw"),
            ("size_ne_sw", dict(relx=1.0, rely=0,   anchor="ne", width=CORNER, height=CORNER), "ne"),
            ("size_ne_sw", dict(relx=0,   rely=1.0, anchor="sw", width=CORNER, height=CORNER), "sw"),
            ("size_nw_se", dict(relx=1.0, rely=1.0, anchor="se", width=CORNER, height=CORNER), "se"),
        ]:
            f = tk.Frame(self.root, cursor=cursor, bg=C["bg"])
            f.place(**kw)
            f.bind("<ButtonPress-1>", lambda e, dd=d: self._start_resize(e, dd))
            f.bind("<B1-Motion>",     lambda e, dd=d: self._do_resize(e, dd))

    def _start_resize(self, event, direction):
        self._resize_data = dict(
            start_x=event.x_root, start_y=event.y_root,
            orig_x=self.root.winfo_x(), orig_y=self.root.winfo_y(),
            orig_w=self.root.winfo_width(), orig_h=self.root.winfo_height())

    def _do_resize(self, event, direction):
        if not self._resize_data: return
        d  = self._resize_data
        dx = event.x_root - d["start_x"]
        dy = event.y_root - d["start_y"]
        x, y, w, h = d["orig_x"], d["orig_y"], d["orig_w"], d["orig_h"]
        if   direction == "se": w=max(MIN_W,w+dx);  h=max(MIN_H,h+dy)
        elif direction == "sw": nw=max(MIN_W,w-dx); x+=w-nw; w=nw; h=max(MIN_H,h+dy)
        elif direction == "ne": w=max(MIN_W,w+dx);  nh=max(MIN_H,h-dy); y+=h-nh; h=nh
        elif direction == "nw": nw=max(MIN_W,w-dx); x+=w-nw; w=nw; nh=max(MIN_H,h-dy); y+=h-nh; h=nh
        elif direction == "e":  w=max(MIN_W,w+dx)
        elif direction == "w":  nw=max(MIN_W,w-dx); x+=w-nw; w=nw
        elif direction == "s":  h=max(MIN_H,h+dy)
        elif direction == "n":  nh=max(MIN_H,h-dy); y+=h-nh; h=nh
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ── FONT ─────────────────────────────────────────────────────────────────

    def _change_font(self, delta):
        self.font_size = max(8, min(24, self.font_size + delta))
        self._configure_tags()
        self._prev_text.config(font=(SERIF, self.font_size - 2, "italic"))
        self._next_text.config(font=(SANS,  self.font_size - 2))
        self.text.config(font=(SANS, self.font_size))
        # Copilot owns the card — the font/tags already updated live; don't repaint
        # the slide over the streaming answer (same guard as _apply_remote_content).
        if self._copilot_active:
            return
        self.render_slide()

    # ── DRAG ─────────────────────────────────────────────────────────────────

    def _start_drag(self, event):
        self._drag_x = event.x_root - self.root.winfo_x()
        self._drag_y = event.y_root - self.root.winfo_y()

    def _do_drag(self, event):
        self.root.geometry(
            f"+{event.x_root-self._drag_x}+{event.y_root-self._drag_y}")

    # ── KEY BINDINGS ─────────────────────────────────────────────────────────

    def _bind_keys(self):
        self.root.bind("<Left>",   lambda e: self.navigate(-1))
        self.root.bind("<Right>",  lambda e: self.navigate(1))
        self.root.bind("<Escape>", self._handle_escape)

    def _handle_escape(self, event):
        if self._settings_win and self._settings_win.winfo_exists():
            self._settings_win.destroy()
        elif self._copilot_active:
            self._stop_copilot()
        elif self._logs_visible:
            self._hide_logs()
        else:
            self._quit()

    # ── GLOBAL SHOW/HIDE HOTKEY (Ctrl+Shift+`) ───────────────────────────────

    def _register_global_hotkey(self):
        """Register a system-wide Ctrl+Shift+` hotkey that toggles the overlay's
        visibility even when another window is focused. Uses Win32 RegisterHotKey
        via ctypes — no extra dependency, so it also works in the packaged .exe.
        The hotkey needs its own message loop, so it runs on a daemon thread and
        marshals the toggle back onto the tk loop with root.after()."""
        try:
            self._hotkey_thread = threading.Thread(
                target=self._hotkey_loop, name="global-hotkey", daemon=True)
            self._hotkey_thread.start()
        except Exception as e:               # noqa: BLE001 — never block startup
            print(f"[Hotkey] could not start global hotkey thread: {e}")

    def _hotkey_loop(self):
        from ctypes import wintypes
        user32 = self._user32
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG),
                                       wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype  = ctypes.c_int

        MOD_SHIFT, MOD_CONTROL, MOD_NOREPEAT = 0x0004, 0x0002, 0x4000
        VK_OEM_3  = 0xC0        # the ` / ~ key (backtick) on a US layout
        WM_HOTKEY = 0x0312
        hk_id     = 0xB1B       # arbitrary unique id for this process
        mods      = MOD_CONTROL | MOD_SHIFT

        # MOD_NOREPEAT stops key auto-repeat from firing the toggle repeatedly;
        # it's unsupported before Win7, so fall back without it.
        if not user32.RegisterHotKey(None, hk_id, mods | MOD_NOREPEAT, VK_OEM_3) \
           and not user32.RegisterHotKey(None, hk_id, mods, VK_OEM_3):
            print("[Hotkey] Ctrl+Shift+` unavailable (another app may own it).")
            return
        self._hotkey_id = hk_id

        msg = wintypes.MSG()
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r in (0, -1):                 # WM_QUIT or error
                break
            if msg.message == WM_HOTKEY:
                try:
                    self.root.after(0, self._toggle_visibility)
                except Exception:            # noqa: BLE001 — root gone, stop the loop
                    break
        try:
            user32.UnregisterHotKey(None, hk_id)
        except Exception:                    # noqa: BLE001
            pass

    def _toggle_visibility(self):
        """Hide the overlay, or bring it back on top. Bound to Ctrl+Shift+`."""
        try:
            if self._hidden:
                self.root.deiconify()
                self.root.overrideredirect(True)      # stay borderless after re-show
                self.root.attributes("-topmost", True)
                self._cloak(self.root)                # re-assert capture cloaking
                self.root.lift()
                self.root.focus_force()
                self._hidden = False
            else:
                self.root.withdraw()
                self._hidden = True
        except tk.TclError:
            pass


# ── .ENV LOADER ──────────────────────────────────────────────────────────────

def _load_dotenv():
    """Load KEY=VALUE pairs from a .env file next to overlay.py (no external deps)."""
    env_path = os.path.join(_app_dir(), ".env")
    if not os.path.exists(env_path):
        return
    # utf-8-sig strips a BOM — Windows Notepad writes UTF-8 with BOM by default,
    # which would otherwise glue ﻿ onto the first key and break the lookup.
    with open(env_path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip(); v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:   # CLI env always wins
                os.environ[k] = v


def _print_local_ip(port):
    import socket
    try:    ip = socket.gethostbyname(socket.gethostname())
    except: ip = "127.0.0.1"
    print(f"Local WebSocket server started.\nOpen the web UI and enter: {ip}:{port}")


# ── ENTRY POINT ──────────────────────────────────────────────────────────────

def main():
    _load_dotenv()
    root = tk.Tk()
    app  = OverlayApp(root)
    # Ensure copilot/STT child processes + global hotkey are torn down on any
    # window-manager close, not just the in-app ✕ / Escape paths.
    root.protocol("WM_DELETE_WINDOW", app._quit)

    args = sys.argv[1:]

    if "--firebase" in args:
        idx = args.index("--firebase")
        # URL arg is optional — falls back to FIREBASE_URL from .env
        if idx + 1 < len(args) and not args[idx + 1].startswith("--"):
            url = args[idx + 1]
        else:
            url = os.environ.get("FIREBASE_URL", "")
        if url:
            app.start_firebase_sync(url)
        else:
            print("No Firebase URL provided. Pass it as: --firebase <URL>  "
                  "or set FIREBASE_URL=<URL> in a .env file next to overlay.py")
    elif "--local" in args:
        port = int(args[args.index("--port") + 1]) if "--port" in args else 8765
        app.start_local_ws_server(port)
        _print_local_ip(port)
    elif args and not args[0].startswith("--"):
        app.load_file(args[0])
    else:
        # No CLI mode flag — auto-start from .env if values are present
        if os.environ.get("FIREBASE_URL"):
            print(f"Auto-connecting to Firebase from .env …")
            app.start_firebase_sync(os.environ["FIREBASE_URL"])
        elif os.environ.get("LOCAL_WS", "").lower() in ("1", "true", "yes"):
            port = int(os.environ.get("LOCAL_WS_PORT", "8765"))
            app.start_local_ws_server(port)
            _print_local_ip(port)

    root.mainloop()
    # mainloop returned -> the window is gone. _quit() already did best-effort
    # teardown (engine.stop terminates the STT child, keyboard.unhook_all removes
    # the global hook). But a surviving non-daemon RealtimeSTT child can still
    # wedge multiprocessing's atexit join — leaving the PROCESS (and its audio
    # capture) alive after the window closes, which is a privacy leak. Kill any
    # child that's left, drop the keyboard hook, then force-exit so the process
    # ALWAYS dies. Terminate BEFORE os._exit so the capture child isn't orphaned.
    try:
        import multiprocessing
        for p in multiprocessing.active_children():
            try:
                p.terminate()
            except Exception:            # noqa: BLE001
                pass
        for p in multiprocessing.active_children():
            p.join(timeout=1.0)
    except Exception:                    # noqa: BLE001
        pass
    try:
        import keyboard
        keyboard.unhook_all()
    except Exception:                    # noqa: BLE001
        pass
    os._exit(0)                          # guarantee the red ✕ ends the script


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()   # safe under RealtimeSTT's spawn child procs
    main()
