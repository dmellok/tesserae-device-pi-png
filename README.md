# tesserae-device-pi-png

Raspberry Pi-side daemon that talks to a [Tesserae](https://github.com/dmellok/tesserae)
server — either by **polling the REST API** (default for fresh installs) or by
**subscribing to an MQTT broker** — downloads PNG frames, applies the server's
rotate / scale / bg / saturation hints, and paints them onto a Pimoroni
e-ink panel via the official [`inky`](https://github.com/pimoroni/inky)
library.

This is the **PNG path** counterpart to
[`tesserae-device-pi-bin`](https://github.com/dmellok/tesserae-device-pi-bin).
Most installs only need ONE of the two.

| | tesserae-pi-bin-client | tesserae-pi-png-client (this) |
|---|---|---|
| Wire format | 4-bpp packed `.bin` | RGB `.png` |
| Quantise / dither done by | Tesserae server | inky lib, on the Pi |
| Hardware | Inky Impression (Spectra 6 / Waveshare E6) | any inky-supported panel |
| Speed per frame | fast (no PIL roundtrip) | slower (per-frame quantise) |
| Inky version pin | exact | range (`>=2.0,<3`) |

Pick the **png** client if any of the following is true:
- you have an Inky pHAT, Inky wHAT, or any Inky Impression
- you're migrating from an inky-dash v3/v4 setup
- you want the broadest possible hardware support

---

## Install

### One-shot (recommended)

```bash
git clone https://github.com/dmellok/tesserae-device-pi-png.git
cd tesserae-device-pi-png
./scripts/install.sh
```

Run as your normal user (NOT root, NOT via sudo). The script invokes sudo
internally for the privileged bits and runs pip in a venv owned by you.
It does, idempotently:

1. `apt-get install` the build prereqs (`python3-dev`, `build-essential`,
   `libopenjp2-7`, `libtiff6`)
2. `raspi-config nonint do_spi 0` to enable SPI
3. `usermod -aG gpio,spi $USER` for HAT access (needs a re-login to take effect)
4. Create `.venv` in the repo dir
5. `pip install -e .` — pulls `inky[rpi]`, `paho-mqtt`, `Pillow`
6. **Prompts** for transport (`rest` default — just the Tesserae server URL
   — or `mqtt` for an existing broker setup), device id, and the
   transport-specific fields, then writes
   `~/.config/tesserae-pi-png-client/config.toml` (skipped if it exists,
   unless `--reconfigure`)
7. Symlink `.venv/bin/tesserae-pi-png-client` to `/usr/local/bin/`
8. Install + enable + start the systemd service (skip with `--no-service`)

Useful flags:

```
--no-service       skip systemd unit install
--paint-test       run --paint-test after install
--skip-apt         skip apt-get
--non-interactive  never prompt — write default config if none exists
--reconfigure      overwrite existing config
--bookworm         also pip install rpi-lgpio (Pi 5 / Bookworm needs it
                   because RPi.GPIO does not work on those boards)
--user USER        user the systemd unit runs as (default: $USER)
```

### Manual (if you prefer step-by-step)

```bash
sudo apt update
sudo apt install -y python3-pip python3-dev build-essential \
                    libopenjp2-7 libtiff6
sudo raspi-config nonint do_spi 0          # pixel data to the panel
sudo raspi-config nonint do_i2c 0          # HAT EEPROM read by auto-detect
# free the CS pin so inky can drive chip-select in software:
echo 'dtoverlay=spi0-0cs' | sudo tee -a /boot/firmware/config.txt
sudo usermod -aG gpio,spi "$USER"          # log out + back in after this
# reboot so SPI + I2C + the overlay take effect before first run

git clone https://github.com/dmellok/tesserae-device-pi-png.git
cd tesserae-device-pi-png
python3 -m venv .venv
.venv/bin/pip install -e .                 # pulls inky[rpi]
# Pi 5 / Bookworm only:
.venv/bin/pip install rpi-lgpio
```

### Verify hardware

Before wiring up MQTT, paint a colour-stripe test pattern to confirm
the SPI path and panel orientation:

```bash
tesserae-pi-png-client --paint-test
```

If you see a refresh and a coloured stripe pattern, the hardware path is good.
If you get `could not auto-detect inky panel`, the README troubleshooting
section below is for you.

### Configure

The first run writes `~/.config/tesserae-pi-png-client/config.toml` with
sensible defaults. For REST mode (the new default) point `rest.server_url`
at the Tesserae server; for MQTT mode point `mqtt.host` at your broker:

```toml
transport_mode = "rest"  # mqtt | rest

[mqtt]
host = "192.168.1.10"
port = 1883
username = ""
password = ""
client_id = "pi-impression-png-1"
device_id = "pi_png"  # MQTT topic prefix (also identifies this device to REST)
keepalive = 60

[rest]
server_url = "http://tesserae.local:8765"
device_token = ""         # auto-populated after pair/discover
pairing_code = ""         # single-use; wiped after first successful register
last_frame_etag = ""      # auto-populated for If-None-Match short-circuit
poll_interval_s = 60      # fallback wake interval if server omits next_poll_s

[http]
download_timeout_s = 30
max_frame_bytes = 16_000_000

[logging]
level = "INFO"
```

`device_id` identifies this Pi to Tesserae and (in MQTT mode) sets the
topic prefix `tesserae/<device_id>/...`. The default `pi_png` matches the
server's built-in `pi_png_client` kind. Give each Pi its own id
(`pi_png_lounge`, `pi_png_kitchen`, …) if you run more than one. It must
be lowercase, 2–32 chars, and start with a letter
(`^[a-z][a-z0-9_-]{1,31}$`).

Note: there is no `[panel]` section — the panel is auto-detected from the
HAT EEPROM. If detection fails the daemon refuses to start (see
troubleshooting).

> **Upgrading?** Two breakage windows to be aware of:
>
> - **Pre-`device_id` configs** (very early installs) had no `device_id`
>   key and hardcoded `tesserae/pi/...`. Those now resolve to `pi_png`
>   on parse, moving you to `tesserae/pi_png/...` — register a `pi_png`
>   device in Tesserae or set `device_id = "pi"` to keep the legacy
>   prefix.
> - **Pre-REST configs** (no `transport_mode` key) keep defaulting to
>   `transport_mode = "mqtt"` via the parser fallback, so a
>   `git pull && systemctl restart` does **not** switch you to REST.
>   Only freshly written configs (and the install prompt) default to
>   REST. To switch an existing install, add `transport_mode = "rest"`
>   at the top of `config.toml` and fill in `[rest].server_url`.

### Install as a service (if you used the manual path)

`scripts/install.sh` already does this. If you installed manually:

```bash
sudo ./scripts/install-service.sh        # uses $SUDO_USER by default
sudo journalctl -fu tesserae-pi-png-client
```

The unit runs as your user with `gpio` + `spi` group membership.

---

## Transports

The client speaks one of two transports, selected by `transport_mode`:

- **`rest`** (default for fresh installs) — polls the Tesserae server's
  `/api/v1/` directly. No broker needed; one round-trip per wake cycle
  (`GET /frame` + `POST /status`). Out of the box the wake cadence is
  **every 60 s**; the server's `/status` response can push a different
  `next_poll_s` per cycle or a durable `config.sleep_interval_s`, both
  clamped to `[30s, 7d]`.
- **`mqtt`** — subscribes to a broker and reacts to retained frame
  announcements. Stays connected; pushes are near-instant. Requires a
  broker on the LAN.

The installer prompts for transport at the top and only asks for the
relevant fields (REST → server URL + optional pairing code; MQTT →
broker host/port/credentials/client id).

Switching mode later is a config-file edit + `sudo systemctl restart
tesserae-pi-png-client`. **Existing `config.toml` files** without
`transport_mode` continue to default to **`mqtt`** (no surprise mode
switch on upgrade); only fresh installs get the new REST default.

### REST mode setup (default install)

When the installer asks for transport, hit Enter to accept `rest`,
then either:

1. **Recommended path (zero typing on the device):** start the daemon
   with no pairing code and click **Register** on the discovered row in
   the server's Settings → Devices page. The daemon's next
   `POST /device/discover` claims the token by MAC match; you'll see
   "registered via discover" in the journal.
2. **Strict path (per-device admin approval):** generate a 6-digit
   pairing code in Settings → Devices and enter it at the installer's
   pairing-code prompt. The code is single-use; after a successful
   register the daemon wipes it and saves the issued `device_token`.

To re-pair after the fact (e.g. token was revoked, or the local
`device_token` got wiped), generate a fresh code and run once with the
CLI override:

```bash
tesserae-pi-png-client --pair 123456
```

`--pair` overrides whatever is in `[rest].pairing_code` for that run
only and is no-op'd if a `device_token` is already saved.

If a 401 ever comes back from the server (token revoked or wiped from
the server side), the daemon clears the local `device_token` and exits.
Restart with `--pair` or rely on the discover loop to recover.

---

## MQTT contract

Active when `transport_mode = "mqtt"`. All topics are prefixed with the
configured `device_id` (default `pi_png`), i.e. `tesserae/<device_id>/...`.
The examples below use the default.

### Subscribe

Topic: `tesserae/<device_id>/frame/png` (QoS 1, not retained)

Payload (all five fields required):

```json
{
  "url": "http://192.168.1.10:8000/renders/3f7a91b2c4e5d6f8.png",
  "rotate": 0,
  "scale": "fit",
  "bg": "white",
  "saturation": 0.5
}
```

| Field | Type | Meaning |
|---|---|---|
| `url` | string | HTTP URL to GET the PNG. No auth. |
| `rotate` | int 0..3 | Quarter-turns CW to apply *before* scaling. Stacks with whatever rotation the server already baked in. |
| `scale` | string | One of `fit`, `fill`, `stretch`, `center`. |
| `bg` | string | Letterbox colour when `fit`/`center` leaves bars. One of `white`, `black`, `red`, `green`, `blue`, `yellow`, `orange`. Unknown names fall back to white. |
| `saturation` | float 0.0..1.0 | Passed straight to `inky.set_image(saturation=...)`. |

### Publish

Topic: `tesserae/<device_id>/status` (QoS 1, retained, also the LWT topic)

```json
{
  "state": "idle",
  "last_paint_at": 1734567890.123,
  "last_error": null,
  "last_digest": "3f7a91b2c4e5d6f8",
  "uptime_s": 3601,
  "fw_version": "0.1.0",
  "panel": "inky_impression_13_3",
  "kind": "pi_png_client",
  "panel_w": 1600,
  "panel_h": 1200,
  "ip": "192.168.1.42"
}
```

`state` is one of `idle`, `rendering`, `error`, `offline` (LWT).
Heartbeat: republished on every state change and at least every 60 s.

The `kind` / `panel_w` / `panel_h` / `ip` keys feed Tesserae's device
discovery: an unregistered `device_id` shows up under Settings → Devices as
a "Discovered" row, and these keys pre-fill the device kind and panel size
so registering it is one click. `panel_w` / `panel_h` are the post-rotation
dimensions the panel actually paints. `ip` is best-effort and blank if the
primary interface can't be determined.

---

## REST contract

Active when `transport_mode = "rest"`. All endpoints sit under `/api/v1/`
on the configured `server_url`. The client sends both `Authorization:
Bearer <token>` and `X-Tesserae-Token: <token>` on every authenticated
call (cheap belt-and-suspenders against header-stripping middleboxes).

### First boot — `POST /device/discover`

Body: `{device_id, kind: "pi_png_client", panel_w, panel_h, fw_version, mac}`.

The server responds in one of two shapes:

- `{registered: false, retry_after_s: 30}` — the device shows up in
  Settings → Devices "Discovered" strip; sleep `retry_after_s` and poll
  again.
- `{registered: true, device_token, device_id, server_time}` — admin
  clicked Register; persist the token, **adopt `device_id` from the
  response** (it may differ from what was sent — using the wrong one
  gives 403 on subsequent calls), then enter the wake loop.

### Alternative first boot — `POST /device/register`

Header: `X-Pairing-Code: <6-digit-code>`, body same as discover.
Returns `201 + {device_token, device_id, reused_existing}` on success;
`403` on bad/expired code (process exits — generate a fresh code);
`429 + Retry-After` if rate-limited.

### Wake loop — `GET /device/<id>/frame`

Header: `Authorization: Bearer <token>`, optional `If-None-Match:
<last_frame_etag>`.

- `200` + JSON `{url, format: "png", render_id, rotate, scale, bg,
  saturation, panel_w, panel_h}` and `ETag: "<sha256>"` — download `url`,
  apply the rotate/scale/bg transforms, paint with `saturation`, save the
  new ETag. The `rotate/scale/bg/saturation` fields use the same vocabulary
  as the MQTT payload.
- `304` — composition unchanged; skip download + paint.
- `204` — server hasn't rendered anything for this device yet.
- `401` — token invalid; the daemon wipes `device_token` from
  `config.toml` and exits.
- `403` — token doesn't match the device id (likely a stale local id);
  exits without wiping the token. Re-pair to refresh.

### Wake loop — `POST /device/<id>/status`

Body: the same heartbeat shape as the MQTT retained
`tesserae/<device_id>/status` payload (`{state, last_paint_at, last_error,
last_digest, uptime_s, fw_version, panel, kind, panel_w, panel_h, ip}`).

Response: `{status, config: {sleep_interval_s?}, next_poll_s?,
server_time}`. `config.sleep_interval_s` is durable (persisted to
`[rest].poll_interval_s` for future cycles), `next_poll_s` is one-shot
(only the next sleep duration). Both clamp to `[30s, 7d]`.

---

## Transform pipeline

For each arriving frame:

1. Download the PNG via plain HTTP (size capped at `max_frame_bytes`).
2. Decode with PIL, coerce to mode `RGB`.
3. Rotate by `rotate * 90°` clockwise (`expand=True` so nothing is cropped).
4. Scale per `scale` mode:
   - `fit` — preserve aspect, letterbox with `bg` colour
   - `fill` — preserve aspect, crop to cover
   - `stretch` — `resize` straight to panel size, distorts
   - `center` — paste at native size, letterbox, crop overflow
5. Hand to `panel.set_image(img, saturation=...)`, then `panel.show()`.

---

## Troubleshooting

**`could not auto-detect inky panel` / `No EEPROM detected`**
- enable **both** SPI and I2C — the panel ID lives in the HAT EEPROM, which
  `inky.auto()` reads over I2C:
  `sudo raspi-config nonint do_spi 0 && sudo raspi-config nonint do_i2c 0`
- **reboot**, then confirm the EEPROM is visible:
  `ls /dev/i2c-1 && sudo i2cdetect -y 1` (expect `50` in the grid)
- check your user is in `gpio` and `spi` groups: `groups`
- if `i2cdetect` shows no `50`, the board has no readable EEPROM (some
  Impression/Spectra units, all non-genuine boards) and auto-detect can't
  identify it

**`Woah there, some pins we need are in use!` / `Chip Select … claimed by spi0 CS0`**
- The panel auto-detects fine but paint fails: the kernel SPI driver reserves
  GPIO8 (CS0), while recent `inky` drives chip-select in software.
- Free the pin with the zero-chip-select overlay, then reboot:
  ```bash
  CONFIG=/boot/firmware/config.txt; [ -f "$CONFIG" ] || CONFIG=/boot/config.txt
  grep -q '^dtoverlay=spi0-0cs' "$CONFIG" || echo 'dtoverlay=spi0-0cs' | sudo tee -a "$CONFIG"
  sudo reboot
  ```
- `scripts/install.sh` does this automatically; this is for manual installs.

**Frame never paints, state stays `idle`**
- Is the broker reachable from the Pi? `mosquitto_sub -h <broker> -t '#'`
- Is the URL reachable from the Pi? `curl -I <url>`
- Tail the journal: `sudo journalctl -fu tesserae-pi-png-client`

**`state: error` with `last_error` ending in `URLError` / `TimeoutError`**
- The Tesserae server's render directory is unreachable from the Pi —
  check firewall, port, and that the server is actually serving on the
  URL the message advertised.

**REST: `not registered yet — admin needs to click Register on the server` loops forever**
- The daemon's `POST /device/discover` is reaching the server, but no one
  has clicked Register in Settings → Devices. Either click it (the next
  discover poll claims the token) or mint a 6-digit pairing code and rerun
  with `--pair <code>` for the strict path.

**REST: `frame GET 401: token invalid`**
- The server revoked the device or the local `device_token` is corrupt.
  The daemon wipes it from `config.toml` and exits — re-pair (`--pair`)
  or wait for the discover loop to re-claim.

**REST: `frame GET 403: token not valid for this device`**
- The local `[mqtt].device_id` no longer matches the server's canonical
  id (admin renamed the device). The discover-claim flow normally adopts
  the new id; if you got here, re-pair to refresh both id and token.

**REST: log shows `server_time skew=...s; check NTP`**
- The Pi's clock has drifted more than a minute from the server's. We
  don't `settimeofday` (NTP owns that on Linux); check that
  `chronyd`/`systemd-timesyncd` is running.

**Colours look washed out / oversaturated**
- Tune the `saturation` field on the Tesserae server side. The Pi just
  passes whatever value arrives straight to `inky.set_image()`.

---

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
ruff check src tests
mypy src
```

The `paho-mqtt` and `Pillow` deps install everywhere; `inky[rpi]` is
Linux-only and won't install on macOS — that's fine, `paint.py` lazy-
imports it so the unit tests run on any host.

### Layout

```
src/tesserae_pi_png_client/
  transforms.py        # pure rotate/scale/bg — fully tested
  config.py            # TOML load + atomic save + transport_mode/[rest]
  paint.py             # inky wrapper (lazy-imported)
  mqtt_loop.py         # paho client + frame dispatcher
  heartbeat.py         # retained status + LWT
  transports/
    mqtt.py            # MQTT wake loop (extracted from main)
    rest.py            # REST polling loop + discover/register/pair
  main.py              # CLI entry point, signal handlers, transport dispatch
```

License: AGPL-3.0-or-later.
