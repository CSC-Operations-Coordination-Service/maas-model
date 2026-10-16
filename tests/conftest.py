"""
Dummy conftest.py for maas_model.

If you don't know what this is for, just leave it empty.
Read more about conftest.py under:
- https://docs.pytest.org/en/stable/fixture.html
- https://docs.pytest.org/en/stable/writing_plugins.html
"""

import os

import pytest

from maas_model.testing.fakevault import FakeVault


@pytest.fixture
def fake_vault(monkeypatch):
    """a running in-process OpenBao / Vault"""
    # keep the requests to the fake vault off a proxy set for the CI runner
    for variable in ("NO_PROXY", "no_proxy"):
        hosts = [os.environ.get(variable), "127.0.0.1", "localhost"]
        monkeypatch.setenv(variable, ",".join(filter(None, hosts)))

    vault = FakeVault()
    vault.start()
    yield vault
    vault.stop()
