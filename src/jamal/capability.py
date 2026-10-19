"""Explicit caller-invoked capability query using recovered schema and route.

Never query automatically or silently select an RCS versus XMS sending route.
"""


async def query(request: object) -> object:
    """Query exact caller-supplied identifiers after schema/route validation."""
    raise NotImplementedError("capability query is not implemented in scaffold")
