import tkinter as tk
from tkinter import filedialog
import ctypes, re, sys, os, threading, json, time, asyncio

WDA_EXCLUDEFROMCAPTURE = 0x00000011

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

    def __init__(self, root):
        self.root      = root
        self.slides    = ["# Welcome\n\nOpen a file via ⚙  or connect to sync."]
        self.current   = 0
        self.font_size = 12

        self._drag_x = self._drag_y = 0
        self._resize_data   = None
        self._sync_mode     = None
        self._sync_version  = 0          # increments to cancel old sync threads
        self._settings_win  = None
        self._logs_visible  = False
        self._logs_frame    = None
        self._elapsed       = 0
        self._live_state    = True

        self._autoscroll_active   = False
        self._autoscroll_words    = []
        self._autoscroll_word_idx = 0
        self._autoscroll_tick     = 0

        self._user32 = ctypes.WinDLL("user32", use_last_error=True)

        self._setup_window()
        self._build_ui()
        self._cloak(self.root)
        self._setup_resize_handles()
        self.render_slide()
        self._tick_timer()
        self.root.focus_force()

    # ── WINDOW ───────────────────────────────────────────────────────────────

    def _setup_window(self):
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.93)
        self.root.geometry("700x500+150+100")
        self.root.configure(bg=C["bg"])
        self.root.minsize(MIN_W, MIN_H)

    def _cloak(self, window):
        window.update()
        inner = window.winfo_id()
        hwnd  = ctypes.windll.user32.GetParent(inner) or inner
        self._user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)

    def _minimize(self):
        """Minimize via the overrideredirect toggle trick."""
        self.root.overrideredirect(False)
        self.root.iconify()
        self.root.bind("<Map>", self._on_restore)

    def _on_restore(self, event):
        self.root.overrideredirect(True)
        self.root.unbind("<Map>")
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
            (C["red"],    "✕", self.root.destroy),
            (C["yellow"], "−", self._minimize),
            (C["green"],  "●", lambda: None),
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
        tk.Label(self._live_frame, text="LIVE SYNCING",
                 bg=C["bar"], fg=C["primary"],
                 font=(LABEL, 7, "bold")).pack(side="left", padx=(3, 0))

    # ── BODY ─────────────────────────────────────────────────────────────────

    def _build_body(self):
        body = tk.Frame(self.root, bg=C["bg"])
        body.pack(fill="both", expand=True)
        self._build_sidebar(body)
        self._build_main_column(body)

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

        make_tab("✏", "NOTES", "notes", self._show_notes)
        make_tab("≡", "LOGS",  "logs",  self._toggle_logs_panel)

        hf = tk.Frame(sb, bg=C["bar"], cursor="hand2")
        hf.pack(side="bottom", pady=12)
        tk.Label(hf, text="?", bg=C["bar"], fg=C["on_sv"],
                 font=(LABEL, 12, "bold")).pack()

    def _set_active_tab(self, name):
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

    def render_slide(self):
        # Stop autoscroll
        if self._autoscroll_active:
            self._autoscroll_active = False
            self._auto_btn.config(text="▶  AUTO", bg=C["on_s"], fg=C["bg"])
            self.text.config(state="normal")
            self.text.tag_remove("autoscroll_hl", "1.0", "end")
            self.text.config(state="disabled")

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

    def navigate(self, delta):
        new = self.current + delta
        if 0 <= new < len(self.slides):
            self.current = new
            self.render_slide()

    def navigate_to(self, index):
        if 0 <= index < len(self.slides):
            self.current = index
            self.render_slide()

    # ── FILE LOADING ─────────────────────────────────────────────────────────

    def load_file(self, path=None):
        if path is None:
            path = filedialog.askopenfilename(
                title="Open notes file",
                filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path or not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as f:
            content = f.read().replace('\r\n', '\n').replace('\r', '\n')
        parsed       = [s.strip() for s in re.split(r'\n\s*---\s*\n', content)
                        if s.strip()]
        self.slides  = parsed if parsed else ["# Empty file\n\nNo slides found."]
        self.current = 0
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
        content = content.replace('\r\n', '\n').replace('\r', '\n')
        parsed  = [s.strip() for s in re.split(r'\n\s*---\s*\n', content)
                   if s.strip()]
        if not parsed:
            return
        self.slides  = parsed
        self.current = min(self.current, len(parsed) - 1)
        self.render_slide()

    def start_firebase_sync(self, db_url):
        self._sync_version += 1
        self._sync_mode = "firebase"
        v = self._sync_version
        self.root.after(0, self._show_live_badge)
        threading.Thread(target=self._firebase_loop,
                         args=(db_url.rstrip("/"), v), daemon=True).start()

    def _firebase_loop(self, db_url, version):
        try:
            import requests
        except ImportError:
            return
        url  = f"{db_url}/overlay/content.json"
        last = None
        while self._sync_version == version:
            try:
                r = requests.get(url, timeout=5)
                if r.ok:
                    val = r.json()
                    if isinstance(val, str) and val != last:
                        last = val
                        self.root.after(0, lambda c=val: self._apply_remote_content(c))
            except Exception:
                time.sleep(2)
            time.sleep(0.15)

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

        async def handler(ws):
            async for msg in ws:
                if isinstance(msg, str) and self._sync_version == version:
                    self.root.after(0, lambda c=msg: self._apply_remote_content(c))

        async with websockets.serve(handler, "0.0.0.0", port):
            while self._sync_version == version:
                await asyncio.sleep(0.5)

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

    def _pulse_live(self):
        if not self._sync_mode:
            return
        self._live_state = not self._live_state
        self._live_dot.config(fg=C["primary"] if self._live_state else C["bar"])
        self.root.after(900, self._pulse_live)

    # ── MEETING TIMER ────────────────────────────────────────────────────────

    def _tick_timer(self):
        self._elapsed += 1
        h, m, s = self._elapsed // 3600, (self._elapsed % 3600) // 60, self._elapsed % 60
        self.timer_label.config(text=f"{h:02d}:{m:02d}:{s:02d}")
        self.root.after(1000, self._tick_timer)

    # ── AUTO-SCROLL + WORD HIGHLIGHT ─────────────────────────────────────────

    def _build_autoscroll_words(self):
        self._autoscroll_words    = []
        self._autoscroll_word_idx = 0
        self._autoscroll_tick     = 0
        content = self.text.get("1.0", "end")
        for m in re.finditer(r'\S+', content):
            self._autoscroll_words.append((
                f"1.0 + {m.start()} chars",
                f"1.0 + {m.end()} chars"))

    def _toggle_autoscroll(self):
        self._autoscroll_active = not self._autoscroll_active
        if self._autoscroll_active:
            self._auto_btn.config(text="⏸  STOP", bg=C["primary"], fg=C["bg"])
            self._autoscroll_word_idx = 0
            self._autoscroll_tick     = 0
            self._autoscroll_step()
        else:
            self._auto_btn.config(text="▶  AUTO", bg=C["on_s"], fg=C["bg"])
            self.text.config(state="normal")
            self.text.tag_remove("autoscroll_hl", "1.0", "end")
            self.text.config(state="disabled")

    def _autoscroll_step(self):
        if not self._autoscroll_active:
            return
        self.text.yview_scroll(1, "units")
        self._autoscroll_tick += 1

        if self._autoscroll_tick % 8 == 0:
            words = self._autoscroll_words
            idx   = self._autoscroll_word_idx
            if words and idx < len(words):
                self.text.config(state="normal")
                self.text.tag_remove("autoscroll_hl", "1.0", "end")
                self.text.tag_add("autoscroll_hl", words[idx][0], words[idx][1])
                self.text.config(state="disabled")
                self._autoscroll_word_idx += 1
            elif idx >= len(words):
                self._toggle_autoscroll()
                return

        self.root.after(50, self._autoscroll_step)

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
                if self._sync_mode == "firebase":
                    entry.insert(0, "currently connected")
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
        if not url or url == "currently connected":
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
        elif self._logs_visible:
            self._hide_logs()
        else:
            self.root.destroy()


# ── ENTRY POINT ──────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    app  = OverlayApp(root)

    args = sys.argv[1:]
    if "--firebase" in args:
        app.start_firebase_sync(args[args.index("--firebase") + 1])
    elif "--local" in args:
        port = 8765
        if "--port" in args:
            port = int(args[args.index("--port") + 1])
        app.start_local_ws_server(port)
        import socket
        try:    ip = socket.gethostbyname(socket.gethostname())
        except: ip = "127.0.0.1"
        print(f"Local WebSocket server started.\nOpen the web UI and enter: {ip}:{port}")
    elif args and not args[0].startswith("--"):
        app.load_file(args[0])

    root.mainloop()


if __name__ == "__main__":
    main()
