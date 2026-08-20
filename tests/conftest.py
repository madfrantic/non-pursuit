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
    """
    original_socket = socket.socket
    def block_socket(family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0, fileno=None):
        if family in (socket.AF_INET, socket.AF_INET6):
            raise RuntimeError("Network access blocked during tests.")
        return original_socket(family, type, proto, fileno)

    monkeypatch.setattr(socket, "socket", block_socket)
