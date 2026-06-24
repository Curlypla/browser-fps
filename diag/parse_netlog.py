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


def _field(h, name):
    return next((x.split(": ", 1)[1] for x in h if x.startswith(name + ": ")), "")


def main(path, host=None):
    d = json.load(open(path))
    types = {v: k for k, v in d.get("constants", {}).get("logEventTypes", {}).items()}
    found = []
    for e in d.get("events", []):
        p = e.get("params") or {}
        h = p.get("headers")
        if isinstance(h, list) and any(str(x).startswith(":method") for x in h):
            found.append((types.get(e.get("type"), str(e.get("type"))), h))
    # pin to the main-document request: GET to the probed host, path "/"
    for tname, h in found:
        if _field(h, ":path") == "/" and (not host or host in _field(h, ":authority")):
            print("event:", tname)
            print("order:", keys(h))
            return
    print("no main-document header-send event found (host=%s)" % host)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
