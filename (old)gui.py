"""
gui.py  (design v2: light theme, sidebar + chat view)
-----------------------------------------------------
This file contains ONLY the user interface. It knows nothing about how
sockets, threads, or TCP work - it just calls methods on a PeerNode
(start, connect_to_peer, send_text, send_file) and shows whatever events
come back from it.

NEW LAYOUT:
    +----------------------+-------------------------------------+
    |  SIDEBAR             |  CHAT AREA                          |
    |   - My Peer          |   header: who you are chatting with |
    |   - Connect          |   message view (bubbles-style)      |
    |   - Connected peers  |   [ type message ] [Send] [File]    |
    +----------------------+-------------------------------------+

The public interface is identical to the old gui.py (class App(root)),
so main.py and the rest of the project need NO changes.

THREAD SAFETY (unchanged, same idea as before):
PeerNode calls on_event(...) from background threads. Tkinter widgets may
only be touched from the main thread, so the callback just puts the event
in a queue.Queue, and the GUI drains that queue every 100 ms using
root.after(). Only then are widgets updated.
"""

import os
import queue
import time
import tkinter as tk
from tkinter import filedialog, messagebox

from p2p_node import PeerNode

# Received files are saved next to this file, in the "downloads" folder.
DOWNLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")

# ---- Color palette (light theme) ------------------------------------
BG = "#f4f6fb"        # main window background
SIDEBAR = "#ffffff"   # sidebar background
ACCENT = "#4f46e5"    # buttons / highlights (indigo)
ACCENT_DARK = "#4338ca"
TEXT = "#1f2937"      # normal text
MUTED = "#6b7280"     # secondary text
BORDER = "#d9dde7"    # thin borders around inputs
ERROR = "#dc2626"
ONLINE = "#16a34a"

FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 11, "bold")
FONT_SMALL = ("Segoe UI", 8)


