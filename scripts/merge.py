"""Merge per-version capture results into the data store.

Maintains both representations the task asks for:
  data/fingerprints.json    canonical, human-diffable store
  data/fingerprints.sqlite  queryable database (rebuilt from the json)
"""
import argparse
import glob
import json
import os
import sqlite3

STORE = "data/fingerprints.json"
DB = "data/fingerprints.sqlite"


def load():
    if os.path.exists(STORE):
        with open(STORE) as f:
            return json.load(f)
    return {"schema": 1, "browsers": {}}


def build_sqlite(store):
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE fingerprints(
        browser TEXT, version TEXT, major INTEGER, engine TEXT, captured_at TEXT,
        user_agent TEXT,
        h2_ja4 TEXT, h2_ja4_r TEXT, h2_akamai TEXT, h2_akamai_hash TEXT,
        h2_peetprint TEXT, h2_peetprint_hash TEXT, h2_ja3 TEXT, h2_ja3_hash TEXT,
        h2_header_order TEXT, h2_header_order_post TEXT, h2_protocol TEXT,
        h3_ja4 TEXT, h3_ja4_r TEXT, h3_protocol TEXT, h3_supported TEXT,
        errors TEXT,
        PRIMARY KEY(browser, version))""")
    rows = []
    for browser, vers in store["browsers"].items():
        for version, r in vers.items():
            h2 = r.get("h2") or {}
            h3 = r.get("h3") or {}
            rows.append((
                browser, version, _major(version), r.get("engine"), r.get("captured_at"),
                r.get("user_agent"),
                h2.get("ja4"), h2.get("ja4_r"), h2.get("akamai_fingerprint"),
                h2.get("akamai_fingerprint_hash"), h2.get("peetprint"),
                h2.get("peetprint_hash"), h2.get("ja3"), h2.get("ja3_hash"),
                json.dumps(h2.get("header_order")),
                json.dumps(h2.get("header_order_post")), h2.get("protocol"),
                h3.get("ja4"), h3.get("ja4_r"), h3.get("protocol"),
                str(h3.get("http3_supported")),
                json.dumps(r.get("errors") or []),
            ))
    con.executemany("INSERT OR REPLACE INTO fingerprints VALUES (%s)"
                    % ",".join("?" * 22), rows)
    con.commit()
    con.close()
    return len(rows)


def _major(v):
    try:
        return int(v.split(".")[0])
    except Exception:  # noqa: BLE001
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--captured-at", default="")
    args = ap.parse_args()

    store = load()
    added = 0
    for path in sorted(glob.glob(os.path.join(args.results, "**", "*.json"), recursive=True)):
        with open(path) as f:
            try:
                r = json.load(f)
            except Exception:  # noqa: BLE001
                continue
        b, v = r.get("browser"), r.get("version")
        if not b or not v:
            continue
        store["browsers"].setdefault(b, {})
        rec = dict(r)
        rec.pop("browser", None)
        rec.pop("version", None)
        store["browsers"][b][v] = rec
        added += 1

    store["generated_at"] = args.captured_at
    counts = {b: len(v) for b, v in store["browsers"].items()}
    store["counts"] = counts

    os.makedirs("data", exist_ok=True)
    with open(STORE, "w") as f:
        json.dump(store, f, indent=2, sort_keys=True)
    n = build_sqlite(store)
    print("merged %d result file(s); store now has %d rows; counts=%s" % (added, n, counts))


if __name__ == "__main__":
    main()
