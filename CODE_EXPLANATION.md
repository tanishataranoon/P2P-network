# Code Explanation: UAP P2P Network

Line numbers refer to the files exactly as they are in your project zip.

## 1. The big picture

```
main.py  ->  gui.py (App)  ->  p2p_node.py (PeerNode)  ->  protocol.py
 start        what you see       sockets + threads          bytes <-> dicts
```

- `protocol.py` knows how to turn a dict into bytes and back. No sockets logic beyond send/recv.
- `p2p_node.py` knows how to listen, connect, handshake, send and receive. No GUI code.
- `gui.py` knows how to draw the window. No socket code.
- `main.py` just starts everything.

The two layers talk in two directions only:
- GUI to network: normal method calls (`node.send_text(...)`).
- Network to GUI: a callback `on_event(event_type, **data)`. The GUI's callback just puts the event in a queue.

## 2. Life of a chat message (trace this for your viva)

1. You type "hi" and press Enter. `gui.py` `_send_text` (line 356) runs.
2. It calls `node.send_text(peer_id, "hi")` (`p2p_node.py` line 358).
3. `send_text` builds the dict `{"type": "text", "sender_id", "sender_name", "message": "hi"}` and calls `protocol.send_message`.
4. `send_message` (`protocol.py` line 137) calls `encode_message`, which makes `[4-byte length][JSON bytes]`, and `sendall()` pushes it onto the TCP socket.
5. On the other computer, that peer's `_listen_loop` thread is blocked in `recv_message`. It reads 4 bytes, then N bytes, and parses the JSON.
6. `_listen_loop` sees `type == "text"` and calls `on_event("text", ...)`.
7. The callback puts the event in the `queue.Queue`.
8. Within 100 ms, `_poll_events` (`gui.py` line 406) pulls it out and `_handle_event` shows it in the chat view.

## 3. protocol.py

| Lines | Name | What it does |
|-------|------|--------------|
| 41 | `LENGTH_PREFIX_SIZE = 4` | Every message starts with a 4-byte length header |
| 45-48 | `MSG_HELLO`, `MSG_HELLO_ACK`, `MSG_TEXT`, `MSG_FILE` | Message type names as constants, so you never mistype a string |
| 55 | `FILE_CHUNK_SIZE = 64 * 1024` | Files are read and sent 64 KB at a time to keep memory low |
| 58-66 | `generate_peer_id()` | `uuid.uuid4().hex[:8]` gives a random 8-character ID like `a83f21c4` |
| 69-83 | `encode_message(message)` | Turns a dict into bytes ready to send |
| 86-112 | `recv_exact(sock, n)` | Reads exactly `n` bytes, looping until done |
| 115-134 | `recv_message(sock)` | Reads one full framed message and returns a dict |
| 137-145 | `send_message(sock, message)` | Encodes and sends with `sendall()` |

### Important lines

**`encode_message`**
- Line 81: `json.dumps(message).encode("utf-8")` turns the dict into JSON text, then into bytes.
- Line 82: `struct.pack(">I", len(payload))` turns the length into 4 bytes. `>` means big-endian (network byte order), `I` means unsigned 32-bit integer.
- Line 83: `length_prefix + payload` is the finished frame.

**`recv_exact`** (the most important function in the project)
- Line 102: `buffer = bytearray()` is where the pieces collect.
- Line 103: `while len(buffer) < num_bytes` keeps looping until enough bytes have arrived.
- Line 105-106: `sock.recv(remaining)` asks only for what is still missing. `recv(n)` may return fewer than `n` bytes, which is why the loop exists.
- Lines 107-110: if `recv` returns empty bytes `b""`, the other side closed the connection, so the function returns `None`.

**`recv_message`**
- Line 124: read the 4-byte header.
- Line 128: `struct.unpack(">I", header)` converts it back to a number (the payload length).
- Line 130: read exactly that many bytes.
- Line 134: `json.loads(...)` gives back the dict.

**`send_message`**: `sendall()` instead of `send()` because `send()` may send only part of the data. `sendall()` keeps going until everything is handed to the OS.

**Why framing is needed**: TCP has no concept of messages. If you send two JSON messages quickly, the receiver could get them glued together or cut in the middle. The length prefix tells the receiver exactly where each message ends.

## 4. p2p_node.py

### `PeerNode.__init__` (lines 45-68)
- Stores `name`, `port`, `downloads_dir`, and creates a random `peer_id`.
- Line 55: `self.on_event = on_event or (lambda ...: None)` means the class still works if no callback is given.
- Line 62: `_connections` is a dictionary `peer_id -> {"socket", "name", "ip", "port"}`. It is the list of everyone you are connected to.
- Line 63: `_connections_lock` is a `threading.Lock`. The dictionary is touched by many threads at once, so access is wrapped in `with self._connections_lock:`.
- Line 68: `os.makedirs(..., exist_ok=True)` creates `downloads/` if missing.

