"""Capture the network fingerprint of a chromium-based browser (Chrome / Edge /
Brave) by driving it through the DevTools Protocol and reading what the
fingerprinting endpoints report back.

HTTP/2 fingerprint  -> https://tls.peet.ws/api/all
HTTP/3 (QUIC) fp    -> https://quic.tools.scrapfly.io/api/fp/quic  (forced over QUIC)

Output: a single JSON object written to --out describing the run.
"""
import argparse
import base64
import json
import os
import time

import cdp as cdplib

PEET_BASE = "https://tls.peet.ws/"
PEET_API = "https://tls.peet.ws/api/all"
QUIC_HOST = "quic.tools.scrapfly.io"
QUIC_API = "https://quic.tools.scrapfly.io/api/fp/quic"


def _drain(cdp, seconds=0.3):
    end = time.time() + seconds
    cdp.ws.settimeout(0.2)
    while time.time() < end:
        try:
            cdp.ws.recv()
        except Exception:  # noqa: BLE001
            break
    cdp.ws.settimeout(60)


def get_body(cdp, trigger, url_match, timeout=45):
    """Run trigger() then collect the response body + protocol for the first
    network request whose URL matches url_match."""
    target = None
    protocol = None
    trigger()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            msg = json.loads(cdp.ws.recv())
        except Exception:  # noqa: BLE001
            continue
        m = msg.get("method")
        p = msg.get("params", {})
        if m == "Network.requestWillBeSent" and target is None:
            if url_match(p.get("request", {}).get("url", "")):
                target = p.get("requestId")
        elif m == "Network.responseReceived" and p.get("requestId") == target:
            protocol = p.get("response", {}).get("protocol")
        elif m == "Network.loadingFinished" and p.get("requestId") == target:
            res = cdp.call("Network.getResponseBody", {"requestId": target})
            body = res.get("body", "")
            if res.get("base64Encoded"):
                body = base64.b64decode(body).decode("utf-8", "replace")
            return body, protocol
        elif m == "Network.loadingFailed" and p.get("requestId") == target:
            raise RuntimeError("loadingFailed: %s" % p.get("errorText"))
    raise TimeoutError("no response body captured")


def header_keys(peet_json):
    """Exact ordered list of header keys (incl. pseudo-headers) Chrome emitted."""
    h2 = peet_json.get("http2", {})
    for fr in h2.get("sent_frames", []):
        if fr.get("frame_type") == "HEADERS":
            keys = []
            for h in fr.get("headers", []):
                # entries look like ":method: GET" or "user-agent: ..."
                if h.startswith(":"):
                    keys.append(":" + h.split(": ", 1)[0].lstrip(":"))
                else:
                    keys.append(h.split(": ", 1)[0])
            # collapse consecutive duplicate keys (e.g. multiple cookie lines)
            collapsed = []
            for k in keys:
                if not collapsed or collapsed[-1] != k:
                    collapsed.append(k)
            return collapsed
    # http/1.1 fallback
    if "http1" in peet_json:
        return [h.split(":", 1)[0] for h in peet_json["http1"].get("headers", [])]
    return []


def find_ja4(obj):
    """Recursively pull ja4 / ja4_r style values out of an arbitrary json."""
    out = {}
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                kl = str(k).lower()
                if isinstance(v, (str, int)) and ("ja4" in kl or "ja3" in kl or "fingerprint" in kl):
                    out.setdefault(kl, v)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    return out


def headless_flags(version):
    try:
        major = int(version.split(".")[0])
    except Exception:  # noqa: BLE001
        major = 999
    # --headless=new landed in Chrome 109; older builds use legacy --headless
    return ["--headless=new"] if major >= 109 else ["--headless"]


