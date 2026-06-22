"""Install one browser version and capture its fingerprint.

Invoked per matrix entry by the workflow. Installs from the given download URL,
then runs the engine-appropriate capture backend, writing results/<key>.json.
"""
import argparse
import json
import os
import subprocess
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--kind", required=True)
    ap.add_argument("--engine", required=True)
    ap.add_argument("--binary", required=True)
    ap.add_argument("--captured-at", default="")
    args = ap.parse_args()

    key = "%s__%s" % (args.browser, args.version.replace("/", "_"))
    out = os.path.join("results", key + ".json")
    os.makedirs("results", exist_ok=True)

    import sources
    entry = {"version": args.version, "url": args.url, "kind": args.kind,
             "engine": args.engine, "binary": args.binary}
    try:
        binary = sources.install(entry)
    except Exception as e:  # noqa: BLE001
        with open(out, "w") as f:
            json.dump({"browser": args.browser, "version": args.version,
                       "engine": args.engine, "captured_at": args.captured_at,
                       "errors": ["install: %s" % e], "h2": None, "h3": None}, f, indent=2)
        print("install failed:", e)
        return 0  # record the failure, don't fail the job

    backend = "capture_firefox.py" if args.engine == "gecko" else "capture_chromium.py"
    cmd = [sys.executable, os.path.join(os.path.dirname(__file__), backend),
           "--binary", binary, "--browser", args.browser, "--version", args.version,
           "--out", out, "--captured-at", args.captured_at]
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd)
    if not os.path.exists(out):
        with open(out, "w") as f:
            json.dump({"browser": args.browser, "version": args.version,
                       "engine": args.engine, "captured_at": args.captured_at,
                       "errors": ["capture backend produced no output (rc=%d)" % r.returncode],
                       "h2": None, "h3": None}, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
