"""Internal unary/stream byte contract, separate from protobuf serialization."""

from collections.abc import AsyncIterator, Mapping


class Transport:
    """Transport boundary; concrete adapters require verified route evidence."""

    async def unary(
        self, method: str, payload: bytes, metadata: Mapping[str, str], deadline: float
    ) -> bytes:
        """Return raw protobuf bytes; deadline is a timeout in seconds, not a timestamp."""
        raise NotImplementedError("unary transport is not implemented in scaffold")

    def server_stream(
        self, method: str, payload: bytes, metadata: Mapping[str, str]
    ) -> AsyncIterator[bytes]:
        """Return raw protobuf messages, without transport framing or decoding."""
        raise NotImplementedError("server-streaming transport is not implemented in scaffold")

    async def aclose(self) -> None:
        """Close owned resources, including any active RPC."""
        raise NotImplementedError("transport close is not implemented in scaffold")


class TransportOverflowError(Exception):
    """The bounded receive buffer filled; the subscription was cancelled, not resumed."""
