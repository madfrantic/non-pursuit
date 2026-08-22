"""
Makes utils/ importable the same way app.py does (flat imports, no package),
so tests can `import mailto_builder` / `import tracker` directly.
"""
import os
import sys
import socket
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "utils"))

@pytest.fixture(autouse=True)
def disable_network_calls(monkeypatch):
    """
    Enforce zero network egress in test mode. Blocks unmocked outbound
    network requests across aiohttp, requests, urllib, and raw sockets.

    The blocker is a *subclass* of socket.socket, not a function, because
    the substitute has to stay a type. dnspython evaluates
    `socket.socket | ssl.SSLSocket` in a module-level annotation, and a
    function on the left of `|` raises TypeError at import time -- so any
    test that was the first to import dnspython under this fixture failed
    on the import rather than on a network call. Whether that happened at
    all depended on which test ran first and left dns cached in
    sys.modules, which made the failure look intermittent.

    Behaviour is unchanged: constructing an AF_INET or AF_INET6 socket
    still raises, and everything else (AF_UNIX, socketpair) still works.
    """
    original_socket = socket.socket

    class BlockedSocket(original_socket):
        def __init__(self, family=socket.AF_INET, type=socket.SOCK_STREAM,
                     proto=0, fileno=None):
            if family in (socket.AF_INET, socket.AF_INET6):
                raise RuntimeError("Network access blocked during tests.")
            super().__init__(family, type, proto, fileno)

    monkeypatch.setattr(socket, "socket", BlockedSocket)
