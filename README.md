# browser-fps

Automated network-fingerprint database for real browsers.

A GitHub Actions workflow watches for new browser releases (and backfills old
ones), downloads each exact version, launches it, drives it to the public
fingerprinting endpoints, and records what the server actually observed:

- **TLS / HTTP-2** via <https://tls.peet.ws/api/all>
  - `ja4`, `ja4_r`, `ja3`, `ja3_hash`
  - `akamai_fingerprint` (+ hash)
  - `peetprint` (+ hash)
  - the **exact ordered list of header keys** the browser sent (incl. HTTP/2
    pseudo-headers), captured **per request type** in `header_orders` because
    Chrome emits a different order depending on the initiator
    (`sec-fetch-dest`/mode), not just which headers are present:
    `navigate` (document), `xhr_get`, `xhr_get_nocors`, `xhr_post` (JSON fetch), `script`,
    `stylesheet`, `beacon`. (`image` ≈ `script`; cross-site only adds `origin`;
    `preflight`/`image` bodies aren't readable headless, so they're omitted.)
    `header_order` (= `navigate`) and `header_order_post` (= `xhr_post`) are
    kept as aliases. Cookies and a referer are injected to enrich the order.
    Firefox only records the JS-readable types (`navigate`, `xhr_get`,
    `xhr_post`) since it has no DevTools protocol to read subresource bodies.
- **HTTP/3 (QUIC)** via <https://quic.tools.scrapfly.io/api/fp/quic>
  - `ja4` / `ja4_r` for the QUIC handshake (connection forced over h3)

## Browsers

| browser  | engine   | version source |
|----------|----------|----------------|
| Chrome   | chromium | [`NDViet/google-chrome-stable`](https://github.com/NDViet/google-chrome-stable) release debs (full history) + newest stable from [`berstend/chrome-versions`](https://github.com/berstend/chrome-versions) (any platform) via Chrome-for-Testing linux64 when not yet in the deb archive. Versions are the clean official number (no debian `-N`). |
| Edge     | chromium | Microsoft package pool debs |
| Brave    | chromium | `brave/brave-browser` release debs (stable channel) |
| Firefox  | gecko    | `ftp.mozilla.org` release tarballs |
| Safari   | webkit   | preinstalled on macOS runner images (`macos-latest` + `macos-14`) — not separately downloadable, it's whatever ships with the image |

Chromium browsers are driven through the DevTools Protocol directly (`scripts/cdp.py`),
so no version-matched driver is needed. Firefox is driven with Selenium + geckodriver,
and Safari with the built-in safaridriver on macOS runners (so Safari capture jobs run
on `runs-on: macos-*`, the rest on `ubuntu-latest`).

Safari can't be pinned to a version (it ships with macOS), so it's **always
re-captured** and stored under the version detected at runtime: as GitHub bumps
Safari in `macos-latest`, new versions accumulate into a history. To keep macOS
runner cost down, Safari runs on a separate **daily** schedule (linux browsers
stay on the 6-hourly one).

## Data store

Two synchronized representations live under `data/`:

- `data/fingerprints.json` — canonical, human-diffable, keyed `browsers[browser][version]`
- `data/fingerprints.sqlite` — queryable table `fingerprints` (rebuilt from the JSON)

```sql
SELECT browser, version, h2_ja4, h2_akamai, h2_header_orders, h3_ja4
FROM fingerprints WHERE browser='chrome' AND fingerprint_source='measured' ORDER BY major DESC;
```

### Complete stable coverage (`fingerprint_source`)

Google ships some stable versions only for Windows/Mac (e.g. `149.0.7827.156`,
`.157`) that never get a Linux build, so they can't be captured directly. But
the network fingerprint is determined by the Chromium **milestone**, not the
patch — verified: across milestones 135-151 every measured patch in a milestone
shares one identical ja4 / akamai / peetprint / header order. So every stable
release (list from the official [version-history API](https://versionhistory.googleapis.com))
is present:

- `fingerprint_source = "measured"` — captured from a real binary.
- `fingerprint_source = "inherited"` — no Linux build; fingerprint inherited from
  a measured version of the same milestone (`inherited_from`). In the JSON these
  are lightweight pointers; in the SQLite the fingerprint is denormalized onto
  the row, so **every version is directly queryable**.

## Workflow

`.github/workflows/update-fingerprints.yml`

1. **discover** — list available versions per browser, subtract what's already
   stored, emit a matrix of the newest pending versions (`--batch`, default 12).
2. **capture** — matrix job per version: install the browser, capture h2 + h3,
   upload a result artifact.
3. **merge** — collect all artifacts, update `fingerprints.json` + `.sqlite`,
   commit.

Runs every 6h on a schedule, or manually via *Run workflow* with inputs:
`browsers` (e.g. `chrome,edge,brave,firefox`), `batch`, `retry_errors`.

Because discover always works newest-first and skips versions already present,
old versions are backfilled incrementally across runs until the archive is
covered.

## Local use

```bash
pip install websocket-client selenium
python3 scripts/sources.py chrome              # list versions
python3 scripts/capture.py --browser chrome --version <v> \
    --url <deb-url> --kind deb --engine chromium \
    --binary /opt/google/chrome/google-chrome --captured-at local
python3 scripts/merge.py --results results --captured-at $(date -u +%FT%TZ)
```
