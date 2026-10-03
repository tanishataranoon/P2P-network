"""
gui.py  (design v3: sharp rendering + per-peer accent color)
--------------------------------------------------------------
Same layout as before (sidebar + chat area), with two fixes:

1. BLUR FIX (Windows only):
   On Windows, Tkinter windows are NOT "DPI aware" by default. If your
   display scaling is set above 100% (very common on laptops, e.g.
   125%/150%), Windows silently renders the window at 100% and then
   stretches the resulting bitmap to fill the screen - which is exactly
   what makes text and colors look blurry/soft. Calling
   SetProcessDpiAwareness() BEFORE any Tk window is created tells
   Windows "let me handle my own scaling", so Tkinter draws everything
   at full, sharp resolution instead of being stretched after the fact.
   This has no effect (and is skipped safely) on macOS/Linux.

2. PER-PEER ACCENT COLOR:
   When you run two instances of this app at once (e.g. to simulate
   Alice and Bob), both used to look identical purple, which made it
   hard to tell the windows apart at a glance. Now, once you click
   "Start Peer", the whole UI re-themes itself using a color picked
   from a small palette based on YOUR port number. Same port -> same
   color every time (predictable), different port -> (almost always)
   a different color, so two peers running side by side are visually
   distinct. Before you start a peer, everything stays the default
   indigo shown in the mockups.

Everything else - the public App(root) interface, the event queue /
thread-safety design - is unchanged from before, so main.py and the
rest of the project need no changes.
"""

import os
import queue
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox

from p2p_node import PeerNode

# --- Windows DPI-awareness fix (must run before any Tk window exists) ---
# This module is imported by main.py BEFORE tk.Tk() is created, so doing
# this here (at import time, not inside a function) is early enough.
if sys.platform == "win32":
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 1 = "system DPI aware"
    except Exception:
        pass  # older Windows without shcore, or any other quirk - just skip it

DOWNLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")

# ---- Base palette (light theme) ---------------------------------------
BG = "#f4f6fb"
SIDEBAR = "#ffffff"
TEXT = "#1f2937"
MUTED = "#6b7280"
BORDER = "#d9dde7"
ERROR = "#dc2626"

DEFAULT_ACCENT = "#4f46e5"  # shown before a peer is started
DEFAULT_ACCENT_DARK = "#4338ca"

# Each running peer picks one of these (accent, accent_dark, name) based
# on its port number, so two peers started on different ports look
# visibly different. Kept distinct from each other and readable on white.
ACCENT_PALETTE = [
    ("#4f46e5", "#4338ca", "Indigo"),
    ("#0d9488", "#0f766e", "Teal"),
    ("#eab308", "#a16207", "Yellow"),
    ("#db2777", "#be185d", "Pink"),
    ("#16a34a", "#15803d", "Green"),
    ("#0891b2", "#0e7490", "Cyan"),
    ("#7c3aed", "#6d28d9", "Violet"),
    ("#e11d48", "#be123c", "Rose"),
]

FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 11, "bold")
FONT_SMALL = ("Segoe UI", 8)


