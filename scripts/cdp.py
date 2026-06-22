"""Minimal Chrome DevTools Protocol (CDP) client over a raw websocket.

Avoids any dependency on a matching chromedriver, so it works across the full
range of Chrome / Edge / Brave versions we drive. Only needs `websocket-client`.
"""
import json
import subprocess
import time
import urllib.request

import websocket  # websocket-client


class CDP:
    def __init__(self, port):
        self.port = port
        self.ws = None
        self._id = 0

    # ---- target / connection ----------------------------------------------
    def _http_json(self, path):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def connect(self, timeout=30):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                targets = self._http_json("/json")
                pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
                if pages:
                    self.ws = websocket.create_connection(
                        pages[0]["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024
                    )
                    self.ws.settimeout(60)
                    return self
            except Exception as e:  # noqa: BLE001
                last = e
            time.sleep(0.4)
        raise RuntimeError("could not connect to CDP: %s" % last)

    # ---- command / event plumbing -----------------------------------------
    def send(self, method, params=None):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        return mid

    def call(self, method, params=None, timeout=30):
        mid = self.send(method, params)
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("%s -> %s" % (method, msg["error"]))
                return msg.get("result", {})
            # ignore events while waiting for our reply
        raise TimeoutError("timeout waiting for %s" % method)

    def wait_event(self, method, predicate=None, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = json.loads(self.ws.recv())
            except websocket.WebSocketTimeoutException:
                continue
            if msg.get("method") == method:
                if predicate is None or predicate(msg.get("params", {})):
                    return msg.get("params", {})
        raise TimeoutError("timeout waiting for event %s" % method)

    def close(self):
        try:
            if self.ws:
                self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def launch(binary, port, extra_flags=None, user_data_dir=None):
    """Launch a chromium-based browser with remote debugging enabled."""
    flags = [
        binary,
        "--remote-debugging-port=%d" % port,
        "--remote-debugging-address=127.0.0.1",
        # Chrome 111+ rejects DevTools websocket connections unless the origin
        # is explicitly allowed.
        "--remote-allow-origins=*",
        "--user-data-dir=%s" % (user_data_dir or "/tmp/cdp-profile-%d" % port),
        "--no-sandbox",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-extensions",
        "--disable-background-networking",
        "--window-size=1280,800",
    ]
    flags += extra_flags or []
    flags.append("about:blank")
    proc = subprocess.Popen(flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc
