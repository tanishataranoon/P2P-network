# UAP P2P Network

A lightweight peer-to-peer (P2P) chat and file-sharing application built in
Python for the CSE 433 (Blockchain & Distributed Security Lab) assignment.

Every running instance of the app is **both a TCP server and a TCP client**.
There is no central server: peers connect directly to each other, exchange
text messages, and transfer any kind of binary file (images, audio, video,
PDFs, ZIPs, ...).

![Main window](screenshots/01_main_window.png)

## Features

- Every peer is a TCP server **and** a TCP client
- Direct peer-to-peer connections (no central server)
- Handshake (`hello` / `hello_ack`) so peers learn each other's ID and name
- Text chat with any connected peer
- Binary file transfer in 64 KB chunks (works for files of any type and size)
- Multiple simultaneous connections (one thread per peer)
- Tkinter GUI with a live peer list and chat view
- Errors (refused connection, timeout, disconnect) are shown in the chat log
  instead of crashing the app

## Project Structure

```
P2P_Network/
├── main.py            # Entry point: creates the window and starts the GUI
├── gui.py             # Tkinter user interface (no networking code)
├── p2p_node.py        # PeerNode class: sockets, threads, handshake, files
├── protocol.py        # Message framing (length prefix + JSON) and constants
├── downloads/         # Received files are saved here
├── screenshots/       # Images used in this README
└── README.md
```

| File | Responsibility |
|------|----------------|
| `main.py` | Creates the Tk window, builds `App`, runs the main loop |
| `gui.py` | Draws the UI, validates input, shows events from the network layer |
| `p2p_node.py` | All networking: listen, connect, handshake, send/receive text and files |
| `protocol.py` | Turns Python dicts into framed bytes and back; message type constants |

## Requirements

- Python 3.10+ (the code uses the `X | None` type syntax)
- No third-party packages: only `socket`, `threading`, `json`, `struct`,
  `uuid`, `queue` and `tkinter` from the standard library
- On some Linux distributions Tkinter must be installed separately:
  `sudo apt install python3-tk`

## How to Run

From inside the `P2P_Network` folder:

```bash
python main.py
```

Run the command more than once (separate terminals, or separate computers on
the same network) to simulate several peers.

## How to Connect Two Peers

1. **Start Peer A**: enter a name (e.g. `Alice`) and a port (e.g. `5000`),
   then click **Start Peer**. The status line turns green.
2. **Start Peer B** in a second instance: name `Bob`, a *different* port
   (e.g. `5001`), then click **Start Peer**.
3. On Peer B, under **Connect to a peer**, enter Peer A's IP and port
   (`127.0.0.1` and `5000` on the same computer), then click **Connect**.
4. Both peers now list each other under **Connected peers**.

To test on two computers on the same Wi-Fi/LAN, use the other computer's
local IP address (e.g. `192.168.1.10`) instead of `127.0.0.1`.

| Peer A (Alice, port 5000) | Peer B (Bob, port 5001) |
|---|---|
| ![Alice connected](screenshots/02_alice_connected.png) | ![Bob connected](screenshots/03_bob_connected.png) |

## How to Send a Text Message

1. Select the target peer in the **Connected peers** list.
2. Type in the message box at the bottom.
3. Click **Send** (or press Enter).

![Text chat](screenshots/04_text_chat.png)

## How to Transfer a File

1. Select the target peer in the **Connected peers** list.
2. Click **Send File...** and pick any file.
3. The file is sent as raw bytes in 64 KB chunks over the same TCP connection.
4. The receiver saves it automatically into its `downloads/` folder.

| Sender | Receiver |
|---|---|
| ![File sent](screenshots/05_file_sent.png) | ![File received](screenshots/06_file_received.png) |

The received file inside the `downloads/` folder:

![Downloads folder](screenshots/07_downloads_folder.png)

## Error Handling

Bad input, refused connections, timeouts and unexpected disconnects are
reported in the chat log rather than crashing the app.

![Error example](screenshots/08_error_example.png)

## How It Works

### Architecture

```
            +---------------------------- one peer ----------------------------+
            |                                                                  |
  user ---> |  gui.py (App)  --calls-->  p2p_node.py (PeerNode)                |
            |      ^                         |  server thread: accept()        |
            |      |                         |  one thread per peer: recv()    |
            |  queue.Queue  <--on_event()----+                                 |
            |                                |  uses protocol.py for framing   |
            +--------------------------------|---------------------------------+
                                             v
                                      TCP sockets  <---->  other peers
```

### Handshake

```
  Peer B (client)                              Peer A (server)
       |  --- connect() ---------------------->   |
       |  --- hello {id, name, port} --------->   |
       |  <-- hello_ack {id, name, port} -----    |
       |   both sides register each other and start listening
```

### Message framing

TCP is a byte stream and does not preserve message boundaries. Every JSON
message is therefore prefixed with its own length:

```
[ 4 bytes: payload length (big-endian) ][ N bytes: JSON payload ]
```

The receiver reads exactly 4 bytes, then exactly N bytes (`recv_exact`), so
messages are never merged or cut in half.

### Message types

| Type | Purpose | Fields |
|------|---------|--------|
| `hello` | Sent by the connecting peer right after `connect()` | `peer_id`, `peer_name`, `port` |
| `hello_ack` | Reply from the accepting peer | `peer_id`, `peer_name`, `port` |
| `text` | Chat message | `sender_id`, `sender_name`, `message` |
| `file` | Metadata sent *before* the raw file bytes | `sender_id`, `sender_name`, `filename`, `filesize` |

### File transfer

```
sender:    [file JSON message: name + size] [raw bytes ... 64 KB chunks ...]
receiver:  read JSON header -> read exactly `filesize` bytes -> write to downloads/
```

### Threads

- 1 thread runs the server `accept()` loop
- 1 thread per connected peer waits for incoming messages
- 1 short-lived thread per outgoing connection attempt (so the GUI never freezes)
- Tkinter is not thread-safe, so worker threads only push events onto a
  `queue.Queue`; the GUI reads that queue every 100 ms with `root.after()`

For a line-by-line walkthrough of the code, see
[CODE_EXPLANATION.md](CODE_EXPLANATION.md).

## Limitations

- No encryption or authentication: messages travel as plain text
- Peers must know each other's IP and port (no discovery)
- If two files with the same name arrive, the newer one overwrites the older
- Files are sent from the GUI thread, so the window can freeze briefly while
  sending a very large file
- Works on a LAN or localhost; no NAT traversal

## Not Included (Out of Scope)

Per the assignment scope, this project intentionally does **not** include
blockchain, cryptocurrency, DHT, NAT traversal, end-to-end encryption,
authentication, or any consensus mechanism. The focus is direct P2P
communication over TCP sockets.
