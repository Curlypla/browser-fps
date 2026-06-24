"""Tiny ASGI app that records the wire order of the (regular) request headers
per HTTP version, so we can compare what Chrome sends over h2 vs h3.
(ASGI consumes the :pseudo-headers into scope.method/path, so only the regular
header order is observed here — which is exactly the question.)"""
import json

LOG = "/tmp/hits.jsonl"


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            m = await receive()
            if m["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif m["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    if scope["type"] != "http":
        return
    while True:
        m = await receive()
        if m["type"] == "http.request" and not m.get("more_body"):
            break
    rec = {
        "http_version": scope.get("http_version"),
        "path": scope.get("path"),
        "headers": [h[0].decode("latin1") for h in scope.get("headers", [])],
    }
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"ok"})
