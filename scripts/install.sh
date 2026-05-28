#!/usr/bin/env bash
# One-shot installer for tesserae-pi-png-client on a fresh Raspberry Pi OS.
#
# Run this AS YOUR NORMAL USER (NOT root, not via sudo). The script calls
# sudo internally for the privileged bits (apt, raspi-config, usermod, ln,
# systemd unit install) and runs the unprivileged bits (venv, pip install)
# as the invoking user — so the venv ends up in that user's home and the
# service runs as them too.
#
# What it does, idempotently:
#   1. apt-get install build + runtime prerequisites (incl. libs Pillow needs)
#   2. raspi-config nonint do_spi 0     (enable SPI bus)
#   3. usermod -aG gpio,spi $USER       (group membership for HAT access)
#   4. python3 -m venv .venv            (in the repo directory)
#   5. .venv/bin/pip install -e .       (project + inky[rpi] + paho-mqtt + Pillow)
#   5b. (--bookworm only) pip install rpi-lgpio   (Pi 5 / Bookworm replaces RPi.GPIO)
#   6. interactive prompt → write ~/.config/.../config.toml
#      (skipped if the file already exists, unless --reconfigure)
#   7. ln -sf .venv/bin/tesserae-pi-png-client /usr/local/bin/...
#   8. scripts/install-service.sh $USER          (systemd unit + enable + start,
#                                                  unless --no-service)
#
# The group change in step 3 only takes effect on the user's next login.
# If you've just been added to gpio/spi, log out + back in (or reboot)
# before running --paint-test or relying on the service.

set -euo pipefail

INSTALL_SERVICE=true
RUN_PAINT_TEST=false
SKIP_APT=false
NON_INTERACTIVE=false
RECONFIGURE=false
INSTALL_LGPIO=false
SERVICE_USER="${USER:-$(id -un)}"

usage() {
    cat <<USAGE
usage: $0 [options]

  --no-service        don't install the systemd unit
  --paint-test        run --paint-test after install (needs fresh login if
                      the gpio/spi groups were just added)
  --skip-apt          skip apt-get update + install
  --non-interactive   never prompt — write a default config if none exists
  --reconfigure       prompt for device id + MQTT values even if a config
                      exists (overwrites the existing file, including device_id)
  --bookworm          also pip install rpi-lgpio (required on Pi 5 / Bookworm,
                      where RPi.GPIO doesn't work). Harmless on older boards.
  --user USER         user the systemd unit runs as (default: \$USER)
  -h, --help          show this message
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-service) INSTALL_SERVICE=false; shift ;;
        --paint-test) RUN_PAINT_TEST=true; shift ;;
        --skip-apt) SKIP_APT=true; shift ;;
        --non-interactive) NON_INTERACTIVE=true; shift ;;
        --reconfigure) RECONFIGURE=true; shift ;;
        --bookworm) INSTALL_LGPIO=true; shift ;;
        --user) SERVICE_USER="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${REPO_DIR}/.venv"
BIN_LINK="/usr/local/bin/tesserae-pi-png-client"
CONFIG_PATH="${HOME}/.config/tesserae-pi-png-client/config.toml"

if [[ "$(id -u)" -eq 0 ]]; then
    echo "error: run as your normal user, NOT root or via sudo." >&2
    echo "       (the script invokes sudo internally where needed)" >&2
    exit 1
fi

if [[ ! -f "${REPO_DIR}/pyproject.toml" ]]; then
    echo "error: ${REPO_DIR}/pyproject.toml not found — wrong repo layout?" >&2
    exit 1
fi

is_rpi=false
if [[ -f /proc/device-tree/model ]] && grep -qi "raspberry pi" /proc/device-tree/model 2>/dev/null; then
    is_rpi=true
fi
if ! $is_rpi; then
    echo "warning: this doesn't look like a Raspberry Pi — continuing anyway."
    echo "         (SPI enable + group add will be no-ops on a normal Linux box.)"
fi

