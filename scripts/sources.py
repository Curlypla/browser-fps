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
# Real Google Chrome debs for the current build of each channel. Chrome for
# Testing was tried here but rejected: for an identical version it emits a
# DIFFERENT ja4 / ja4_r (an extra TLS extension) and different header orders, so
# it is not a faithful stand-in for real Chrome (see .github/workflows/cft-vs-deb.yml).
GOOGLE_PACKAGES = "https://dl.google.com/linux/chrome/deb/dists/stable/main/binary-amd64/Packages"
GOOGLE_CHANNELS = {
    "google-chrome-stable": ("stable", "/opt/google/chrome/google-chrome"),
    "google-chrome-beta": ("beta", "/opt/google/chrome-beta/google-chrome-beta"),
    "google-chrome-unstable": ("dev", "/opt/google/chrome-unstable/google-chrome-unstable"),
}


def _google_channel_versions():
    out = []
    txt = _get(GOOGLE_PACKAGES).decode("utf-8", "replace")
    pkg = ver = fn = None
    for line in txt.splitlines() + [""]:
        if line.startswith("Package:"):
            pkg = line.split(":", 1)[1].strip()
        elif line.startswith("Version:"):
            ver = line.split(":", 1)[1].strip()
        elif line.startswith("Filename:"):
            fn = line.split(":", 1)[1].strip()
        elif not line.strip():
            if pkg in GOOGLE_CHANNELS and ver and fn:
                ch, binp = GOOGLE_CHANNELS[pkg]
                out.append((ch, ver, "https://dl.google.com/linux/chrome/deb/" + fn, binp))
            pkg = ver = fn = None
    return out


def chrome_versions(token=None):
    out, seen = [], set()
    # 1. NDViet archive = full history of real Google Chrome stable debs. Their
    #    tags carry a debian revision (`-1`) which isn't part of the official
    #    Chrome version, so we key on the clean version and keep the tag only to
    #    build the download URL.
    rel = _gh_paginate("https://api.github.com/repos/NDViet/google-chrome-stable/releases", token)
    for r in rel:
        if r.get("draft"):
            continue
        tag = r["tag_name"]  # e.g. 149.0.7827.155-1
        if not re.match(r"^\d+\.\d+\.\d+\.\d+", tag):
            continue
        clean = re.sub(r"-\d+$", "", tag)
        if clean in seen:
            continue
        seen.add(clean)
        url = ("https://github.com/NDViet/google-chrome-stable/releases/download/"
               "%s/google-chrome-stable_%s_amd64.deb" % (tag, tag))
        out.append({"version": clean, "url": url, "kind": "deb", "engine": "chromium",
                    "binary": "/opt/google/chrome/google-chrome",
                    "source": "ndviet", "channel": "stable"})
    # 2. the current build of each official channel (stable/beta/dev) — real
    #    Chrome, so faithful fingerprints. This covers the newest stable before
    #    NDViet archives it, plus the ahead-of-stable beta/dev versions (which on
    #    other platforms are already "stable"). Labelled with their channel.
    try:
        for ch, tagver, url, binp in _google_channel_versions():
            clean = re.sub(r"-\d+$", "", tagver)
            if clean in seen:
                continue
            seen.add(clean)
            out.append({"version": clean, "url": url, "kind": "deb", "engine": "chromium",
                        "binary": binp, "source": "google", "channel": ch})
    except Exception:  # noqa: BLE001
        pass
    out.sort(key=lambda e: _verkey(e["version"]), reverse=True)
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
                    "engine": "chromium", "binary": "/usr/bin/brave-browser",
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


# --------------------------------------------------------------------------- #
# Safari  (not downloadable — its version is whatever ships with the macOS
# runner image, driven by the preinstalled safaridriver)
# --------------------------------------------------------------------------- #
# `macos-latest` is re-captured every run (track=True) so it follows the newest
# Safari and the store accumulates a version history as GitHub bumps the image;
# `macos-14` is captured once (track=False) for older-line breadth, to spare
# (×10-billed) macOS runner minutes.
SAFARI_IMAGES = [("macos-latest", True), ("macos-14", False)]


def safari_versions(token=None):
    # `version` is a placeholder (the runner image); the real Safari version is
    # detected at capture time from the user-agent and used as the store key.
    return [{"version": img, "image": img, "os": img, "engine": "webkit",
             "kind": "safari", "binary": "safari", "url": "", "track": track}
            for img, track in SAFARI_IMAGES]


def _verkey(v):
    parts = re.split(r"[.\-]", v)
    return [int(p) if p.isdigit() else 0 for p in parts]


BROWSERS = {
    "chrome": chrome_versions,
    "edge": edge_versions,
    "brave": brave_versions,
    "firefox": firefox_versions,
    "safari": safari_versions,
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


def _ensure_brave_keyring():
    """brave-browser debs depend on the brave-keyring package, which only lives
    in brave's apt repo. Install it so dpkg of a pinned version configures."""
    key = "/usr/share/keyrings/brave-browser-archive-keyring.gpg"
    subprocess.run(["sudo", "curl", "-fsSL", "-o", key,
                    "https://brave-browser-apt-release.s3.brave.com/"
                    "brave-browser-archive-keyring.gpg"])
    listline = ("deb [signed-by=%s] https://brave-browser-apt-release.s3.brave.com/ "
                "stable main" % key)
    subprocess.run("echo '%s' | sudo tee /etc/apt/sources.list.d/brave.list" % listline,
                   shell=True)
    subprocess.run(["sudo", "apt-get", "update", "-qq"])
    subprocess.run(["sudo", "apt-get", "install", "-y", "-qq", "brave-keyring"])


def install(entry, workdir="/tmp/bfp"):
    if entry["kind"] == "safari":
        return entry["binary"]  # preinstalled on the macOS runner
    os.makedirs(workdir, exist_ok=True)
    if entry["kind"] == "deb":
        if "brave" in entry["binary"]:
            _ensure_brave_keyring()
        deb = download(entry["url"], os.path.join(workdir, "browser.deb"))
        # dpkg then resolve deps
        r = subprocess.run(["sudo", "dpkg", "-i", deb])
        if r.returncode != 0:
            _run(["sudo", "apt-get", "-f", "install", "-y"])
            subprocess.run(["sudo", "dpkg", "-i", deb])
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
