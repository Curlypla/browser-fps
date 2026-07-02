"""Capture the network fingerprint of Firefox via Selenium + geckodriver.

Firefox does not expose the chromium DevTools protocol, so we drive it with
geckodriver. The JSON viewer is disabled so a navigation to the api returns raw
text we can read straight off the page (preserving the navigation header order).
"""
import argparse
import json
import os
import shutil
import time

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service

from capture_chromium import (header_keys, find_ja4, ja4_quic_from_tls,
                              quic_tp_from_raw, PEET_API, QUIC_API)


def make_driver(binary):
    opts = Options()
    opts.binary_location = binary
    opts.add_argument("-headless")
    # raw JSON instead of the interactive viewer
    opts.set_preference("devtools.jsonview.enabled", False)
    # make sure HTTP/3 is allowed
    opts.set_preference("network.http.http3.enable", True)
    # add a cookie-rich, referer-bearing context later via JS
    driver_path = (os.environ.get("GECKODRIVER") or shutil.which("geckodriver")
                   or "/usr/local/bin/geckodriver")
    service = Service(executable_path=driver_path)
    return webdriver.Firefox(options=opts, service=service)


def body_text(driver, url):
    driver.get(url)
    time.sleep(1.0)
    return driver.find_element("tag name", "pre").text if _has_pre(driver) else \
        driver.execute_script("return document.body.innerText")


def _has_pre(driver):
    try:
        driver.find_element("tag name", "pre")
        return True
    except Exception:  # noqa: BLE001
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True)
    ap.add_argument("--browser", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--captured-at", default="")
    args = ap.parse_args()

    result = {"browser": args.browser, "version": args.version,
              "major": args.version.split(".")[0], "engine": "gecko",
              "captured_at": args.captured_at, "errors": []}
    driver = None
    try:
        driver = make_driver(args.binary)
        # warm up + set cookies to enrich header order
        driver.get("https://tls.peet.ws/")
        for n in ("fp_session", "fp_consent", "fp_prefs"):
            driver.add_cookie({"name": n, "value": "1", "domain": "tls.peet.ws"})
        data = json.loads(body_text(driver, PEET_API))
        tls = data.get("tls", {})
        h2 = data.get("http2", {})
        result["user_agent"] = data.get("user_agent")
        orders = {"navigate": header_keys(data)}

        # Firefox has no CDP, so we can only read response bodies for requests
        # JS can read: same-origin fetch GET/POST. Subresource (script/image)
        # orders aren't readable without a proxy, so they're omitted here.
        def fetch_order(key, opts):
            try:
                txt = driver.execute_async_script(
                    "var cb=arguments[arguments.length-1];"
                    "fetch(arguments[0],%s).then(r=>r.text()).then(t=>cb(t))"
                    ".catch(e=>cb('ERR:'+e));" % opts, PEET_API + "?k=" + key)
                orders[key] = header_keys(json.loads(txt))
            except Exception as e:  # noqa: BLE001
                orders[key] = None
                orders.setdefault("_errors", {})[key] = str(e)

        fetch_order("xhr_get", "{credentials:'include'}")
        fetch_order("xhr_get_nocors", "{mode:'no-cors',credentials:'include'}")
        fetch_order("xhr_post", "{method:'POST',headers:{'Content-Type':'application/json'},"
                                "body:'{\"fp\":1}',credentials:'include'}")

        result["h2"] = {
            "protocol": data.get("http_version"),
            "user_agent": data.get("user_agent"),
            "ja3": tls.get("ja3"), "ja3_hash": tls.get("ja3_hash"),
            "ja4": tls.get("ja4"), "ja4_r": tls.get("ja4_r"),
            "peetprint": tls.get("peetprint"), "peetprint_hash": tls.get("peetprint_hash"),
            "akamai_fingerprint": h2.get("akamai_fingerprint"),
            "akamai_fingerprint_hash": h2.get("akamai_fingerprint_hash"),
            "raw_tls_version": tls.get("tls_version_negotiated"),
            "orders_kind": "v4",
            "header_orders": orders,
        }
    except Exception as e:  # noqa: BLE001
        result["errors"].append("h2: %s" % e)
        result["h2"] = None

    # HTTP/3: Firefox upgrades via Alt-Svc, so hit the endpoint twice.
    try:
        if driver is None:
            driver = make_driver(args.binary)
        # Firefox upgrades to QUIC via Alt-Svc on a later request, which can take
        # several tries — poll until it reports an h3 fingerprint.
        qd = {}
        for attempt in range(8):
            try:
                qd = json.loads(body_text(driver, QUIC_API))
            except Exception:  # noqa: BLE001
                qd = {}
            if find_ja4(qd).get("ja4"):
                break
            time.sleep(3.0)
        ja4 = find_ja4(qd)
        r_ja4, r_ja4r = ja4_quic_from_tls(qd)
        tp, tpr = quic_tp_from_raw(qd)
        result["h3"] = {"ja4": r_ja4 or qd.get("ja4") or ja4.get("ja4"),
                        "ja4_r": r_ja4r or qd.get("ja4_r") or ja4.get("ja4_r"),
                        "h3_text": qd.get("h3_text"),
                        "quic_tp": tp, "quic_tp_r": tpr}
        result["h3_raw"] = qd
    except Exception as e:  # noqa: BLE001
        result["errors"].append("h3: %s" % e)
        result["h3"] = None
    finally:
        if driver:
            driver.quit()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps({"browser": args.browser, "version": args.version, "errors": result["errors"]}))
    return 0 if result["h2"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
