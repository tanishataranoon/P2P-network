"""
p2p_node.py
-----------
This is the heart of the assignment: the P2P networking layer.

WHY THIS FILE EXISTS:
The assignment requires that every running instance of the app be BOTH
a TCP server (so other peers can connect to it) AND a TCP client (so it
can connect out to other peers). This file implements exactly that in
one class, PeerNode:

    Every peer = TCP Server + TCP Client

It has nothing to do with the GUI - gui.py only ever calls methods on a
PeerNode object (start, stop, connect_to_peer, send_text, send_file) and
receives events back through a callback. This separation means the
networking code can be tested/run completely independently of Tkinter.

THREADING MODEL:
- One background thread runs the server's accept() loop.
- One additional background thread is spawned per connected peer, to
  continuously listen for incoming messages from that peer without
  blocking anything else (this is what lets a peer "remain available"
  while already talking to other peers, as required by the assignment).
- Because Tkinter is NOT thread-safe, this class never touches the GUI
  directly. Instead it reports everything that happens (connections,
  messages, files, errors) through a single `on_event` callback, which
  gui.py implements by pushing events onto a thread-safe queue.
"""

import json
import os
import socket
import threading

import protocol


class PeerNode:
    """
    Represents "this" peer: holds its own server socket plus a table of
    all currently-connected peers (each with their own live socket).
    """

    def __init__(self, name: str, port: int, downloads_dir: str, on_event=None):
        self.name = name
        self.port = port
        self.peer_id = protocol.generate_peer_id()
        self.downloads_dir = downloads_dir

        # on_event(event_type: str, **data) is called from worker threads
        # whenever something happens that the GUI should know about
        # (peer connected, message received, file received, error, etc).
        # Kept optional (None) so this class can be used/tested standalone.
        self.on_event = on_event or (lambda event_type, **data: None)

        # connections maps peer_id -> info dict:
        #   {"socket": conn, "name": str, "ip": str, "port": int}
        # A lock is required because this dict is read/written from
        # several threads at once (the accept loop, each per-peer listen
        # loop, and whenever the GUI calls send_text/send_file).
        self._connections: dict[str, dict] = {}
        self._connections_lock = threading.Lock()

        self._server_socket: socket.socket | None = None
        self._running = False

        os.makedirs(self.downloads_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Starting / stopping this peer
    # ------------------------------------------------------------------

    def start(self) -> None:
        """
        Start listening for incoming connections (the "server" half of
        this peer). Follows the standard socket/bind/listen/accept flow
        described in the assignment, then hands off the accept() loop to
        a background thread so it doesn't block the GUI.
        """
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # SO_REUSEADDR lets us restart the peer quickly on the same port
        # without waiting for the OS to release the old socket (common
        # annoyance during development/testing).
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind(("0.0.0.0", self.port))
        self._server_socket.listen()

        self._running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()

        self.on_event("started", peer_id=self.peer_id, name=self.name, port=self.port)

    def stop(self) -> None:
        """
        Cleanly shut everything down: stop accepting new connections and
        close every existing peer connection.
        """
        self._running = False

        if self._server_socket:
            try:
                self._server_socket.close()
            except OSError:
                pass
            self._server_socket = None

        with self._connections_lock:
            peer_ids = list(self._connections.keys())
        for peer_id in peer_ids:
            self._remove_peer(peer_id, notify=False)

        self.on_event("stopped")

    # ------------------------------------------------------------------
    # Server role: accepting incoming connections
    # ------------------------------------------------------------------

    def _accept_loop(self) -> None:
        """
        Runs on its own thread for as long as the peer is started.
        Blocks on accept() until a new peer connects, then spawns a
        dedicated thread to handle the handshake + ongoing messages for
        THAT connection, and immediately loops back to accept the next
        one. This is what allows multiple peers to connect concurrently.
        """
        while self._running:
            try:
                conn, addr = self._server_socket.accept()
            except OSError:
                # Socket was closed (stop() was called) - exit quietly.
                break

            threading.Thread(
                target=self._handle_incoming_connection,
                args=(conn, addr),
                daemon=True,
            ).start()

    def _handle_incoming_connection(self, conn: socket.socket, addr) -> None:
        """
        Runs for one freshly-accepted connection. First performs our side
        of the HELLO handshake (wait for the other peer's 'hello', then
        reply with 'hello_ack'), then falls into the normal listen loop.
        """
        try:
            hello = protocol.recv_message(conn)
            if hello is None or hello.get("type") != protocol.MSG_HELLO:
                conn.close()
                return

            remote_id = hello["peer_id"]
            remote_name = hello["peer_name"]
            remote_port = hello["port"]

            # Reply with our own identity so the other side knows who
            # THEY are now connected to (the handshake is two-way).
            protocol.send_message(
                conn,
                {
                    "type": protocol.MSG_HELLO_ACK,
                    "peer_id": self.peer_id,
                    "peer_name": self.name,
                    "port": self.port,
                },
            )

            self._register_peer(remote_id, remote_name, conn, addr[0], remote_port)
            self._listen_loop(conn, remote_id)

        except (OSError, ConnectionError, json.JSONDecodeError) as exc:
            self.on_event("error", message=f"Incoming connection failed: {exc}")
            try:
                conn.close()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Client role: connecting out to another peer
    # ------------------------------------------------------------------

    def connect_to_peer(self, ip: str, port: int) -> None:
        """
        Initiate an outgoing connection to another peer (the "client"
        half of this peer). Performs our side of the HELLO handshake
        (send 'hello', wait for 'hello_ack'), registers the new peer,
        then spawns a thread to keep listening for further messages.

        Runs its own body in a background thread so a slow/unreachable
        peer (e.g. "connection refused") never freezes the GUI.
        """
        threading.Thread(
            target=self._connect_worker, args=(ip, port), daemon=True
        ).start()

    def _connect_worker(self, ip: str, port: int) -> None:
        try:
            client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client_socket.settimeout(5)  # fail fast on unreachable peers
            client_socket.connect((ip, port))
            client_socket.settimeout(None)  # back to blocking mode for normal use

            protocol.send_message(
                client_socket,
                {
                    "type": protocol.MSG_HELLO,
                    "peer_id": self.peer_id,
                    "peer_name": self.name,
                    "port": self.port,
                },
            )

            ack = protocol.recv_message(client_socket)
            if ack is None or ack.get("type") != protocol.MSG_HELLO_ACK:
                self.on_event(
                    "error", message="Handshake failed: no hello_ack received"
                )
                client_socket.close()
                return

            remote_id = ack["peer_id"]
            remote_name = ack["peer_name"]

            self._register_peer(remote_id, remote_name, client_socket, ip, port)

            # Keep listening for messages this peer sends us AFTER the
            # handshake (text, files, etc) on a dedicated thread.
            threading.Thread(
                target=self._listen_loop, args=(client_socket, remote_id), daemon=True
            ).start()

        except (ConnectionRefusedError,):
            self.on_event("error", message=f"Connection refused by {ip}:{port}")
        except (socket.timeout, TimeoutError):
            self.on_event("error", message=f"Connection to {ip}:{port} timed out")
        except OSError as exc:
            self.on_event("error", message=f"Could not connect to {ip}:{port}: {exc}")

    # ------------------------------------------------------------------
    # Shared listen loop (used for BOTH incoming and outgoing connections
    # once the handshake is done) - this is where text/file messages are
    # received for the lifetime of the connection.
    # ------------------------------------------------------------------

    def _listen_loop(self, conn: socket.socket, peer_id: str) -> None:
        while self._running:
            try:
                message = protocol.recv_message(conn)
            except (OSError, ConnectionError):
                message = None

            if message is None:
                # Empty message = the peer disconnected (gracefully or
                # not). Clean up instead of crashing, as required.
                break

            msg_type = message.get("type")

            if msg_type == protocol.MSG_TEXT:
                self.on_event(
                    "text",
                    sender_id=message.get("sender_id"),
                    sender_name=message.get("sender_name"),
                    text=message.get("message"),
                )

            elif msg_type == protocol.MSG_FILE:
                self._receive_file(conn, peer_id, message)

            else:
                self.on_event("error", message=f"Unknown message type: {msg_type}")

        self._remove_peer(peer_id)

    # ------------------------------------------------------------------
    # File transfer
    # ------------------------------------------------------------------

    def _receive_file(self, conn: socket.socket, peer_id: str, metadata: dict) -> None:
        """
        Called once a 'file' metadata message has arrived. The metadata
        tells us the filename and exact byte count that follow RAW on
        the socket (not wrapped in JSON/framing - just the file's bytes).

        We must read EXACTLY `filesize` bytes, in chunks, and must NOT
        assume a single recv() call delivers the whole file - that is
        exactly what protocol.recv_exact() guarantees for us.
        """
        filename = os.path.basename(metadata.get("filename", "unnamed_file"))
        filesize = metadata.get("filesize", 0)
        sender_name = metadata.get("sender_name", "unknown")

        save_path = os.path.join(self.downloads_dir, filename)

        try:
            received = 0
            with open(save_path, "wb") as f:
                while received < filesize:
                    remaining = filesize - received
                    chunk = conn.recv(min(protocol.FILE_CHUNK_SIZE, remaining))
                    if not chunk:
                        raise ConnectionError("Peer disconnected mid-file-transfer")
                    f.write(chunk)
                    received += len(chunk)

            self.on_event(
                "file_received",
                peer_id=peer_id,
                sender_name=sender_name,
                filename=filename,
                path=save_path,
                filesize=filesize,
            )
        except (OSError, ConnectionError) as exc:
            self.on_event(
                "error",
                peer_id=peer_id,
                message=f"File transfer from {sender_name} failed: {exc}",
            )

    def send_file(self, peer_id: str, filepath: str) -> None:
        """
        Send a file to an already-connected peer, as required by the
        assignment: metadata message first, then the raw bytes streamed
        in chunks (never loading the whole file into memory at once).
        """
        if not os.path.isfile(filepath):
            self.on_event("error", message=f"File does not exist: {filepath}")
            return

        conn = self._get_socket(peer_id)
        if conn is None:
            self.on_event("error", message="Cannot send file: peer not connected")
            return

        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)

        try:
            protocol.send_message(
                conn,
                {
                    "type": protocol.MSG_FILE,
                    "sender_id": self.peer_id,
                    "sender_name": self.name,
                    "filename": filename,
                    "filesize": filesize,
                },
            )

            with open(filepath, "rb") as f:
                while True:
                    chunk = f.read(protocol.FILE_CHUNK_SIZE)
                    if not chunk:
                        break
                    conn.sendall(chunk)

            self.on_event(
                "file_sent", peer_id=peer_id, filename=filename, filesize=filesize
            )
        except OSError as exc:
            self.on_event(
                "error", peer_id=peer_id, message=f"Failed to send file: {exc}"
            )
            self._remove_peer(peer_id)

    # ------------------------------------------------------------------
    # Text messaging
    # ------------------------------------------------------------------

    def send_text(self, peer_id: str, text: str) -> None:
        """Send a plain text message to one already-connected peer."""
        conn = self._get_socket(peer_id)
        if conn is None:
            self.on_event("error", message="Cannot send message: peer not connected")
            return

        try:
            protocol.send_message(
                conn,
                {
                    "type": protocol.MSG_TEXT,
                    "sender_id": self.peer_id,
                    "sender_name": self.name,
                    "message": text,
                },
            )
        except OSError as exc:
            self.on_event(
                "error", peer_id=peer_id, message=f"Failed to send message: {exc}"
            )
            self._remove_peer(peer_id)

    # ------------------------------------------------------------------
    # Internal helpers for managing the connections table
    # ------------------------------------------------------------------

    def _register_peer(
        self, peer_id: str, name: str, conn: socket.socket, ip: str, port: int
    ) -> None:
        with self._connections_lock:
            self._connections[peer_id] = {
                "socket": conn,
                "name": name,
                "ip": ip,
                "port": port,
            }
        self.on_event("peer_connected", peer_id=peer_id, name=name, ip=ip, port=port)

    def _remove_peer(self, peer_id: str, notify: bool = True) -> None:
        with self._connections_lock:
            info = self._connections.pop(peer_id, None)
        if info is not None:
            sock = info["socket"]
            try:
                # shutdown() (not just close()) matters here: this
                # socket's OWN _listen_loop thread may currently be
                # blocked inside recv() on it. A bare close() from a
                # different thread does not reliably unblock that
                # recv() or send the TCP FIN packet to the other side
                # while a syscall is in-flight on the same fd.
                # shutdown(SHUT_RDWR) forces the blocked recv() to
                # return immediately (with 0 bytes = EOF) and properly
                # notifies the remote peer that the connection is closed.
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # socket may already be half-closed/broken - fine
            try:
                sock.close()
            except OSError:
                pass
            if notify:
                self.on_event("peer_disconnected", peer_id=peer_id, name=info["name"])

    def _get_socket(self, peer_id: str) -> socket.socket | None:
        with self._connections_lock:
            info = self._connections.get(peer_id)
            return info["socket"] if info else None

    def get_connected_peers(self) -> list[dict]:
        """Returns a snapshot list of currently connected peers for the GUI."""
        with self._connections_lock:
            return [
                {
                    "peer_id": pid,
                    "name": info["name"],
                    "ip": info["ip"],
                    "port": info["port"],
                }
                for pid, info in self._connections.items()
            ]
