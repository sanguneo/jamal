"""Small offline checks for import and CLI scaffold boundaries."""

import importlib
import pkgutil
import socket
import subprocess
import sys
from typing import Never

import pytest


def test_imports_do_not_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> Never:
        raise AssertionError("package import attempted network access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    package = importlib.import_module("jamal")
    importlib.reload(package)
    for module in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        importlib.import_module(module.name)


@pytest.mark.parametrize(
    ("args", "returncode"),
    [
        (["--help"], 0),
        (["credentials", "inspect", "--credentials", "absent.json"], 1),
        (["config", "fetch", "--credentials", "absent.json", "--out", "unused.json"], 2),
        (["decode", "--input", "absent.bin", "--type", "Unknown"], 2),
        (
            [
                "subscribe", "--credentials", "absent.json", "--config", "absent.json",
                "--device", "absent.json",
            ],
            1,
        ),
        (["capability", "query", "--request", "absent.json"], 2),
        (["send", "rcs", "--request", "absent.json"], 2),
        (["send", "xms", "--request", "absent.json"], 2),
    ],
)
def test_cli(args: list[str], returncode: int) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "jamal", *args],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == returncode
    if returncode == 0:
        assert "usage:" in result.stdout
        assert result.stderr == ""
    else:
        assert result.stdout == ""
        assert result.stderr
        assert "Traceback" not in result.stderr
