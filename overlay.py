import tkinter as tk
from tkinter import filedialog
import ctypes
import re
import sys
import os
import threading
import json
import time
import asyncio

WDA_EXCLUDEFROMCAPTURE = 0x00000011

C = {
    "bg":           "#2b2d42",
    "bar":          "#1a1b2e",
    "content":      "#252737",
    "h1":           "#e0c3fc",
    "h2":           "#72efdd",
    "body":         "#d9d9e3",
    "accent":       "#f77f00",
    "dim":          "#6c6f93",
    "border":       "#3d3f5c",
}

MIN_W, MIN_H = 300, 200


class OverlayApp:
    def __init__(self, root):
        self.root = root
        self.slides = ["# No file loaded\n\nClick  ⋮ → Load File  to open a .txt file."]
        self.current = 0
        self.font_size = 11
        self._drag_x = self._drag_y = 0
        self._resize_data = None
        self._controls_win = None
        self._sync_mode = None
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)

        self._setup_window()
        self._build_ui()
        self._configure_tags()
        self._cloak(self.root)
        self._setup_resize_handles()
        self.render_slide()
        self.root.focus_force()

    # ── WINDOW ───────────────────────────────────────────────────────────────

    def _setup_window(self):
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.92)
        self.root.geometry("560x380+200+150")
        self.root.configure(bg=C["bg"])
        self.root.minsize(MIN_W, MIN_H)

    def _cloak(self, window):
        # window.update() flushes tkinter's work queue so the Win32 HWND exists.
        # winfo_id() returns the inner child HWND; GetParent() gives the real
        # top-level wrapper that SetWindowDisplayAffinity requires.
        window.update()
        inner = window.winfo_id()
        hwnd = ctypes.windll.user32.GetParent(inner) or inner
        self._user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)

    # ── UI CONSTRUCTION ──────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_topbar()
        self._build_content()
        self._build_bottombar()
        self._bind_keys()

    def _build_topbar(self):
        bar = tk.Frame(self.root, bg=C["bar"], height=34)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        _btn = dict(bg=C["bar"], bd=0, font=("Segoe UI", 11), cursor="hand2",
                    activebackground=C["bar"], relief="flat", padx=6)

        self.prev_btn = tk.Button(bar, text="◀", fg=C["dim"],
                                  activeforeground=C["accent"],
                                  command=lambda: self.navigate(-1), **_btn)
        self.prev_btn.pack(side="left", padx=(8, 0), pady=4)

        self.counter = tk.Label(bar, text="1/1", bg=C["bar"],
                                fg=C["dim"], font=("Segoe UI", 9))
        self.counter.pack(side="left", padx=6)

        self.next_btn = tk.Button(bar, text="▶", fg=C["dim"],
                                  activeforeground=C["accent"],
                                  command=lambda: self.navigate(1), **_btn)
        self.next_btn.pack(side="left")

        # Sync indicator — shows "⬤ live" when Firebase/WS connected
        self.sync_label = tk.Label(bar, text="", bg=C["bar"],
                                   fg=C["dim"], font=("Segoe UI", 8))
        self.sync_label.pack(side="left", padx=(10, 0))

        self.menu_btn = tk.Button(bar, text="⋮", fg=C["dim"],
                                  activeforeground=C["accent"],
                                  command=self.toggle_controls, **_btn)
        self.menu_btn.pack(side="right", padx=(0, 8))

        for widget in (bar, self.counter):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._do_drag)

    def _build_content(self):
        frame = tk.Frame(self.root, bg=C["content"])
        frame.pack(fill="both", expand=True)

        self.text = tk.Text(
            frame,
            bg=C["content"], fg=C["body"],
            relief="flat", bd=0,
            state="disabled",
            wrap="word",
            cursor="arrow",
            font=("Segoe UI", 11),
            padx=18, pady=14,
            spacing2=3,
            selectbackground=C["border"],
            insertwidth=0,
        )
        self.text.pack(fill="both", expand=True)

    def _build_bottombar(self):
        bottom = tk.Frame(self.root, bg=C["bar"], height=22)
        bottom.pack(fill="x", side="bottom")
        bottom.pack_propagate(False)

        self.dots_frame = tk.Frame(bottom, bg=C["bar"])
        self.dots_frame.pack(side="left", padx=10, pady=4)

    def _bind_keys(self):
        self.root.bind("<Left>",   lambda e: self.navigate(-1))
        self.root.bind("<Right>",  lambda e: self.navigate(1))
        self.root.bind("<Escape>", self._handle_escape)

    # ── RESIZE HANDLES ───────────────────────────────────────────────────────
    #
    # 8 invisible Frame widgets placed with relative coordinates (relx/rely)
    # so they automatically track window size changes.
    # Edges first (lower z-order), corners on top.

    def _setup_resize_handles(self):
        CORNER = 14
        EDGE   = 6

        specs = [
            ("size_ns",    dict(relx=0.5, rely=0,   anchor="n",  relwidth=1.0, height=EDGE,   width=0),  "n"),
            ("size_ns",    dict(relx=0.5, rely=1.0, anchor="s",  relwidth=1.0, height=EDGE,   width=0),  "s"),
            ("size_we",    dict(relx=0,   rely=0.5, anchor="w",  width=EDGE,   relheight=1.0, height=0), "w"),
            ("size_we",    dict(relx=1.0, rely=0.5, anchor="e",  width=EDGE,   relheight=1.0, height=0), "e"),
            ("size_nw_se", dict(relx=0,   rely=0,   anchor="nw", width=CORNER, height=CORNER),           "nw"),
            ("size_ne_sw", dict(relx=1.0, rely=0,   anchor="ne", width=CORNER, height=CORNER),           "ne"),
            ("size_ne_sw", dict(relx=0,   rely=1.0, anchor="sw", width=CORNER, height=CORNER),           "sw"),
            ("size_nw_se", dict(relx=1.0, rely=1.0, anchor="se", width=CORNER, height=CORNER),           "se"),
        ]

        for cursor, place_kw, direction in specs:
            f = tk.Frame(self.root, cursor=cursor, bg=C["bg"])
            f.place(**place_kw)
            f.bind("<ButtonPress-1>", lambda e, d=direction: self._start_resize(e, d))
            f.bind("<B1-Motion>",     lambda e, d=direction: self._do_resize(e, d))

    def _start_resize(self, event, direction):
        self._resize_data = {
            "start_x": event.x_root,
            "start_y": event.y_root,
            "orig_x":  self.root.winfo_x(),
            "orig_y":  self.root.winfo_y(),
            "orig_w":  self.root.winfo_width(),
            "orig_h":  self.root.winfo_height(),
        }

    def _do_resize(self, event, direction):
        if not self._resize_data:
            return
        d = self._resize_data
        dx = event.x_root - d["start_x"]
        dy = event.y_root - d["start_y"]
        x, y = d["orig_x"], d["orig_y"]
        w, h = d["orig_w"], d["orig_h"]

        if direction == "se":
            w = max(MIN_W, w + dx);  h = max(MIN_H, h + dy)
        elif direction == "sw":
            nw = max(MIN_W, w - dx); x += w - nw; w = nw
            h  = max(MIN_H, h + dy)
        elif direction == "ne":
            w  = max(MIN_W, w + dx)
            nh = max(MIN_H, h - dy); y += h - nh; h = nh
        elif direction == "nw":
            nw = max(MIN_W, w - dx); x += w - nw; w = nw
            nh = max(MIN_H, h - dy); y += h - nh; h = nh
        elif direction == "e":
            w = max(MIN_W, w + dx)
        elif direction == "w":
            nw = max(MIN_W, w - dx); x += w - nw; w = nw
        elif direction == "s":
            h = max(MIN_H, h + dy)
        elif direction == "n":
            nh = max(MIN_H, h - dy); y += h - nh; h = nh

        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ── TEXT TAGS ────────────────────────────────────────────────────────────

    def _configure_tags(self):
        fs = self.font_size
        self.text.tag_configure("h1",     font=("Segoe UI", fs + 6, "bold"),
                                           foreground=C["h1"], spacing1=10, spacing3=5)
        self.text.tag_configure("h2",     font=("Segoe UI", fs + 2, "bold"),
                                           foreground=C["h2"], spacing1=6, spacing3=3)
        self.text.tag_configure("body",   font=("Segoe UI", fs),
                                           foreground=C["body"], spacing1=1)
        self.text.tag_configure("bold",   font=("Segoe UI", fs, "bold"),
                                           foreground=C["body"])
        self.text.tag_configure("bullet", font=("Segoe UI", fs),
                                           foreground=C["body"],
                                           lmargin1=26, lmargin2=26, spacing1=2)
        self.text.tag_configure("dim",    font=("Segoe UI", fs - 1, "italic"),
                                           foreground=C["dim"])

    def _insert_line(self, line, base_tag):
        parts = re.split(r'\*\*', line)
        for i, part in enumerate(parts):
            tag = "bold" if i % 2 == 1 else base_tag
            if part:
                self.text.insert("end", part, tag)
        self.text.insert("end", "\n")

    # ── SLIDE RENDERING ──────────────────────────────────────────────────────

    def render_slide(self):
        self.text.config(state="normal")
        self.text.delete("1.0", "end")

        slide = self.slides[self.current]
        blank_pending = False

        for raw_line in slide.split('\n'):
            line = raw_line.strip()

            if line == '':
                blank_pending = True
                continue

            if blank_pending:
                self.text.insert("end", "\n")
                blank_pending = False

            if line.startswith('## '):
                self._insert_line(line[3:], "h2")
            elif line.startswith('# '):
                self._insert_line(line[2:], "h1")
            elif line.startswith(('- ', '* ')):
                self._insert_line('  •  ' + line[2:], "bullet")
            else:
                self._insert_line(line, "body")

        self.text.config(state="disabled")
        self._update_nav()

    def _update_nav(self):
        total = len(self.slides)
        self.counter.config(text=f"{self.current + 1} / {total}")
        self.prev_btn.config(fg=C["accent"] if self.current > 0         else C["dim"])
        self.next_btn.config(fg=C["accent"] if self.current < total - 1 else C["dim"])
        self._update_dots()

    def _update_dots(self):
        for w in self.dots_frame.winfo_children():
            w.destroy()
        for i in range(len(self.slides)):
            color = C["accent"] if i == self.current else C["border"]
            tk.Label(self.dots_frame, text="●", fg=color,
                     bg=C["bar"], font=("Segoe UI", 7)).pack(side="left", padx=1)

    def navigate(self, delta):
        new = self.current + delta
        if 0 <= new < len(self.slides):
            self.current = new
            self.render_slide()

    # ── FILE LOADING ─────────────────────────────────────────────────────────

    def load_file(self, path=None):
        if path is None:
            path = filedialog.askopenfilename(
                title="Open notes file",
                filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
            )
        if not path or not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as f:
            content = f.read().replace('\r\n', '\n').replace('\r', '\n')
        parsed = [s.strip() for s in re.split(r'\n\s*---\s*\n', content) if s.strip()]
        self.slides = parsed if parsed else ["# Empty file\n\nNo slides found."]
        self.current = 0
        self.render_slide()
        self.root.focus_force()

    # ── REMOTE SYNC — SHARED ─────────────────────────────────────────────────

    def _apply_remote_content(self, content):
        """Called on the main tkinter thread via root.after(0, ...).
        Parses incoming text and re-renders slides without resetting current index
        unless the slide count changed."""
        content = content.replace('\r\n', '\n').replace('\r', '\n')
        parsed = [s.strip() for s in re.split(r'\n\s*---\s*\n', content) if s.strip()]
        if not parsed:
            return
        self.slides = parsed
        self.current = min(self.current, len(parsed) - 1)
        self.render_slide()

    # ── REMOTE SYNC — FIREBASE RTDB (SSE) ────────────────────────────────────
    #
    # Firebase exposes its Realtime DB as a standard SSE endpoint.
    # We open a persistent HTTP connection; Firebase pushes a "put" event
    # every time the value at the path changes. No polling, no SDK needed.
    #
    # The stream runs in a daemon thread so it dies with the app automatically.
    # On any network error it waits 3s then reconnects — handles WiFi drops,
    # sleep/wake cycles, etc.

    def start_firebase_sync(self, db_url):
        self._sync_mode = "firebase"
        self.root.after(0, lambda: self.sync_label.config(
            text="⬤ firebase", fg="#72efdd"))
        t = threading.Thread(
            target=self._firebase_stream_loop,
            args=(db_url.rstrip("/"),),
            daemon=True,
        )
        t.start()

    def _firebase_stream_loop(self, db_url):
        try:
            import requests
        except ImportError:
            self.root.after(0, lambda: self.sync_label.config(
                text="✗ install requests", fg="#ff6b6b"))
            return

        url = f"{db_url}/overlay/content.json"
        last = None
        while True:
            try:
                r = requests.get(url, timeout=5)
                if r.ok:
                    val = r.json()
                    if isinstance(val, str) and val != last:
                        last = val
                        self.root.after(0, lambda c=val: self._apply_remote_content(c))
                    if not r.ok:
                        raise Exception(r.status_code)
            except Exception:
                self.root.after(0, lambda: self.sync_label.config(
                    text="⬤ reconnecting…", fg=C["dim"]))
                time.sleep(2)
                self.root.after(0, lambda: self.sync_label.config(
                    text="⬤ firebase", fg="#72efdd"))
            time.sleep(0.15)

    # ── REMOTE SYNC — LOCAL WEBSOCKET ────────────────────────────────────────
    #
    # Runs an asyncio WebSocket server inside a daemon thread.
    # The phone/laptop web UI connects to ws://YOUR-IP:8765 and sends the full
    # notes content each time the user pauses typing (debounced 300ms).
    # asyncio.run() creates its own event loop inside the thread — the rest
    # of the app stays fully synchronous.

    def start_local_ws_server(self, port=8765):
        self._sync_mode = "local"
        self.root.after(0, lambda: self.sync_label.config(
            text=f"⬤ ws:{port}", fg="#f77f00"))
        t = threading.Thread(
            target=self._run_ws_server,
            args=(port,),
            daemon=True,
        )
        t.start()

    def _run_ws_server(self, port):
        asyncio.run(self._ws_serve(port))

    async def _ws_serve(self, port):
        try:
            import websockets
        except ImportError:
            self.root.after(0, lambda: self.sync_label.config(
                text="✗ install websockets", fg="#ff6b6b"))
            return

        async def handler(ws):
            async for message in ws:
                if isinstance(message, str):
                    self.root.after(0, lambda c=message: self._apply_remote_content(c))

        async with websockets.serve(handler, "0.0.0.0", port):
            await asyncio.Future()   # run forever until the process exits

    # ── CONTROLS DROPDOWN ────────────────────────────────────────────────────

    def toggle_controls(self):
        if self._controls_win and self._controls_win.winfo_exists():
            self._controls_win.destroy()
            self._controls_win = None
            return

        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=C["bar"])

        self._populate_controls(win)

        self.root.update_idletasks()
        bx = self.menu_btn.winfo_rootx()
        by = self.menu_btn.winfo_rooty() + self.menu_btn.winfo_height() + 2
        win.geometry(f"200x120+{bx - 148}+{by}")

        self._cloak(win)

        def _on_focus_out(e):
            win.after(100, lambda: win.destroy() if win.winfo_exists() else None)

        win.bind("<FocusOut>", _on_focus_out)
        win.focus_set()
        self._controls_win = win

    def _populate_controls(self, win):
        _lbl = dict(bg=C["bar"], fg=C["dim"], font=("Segoe UI", 8))
        _btn = dict(bg=C["border"], fg=C["body"], bd=0, font=("Segoe UI", 9, "bold"),
                    width=3, cursor="hand2", activebackground=C["accent"],
                    activeforeground="white", relief="flat")

        row = tk.Frame(win, bg=C["bar"])
        row.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(row, text="Font size", **_lbl).pack(side="left")
        tk.Button(row, text="A+", command=lambda: self._change_font(1),  **_btn).pack(side="right", padx=(2, 0))
        tk.Button(row, text="A−", command=lambda: self._change_font(-1), **_btn).pack(side="right")

        tk.Frame(win, bg=C["border"], height=1).pack(fill="x", padx=10)

        row2 = tk.Frame(win, bg=C["bar"])
        row2.pack(fill="x", padx=10, pady=4)
        tk.Label(row2, text="Opacity", **_lbl).pack(side="left")
        scale = tk.Scale(
            row2, from_=0.3, to=1.0, resolution=0.05,
            orient="horizontal", length=95, showvalue=False,
            bg=C["bar"], troughcolor=C["border"],
            highlightthickness=0, bd=0, sliderrelief="flat",
            command=lambda v: self.root.attributes("-alpha", float(v)),
        )
        scale.set(self.root.attributes("-alpha"))
        scale.pack(side="right")

        tk.Frame(win, bg=C["border"], height=1).pack(fill="x", padx=10)

        tk.Button(
            win, text="📂  Load File",
            command=lambda: [win.destroy(), self.load_file()],
            bg=C["bar"], fg=C["body"], bd=0, font=("Segoe UI", 9),
            activebackground=C["border"], relief="flat", anchor="w",
            cursor="hand2",
        ).pack(fill="x", padx=10, pady=(4, 8))

    def _change_font(self, delta):
        self.font_size = max(8, min(24, self.font_size + delta))
        self._configure_tags()
        self.render_slide()

    # ── DRAG ─────────────────────────────────────────────────────────────────

    def _start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _do_drag(self, event):
        x = self.root.winfo_x() + (event.x - self._drag_x)
        y = self.root.winfo_y() + (event.y - self._drag_y)
        self.root.geometry(f"+{x}+{y}")

    # ── MISC ─────────────────────────────────────────────────────────────────

    def _handle_escape(self, event):
        if self._controls_win and self._controls_win.winfo_exists():
            self._controls_win.destroy()
            self._controls_win = None
        else:
            self.root.destroy()


# ── ENTRY POINT ──────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    app = OverlayApp(root)

    args = sys.argv[1:]

    if "--firebase" in args:
        db_url = args[args.index("--firebase") + 1]
        app.start_firebase_sync(db_url)
    elif "--local" in args:
        port = 8765
        if "--port" in args:
            port = int(args[args.index("--port") + 1])
        app.start_local_ws_server(port)
        # Print the local IP so the user knows what to type in the web UI
        import socket
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            ip = "127.0.0.1"
        print(f"Local WebSocket server started.")
        print(f"Open the web UI and enter: {ip}:{port}")
    elif args and not args[0].startswith("--"):
        app.load_file(args[0])

    root.mainloop()


if __name__ == "__main__":
    main()
