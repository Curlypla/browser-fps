"""Expand the Chrome store to every stable release.

Google ships a stable version for Windows/Mac that never gets a Linux build
(e.g. 149.0.7827.156/.157), so it can't be captured directly. But the network
fingerprint (ja4, akamai, peetprint, header orders) is determined by the
Chromium *milestone*, not the patch — verified empirically: across milestones
135-151 every captured patch in a milestone shares one identical fingerprint.

So for every stable version with no real capture we inherit the fingerprint of a
measured version from the same milestone, flagged `fingerprint_source:inherited`
(+ `inherited_from`). Measured entries are flagged `measured` and always win.
"""
import json
import re
import urllib.request

VH = ("https://versionhistory.googleapis.com/v1/chrome/platforms/all/"
      "channels/stable/versions")


def stable_versions():
    req = urllib.request.Request(VH, headers={"User-Agent": "browser-fps-bot"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read())
    return sorted({v["version"] for v in d.get("versions", [])
                   if re.match(r"^\d+\.\d+\.\d+\.\d+$", v.get("version", ""))})


def _vk(v):
    return [int(x) for x in v.split(".")]


def expand_chrome(store):
    chrome = store.get("browsers", {}).get("chrome")
    if not chrome:
        return 0
    # fetch the full release list first — if it fails, leave the store untouched
    # rather than pruning inherited rows we can't rebuild.
    try:
        allv = stable_versions()
    except Exception:  # noqa: BLE001
        return 0

    # drop previously-inherited rows, keep only real measurements
    for v in list(chrome):
        if chrome[v].get("fingerprint_source", "measured") != "measured":
            del chrome[v]
    for e in chrome.values():
        e.setdefault("fingerprint_source", "measured")

    # representative measured version per milestone (highest patch)
    repr_by_mil = {}
    for v, e in chrome.items():
        if not (e.get("h2") or {}).get("ja4"):
            continue
        m = int(v.split(".")[0])
        if m not in repr_by_mil or _vk(v) > _vk(repr_by_mil[m]):
            repr_by_mil[m] = v

    added = 0
    for v in allv:
        m = int(v.split(".")[0])
        src = repr_by_mil.get(m)
        if not src or v in chrome:
            continue
        # lightweight pointer only — the full fingerprint lives on the measured
        # `inherited_from` entry (and is denormalized into the sqlite db). This
        # keeps the json small even with thousands of releases.
        chrome[v] = {
            "engine": "chromium",
            "channel": chrome[src].get("channel", "stable"),
            "major": v.split(".")[0],
            "fingerprint_source": "inherited",
            "inherited_from": src,
        }
        added += 1
    return added


if __name__ == "__main__":
    import sys
    s = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "data/fingerprints.json"))
    print("added", expand_chrome(s), "inherited chrome versions")
