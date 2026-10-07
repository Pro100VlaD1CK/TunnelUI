"""Privileged Linux agent boundary.

Linux-only facilities are imported lazily so the backend and its Windows test suite
can import protocol and client types without fcntl, pwd, systemd, or Unix sockets.
"""

