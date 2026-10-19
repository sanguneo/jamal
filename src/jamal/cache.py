"""Bridge to the official client's local Hive cache adapter.

The existing chatplus-core adapter decodes the desktop client's encrypted
folder, object and contact stores. It verifies each room's write target in
the desktop client before sending. This client calls that adapter over
loopback or Tailscale HTTPS; it never opens the client's files itself.
"""

from __future__ import annotations

from typing import Any

import httpx


class CacheSource:
    def __init__(self, client: httpx.AsyncClient, token: str) -> None:
        self.client = client
        self.token = token

    async def get(self, path: str, params: dict[str, str | int] | None = None) -> dict[str, Any]:
        response = await self.client.get(
            path, params=params, headers={"Authorization": f"Bearer {self.token}"}
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise ValueError("Cache reader returned an invalid answer")
        return data

    async def rooms(self) -> dict[str, Any]:
        return await self.get("/rooms")

    async def capabilities(self) -> dict[str, Any]:
        return await self.get("/capabilities")

    async def messages(
        self,
        room: str,
        limit: int,
        before: str | None,
    ) -> dict[str, Any]:
        params: dict[str, str | int] = {"roomId": room, "limit": limit}
        if before is not None:
            params["before"] = before
        return await self.get("/messages", params)

    async def send(self, room: str, text: str) -> httpx.Response:
        return await self.client.post(
            "/send", json={"roomId": room, "text": text},
            headers={"Authorization": f"Bearer {self.token}"},
        )

    async def upload(
        self, room: str, file_name: str, content_type: str, data: bytes,
    ) -> httpx.Response:
        return await self.client.post(
            "/upload", data={"roomId": room},
            files={"file": (file_name, data, content_type)},
            headers={"Authorization": f"Bearer {self.token}"},
        )

    async def media(self, attachment_id: str) -> httpx.Response:
        return await self.client.get(
            "/media",
            params={"id": attachment_id},
            headers={"Authorization": f"Bearer {self.token}"},
        )

    async def aclose(self) -> None:
        await self.client.aclose()
