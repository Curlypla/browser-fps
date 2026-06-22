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
    pseudo-headers). Cookies and a referer are injected so the captured order is
    as complete as possible.
- **HTTP/3 (QUIC)** via <https://quic.tools.scrapfly.io/api/fp/quic>
  - `ja4` / `ja4_r` for the QUIC handshake (connection forced over h3)

## Browsers

| browser  | engine   | version source |
|----------|----------|----------------|
| Chrome   | chromium | [`NDViet/google-chrome-stable`](https://github.com/NDViet/google-chrome-stable) release debs (every sub-version) |
| Edge     | chromium | Microsoft package pool debs |
| Brave    | chromium | `brave/brave-browser` release debs (stable channel) |
| Firefox  | gecko    | `ftp.mozilla.org` release tarballs |

Chromium browsers are driven through the DevTools Protocol directly (`scripts/cdp.py`),
so no version-matched driver is needed. Firefox is driven with Selenium + geckodriver.

## Data store

Two synchronized representations live under `data/`:

- `data/fingerprints.json` — canonical, human-diffable, keyed `browsers[browser][version]`
- `data/fingerprints.sqlite` — queryable table `fingerprints` (rebuilt from the JSON)

```sql
SELECT browser, version, h2_ja4, h2_akamai, h2_header_order, h3_ja4
FROM fingerprints WHERE browser='chrome' ORDER BY major DESC;
```

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
