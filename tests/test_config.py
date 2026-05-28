from __future__ import annotations

from pathlib import Path

import pytest

from tesserae_pi_png_client.config import (
    DEFAULT_TOML,
    Config,
    load_config,
    parse_toml,
    render_config_toml,
)


def test_default_toml_parses() -> None:
    cfg = parse_toml(DEFAULT_TOML)
    assert isinstance(cfg, Config)
    assert cfg.mqtt.host == "192.168.1.10"
    assert cfg.mqtt.port == 1883
    assert cfg.mqtt.username == ""
    assert cfg.mqtt.password == ""
    assert cfg.mqtt.client_id == "pi-impression-png-1"
    assert cfg.mqtt.device_id == "pi_png"
    assert cfg.mqtt.keepalive == 60
    assert cfg.http.download_timeout_s == 30
    assert cfg.http.max_frame_bytes == 16_000_000
    assert cfg.logging.level == "INFO"


def test_default_toml_has_no_panel_section() -> None:
    # Auto-detection via HAT EEPROM means no [panel] in the file.
    assert "[panel]" not in DEFAULT_TOML


def test_load_config_creates_default_on_first_run(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "config.toml"
    assert not path.exists()
    cfg = load_config(path)
    assert path.exists()
    assert cfg.mqtt.client_id == "pi-impression-png-1"


def test_bad_port_rejected() -> None:
    bad = DEFAULT_TOML.replace("port = 1883", "port = 0")
    with pytest.raises(ValueError, match="out of range"):
        parse_toml(bad)


def test_port_above_max_rejected() -> None:
    bad = DEFAULT_TOML.replace("port = 1883", "port = 65536")
    with pytest.raises(ValueError, match="out of range"):
        parse_toml(bad)


def test_negative_keepalive_rejected() -> None:
    bad = DEFAULT_TOML.replace("keepalive = 60", "keepalive = -1")
    with pytest.raises(ValueError, match="keepalive"):
        parse_toml(bad)


def test_missing_client_id_rejected() -> None:
    bad = DEFAULT_TOML.replace(
        'client_id = "pi-impression-png-1"\n', ""
    )
    with pytest.raises(ValueError, match="client_id"):
        parse_toml(bad)


def test_unknown_log_level_rejected() -> None:
    bad = DEFAULT_TOML.replace('level = "INFO"', 'level = "VERBOSE"')
    with pytest.raises(ValueError, match="level"):
        parse_toml(bad)


def test_non_positive_timeout_rejected() -> None:
    bad = DEFAULT_TOML.replace("download_timeout_s = 30", "download_timeout_s = 0")
    with pytest.raises(ValueError, match="download_timeout_s"):
        parse_toml(bad)


def test_non_positive_max_frame_bytes_rejected() -> None:
    bad = DEFAULT_TOML.replace("max_frame_bytes = 16000000", "max_frame_bytes = -1")
    with pytest.raises(ValueError, match="max_frame_bytes"):
        parse_toml(bad)


def test_overrides_take_effect() -> None:
    bad = DEFAULT_TOML.replace(
        'host = "192.168.1.10"', 'host = "broker.local"'
    ).replace("port = 1883", "port = 8883")
    cfg = parse_toml(bad)
    assert cfg.mqtt.host == "broker.local"
    assert cfg.mqtt.port == 8883


def test_render_no_args_matches_default() -> None:
    assert render_config_toml() == DEFAULT_TOML


def test_render_overrides_round_trip() -> None:
    body = render_config_toml(
        mqtt_host="broker.lan",
        mqtt_port=8883,
        mqtt_username="alice",
        mqtt_password="hunter2",
        mqtt_client_id="kitchen-display",
    )
    cfg = parse_toml(body)
    assert cfg.mqtt.host == "broker.lan"
    assert cfg.mqtt.port == 8883
    assert cfg.mqtt.username == "alice"
    assert cfg.mqtt.password == "hunter2"
    assert cfg.mqtt.client_id == "kitchen-display"


def test_render_escapes_quote_in_string_value() -> None:
    body = render_config_toml(mqtt_password='abc"def')
    cfg = parse_toml(body)
    assert cfg.mqtt.password == 'abc"def'


def test_render_escapes_backslash_in_string_value() -> None:
    body = render_config_toml(mqtt_password=r"a\b")
    cfg = parse_toml(body)
    assert cfg.mqtt.password == r"a\b"


def test_bool_value_for_int_field_rejected() -> None:
    bad = DEFAULT_TOML.replace("port = 1883", "port = true")
    with pytest.raises(ValueError, match="port"):
        parse_toml(bad)


def test_device_id_defaults_when_missing() -> None:
    # An older config.toml predating device_id should parse with the new default.
    without = DEFAULT_TOML.replace('device_id = "pi_png"  # MQTT topic prefix\n', "")
    assert "device_id" not in without
    cfg = parse_toml(without)
    assert cfg.mqtt.device_id == "pi_png"


def test_device_id_override_round_trips() -> None:
    body = render_config_toml(device_id="pi_lounge")
    cfg = parse_toml(body)
    assert cfg.mqtt.device_id == "pi_lounge"


def test_invalid_device_id_uppercase_rejected() -> None:
    bad = DEFAULT_TOML.replace('device_id = "pi_png"', 'device_id = "Pi_PNG"')
    with pytest.raises(ValueError, match="device_id"):
        parse_toml(bad)


def test_invalid_device_id_leading_digit_rejected() -> None:
    bad = DEFAULT_TOML.replace('device_id = "pi_png"', 'device_id = "1pi"')
    with pytest.raises(ValueError, match="device_id"):
        parse_toml(bad)


def test_invalid_device_id_too_short_rejected() -> None:
    bad = DEFAULT_TOML.replace('device_id = "pi_png"', 'device_id = "p"')
    with pytest.raises(ValueError, match="device_id"):
        parse_toml(bad)
