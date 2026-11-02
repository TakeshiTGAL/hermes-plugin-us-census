"""Block sockets and a real Census key for every test."""

from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise AssertionError("network blocked")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture(autouse=True)
def hide_profile_key(monkeypatch):
    try:
        import agent.secret_scope as scope
    except ImportError:
        return
    monkeypatch.setattr(scope, "get_secret", lambda *_args, **_kwargs: None)