class App:
    """The main application window."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("UAP P2P Network")
        self.root.geometry("960x620")
        self.root.minsize(800, 520)
        self.root.configure(bg=BG)

        # Created only after the user presses "Start Peer" (needs name/port).
        self.node: PeerNode | None = None

        # Thread-safe hand-off between PeerNode's threads and this GUI.
        self.event_queue: queue.Queue = queue.Queue()

        # The listbox only stores display text, so we keep a parallel list
        # of peer_ids: index i in the listbox == self._peer_ids[i].
        self._peer_ids: list[str] = []

        self._build_sidebar()
        self._build_chat_area()

        self.root.after(100, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Small widget helpers (keeps the build code short and consistent)
    # ------------------------------------------------------------------

    def _make_entry(self, parent, width: int, default: str = "") -> tk.Entry:
        """A flat text box with a thin border that turns accent-colored on focus."""
        entry = tk.Entry(
            parent, width=width, font=FONT, fg=TEXT, bg="white", relief="flat",
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
            disabledbackground="#eef0f5", disabledforeground=MUTED,
        )
        entry.insert(0, default)
        return entry

    def _make_button(self, parent, text: str, command, primary: bool = True) -> tk.Button:
        """A flat button. primary=True -> filled accent color, else outlined/grey."""
        if primary:
            return tk.Button(
                parent, text=text, command=command, font=FONT_BOLD,
                bg=ACCENT, fg="white", activebackground=ACCENT_DARK, activeforeground="white",
                disabledforeground="#c7c9d6", relief="flat", bd=0, padx=12, pady=5, cursor="hand2",
            )
        return tk.Button(
            parent, text=text, command=command, font=FONT,
            bg="#e5e7ef", fg=TEXT, activebackground="#d5d8e4", activeforeground=TEXT,
            disabledforeground="#a3a6b5", relief="flat", bd=0, padx=12, pady=5, cursor="hand2",
        )

    def _section_label(self, parent, text: str) -> None:
        tk.Label(parent, text=text.upper(), font=FONT_SMALL, fg=MUTED, bg=SIDEBAR).pack(
            anchor="w", padx=16, pady=(16, 4)
        )

    # ------------------------------------------------------------------
    # Left sidebar: my peer, connect form, connected peers
    # ------------------------------------------------------------------

    def _build_sidebar(self) -> None:
        sidebar = tk.Frame(self.root, bg=SIDEBAR, width=300)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)  # keep the fixed width

        tk.Label(sidebar, text="P2P Network", font=("Segoe UI", 15, "bold"), fg=ACCENT, bg=SIDEBAR).pack(
            anchor="w", padx=16, pady=(16, 0)
        )

        # ---- My Peer ----
        self._section_label(sidebar, "My peer")
        form = tk.Frame(sidebar, bg=SIDEBAR)
        form.pack(fill="x", padx=16)

        tk.Label(form, text="Name", font=FONT, fg=TEXT, bg=SIDEBAR).grid(row=0, column=0, sticky="w")
        self.name_entry = self._make_entry(form, 14, "Tan")
        self.name_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=2, ipady=3)

        tk.Label(form, text="Port", font=FONT, fg=TEXT, bg=SIDEBAR).grid(row=1, column=0, sticky="w")
        self.port_entry = self._make_entry(form, 14, "5000")
        self.port_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=2, ipady=3)
        form.columnconfigure(1, weight=1)

        buttons = tk.Frame(sidebar, bg=SIDEBAR)
        buttons.pack(fill="x", padx=16, pady=(8, 0))
        self.start_button = self._make_button(buttons, "Start Peer", self._start_peer)
        self.start_button.pack(side="left")
        self.stop_button = self._make_button(buttons, "Stop", self._stop_peer, primary=False)
        self.stop_button.pack(side="left", padx=(8, 0))
        self.stop_button.configure(state="disabled")

        self.status_label = tk.Label(
            sidebar, text="\u25cf Offline", font=FONT_SMALL, fg=MUTED, bg=SIDEBAR, anchor="w", justify="left"
        )
        self.status_label.pack(fill="x", padx=16, pady=(8, 0))

        # ---- Connect to another peer ----
        self._section_label(sidebar, "Connect to a peer")
        cform = tk.Frame(sidebar, bg=SIDEBAR)
        cform.pack(fill="x", padx=16)

        tk.Label(cform, text="IP", font=FONT, fg=TEXT, bg=SIDEBAR).grid(row=0, column=0, sticky="w")
        self.ip_entry = self._make_entry(cform, 14, "127.0.0.1")
        self.ip_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=2, ipady=3)

        tk.Label(cform, text="Port", font=FONT, fg=TEXT, bg=SIDEBAR).grid(row=1, column=0, sticky="w")
        self.connect_port_entry = self._make_entry(cform, 14, "5001")
        self.connect_port_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=2, ipady=3)
        cform.columnconfigure(1, weight=1)

        self._make_button(sidebar, "Connect", self._connect_to_peer).pack(anchor="w", padx=16, pady=(8, 0))

        # ---- Connected peers list ----
        self._section_label(sidebar, "Connected peers")
        list_frame = tk.Frame(sidebar, bg=BORDER)  # 1px border look
        list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        self.peer_listbox = tk.Listbox(
            list_frame, font=FONT, fg=TEXT, bg="white", relief="flat", borderwidth=0,
            highlightthickness=0, activestyle="none",
            selectbackground="#e0e7ff", selectforeground=ACCENT,
            # exportselection=False keeps the highlighted peer selected even
            # when you click into the message box (otherwise Tk clears it).
            exportselection=False,
        )
        self.peer_listbox.pack(fill="both", expand=True, padx=1, pady=1)
        self.peer_listbox.bind("<<ListboxSelect>>", lambda event: self._update_chat_title())

    # ------------------------------------------------------------------
    # Right side: chat header, message view, input bar
    # ------------------------------------------------------------------

    def _build_chat_area(self) -> None:
        area = tk.Frame(self.root, bg=BG)
        area.pack(side="left", fill="both", expand=True)

        # Header: shows who the selected peer is (messages/files go to them).
        self.chat_title = tk.Label(
            area, text="Select a peer to chat", font=FONT_TITLE, fg=TEXT, bg=BG, anchor="w"
        )
        self.chat_title.pack(fill="x", padx=20, pady=(16, 8))

        # Message view + scrollbar
        view = tk.Frame(area, bg=BG)
        view.pack(fill="both", expand=True, padx=20)

        scrollbar = tk.Scrollbar(view)
        scrollbar.pack(side="right", fill="y")

        self.log_text = tk.Text(
            view, bg="white", fg=TEXT, relief="flat", wrap="word", state="disabled",
            font=FONT, highlightthickness=1, highlightbackground=BORDER,
            padx=12, pady=8, yscrollcommand=scrollbar.set, cursor="arrow",
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=self.log_text.yview)

        # Text "tags" are how Tk styles parts of a Text widget. Sent messages
        # sit on the right in accent color, received ones on the left, and
        # system/error lines are centered and muted.
        self.log_text.tag_configure("sent_meta", justify="right", foreground=MUTED, font=FONT_SMALL,
                                    spacing1=8, rmargin=6)
        self.log_text.tag_configure("sent", justify="right", foreground=ACCENT, font=FONT,
                                    lmargin1=120, lmargin2=120, rmargin=6)
        self.log_text.tag_configure("recv_meta", justify="left", foreground=MUTED, font=FONT_SMALL,
                                    spacing1=8, lmargin1=6, lmargin2=6)
        self.log_text.tag_configure("recv", justify="left", foreground=TEXT, font=FONT,
                                    lmargin1=6, lmargin2=6, rmargin=120)
        self.log_text.tag_configure("system", justify="center", foreground=MUTED,
                                    font=("Segoe UI", 9, "italic"), spacing1=6)
        self.log_text.tag_configure("error", justify="center", foreground=ERROR,
                                    font=("Segoe UI", 9, "bold"), spacing1=6)

        # Input bar: message box, Send, and file button all in one row.
        bar = tk.Frame(area, bg=BG)
        bar.pack(fill="x", padx=20, pady=16)

        self.message_entry = self._make_entry(bar, 10)
        self.message_entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.message_entry.bind("<Return>", lambda event: self._send_text())

        self._make_button(bar, "Send", self._send_text).pack(side="left", padx=(8, 0))
        self._make_button(bar, "Send File...", self._choose_and_send_file, primary=False).pack(
            side="left", padx=(8, 0)
        )

        self._add_system("Start your peer, then connect to another one to begin.")

    # ------------------------------------------------------------------
    # Writing into the message view (main thread only)
    # ------------------------------------------------------------------

    def _append(self, text: str, tag: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _add_chat(self, who: str, text: str, sent: bool) -> None:
        """A chat message = a small 'name  time' line + the message line."""
        stamp = time.strftime("%H:%M")
        if sent:
            self._append(f"{who}  {stamp}", "sent_meta")
            self._append(text, "sent")
        else:
            self._append(f"{who}  {stamp}", "recv_meta")
            self._append(text, "recv")

    def _add_system(self, text: str) -> None:
        self._append(text, "system")

    def _add_error(self, text: str) -> None:
        self._append(f"[ERROR] {text}", "error")

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
            messagebox.showerror("Invalid input", "Please enter a valid port number (1-65535).")
            return

        port = int(port_text)

        # The callback runs on PeerNode's threads, so it only touches the queue.
        self.node = PeerNode(
            name=name,
            port=port,
            downloads_dir=DOWNLOADS_DIR,
            on_event=lambda event_type, **data: self.event_queue.put((event_type, data)),
        )

        try:
            self.node.start()
        except OSError as exc:
            messagebox.showerror("Could not start peer", f"Port {port} may already be in use.\n\n{exc}")
            self.node = None
            return

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

    def _connect_to_peer(self) -> None:
        if not self._require_started():
            return

        ip = self.ip_entry.get().strip()
        port_text = self.connect_port_entry.get().strip()

        if not ip:
            messagebox.showerror("Invalid input", "Please enter an IP address.")
            return
        if not port_text.isdigit() or not (0 < int(port_text) < 65536):
            messagebox.showerror("Invalid input", "Please enter a valid port number (1-65535).")
            return

        self.node.connect_to_peer(ip, int(port_text))
        self._add_system(f"Connecting to {ip}:{port_text} ...")

    def _selected_peer_id(self) -> str | None:
        selection = self.peer_listbox.curselection()
        if not selection:
            return None
        return self._peer_ids[selection[0]]

    def _update_chat_title(self) -> None:
        """Header text follows the currently selected peer."""
        peer_id = self._selected_peer_id()
        if peer_id is None or self.node is None:
            self.chat_title.configure(text="Select a peer to chat")
        else:
            self.chat_title.configure(text=f"Chatting with {self._peer_name(peer_id)}")

    def _send_text(self) -> None:
        if not self._require_started():
            return

        peer_id = self._selected_peer_id()
        if peer_id is None:
            messagebox.showwarning("No peer selected", "Select a connected peer from the list first.")
            return

        text = self.message_entry.get().strip()
        if not text:
            return

        self.node.send_text(peer_id, text)
        self._add_chat(f"You \u2192 {self._peer_name(peer_id)}", text, sent=True)
        self.message_entry.delete(0, "end")

    def _choose_and_send_file(self) -> None:
        if not self._require_started():
            return

        peer_id = self._selected_peer_id()
        if peer_id is None:
            messagebox.showwarning("No peer selected", "Select a connected peer from the list first.")
            return

        filepath = filedialog.askopenfilename(title="Choose a file to send")
        if not filepath:
            return  # user cancelled

        self._add_system(f"Sending {os.path.basename(filepath)} to {self._peer_name(peer_id)} ...")
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
            self.root.after(100, self._poll_events)  # reschedule ourselves

    def _handle_event(self, event_type: str, data: dict) -> None:
        if event_type == "started":
            self.status_label.configure(
                text=f"\u25cf Online: {data['name']} [{data['peer_id']}] on port {data['port']}",
                fg=ONLINE,
            )
            self._add_system(f"Peer started: {data['name']} [{data['peer_id']}] on port {data['port']}")

        elif event_type == "peer_connected":
            self._peer_ids.append(data["peer_id"])
            self.peer_listbox.insert("end", f"{data['name']}  [{data['peer_id']}]\n{data['ip']}:{data['port']}".replace("\n", "   "))
            self._add_system(f"Connected to {data['name']} ({data['ip']}:{data['port']})")

        elif event_type == "peer_disconnected":
            if data["peer_id"] in self._peer_ids:
                idx = self._peer_ids.index(data["peer_id"])
                self._peer_ids.pop(idx)
                self.peer_listbox.delete(idx)
                self._update_chat_title()
            self._add_system(f"{data['name']} disconnected")

        elif event_type == "text":
            self._add_chat(f"{data['sender_name']} \u2192 You", data["text"], sent=False)

        elif event_type == "file_received":
            self._add_system(
                f"File received from {data['sender_name']}: {data['filename']} "
                f"({data['filesize']} bytes) saved to downloads/"
            )

        elif event_type == "file_sent":
            self._add_system(f"File sent: {data['filename']} ({data['filesize']} bytes)")

        elif event_type == "error":
            self._add_error(data["message"])

        elif event_type == "stopped":
            pass  # already handled in _stop_peer

    # ------------------------------------------------------------------
    # Window close
    # ------------------------------------------------------------------

    def _on_close(self) -> None:
        if self.node:
            self.node.stop()
        self.root.destroy()