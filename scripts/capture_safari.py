"""Capture the network fingerprint of Safari (WebKit) via safaridriver.

Safari has neither a headless mode nor the chromium DevTools protocol, so we
drive the real Safari preinstalled on the macOS runner through its built-in
WebDriver (safaridriver, enabled with `safaridriver --enable`). Only the
JS-readable request types are captured (navigate + same-origin fetch GET/POST),
since there's no way to read a subresource body without a proxy.

The Safari version isn't selectable: it's whatever ships with the runner image,
detected here from the user-agent and used as the store key.
"""
import argparse
import json
import os
import re
import time

from selenium import webdriver
from selenium.webdriver.safari.options import Options

from capture_chromium import (header_keys, header_values, find_ja4, ja4_quic_from_tls,
                              quic_tp_from_raw, PEET_API, PEET_BASE, QUIC_API, BL_API)


def safari_version(ua):
    m = re.search(r"Version/([0-9.]+) .*Safari", ua or "")
    return m.group(1) if m else None


def read_body(driver, url):
    driver.get(url)
    time.sleep(1.2)
    try:
        return driver.find_element("tag name", "pre").text
    except Exception:  # noqa: BLE001
        return driver.execute_script("return document.body.innerText")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", default="safari")
    ap.add_argument("--browser", default="safari")
    ap.add_argument("--version", required=True)  # placeholder (runner image)
    ap.add_argument("--image", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--captured-at", default="")
    args = ap.parse_args()

    result = {"browser": "safari", "version": args.version, "engine": "webkit",
              "runner_image": args.image or args.version,
              "captured_at": args.captured_at, "errors": []}
    driver = None
    try:
        driver = webdriver.Safari(options=Options())
        driver.set_page_load_timeout(45)

        # warm up the origin so we can set cookies + carry a referer
        driver.get(PEET_BASE)
        for n in ("fp_session", "fp_consent", "fp_prefs"):
            try:
                driver.add_cookie({"name": n, "value": "1", "domain": "tls.peet.ws"})
            except Exception:  # noqa: BLE001
                pass

        orders = {}

        def fetch_order(key, opts):
            try:
                txt = driver.execute_async_script(
                    "var cb=arguments[arguments.length-1];"
                    "fetch(arguments[0],%s).then(r=>r.text()).then(t=>cb(t))"
                    ".catch(e=>cb('ERR:'+e));" % opts, PEET_API + "?k=" + key)
                data = json.loads(txt)
                orders[key] = header_keys(data)
                return data
            except Exception as e:  # noqa: BLE001
                orders[key] = None
                orders.setdefault("_errors", {})[key] = str(e)
                return None

        # the main TLS/h2 fingerprint can come from any reflected request
        base = fetch_order("xhr_get", "{credentials:'include'}")
        fetch_order("xhr_get_nocors", "{mode:'no-cors',credentials:'include'}")
        fetch_order("xhr_post", "{method:'POST',headers:{'Content-Type':'application/json'},"
                                "body:'{\"fp\":1}',credentials:'include'}")

        # document navigation order (best-effort: Safari may render JSON inline)
        try:
            nav = json.loads(read_body(driver, PEET_API))
            orders["navigate"] = header_keys(nav)
            base = base or nav
        except Exception as e:  # noqa: BLE001
            orders.setdefault("_errors", {})["navigate"] = str(e)

        if not base:
            raise RuntimeError("no reflected response could be read")

        tls = base.get("tls", {})
        h2 = base.get("http2", {})
        result["version"] = safari_version(base.get("user_agent")) or args.version
        result["user_agent"] = base.get("user_agent")
        result["h2"] = {
            "protocol": base.get("http_version"),
            "user_agent": base.get("user_agent"),
            "ja3": tls.get("ja3"), "ja3_hash": tls.get("ja3_hash"),
            "ja4": tls.get("ja4"), "ja4_r": tls.get("ja4_r"),
            "peetprint": tls.get("peetprint"), "peetprint_hash": tls.get("peetprint_hash"),
            "akamai_fingerprint": h2.get("akamai_fingerprint"),
            "akamai_fingerprint_hash": h2.get("akamai_fingerprint_hash"),
            "raw_tls_version": tls.get("tls_version_negotiated"),
            "orders_kind": "v4",
            "header_orders": orders,
            "header_values": header_values(data),
        }
    except Exception as e:  # noqa: BLE001
        result["errors"].append("h2: %s" % e)
        result["h2"] = None

    # HTTP/3: Safari only upgrades to QUIC via Alt-Svc, i.e. on a *later* request
    # after it has seen the alt-svc header. That can take several tries, so we
    # poll the endpoint until it actually reports an h3 fingerprint.
    try:
        if driver is None:
            driver = webdriver.Safari(options=Options())
        qd = {}
        for attempt in range(8):
            try:
                qd = json.loads(read_body(driver, QUIC_API))
            except Exception:  # noqa: BLE001
                qd = {}
            if find_ja4(qd).get("ja4"):
                break
            time.sleep(3.0)
        ja4 = find_ja4(qd)
        r_ja4, r_ja4r = ja4_quic_from_tls(qd)
        tp, tpr = quic_tp_from_raw(qd)
        bl = {}
        for attempt in range(6):
            try:
                bl = json.loads(read_body(driver, BL_API))
            except Exception:  # noqa: BLE001
                bl = {}
            if bl.get("h3_text"):
                break
            time.sleep(3.0)
        result["h3"] = {"ja4": r_ja4 or bl.get("ja4") or qd.get("ja4") or ja4.get("ja4"),
                        "ja4_r": r_ja4r or bl.get("ja4_r") or qd.get("ja4_r") or ja4.get("ja4_r"),
                        "h3_text": qd.get("h3_text"),
                        "http3": bl.get("h3_text"),
                        "quic_tp": tp, "quic_tp_r": tpr}
        result["h3_raw"] = qd
        result["browserleaks"] = bl
    except Exception as e:  # noqa: BLE001
        result["errors"].append("h3: %s" % e)
        result["h3"] = None
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:  # noqa: BLE001
                pass

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps({"browser": "safari", "version": result["version"],
                      "errors": result["errors"]}))
    return 0 if result["h2"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
