"""Decode a gRPC-framed protobuf message into a raw field tree (analysis only).

Usage:
    python decode_grpc.py <base64-of-grpc-frame>     # 1B flag + 4B len + protobuf
    python decode_grpc.py --raw <base64-of-protobuf> # protobuf without the 5B gRPC frame

Prints a nested field tree: field number, wire type, and a best-effort value
(varint, length-delimited bytes shown as text/hex/nested-message guess, fixed32/64).
This is for reverse-engineering notification wire bytes; it does NOT prove a schema.
Wire types: 0=varint 1=fixed64 2=len-delimited 5=fixed32. Anything else => not protobuf
at that offset (likely base64 text or framing mismatch).
"""
from __future__ import annotations

import base64
import sys
from typing import cast


def _varint(b: bytes, p: int) -> tuple[int, int]:
    r = 0
    s = 0
    while p < len(b):
        x = b[p]
        p += 1
        r |= (x & 0x7F) << s
        if not (x & 0x80):
            return r, p
        s += 7
    raise ValueError("truncated varint")


def parse(b: bytes, depth: int = 0) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    p = 0
    while p < len(b):
        tag, p = _varint(b, p)
        field, wt = tag >> 3, tag & 7
        if wt == 0:
            v, p = _varint(b, p)
            out.append({"f": field, "wt": wt, "v": v})
        elif wt == 2:
            ln, p = _varint(b, p)
            chunk = b[p : p + ln]
            p += ln
            node: dict[str, object] = {"f": field, "wt": wt, "len": ln}
            try:
                txt = chunk.decode("utf-8")
                if txt.isprintable() or all(c in "\r\n\t " for c in txt if not c.isprintable()):
                    node["text"] = txt[:120]
            except UnicodeDecodeError:
                pass
            if ln and depth < 6:
                try:
                    nested = parse(chunk, depth + 1)
                    sane = nested and all(
                        0 < cast(int, n["f"]) < 536870912
                        and n["wt"] in (0, 1, 2, 5)
                        for n in nested
                    )
                    if sane:
                        node["msg"] = nested
                except (ValueError, IndexError):
                    pass
            if "text" not in node and "msg" not in node:
                node["hex"] = chunk[:48].hex()
            out.append(node)
        elif wt == 5:
            out.append({"f": field, "wt": wt, "v": int.from_bytes(b[p : p + 4], "little")})
            p += 4
        elif wt == 1:
            out.append({"f": field, "wt": wt, "v": int.from_bytes(b[p : p + 8], "little")})
            p += 8
        else:
            raise ValueError(f"bad wire type {wt} at {p} (not protobuf here)")
    return out


def main(argv: list[str]) -> int:
    raw_only = "--raw" in argv
    args = [a for a in argv[1:] if a != "--raw"]
    if not args:
        print(__doc__)
        return 2
    data = base64.b64decode(args[0])
    if not raw_only:
        flag = data[0]
        mlen = int.from_bytes(data[1:5], "big")
        print(f"grpc frame: compressed_flag={flag} msg_len={mlen} total={len(data)}")
        data = data[5 : 5 + mlen]
    import json

    print(json.dumps(parse(data), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