# Detect Pi 5 / Bookworm-or-later automatically and hint about --bookworm
# without forcing it — leaves the choice in the user's hands.
if $is_rpi && ! $INSTALL_LGPIO; then
    model="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
    if [[ "$model" == *"Raspberry Pi 5"* ]]; then
        echo "hint: detected $model — RPi.GPIO does not work on Pi 5."
        echo "      re-run with --bookworm to also install rpi-lgpio."
    fi
fi

# ----- prompt helpers (only fire when stdin is a TTY and we're interactive) -----
prompt_default() {
    # $1=var name to assign, $2=question, $3=default
    local __var="$1" __q="$2" __default="$3" __input
    if [[ -n "$__default" ]]; then
        read -r -p "${__q} [${__default}]: " __input
    else
        read -r -p "${__q}: " __input
    fi
    printf -v "$__var" '%s' "${__input:-$__default}"
}

prompt_secret() {
    # $1=var name to assign, $2=question
    local __var="$1" __q="$2" __input
    read -r -s -p "${__q} (input hidden; press Enter for none): " __input
    echo
    printf -v "$__var" '%s' "$__input"
}

echo "==> caching sudo credentials"
sudo -v

# ----- 1. apt -----
# libopenjp2-7 + libtiff6 cover Pillow's optional image format paths. We only
# decode PNG on the wire, but Pillow loads those libs at import time when
# present and tries to fall back to internal codecs when missing — installing
# them up front prevents the "couldn't open PNG, missing libopenjp2" class of
# surprise on unusual builds. python3-dev + build-essential are for the C
# extensions inky's deps may need to compile (RPi.GPIO, spidev).
if $SKIP_APT; then
    echo "==> skipping apt (--skip-apt)"
else
    echo "==> apt-get update + install"
    sudo apt-get update -qq
    sudo apt-get install -y --no-install-recommends \
        git \
        python3 \
        python3-venv \
        python3-pip \
        python3-dev \
        build-essential \
        libopenjp2-7 \
        libtiff6
fi

# ----- 2. SPI -----
if $is_rpi && command -v raspi-config >/dev/null 2>&1; then
    echo "==> enabling SPI via raspi-config"
    sudo raspi-config nonint do_spi 0
else
    echo "==> skipping SPI enable (no raspi-config / not on a Pi)"
fi

# ----- 3. groups -----
needs_relogin=false
for group in gpio spi; do
    if ! getent group "$group" >/dev/null 2>&1; then
        echo "==> group $group does not exist on this system; skipping"
        continue
    fi
    if id -nG "$USER" | tr ' ' '\n' | grep -qx "$group"; then
        echo "==> $USER already in $group"
    else
        echo "==> adding $USER to $group"
        sudo usermod -aG "$group" "$USER"
        needs_relogin=true
    fi
done

# ----- 4-5. venv + pip install -----
if [[ ! -d "$VENV_DIR" ]]; then
    echo "==> creating venv at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi
echo "==> upgrading pip in venv"
"$VENV_DIR/bin/pip" install -q --upgrade pip
echo "==> installing package (this pulls inky[rpi] + Pillow and can take a few minutes)"
"$VENV_DIR/bin/pip" install -e "$REPO_DIR"

if $INSTALL_LGPIO; then
    echo "==> installing rpi-lgpio (Pi 5 / Bookworm replacement for RPi.GPIO)"
    "$VENV_DIR/bin/pip" install -q rpi-lgpio
fi

# ----- 6. config -----
collect_config_via_prompts() {
    echo
    echo "==> MQTT configuration (panel auto-detects via HAT EEPROM — no prompt)"
    echo "    Press Enter at any prompt to accept the default in brackets."
    echo
    echo "    A device id identifies this Pi to the Tesserae server."
    echo "    Use 'pi_png' if this is your only PNG-protocol Pi display;"
    echo "    pick something like 'pi_lounge' if you're running more"
    echo "    than one (each must have its own id)."
    prompt_default device_id       "Device id"          "pi_png"
    # basic client-side validation; the parser also enforces this
    if ! [[ "$device_id" =~ ^[a-z][a-z0-9_-]{1,31}$ ]]; then
        echo "    invalid device id; falling back to 'pi_png'" >&2
        device_id="pi_png"
    fi
    echo
    prompt_default mqtt_host       "MQTT broker host"   "192.168.1.10"
    prompt_default mqtt_port       "MQTT broker port"   "1883"
    prompt_default mqtt_username   "MQTT username (blank for anonymous)" ""
    if [[ -n "$mqtt_username" ]]; then
        prompt_secret mqtt_password "MQTT password"
    else
        mqtt_password=""
    fi
    prompt_default mqtt_client_id  "MQTT client id"     "pi-impression-png-1"
    echo
}