def capture_h2(binary, port, profile, hflags):
    proc = cdplib.launch(binary, port, extra_flags=list(hflags), user_data_dir=profile)
    cdp = cdplib.CDP(port)
    try:
        cdp.connect(timeout=40)
        cdp.call("Network.enable")
        cdp.call("Page.enable")
        # Enrich the header set so the captured header order is as rich as
        # possible: cookies add a `cookie` header, the warm-up navigation makes
        # the api request carry a `referer`.
        for name in ("fp_session", "fp_consent", "fp_prefs", "fp_id"):
            try:
                cdp.call("Network.setCookie", {
                    "name": name, "value": "1",
                    "domain": "tls.peet.ws", "path": "/", "secure": True,
                })
            except Exception:  # noqa: BLE001
                pass
        # warm up base page
        get_body(cdp, lambda: cdp.send("Page.navigate", {"url": PEET_BASE}),
                 lambda u: u.rstrip("/") == PEET_BASE.rstrip("/"), timeout=30)
        _drain(cdp, 0.5)
        # navigate to the api FROM the page so a referer is attached
        body, proto = get_body(
            cdp,
            lambda: cdp.send("Runtime.evaluate", {"expression": "location.href=%r" % PEET_API}),
            lambda u: u == PEET_API, timeout=40,
        )
        data = json.loads(body)
        tls = data.get("tls", {})
        h2 = data.get("http2", {})
        result = {
            "protocol": data.get("http_version") or proto,
            "user_agent": data.get("user_agent"),
            "ja3": tls.get("ja3"),
            "ja3_hash": tls.get("ja3_hash"),
            "ja4": tls.get("ja4"),
            "ja4_r": tls.get("ja4_r"),
            "peetprint": tls.get("peetprint"),
            "peetprint_hash": tls.get("peetprint_hash"),
            "akamai_fingerprint": h2.get("akamai_fingerprint"),
            "akamai_fingerprint_hash": h2.get("akamai_fingerprint_hash"),
            "header_order": header_keys(data),
            "raw_tls_version": tls.get("tls_version_negotiated"),
        }
        # POST: submit a real form so we capture the (different) header order a
        # browser emits for POST navigations (adds content-type/content-length).
        try:
            _drain(cdp, 0.4)
            post_js = (
                "var f=document.createElement('form');f.method='POST';"
                "f.action=%r;var i=document.createElement('input');"
                "i.name='fp';i.value='1';f.appendChild(i);"
                "document.body.appendChild(f);f.submit();" % PEET_API
            )
            pbody, _ = get_body(
                cdp, lambda: cdp.send("Runtime.evaluate", {"expression": post_js}),
                lambda u: u == PEET_API, timeout=40,
            )
            pdata = json.loads(pbody)
            result["method_post"] = pdata.get("method")
            result["header_order_post"] = header_keys(pdata)
        except Exception as e:  # noqa: BLE001
            result["header_order_post"] = None
            result["post_error"] = str(e)
        return result
    finally:
        cdp.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()


def capture_h3(binary, port, profile, hflags):
    flags = list(hflags) + [
        "--enable-quic",
        "--origin-to-force-quic-on=%s:443" % QUIC_HOST,
    ]
    proc = cdplib.launch(binary, port, extra_flags=flags, user_data_dir=profile)
    cdp = cdplib.CDP(port)
    try:
        cdp.connect(timeout=40)
        cdp.call("Network.enable")
        cdp.call("Page.enable")
        body, proto = get_body(
            cdp, lambda: cdp.send("Page.navigate", {"url": QUIC_API}),
            lambda u: u.startswith(QUIC_API), timeout=45,
        )
        try:
            data = json.loads(body)
        except Exception:  # noqa: BLE001
            data = {"_raw_text": body[:2000]}
        ja4 = find_ja4(data)
        return {
            "protocol": proto or data.get("protocol"),
            "http3_supported": data.get("http3_supported"),
            "ja4": ja4.get("ja4"),
            "ja4_r": ja4.get("ja4_r"),
            "extracted": ja4,
            "raw": data,
        }
    finally:
        cdp.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True)
    ap.add_argument("--browser", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--captured-at", default="")
    args = ap.parse_args()

    result = {
        "browser": args.browser,
        "version": args.version,
        "major": args.version.split(".")[0],
        "engine": "chromium",
        "captured_at": args.captured_at,
        "errors": [],
    }
    hflags = headless_flags(args.version)
    try:
        result["h2"] = capture_h2(args.binary, 9222, "/tmp/prof-h2", hflags)
        result["user_agent"] = result["h2"].get("user_agent")
    except Exception as e:  # noqa: BLE001
        result["errors"].append("h2: %s" % e)
        result["h2"] = None
    try:
        result["h3"] = capture_h3(args.binary, 9333, "/tmp/prof-h3", hflags)
    except Exception as e:  # noqa: BLE001
        result["errors"].append("h3: %s" % e)
        result["h3"] = None

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps({k: result[k] for k in ("browser", "version", "errors")}))
    # success if at least the h2 fingerprint was captured
    return 0 if result["h2"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