### `start()` (lines 74-92): the server half
- Line 81: create a TCP socket (`AF_INET` = IPv4, `SOCK_STREAM` = TCP).
- Line 85: `SO_REUSEADDR` lets you restart on the same port immediately.
- Line 86: `bind(("0.0.0.0", port))` listens on all network interfaces, so other computers on the LAN can reach you.
- Line 87: `listen()` puts the socket in listening mode.
- Line 90: the `accept` loop runs in a background thread (`daemon=True` means it dies when the app closes), so the GUI stays responsive.
- Line 92: tells the GUI "started".

### `stop()` (lines 94-113)
Sets `_running = False`, closes the server socket, then removes every peer with `notify=False` (no "disconnected" messages, because you are the one leaving).

### `_accept_loop()` (lines 119-138)
- Line 129: `accept()` blocks until someone connects, then returns `(conn, addr)`.
- Line 130-132: when `stop()` closes the server socket, `accept()` raises `OSError` and the loop exits quietly.
- Lines 134-138: each new connection gets its own thread, and the loop goes straight back to `accept()`. This is what allows many peers to connect at once.

### `_handle_incoming_connection()` (lines 140-176): server side of the handshake
1. Line 147: wait for the other peer's `hello`.
2. Lines 148-150: if it is not a `hello`, close and leave.
3. Lines 152-154: read the remote peer's ID, name and listening port.
4. Lines 158-166: reply with `hello_ack` containing our own details.
5. Line 168: `_register_peer(...)` saves them. Note `addr[0]` is the IP, and `remote_port` is their *listening* port (not the temporary port their outgoing socket used).
6. Line 169: `_listen_loop(...)` runs directly in this thread for the rest of the connection.

### `connect_to_peer()` and `_connect_worker()` (lines 182-233): the client half
- `connect_to_peer` only starts a thread, so a slow or dead address never freezes the GUI.
- Line 197: `settimeout(5)` so an unreachable peer fails after 5 seconds.
- Line 198: `connect((ip, port))` opens the connection.
- Line 199: `settimeout(None)` switches back to normal blocking mode for chatting.
- Lines 201-209: send `hello`.
- Line 211-215: wait for `hello_ack`; if it does not arrive, report the error and close.
- Line 220: register the peer.
- Lines 224-226: start `_listen_loop` in a new thread.
- Lines 228-233: `ConnectionRefusedError` (nothing listening on that port), timeout, and other `OSError` each show a clear message.

### `_listen_loop()` (lines 241-269): receives everything after the handshake
- Line 242: runs while the node is running.
- Line 244: `recv_message` blocks here, waiting for the next message.
- Line 248-251: `None` means the peer disconnected, so leave the loop.
- Lines 255-261: `text` message: report it to the GUI.
- Lines 263-264: `file` message: call `_receive_file`.
- Line 269: after the loop ends, `_remove_peer` cleans up.

### `_receive_file()` (lines 275-310)
- Line 285: `os.path.basename(...)` strips any folder part from the filename. This is a security measure: without it, a malicious name like `../../evil.py` could write outside `downloads/`.
- Line 286: `filesize` comes from the metadata message, so we know exactly how many raw bytes follow.
- Line 293: open the destination file in `"wb"` (write binary).
- Lines 294-300: loop until `received == filesize`. Each pass asks for `min(64 KB, remaining)` so we never read past the end of the file into the next message. Empty chunk means the peer vanished mid-file, so raise an error.
- Lines 302-308: report `file_received` to the GUI.

### `send_file()` (lines 312-352)
- Lines 318-320: check the file exists.
- Lines 322-325: look up the peer's socket.
- Lines 331-340: send the `file` metadata (name and size) as a normal framed message.
- Lines 342-347: open the file in `"rb"`, read 64 KB at a time and `sendall()` each chunk. The raw bytes are NOT JSON-wrapped; the receiver already knows how many to expect.
- Line 349: report `file_sent`. Lines 350-352: on failure, report and drop the peer.

### `send_text()` (lines 358-377)
Looks up the socket, builds the `text` dict, sends it with `protocol.send_message`. On failure, drops that peer.

### Helpers (lines 383-429)
- `_register_peer`: adds the peer to `_connections` under the lock and fires `peer_connected`.
- `_remove_peer`: removes the peer under the lock, then `shutdown(SHUT_RDWR)` followed by `close()`. `shutdown` matters because another thread may be blocked in `recv()` on that socket; it forces that `recv()` to return so the thread can end, and it tells the remote side the connection is closed.
- `_get_socket`: safe lookup of one peer's socket.
- `get_connected_peers`: returns a copy (snapshot) of the peer list for the GUI.

## 5. gui.py

