"""Opt-in saya registration, heartbeat, and withdrawal."""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

LOG = logging.getLogger(__name__)


@dataclass(frozen=True, repr=False)
class Registration:
    register_url: str
    register_token: str
    url: str
    token: str


def registration_from_env(
    env: Mapping[str, str], *, port: int, token: str
) -> Registration | None:
    url, register_token = env.get("SAYA_REGISTER_URL"), env.get("SAYA_REGISTER_TOKEN")
    if not url and not register_token:
        return None
    if not url or not register_token:
        raise ValueError("SAYA_REGISTER_URL and SAYA_REGISTER_TOKEN must be set together")
    return Registration(
        url.rstrip("/") + "/register", register_token,
        env.get("SAYA_ADVERTISE_URL") or f"http://127.0.0.1:{port}", token,
    )


class Registrar:
    def __init__(self, registration: Registration) -> None:
        self.registration = registration
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, name="saya-register", daemon=True)

    def request(self, method: str) -> float:
        registration = self.registration
        body: dict[str, Any] = {"kind": "adapter", "id": "chatplus"}
        if method == "POST":
            body.update(url=registration.url, token=registration.token)
        try:
            response = httpx.request(
                method, registration.register_url, json=body,
                headers={"Authorization": f"Bearer {registration.register_token}"},
                timeout=10, follow_redirects=False,
            )
            if not response.is_success:
                LOG.warning("Saya registration refused with HTTP %d", response.status_code)
                return 30
            if method == "POST":
                interval = response.json().get("interval", 30)
                if (
                    isinstance(interval, (int, float)) and not isinstance(interval, bool)
                    and math.isfinite(interval) and interval > 0
                ):
                    return float(interval)
        except (httpx.HTTPError, ValueError, AttributeError):
            LOG.warning("Saya registration unavailable")
        return 30

    def run(self) -> None:
        while not self.stopped.is_set():
            interval = self.request("POST")
            if self.stopped.wait(interval):
                return

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stopped.set()
        self.thread.join()
        self.request("DELETE")
