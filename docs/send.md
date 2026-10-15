# Send path (SendXms text and image)

Sending dispatches a real message over the network. Every send runs only behind an
explicit confirmation; the CLI enforces this with `--confirm-send`, and the library
`send_message` primitive documents the same requirement. The schema below was
recovered from one captured self-send and each builder is verified byte-exact
against that capture.

In the captured client both text and image go through `SendXms`; `SendRcs` was not
observed and is not encoded.

## Wire schema (SendXms)

Text request:

```text
SendXmsRequest {
  1: XmsText {
    1: recipient MSISDN (string)
    4: kind (varint, 1 for text)
    31: body (string, UTF-8)
  }
  10: client_ip (string)
}
```

Image request (after a file upload):

```text
SendXmsRequest {
  3: Mms {
    1: recipient MSISDN (string)
    31: caption (string, may be empty)
    33: FileInfo {
      1: size (varint)
      2: file_name (string)
      3: content_type (string)
      4: href (string, from the upload response)
    }
  }
  10: client_ip (string)
}
```

The server assigns the message id; the request carries none. A successful send
returns a non-empty response message.

## File upload (image prerequisite)

```text
POST <small_file_root>/oapi/v1/mms-file
  authorization: Bearer <file_bearer_token>
  content-type: multipart/form-data
  part "Content-Type" = <content_type>
  part "file"; filename=<name>; content-type=<content_type> = <bytes>
-> 201 { "file": { "contentType", "fileName", "href", "size" } }
```

The `href` becomes `FileInfo.href`. The upload authenticates with the
`file_bearer_token`, which is separate from the gRPC `access_token` and is not part
of the gRPC session export; an image send needs that token supplied explicitly.

## Library

```python
from chatplus_core.client import SubscribeClient

async with SubscribeClient(session, config=config) as client:
    await client.send_xms_text(device, "+8210...", "hello", client_ip="203.0.113.9")
    await client.send_xms_image(
        device, "+8210...", "pic.png", "image/png", data, file_token,
        small_file_root=session.roots["small_file"], caption="",
    )
```

- `send.build_xms_text_request` / `send.build_xms_mms_request`: pure encoders,
  byte-exact against the capture.
- `upload.upload_mms_file`: multipart upload returning a `FileRef`.
- `send.send_message`: low-level unary dispatch; owns no gate.

## CLI

```console
uv run chatplus-core send xms --credentials session.json --device device.json \
    --to +8210... --text "hello" [--config config.json] [--client-ip IP] --confirm-send

uv run chatplus-core send xms-image --credentials session.json --device device.json \
    --to +8210... --file pic.png --file-token-file ftok.txt \
    [--content-type image/png] [--caption ""] [--client-ip IP] --confirm-send
```

Without `--confirm-send` each command prints a dry-run summary to stderr and exits
non-zero without sending. `send xms-image` reads the `file_bearer_token` from
`--file-token-file` (never from the command line) and infers the content type from
the file name unless `--content-type` is given. `send rcs` is not available
(SendRcs was not captured).