write_config() {
    # $1 = "1" to overwrite an existing file
    env \
        T_CONFIG_PATH="$CONFIG_PATH" \
        T_MQTT_HOST="${mqtt_host:-}" \
        T_MQTT_PORT="${mqtt_port:-}" \
        T_MQTT_USERNAME="${mqtt_username:-}" \
        T_MQTT_PASSWORD="${mqtt_password:-}" \
        T_MQTT_CLIENT_ID="${mqtt_client_id:-}" \
        T_DEVICE_ID="${device_id:-}" \
        T_OVERWRITE="$1" \
        "$VENV_DIR/bin/python" -m tesserae_pi_png_client.bootstrap_config
}

config_existed_before=false
if [[ -f "$CONFIG_PATH" ]]; then
    config_existed_before=true
fi

if $config_existed_before && ! $RECONFIGURE; then
    echo "==> config already exists at $CONFIG_PATH — leaving it alone"
    echo "    (re-run with --reconfigure to overwrite)"
elif $NON_INTERACTIVE; then
    echo "==> writing default config (--non-interactive)"
    write_config "$($RECONFIGURE && echo 1 || echo 0)" >/dev/null
    echo "    wrote $CONFIG_PATH"
elif [[ ! -t 0 ]]; then
    echo "==> stdin is not a TTY — writing default config without prompting"
    write_config "$($RECONFIGURE && echo 1 || echo 0)" >/dev/null
    echo "    wrote $CONFIG_PATH"
else
    collect_config_via_prompts
    write_config "$($RECONFIGURE && echo 1 || echo 0)" >/dev/null
    echo "==> wrote $CONFIG_PATH"
fi

# ----- 7. /usr/local/bin symlink -----
echo "==> linking $BIN_LINK -> $VENV_DIR/bin/tesserae-pi-png-client"
sudo ln -sf "$VENV_DIR/bin/tesserae-pi-png-client" "$BIN_LINK"

# ----- 8. systemd unit (optional) -----
if $INSTALL_SERVICE; then
    echo "==> installing systemd unit (user=$SERVICE_USER)"
    sudo "$REPO_DIR/scripts/install-service.sh" "$SERVICE_USER"
else
    echo "==> skipping systemd unit install (--no-service)"
fi

# ----- 9. optional paint test -----
if $RUN_PAINT_TEST; then
    if $needs_relogin; then
        echo "==> NOT running --paint-test — gpio/spi groups were just added"
        echo "    and won't take effect until you log out + back in."
    else
        echo "==> running --paint-test"
        "$VENV_DIR/bin/tesserae-pi-png-client" --paint-test || \
            echo "    (paint-test failed — see logs above)"
    fi
fi

echo
echo "================================================================"
echo "  install complete"
echo "================================================================"
echo
echo "  config:  $CONFIG_PATH"
if $config_existed_before && ! $RECONFIGURE; then
    echo "           (existing file kept; re-run with --reconfigure to change)"
fi
echo
if $needs_relogin; then
    echo "  groups:  $USER was added to gpio/spi — LOG OUT + BACK IN (or reboot)"
    echo "           before running --paint-test or relying on the service."
    echo
fi
if $INSTALL_SERVICE; then
    echo "  service: sudo systemctl status tesserae-pi-png-client"
    echo "           sudo journalctl -u tesserae-pi-png-client -f"
    echo "           sudo systemctl restart tesserae-pi-png-client  # after config edits"
else
    echo "  service: not installed (re-run without --no-service to install)"
fi
echo
echo "  manual:  tesserae-pi-png-client --paint-test   # paints a colour stripe"
echo "           tesserae-pi-png-client                # run in foreground"
echo
