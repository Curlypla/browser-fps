"""Extract the ordered request header list Chrome logged in a net-log, for the
main-document request. Works for both h2 (HTTP2_SESSION_SEND_HEADERS) and h3
(HTTP3_HEADERS_SENT / QUIC...HEADERS) since both log params.headers in order."""
import json
import sys


def keys(hlist):
    out = []
    for h in hlist:
        if h.startswith(":"):
            out.append(":" + h.split(": ", 1)[0].lstrip(":"))
        else:
            out.append(h.split(": ", 1)[0])
    return out


def main(path):
    d = json.load(open(path))
    types = {v: k for k, v in d.get("constants", {}).get("logEventTypes", {}).items()}
    found = []
    for e in d.get("events", []):
        p = e.get("params") or {}
        h = p.get("headers")
        if isinstance(h, list) and any(str(x).startswith(":method") for x in h):
            tname = types.get(e.get("type"), str(e.get("type")))
            found.append((tname, h))
    # the main document is the first GET to path "/"
    for tname, h in found:
        if any(x in (":path: /", ":method: GET") for x in h):
            print("event:", tname)
            print("order:", keys(h))
            return
    if found:
        print("event:", found[0][0])
        print("order:", keys(found[0][1]))
    else:
        print("no header-send event found")


if __name__ == "__main__":
    main(sys.argv[1])
