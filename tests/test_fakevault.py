"""Tests for the in-process vault the tests of the MAAS services use"""

import socket
import threading
from urllib.parse import urlsplit

import pytest

from maas_model.testing.fakevault import FakeVault


def test_stop_without_start_closes_the_server():
    """a fixture that fails between FakeVault() and start() must neither hang the
    test run when it stops the vault nor leave its port open"""
    vault = FakeVault()
    port = urlsplit(vault.url).port

    stopping = threading.Thread(target=vault.stop, daemon=True)
    stopping.start()
    stopping.join(timeout=5)

    assert not stopping.is_alive(), "stop() waits for a server that never started"

    with pytest.raises(ConnectionRefusedError):
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