class App:
    """The main application window."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Jogajog")
        self.root.geometry("960x620")
        self.root.minsize(800, 520)
        self.root.configure(bg=BG)

        self.node: PeerNode | None = None
        self.event_queue: queue.Queue = queue.Queue()
        self._peer_ids: list[str] = []

        # --- Per-peer chat surfaces (like a real messenger) ---
        # Instead of one shared log, every connected peer gets its own
        # separate message history. histories[key] is a list of
        # (tag, line) tuples ready to be replayed into the Text widget.
        # The special key "_system" holds messages that aren't about any
        # one peer (peer started/stopped, "connecting...", handshake
        # failures before we even know who's on the other end) and is
        # what's shown before any peer is selected.
        self.histories: dict[str, list[tuple[str, str]]] = {}
        self.current_key: str = "_system"

        # Current accent color (starts as the default; _apply_accent()
        # swaps this out once a peer is started - see module docstring).
        self.accent = DEFAULT_ACCENT
        self.accent_dark = DEFAULT_ACCENT_DARK

        self._build_sidebar()
        self._build_chat_area()

        self.root.after(100, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Small widget helpers
    # ------------------------------------------------------------------

    def _make_entry(self, parent, width: int, default: str = "") -> tk.Entry:
        entry = tk.Entry(
            parent,
            width=width,
            font=FONT,
            fg=TEXT,
            bg="white",
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=self.accent,
            disabledbackground="#eef0f5",
            disabledforeground=MUTED,
        )
        entry.insert(0, default)
        return entry

    def _make_button(
        self, parent, text: str, command, primary: bool = True
    ) -> tk.Button:
        if primary:
            return tk.Button(
                parent,
                text=text,
                command=command,
                font=FONT_BOLD,
                bg=self.accent,
                fg="white",
                activebackground=self.accent_dark,
                activeforeground="white",
                disabledforeground="#c7c9d6",
                relief="flat",
                bd=0,
                padx=12,
                pady=5,
                cursor="hand2",
            )
        return tk.Button(
            parent,
            text=text,
            command=command,
            font=FONT,
            bg="#e5e7ef",
            fg=TEXT,
            activebackground="#d5d8e4",
            activeforeground=TEXT,
            disabledforeground="#a3a6b5",
            relief="flat",
            bd=0,
            padx=12,
            pady=5,
            cursor="hand2",
        )

    def _section_label(self, parent, text: str) -> None:
        tk.Label(parent, text=text.upper(), font=FONT_SMALL, fg=MUTED, bg=SIDEBAR).pack(
            anchor="w", padx=16, pady=(16, 4)
        )

    # ------------------------------------------------------------------
    # Left sidebar
    # ------------------------------------------------------------------

    def _build_sidebar(self) -> None:
        sidebar = tk.Frame(self.root, bg=SIDEBAR, width=300)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        self.app_title = tk.Label(
            sidebar,
            text="P2P Network",
            font=("Segoe UI", 15, "bold"),
            fg=self.accent,
            bg=SIDEBAR,
        )
        self.app_title.pack(anchor="w", padx=16, pady=(16, 0))

        self._section_label(sidebar, "My peer")
        form = tk.Frame(sidebar, bg=SIDEBAR)
        form.pack(fill="x", padx=16)

        tk.Label(form, text="Name", font=FONT, fg=TEXT, bg=SIDEBAR).grid(
            row=0, column=0, sticky="w"
        )
        self.name_entry = self._make_entry(form, 14, "Tan")
        self.name_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=2, ipady=3)

        tk.Label(form, text="Port", font=FONT, fg=TEXT, bg=SIDEBAR).grid(
            row=1, column=0, sticky="w"
        )
        self.port_entry = self._make_entry(form, 14, "6000")
        self.port_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=2, ipady=3)
        form.columnconfigure(1, weight=1)

        buttons = tk.Frame(sidebar, bg=SIDEBAR)
        buttons.pack(fill="x", padx=16, pady=(8, 0))
        self.start_button = self._make_button(buttons, "Start Peer", self._start_peer)
        self.start_button.pack(side="left")
        self.stop_button = self._make_button(
            buttons, "Stop", self._stop_peer, primary=False
        )
        self.stop_button.pack(side="left", padx=(8, 0))
        self.stop_button.configure(state="disabled")

        self.status_label = tk.Label(
            sidebar,
            text="\u25cf Offline",
            font=FONT_SMALL,
            fg=MUTED,
            bg=SIDEBAR,
            anchor="w",
            justify="left",
        )
        self.status_label.pack(fill="x", padx=16, pady=(8, 0))

        self._section_label(sidebar, "Connect to a peer")
        cform = tk.Frame(sidebar, bg=SIDEBAR)
        cform.pack(fill="x", padx=16)

        tk.Label(cform, text="IP", font=FONT, fg=TEXT, bg=SIDEBAR).grid(
            row=0, column=0, sticky="w"
        )
        self.ip_entry = self._make_entry(cform, 14, "127.0.0.1")
        self.ip_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=2, ipady=3)

        tk.Label(cform, text="Port", font=FONT, fg=TEXT, bg=SIDEBAR).grid(
            row=1, column=0, sticky="w"
        )
        self.connect_port_entry = self._make_entry(cform, 14, "6001")
        self.connect_port_entry.grid(
            row=1, column=1, sticky="ew", padx=(8, 0), pady=2, ipady=3
        )
        cform.columnconfigure(1, weight=1)

        self.connect_button = self._make_button(
            sidebar, "Connect", self._connect_to_peer
        )
        self.connect_button.pack(anchor="w", padx=16, pady=(8, 0))

        self._section_label(sidebar, "Connected peers")
        list_frame = tk.Frame(sidebar, bg=BORDER)
        list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        self.peer_listbox = tk.Listbox(
            list_frame,
            font=FONT,
            fg=TEXT,
            bg="white",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            activestyle="none",
            selectbackground="#e0e7ff",
            selectforeground=self.accent,
            exportselection=False,
        )
        self.peer_listbox.pack(fill="both", expand=True, padx=1, pady=1)
        self.peer_listbox.bind(
            "<<ListboxSelect>>", lambda event: self._update_chat_title()
        )

    # ------------------------------------------------------------------
    # Right side: chat header, message view, input bar
    # ------------------------------------------------------------------

    def _build_chat_area(self) -> None:
        area = tk.Frame(self.root, bg=BG)
        area.pack(side="left", fill="both", expand=True)

        self.chat_title = tk.Label(
            area,
            text="Select a peer to chat",
            font=FONT_TITLE,
            fg=TEXT,
            bg=BG,
            anchor="w",
        )
        self.chat_title.pack(fill="x", padx=20, pady=(16, 8))

        view = tk.Frame(area, bg=BG)
        view.pack(fill="both", expand=True, padx=20)

        scrollbar = tk.Scrollbar(view)
        scrollbar.pack(side="right", fill="y")

        self.log_text = tk.Text(
            view,
            bg="white",
            fg=TEXT,
            relief="flat",
            wrap="word",
            state="disabled",
            font=FONT,
            highlightthickness=1,
            highlightbackground=BORDER,
            padx=12,
            pady=8,
            yscrollcommand=scrollbar.set,
            cursor="arrow",
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=self.log_text.yview)

        self.log_text.tag_configure(
            "sent_meta",
            justify="right",
            foreground=MUTED,
            font=FONT_SMALL,
            spacing1=8,
            rmargin=6,
        )
        self.log_text.tag_configure(
            "sent",
            justify="right",
            foreground=self.accent,
            font=FONT,
            lmargin1=120,
            lmargin2=120,
            rmargin=6,
        )
        self.log_text.tag_configure(
            "recv_meta",
            justify="left",
            foreground=MUTED,
            font=FONT_SMALL,
            spacing1=8,
            lmargin1=6,
            lmargin2=6,
        )
        self.log_text.tag_configure(
            "recv",
            justify="left",
            foreground=TEXT,
            font=FONT,
            lmargin1=6,
            lmargin2=6,
            rmargin=120,
        )
        self.log_text.tag_configure(
            "system",
            justify="center",
            foreground=MUTED,
            font=("Segoe UI", 9, "italic"),
            spacing1=6,
        )
        self.log_text.tag_configure(
            "error",
            justify="center",
            foreground=ERROR,
            font=("Segoe UI", 9, "bold"),
            spacing1=6,
        )

        bar = tk.Frame(area, bg=BG)
        bar.pack(fill="x", padx=20, pady=16)

        self.message_entry = self._make_entry(bar, 10)
        self.message_entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.message_entry.bind("<Return>", lambda event: self._send_text())

        self.send_button = self._make_button(bar, "Send", self._send_text)
        self.send_button.pack(side="left", padx=(8, 0))
        self._make_button(
            bar, "Send File...", self._choose_and_send_file, primary=False
        ).pack(side="left", padx=(8, 0))

        self._add_system("Start your peer, then connect to another one to begin.")

    # ------------------------------------------------------------------
    # Per-peer accent color (the new bit)
    # ------------------------------------------------------------------

    def _apply_accent(self, port: int) -> None:
        """
        Picks a color for THIS peer based on its port number and re-themes
        the parts of the UI that carry the app's "brand" color, so two
        peer windows running side by side (different ports) look visibly
        different instead of both being the same default purple.
        """
        accent, accent_dark, _color_name = ACCENT_PALETTE[port % len(ACCENT_PALETTE)]
        self.accent, self.accent_dark = accent, accent_dark

        self.app_title.configure(fg=accent)
        self.status_label.configure(fg=accent)

        # Every accent-colored button needs updating here, including
        # Start Peer itself - it was built with the default indigo
        # before we knew this peer's port, so without this line it
        # would stay purple even after the rest of the UI re-themed.
        self.start_button.configure(bg=accent, activebackground=accent_dark)
        self.send_button.configure(bg=accent, activebackground=accent_dark)
        self.connect_button.configure(bg=accent, activebackground=accent_dark)

        self.message_entry.configure(highlightcolor=accent)
        self.name_entry.configure(highlightcolor=accent)
        self.port_entry.configure(highlightcolor=accent)
        self.ip_entry.configure(highlightcolor=accent)
        self.connect_port_entry.configure(highlightcolor=accent)

        self.peer_listbox.configure(selectforeground=accent)
        self.log_text.tag_configure("sent", foreground=accent)

        self._set_titlebar_color(accent)

    def _set_titlebar_color(self, hex_color: str) -> None:
        """
        Best-effort attempt to recolor the OS window title bar itself
        (not just the app's own header) to match the peer's accent.

        Tkinter has no cross-platform API for this - the title bar is
        drawn by the operating system, not by Tk. On Windows 11 there is
        a DWM (Desktop Window Manager) call that lets an app request a
        caption color, so we use that via ctypes. On anything else
        (older Windows, macOS, Linux) this silently does nothing and the
        title bar just stays whatever the OS default is - the rest of
        the theming (title text, buttons, etc.) still changes normally.
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes

            self.root.update_idletasks()  # make sure the window handle actually exists
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())

            r = int(hex_color[1:3], 16)
            g = int(hex_color[3:5], 16)
            b = int(hex_color[5:7], 16)
            colorref = r | (g << 8) | (b << 16)  # Windows COLORREF is 0x00BBGGRR

            DWMWA_CAPTION_COLOR = 35
            DWMWA_TEXT_COLOR = 36
            WHITE_TEXT = 0x00FFFFFF

            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_CAPTION_COLOR,
                ctypes.byref(ctypes.c_int(colorref)),
                ctypes.sizeof(ctypes.c_int),
            )
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_TEXT_COLOR,
                ctypes.byref(ctypes.c_int(WHITE_TEXT)),
                ctypes.sizeof(ctypes.c_int),
            )
        except Exception:
            pass  # unsupported Windows version, or any other quirk - just skip it

    # ------------------------------------------------------------------
    # Writing into the message view (main thread only)
    # ------------------------------------------------------------------
    #
    # Every line is first recorded into the relevant peer's history
    # (self.histories[key]), and only actually drawn into the Text
    # widget if that peer's conversation is the one currently on screen.
    # This is what makes each connection its own surface: switching the
    # listbox selection just re-renders a different history list,
    # exactly like opening a different conversation in a messenger app.

    def _append(self, text: str, tag: str) -> None:
        """Draw one line into the Text widget. Assumes it's already on screen."""
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _record(self, key: str, tag: str, text: str) -> None:
        """Save one line into `key`'s history, and draw it if that chat is open."""
        self.histories.setdefault(key, []).append((tag, text))
        if key == self.current_key:
            self._append(text, tag)

    def _render_current_chat(self) -> None:
        """Wipe the Text widget and replay the currently-selected peer's history."""
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        for tag, text in self.histories.get(self.current_key, []):
            self._append(text, tag)

    def _add_chat(self, who: str, text: str, sent: bool, key: str) -> None:
        stamp = time.strftime("%H:%M")
        if sent:
            self._record(key, "sent_meta", f"{who}  {stamp}")
            self._record(key, "sent", text)
        else:
            self._record(key, "recv_meta", f"{who}  {stamp}")
            self._record(key, "recv", text)

    def _add_system(self, text: str, key: str = "_system") -> None:
        self._record(key, "system", text)

    def _add_error(self, text: str, key: str = "_system") -> None:
        self._record(key, "error", f"[ERROR] {text}")

    # ------------------------------------------------------------------
    # Button handlers (run on the main thread)
    # ------------------------------------------------------------------

    def _start_peer(self) -> None:
        name = self.name_entry.get().strip()
        port_text = self.port_entry.get().strip()

        if not name:
            messagebox.showerror("Invalid input", "Please enter a peer name.")
            return
        if not port_text.isdigit() or not (0 < int(port_text) < 65536):
            messagebox.showerror(
                "Invalid input", "Please enter a valid port number (1-65535)."
            )
            return

        port = int(port_text)

        self.node = PeerNode(
            name=name,
            port=port,
            downloads_dir=DOWNLOADS_DIR,
            on_event=lambda event_type, **data: self.event_queue.put(
                (event_type, data)
            ),
        )

        try:
            self.node.start()
        except OSError as exc:
            messagebox.showerror(
                "Could not start peer", f"Port {port} may already be in use.\n\n{exc}"
            )
            self.node = None
            return

        self._apply_accent(port)  # re-theme the UI based on this peer's port

        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.name_entry.configure(state="disabled")
        self.port_entry.configure(state="disabled")

    def _stop_peer(self) -> None:
        if self.node:
            self.node.stop()
            self.node = None

        self.peer_listbox.delete(0, "end")
        self._peer_ids.clear()
        self._update_chat_title()

        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.name_entry.configure(state="normal")
        self.port_entry.configure(state="normal")
        self.status_label.configure(text="\u25cf Offline", fg=MUTED)
        self._add_system("Peer stopped")
        # Note: we intentionally leave the accent color as-is after
        # stopping, so the log history you're still looking at doesn't
        # visually jump back to purple underneath you.

    def _connect_to_peer(self) -> None:
        if not self._require_started():
            return

        ip = self.ip_entry.get().strip()
        port_text = self.connect_port_entry.get().strip()

        if not ip:
            messagebox.showerror("Invalid input", "Please enter an IP address.")
            return
        if not port_text.isdigit() or not (0 < int(port_text) < 65536):
            messagebox.showerror(
                "Invalid input", "Please enter a valid port number (1-65535)."
            )
            return

        self.node.connect_to_peer(ip, int(port_text))
        self._add_system(f"Connecting to {ip}:{port_text} ...")

    def _selected_peer_id(self) -> str | None:
        selection = self.peer_listbox.curselection()
        if not selection:
            return None
        return self._peer_ids[selection[0]]

    def _update_chat_title(self) -> None:
        """
        Called whenever the peer selection changes. This is the switch
        that makes each connection its own surface: it points
        current_key at the selected peer (or back at the shared "_system"
        feed when nothing's selected) and re-renders the Text widget
        from that peer's own history.
        """
        peer_id = self._selected_peer_id()
        if peer_id is None or self.node is None:
            self.current_key = "_system"
            self.chat_title.configure(text="Select a peer to chat")
        else:
            self.current_key = peer_id
            self.chat_title.configure(text=f"Chatting with {self._peer_name(peer_id)}")
        self._render_current_chat()

    def _send_text(self) -> None:
        if not self._require_started():
            return

        peer_id = self._selected_peer_id()
        if peer_id is None:
            messagebox.showwarning(
                "No peer selected", "Select a connected peer from the list first."
            )
            return

        text = self.message_entry.get().strip()
        if not text:
            return

        self.node.send_text(peer_id, text)
        # "who" is just "You" (not "You -> Bob") because this message is
        # already being drawn inside Bob's own chat surface - the header
        # above already says who you're talking to.
        self._add_chat("You", text, sent=True, key=peer_id)
        self.message_entry.delete(0, "end")

    def _choose_and_send_file(self) -> None:
        if not self._require_started():
            return

        peer_id = self._selected_peer_id()
        if peer_id is None:
            messagebox.showwarning(
                "No peer selected", "Select a connected peer from the list first."
            )
            return

        filepath = filedialog.askopenfilename(title="Choose a file to send")
        if not filepath:
            return

        self._add_system(f"Sending {os.path.basename(filepath)} ...", key=peer_id)
        self.node.send_file(peer_id, filepath)

    def _require_started(self) -> bool:
        if self.node is None:
            messagebox.showwarning("Peer not started", "Start your peer first.")
            return False
        return True

    def _peer_name(self, peer_id: str) -> str:
        if self.node:
            for peer in self.node.get_connected_peers():
                if peer["peer_id"] == peer_id:
                    return peer["name"]
        return peer_id

    # ------------------------------------------------------------------
    # Event queue polling (keeps the GUI thread-safe)
    # ------------------------------------------------------------------

    def _poll_events(self) -> None:
        try:
            while True:
                event_type, data = self.event_queue.get_nowait()
                self._handle_event(event_type, data)
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._poll_events)

    def _handle_event(self, event_type: str, data: dict) -> None:
        if event_type == "started":
            self.status_label.configure(
                text=f"\u25cf Online: {data['name']} [{data['peer_id']}] on port {data['port']}",
            )
            self._add_system(
                f"Peer started: {data['name']} [{data['peer_id']}] on port {data['port']}"
            )

        elif event_type == "peer_connected":
            peer_id = data["peer_id"]
            self._peer_ids.append(peer_id)
            self.peer_listbox.insert(
                "end", f"{data['name']}  [{peer_id}]   {data['ip']}:{data['port']}"
            )
            # This becomes the first line of that peer's own chat surface.
            self._add_system(
                f"Connected to {data['name']} ({data['ip']}:{data['port']})",
                key=peer_id,
            )

        elif event_type == "peer_disconnected":
            # The peer is leaving the connected-peers list, so there's no
            # surface left to show this on - it goes to the general feed.
            if data["peer_id"] in self._peer_ids:
                idx = self._peer_ids.index(data["peer_id"])
                self._peer_ids.pop(idx)
                self.peer_listbox.delete(idx)
                self._update_chat_title()
            self._add_system(f"{data['name']} disconnected")

        elif event_type == "text":
            # sender_id IS that peer's own peer_id, so it doubles as the
            # key for their chat surface - route it straight there.
            self._add_chat(
                data["sender_name"], data["text"], sent=False, key=data["sender_id"]
            )

        elif event_type == "file_received":
            self._add_system(
                f"File received: {data['filename']} ({data['filesize']} bytes) saved to downloads/",
                key=data.get("peer_id", "_system"),
            )

        elif event_type == "file_sent":
            self._add_system(
                f"File sent: {data['filename']} ({data['filesize']} bytes)",
                key=data.get("peer_id", "_system"),
            )

        elif event_type == "error":
            # Errors tied to a specific connection (failed send, failed
            # file transfer) land in that peer's chat; anything that
            # happened before we knew who we were talking to (refused
            # connection, bad handshake) falls back to the general feed.
            self._add_error(data["message"], key=data.get("peer_id", "_system"))

        elif event_type == "stopped":
            pass

    # ------------------------------------------------------------------
    # Window close
    # ------------------------------------------------------------------

    def _on_close(self) -> None:
        if self.node:
            self.node.stop()
        self.root.destroy()
