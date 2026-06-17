# tesserae-pi-png-client

Raspberry Pi-side daemon that subscribes to a [Tesserae](https://github.com/dmellok/tesserae)
server over MQTT, downloads PNG frames, applies the message's
rotate / scale / bg / saturation hints, and paints them onto a Pimoroni
e-ink panel via the official [`inky`](https://github.com/pimoroni/inky)
library.

This is the **PNG path** counterpart to
[`tesserae-pi-bin-client`](https://github.com/dmellok/tesserae-pi-bin-client).
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
git clone https://github.com/dmellok/tesserae-pi-png-client
cd tesserae-pi-png-client
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
6. Prompt for device id + MQTT host/port/user/pass/client-id → write
   `~/.config/tesserae-pi-png-client/config.toml` (skipped if it exists, unless
   `--reconfigure`)
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

git clone https://github.com/dmellok/tesserae-pi-png-client
cd tesserae-pi-png-client
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
sensible defaults. Edit `mqtt.host` to point at your broker, then re-run:

```toml
[mqtt]
host = "192.168.1.10"
port = 1883
username = ""
password = ""
client_id = "pi-impression-png-1"
device_id = "pi_png"  # MQTT topic prefix
keepalive = 60

[http]
download_timeout_s = 30
max_frame_bytes = 16_000_000

[logging]
level = "INFO"
```

`device_id` sets the MQTT topic prefix this client subscribes and publishes
on (see *MQTT contract* below), and is the id Tesserae identifies the device
by. The default `pi_png` matches Tesserae's built-in `pi_png_client` device
kind. Give each Pi its own id (`pi_lounge`, `pi_kitchen`, …) if you run more
than one. It must be lowercase, 2–32 chars, and start with a letter
(`^[a-z][a-z0-9_-]{1,31}$`).

Note: there is no `[panel]` section — the panel is auto-detected from the
HAT EEPROM. If detection fails the daemon refuses to start (see
troubleshooting).

> **Upgrading from an older version?** This client used to hardcode the
> `tesserae/pi/...` topic prefix. It now defaults to `tesserae/pi_png/...`.
> **This is a breaking topic change**: an existing `config.toml` with no
> `device_id` parses with the new `pi_png` default, so the client moves to
> the new topics. Either register a `pi_png` device in Tesserae
> (Settings → Devices — the built-in `pi_png_client` kind), or, to keep the
> legacy prefix, set `device_id = "pi"` by hand and add a matching device in
> Tesserae.

### Install as a service (if you used the manual path)

`scripts/install.sh` already does this. If you installed manually:

```bash
sudo ./scripts/install-service.sh        # uses $SUDO_USER by default
sudo journalctl -fu tesserae-pi-png-client
```

The unit runs as your user with `gpio` + `spi` group membership.

---

## MQTT contract

All topics are prefixed with the configured `device_id` (default `pi_png`),
i.e. `tesserae/<device_id>/...`. The examples below use the default.

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
  transforms.py    # pure rotate/scale/bg — fully tested
  config.py        # TOML load + defaults
  paint.py         # inky wrapper (lazy-imported)
  mqtt_loop.py     # paho client + frame dispatcher
  heartbeat.py     # retained status + LWT
  main.py          # CLI entry point, signal handlers
```

License: AGPL-3.0-or-later.
