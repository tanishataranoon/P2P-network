"""
protocol.py
-----------
This file defines the *application-level protocol* used between peers.

WHY THIS FILE EXISTS:
TCP only guarantees a reliable, ordered stream of BYTES. It has no idea
about "messages" - if Alice sends two JSON messages back to back, Bob's
recv() call might get them stuck together, or split halfway through one
of them. This is called the "TCP message framing problem".

To solve it, every message we send is wrapped like this:

    [ 4 bytes: length of the JSON payload ][ N bytes: the JSON payload ]

The receiver always:
  1. Reads exactly 4 bytes -> figures out how many more bytes to expect.
  2. Reads exactly that many bytes -> decodes them as JSON.

This guarantees message boundaries are never lost, no matter how TCP
decides to chop up the underlying bytes.

This file also defines the message "types" used by the app-level protocol:
    hello       - sent right after connecting, introduces a peer
    hello_ack   - reply to hello, introduces the other side back
    text        - a chat message
    file        - metadata describing a file that is about to be sent
                  (the raw file bytes are streamed separately, right
                   after this message - see p2p_node.py send_file/handle)
"""

import json
import socket
import struct
import uuid

# How many bytes we use as the length-prefix header.
# 4 bytes (unsigned int, big-endian) lets us describe messages up to ~4GB,
# which is far more than we need since 'file' messages only carry metadata
# (the actual file bytes are sent raw afterwards, not as JSON).
LENGTH_PREFIX_SIZE = 4

# Message type constants - avoids typos like "hallo" vs "hello" scattered
# around the codebase. Import these instead of hardcoding strings.
MSG_HELLO = "hello"
MSG_HELLO_ACK = "hello_ack"
MSG_TEXT = "text"
MSG_FILE = "file"

# Size of each chunk when streaming file bytes over the socket.
# Reading/sending a huge file in one shot would use a lot of memory and
# block for a long time; chunking keeps memory usage low and lets us
# stream files of (almost) any size. 64 KB is the size suggested by the
# assignment itself.
FILE_CHUNK_SIZE = 64 * 1024


def generate_peer_id() -> str:
    """
    Create a short, human-friendly unique ID for this peer, e.g. 'a83f21c4'.

    We use uuid4 (random UUID) and just take the first 8 hex characters -
    good enough to tell peers apart in a small classroom-sized network,
    and short enough to display nicely in the GUI's peer list.
    """
    return uuid.uuid4().hex[:8]


def encode_message(message: dict) -> bytes:
    """
    Turn a Python dict into the framed bytes ready to send over a socket.

    Steps:
      1. Serialize the dict to a JSON string, then to UTF-8 bytes.
      2. Prefix those bytes with their own length, packed as a 4-byte
         big-endian unsigned integer ('>I' in struct format).

    The receiving side (recv_message, below) knows how to reverse this
    exact process.
    """
    payload = json.dumps(message).encode("utf-8")
    length_prefix = struct.pack(">I", len(payload))
    return length_prefix + payload


def recv_exact(sock: socket.socket, num_bytes: int) -> bytes | None:
    """
    Read EXACTLY `num_bytes` bytes from a socket, no more, no less.

    WHY THIS IS NECESSARY:
    A single sock.recv(n) call is only allowed to return "up to n" bytes -
    it might return fewer (e.g. if the data hasn't fully arrived yet, or
    it got split across network packets). Naively assuming one recv()
    call equals one complete message/file is a classic beginner bug that
    silently truncates files and messages.

    This helper loops, accumulating bytes into a buffer, until either:
      - we have exactly the number of bytes we asked for (success), or
      - the connection closes before that happens (returns None, meaning
        "the peer disconnected mid-transfer").
    """
    buffer = bytearray()
    while len(buffer) < num_bytes:
        remaining = num_bytes - len(buffer)
        # Never ask for more than we still need.
        chunk = sock.recv(remaining)
        if not chunk:
            # An empty bytes object from recv() means the other side
            # closed the connection (a clean TCP disconnect).
            return None
        buffer.extend(chunk)
    return bytes(buffer)


def recv_message(sock: socket.socket) -> dict | None:
    """
    Read one complete, framed JSON message from the socket and return it
    as a Python dict. Returns None if the peer disconnected.

    This is the mirror image of encode_message(): first read the 4-byte
    length prefix, then read exactly that many more bytes, then parse
    them as JSON.
    """
    header = recv_exact(sock, LENGTH_PREFIX_SIZE)
    if header is None:
        return None  # peer disconnected before sending a full header

    (payload_length,) = struct.unpack(">I", header)

    payload = recv_exact(sock, payload_length)
    if payload is None:
        return None  # peer disconnected mid-message

    return json.loads(payload.decode("utf-8"))


def send_message(sock: socket.socket, message: dict) -> None:
    """
    Convenience wrapper: encode a dict and send it fully over the socket.

    We use sendall() (not send()) because send() is also allowed to send
    only part of the data on a single call - sendall() loops internally
    until every byte has actually been handed to the OS.
    """
    sock.sendall(encode_message(message))