### Constants (lines 36-52)
`DOWNLOADS_DIR` is built from the location of `gui.py`, so it works no matter where you launch from. The rest is the colour palette and fonts, kept in one place so the whole look can be changed easily.

### `App.__init__` (lines 58-79)
- Lines 60-63: window title, size, minimum size, background.
- Line 66: `self.node = None`. The `PeerNode` does not exist until you press **Start Peer**.
- Line 69: `self.event_queue` is the thread-safe bridge between the network threads and the GUI.
- Line 73: `_peer_ids` is a list parallel to the listbox: row `i` in the listbox is peer `_peer_ids[i]`. The listbox only stores display text, so this list is how we know which peer a row means.
- Lines 75-76: build the two halves of the window.
- Line 78: `root.after(100, self._poll_events)` starts the 100 ms polling.
- Line 79: `protocol("WM_DELETE_WINDOW", self._on_close)` makes the window's X button shut down sockets cleanly.

### Helpers (lines 85-112)
`_make_entry`, `_make_button`, `_section_label` only reduce repeated styling code.

### `_build_sidebar()` (lines 118-184)
Builds three blocks: **My peer** (name, port, Start/Stop, status), **Connect to a peer** (IP, port, Connect), and **Connected peers** (a listbox). Line 181 `exportselection=False` keeps the selected peer highlighted when you click in the message box. Line 184 binds selection changes to `_update_chat_title`.

### `_build_chat_area()` (lines 190-244)
Builds the header, the read-only `Text` widget with a scrollbar, and the input row. Lines 218-229 define text *tags* (sent right-aligned in accent colour, received left-aligned, system messages centred and grey, errors red). Line 237 binds the Enter key to `_send_text`.

### Writing to the chat (lines 250-270)
- `_append`: enables the widget, inserts text, scrolls to the end, disables it again (so the user cannot type in the log).
- `_add_chat`, `_add_system`, `_add_error`: choose the right tag.

### Button handlers (lines 276-393)
- **`_start_peer`**: validates name and port (`isdigit()` and range 1-65535), creates the `PeerNode` with an `on_event` lambda that only puts events in the queue (line 294), calls `start()`, and shows an error box if the port is already in use (line 299-302). Then disables the name/port fields.
- **`_stop_peer`**: stops the node, clears the peer list, re-enables the fields, shows Offline.
- **`_connect_to_peer`**: validates IP and port and calls `node.connect_to_peer`.
- **`_selected_peer_id`**: turns the selected listbox row into a `peer_id`.
- **`_send_text`**: requires a started node, a selected peer and non-empty text; sends and echoes the message in the log.
- **`_choose_and_send_file`**: opens the file picker and calls `node.send_file`.
- **`_require_started`**, **`_peer_name`**: small utilities.

### `_poll_events()` and `_handle_event()` (lines 406-453)
- `_poll_events` empties the queue with `get_nowait()` until it raises `queue.Empty`, then reschedules itself with `after(100, ...)` (the `finally` guarantees it always reschedules).
- `_handle_event` is a switch on `event_type`: `started`, `peer_connected` (adds a listbox row and appends to `_peer_ids`), `peer_disconnected` (removes the matching row), `text`, `file_received`, `file_sent`, `error`.

**Why the queue?** Tkinter is not thread-safe. If a network thread touched a widget directly the app could crash or behave randomly. So only the main thread updates widgets.

## 6. Questions you may be asked

1. **Why is the peer both server and client?** So any peer can accept connections and also start connections, with no central server.
2. **Why does TCP need message framing?** It is a byte stream with no message boundaries.
3. **Why `recv_exact` instead of a single `recv`?** `recv(n)` may return fewer bytes than requested.
4. **Why threads?** `accept()` and `recv()` block. Without threads the app could only do one thing at a time.
5. **Why a lock?** Several threads read and write `_connections` at once.
6. **Why a queue between network and GUI?** Tkinter widgets may only be touched from the main thread.
7. **Why send file metadata first?** The receiver must know the filename and exactly how many raw bytes to read.
8. **Why `shutdown()` before `close()`?** It wakes a thread blocked in `recv()` and notifies the other side.
9. **Why `basename` on the filename?** To block path traversal such as `../../file`.
10. **What is `0.0.0.0`?** Listen on every network interface, not only localhost.

## 7. Known limitations (be honest about these)

- `_receive_file` reads with its own `conn.recv` loop rather than calling `protocol.recv_exact`. It is correct (the loop reads exactly `filesize` bytes) but the comment on lines 281-283 wrongly says it uses `recv_exact`.
- A file with an existing name is overwritten.
- `send_file` runs on the GUI thread, so a huge file can freeze the window while sending.
- Connecting twice to the same peer adds a second list row while the dictionary keeps one entry.
- A malformed JSON message in `_listen_loop` is not caught and would end that peer's thread.
- No encryption or authentication (out of scope for the assignment).
