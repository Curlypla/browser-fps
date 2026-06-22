"""Browser source definitions.

Each browser exposes:
  list_versions()  -> list of {"version", "url", "kind"} newest-first
  install(entry)   -> absolute path to the launchable binary

`kind` is "deb" | "tar" so the installer knows how to unpack.
Engine is "chromium" or "gecko" (selects the capture backend).
"""
import json
import os
import re
import subprocess
import urllib.request

UA = {"User-Agent": "browser-fps-bot"}


def _get(url, token=None, accept=None):
    headers = dict(UA)
    if token:
        headers["Authorization"] = "Bearer " + token
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _gh_paginate(url, token, max_pages=15):
    items = []
    for page in range(1, max_pages + 1):
        sep = "&" if "?" in url else "?"
        data = json.loads(_get("%s%spage=%d&per_page=100" % (url, sep, page), token))
        if not data:
            break
        items.extend(data)
        if len(data) < 100:
            break
    return items


# --------------------------------------------------------------------------- #
# Chrome  (archived debs from NDViet/google-chrome-stable releases)
# --------------------------------------------------------------------------- #
def chrome_versions(token=None):
    rel = _gh_paginate("https://api.github.com/repos/NDViet/google-chrome-stable/releases", token)
    out = []
    for r in rel:
        if r.get("draft"):
            continue
        tag = r["tag_name"]  # e.g. 149.0.7827.155-1
        if not re.match(r"^\d+\.\d+\.\d+\.\d+", tag):
            continue
        url = ("https://github.com/NDViet/google-chrome-stable/releases/download/"
               "%s/google-chrome-stable_%s_amd64.deb" % (tag, tag))
        out.append({"version": tag, "url": url, "kind": "deb",
                    "engine": "chromium", "binary": "/opt/google/chrome/google-chrome"})
    return out


# --------------------------------------------------------------------------- #
# Edge  (versioned debs from the Microsoft package pool)
# --------------------------------------------------------------------------- #
def edge_versions(token=None):
    html = _get("https://packages.microsoft.com/repos/edge/pool/main/m/"
                "microsoft-edge-stable/").decode("utf-8", "replace")
    seen = {}
    for m in re.finditer(r"microsoft-edge-stable_([0-9.]+-1)_amd64\.deb", html):
        ver = m.group(1)
        url = ("https://packages.microsoft.com/repos/edge/pool/main/m/"
               "microsoft-edge-stable/microsoft-edge-stable_%s_amd64.deb" % ver)
        seen[ver] = url
    out = [{"version": v, "url": u, "kind": "deb", "engine": "chromium",
            "binary": "/opt/microsoft/msedge/microsoft-edge"} for v, u in seen.items()]
    out.sort(key=lambda e: _verkey(e["version"]), reverse=True)
    return out


# --------------------------------------------------------------------------- #
# Brave  (debs from brave/brave-browser GitHub releases)
# --------------------------------------------------------------------------- #
def brave_versions(token=None, max_pages=8):
    rel = _gh_paginate("https://api.github.com/repos/brave/brave-browser/releases",
                       token, max_pages=max_pages)
    out = []
    for r in rel:
        if r.get("draft"):
            continue
        # the brave-browser repo mixes Nightly/Beta/Release in one release feed;
        # the channel lives in the release name, not the prerelease flag.
        if not str(r.get("name", "")).strip().startswith("Release"):
            continue
        deb = next((a for a in r.get("assets", [])
                    if re.match(r"^brave-browser_[0-9.]+_amd64\.deb$", a["name"])), None)
        if not deb:
            continue
        ver = r["tag_name"].lstrip("v")
        cm = re.search(r"Chromium\s+([0-9.]+)", r.get("name", ""))
        out.append({"version": ver, "url": deb["browser_download_url"], "kind": "deb",
                    "engine": "chromium", "binary": "/opt/brave.com/brave/brave-browser",
                    "chromium": cm.group(1) if cm else None})
    return out


# --------------------------------------------------------------------------- #
# Firefox  (tarballs from ftp.mozilla.org, version list from product-details)
# --------------------------------------------------------------------------- #
def firefox_versions(token=None):
    data = json.loads(_get("https://product-details.mozilla.org/1.0/firefox.json"))
    out = []
    for _, rel in data.get("releases", {}).items():
        cat = rel.get("category", "")
        if cat not in ("major", "stability"):
            continue
        ver = rel["version"]
        if not re.match(r"^\d+\.\d+(\.\d+)?$", ver):
            continue
        url = ("https://ftp.mozilla.org/pub/firefox/releases/%s/linux-x86_64/"
               "en-US/firefox-%s.tar.xz" % (ver, ver))
        out.append({"version": ver, "url": url, "kind": "tar", "engine": "gecko",
                    "binary": "/opt/firefox/firefox"})
    out.sort(key=lambda e: _verkey(e["version"]), reverse=True)
    return out


def _verkey(v):
    parts = re.split(r"[.\-]", v)
    return [int(p) if p.isdigit() else 0 for p in parts]


BROWSERS = {
    "chrome": chrome_versions,
    "edge": edge_versions,
    "brave": brave_versions,
    "firefox": firefox_versions,
}


def list_versions(browser, token=None):
    return BROWSERS[browser](token)


# --------------------------------------------------------------------------- #
# Installation
# --------------------------------------------------------------------------- #
def _run(cmd):
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def download(url, dest):
    print("downloading", url, flush=True)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    return dest


def install(entry, workdir="/tmp/bfp"):
    os.makedirs(workdir, exist_ok=True)
    if entry["kind"] == "deb":
        deb = download(entry["url"], os.path.join(workdir, "browser.deb"))
        # dpkg then resolve deps
        r = subprocess.run(["sudo", "dpkg", "-i", deb])
        if r.returncode != 0:
            _run(["sudo", "apt-get", "-f", "install", "-y"])
        return entry["binary"]
    if entry["kind"] == "tar":
        tar = download(entry["url"], os.path.join(workdir, "browser.tar.xz"))
        _run(["sudo", "rm", "-rf", "/opt/firefox"])
        _run(["sudo", "tar", "-C", "/opt", "-xf", tar])
        return entry["binary"]
    raise ValueError("unknown kind %s" % entry["kind"])


if __name__ == "__main__":
    import sys
    tok = os.environ.get("GITHUB_TOKEN")
    vs = list_versions(sys.argv[1], tok)
    print(len(vs), "versions; newest:", vs[0] if vs else None)
    for e in vs[:5]:
        print(" ", e["version"])
