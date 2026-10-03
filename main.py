"""
main.py
-------
Entry point for the whole project. Run this file to launch the app:

    python main.py

WHY THIS FILE IS SO SMALL:
Per the assignment's recommended project structure, main.py's job is
just "user interface and interaction with the user" - all the actual UI
code lives in gui.py, and all the networking code lives in p2p_node.py.
This file's only responsibility is to wire those pieces together and
start Tkinter's main event loop.
"""

import tkinter as tk

from gui import App


def main() -> None:
    root = tk.Tk()
    App(root)          # builds the whole UI and hooks it up to PeerNode
    root.mainloop()     # blocks here, handling all GUI events, until the window closes


if __name__ == "__main__":
    main()
