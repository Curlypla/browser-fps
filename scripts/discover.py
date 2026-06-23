"""Compute which (browser, version) pairs still need fingerprinting.

Reads the existing data store, lists available versions for each requested
browser, and emits a GitHub Actions matrix of the newest pending versions
(bounded by --batch so a single run stays reasonable). Backfill of older
versions happens automatically over successive runs.
"""
import argparse
import json
import os

import sources

STORE = "data/fingerprints.json"


def load_store():
    if os.path.exists(STORE):
        with open(STORE) as f:
            return json.load(f)
    return {"browsers": {}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--browsers", default="chrome")
    ap.add_argument("--batch", type=int, default=12, help="max versions per browser per run")
    ap.add_argument("--retry-errors", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    store = load_store().get("browsers", {})
    include = []
    summary = []

    for browser in [b.strip() for b in args.browsers.split(",") if b.strip()]:
        have = store.get(browser, {})
        try:
            versions = sources.list_versions(browser, token)
        except Exception as e:  # noqa: BLE001
            summary.append("%s: list failed: %s" % (browser, e))
            continue
        pending = []
        for entry in versions:
            v = entry["version"]
            existing = have.get(v)
            done = existing is not None and not (existing.get("errors") and args.retry_errors)
            # treat a record without a captured h2 as not-done when retrying errors
            if existing and args.retry_errors and not existing.get("h2"):
                done = False
            # backfill: re-capture once if the record predates the current
            # per-initiator header-order capture (orders_kind v2).
            if existing and existing.get("h2") and existing["h2"].get("orders_kind") != "v3":
                done = False
            if not done:
                pending.append(entry)
        take = pending[: args.batch]
        for e in take:
            include.append({
                "browser": browser, "version": e["version"], "url": e["url"],
                "kind": e["kind"], "engine": e["engine"], "binary": e["binary"],
            })
        summary.append("%s: %d available, %d pending, %d queued"
                       % (browser, len(versions), len(pending), len(take)))

    matrix = {"include": include}
    print("\n".join(summary))
    print("queued total:", len(include))
    if args.out:
        with open(args.out, "a") as f:
            f.write("matrix=%s\n" % json.dumps(matrix))
            f.write("count=%d\n" % len(include))
            f.write("empty=%s\n" % ("true" if not include else "false"))


if __name__ == "__main__":
    main()
