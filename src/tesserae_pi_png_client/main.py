from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import __version__
from .config import DEFAULT_CONFIG_PATH, Config, load_config
from .heartbeat import Heartbeat, Status, status_topic
from .mqtt_loop import FrameDispatcher, MessageHandler, frame_topic, make_mqtt_loop
from .paint import auto_panel, model_name, paint, panel_resolution, stripe_test_image

log = logging.getLogger(__name__)


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _detect_panel() -> Any:
    """Auto-detect the inky panel. Raises a clear error if HAT/SPI is off."""
    try:
        return auto_panel()
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            "could not auto-detect inky panel: "
            f"{type(exc).__name__}: {exc}\n"
            "Troubleshooting:\n"
            "  1. raspi-config -> Interface Options -> SPI -> enable\n"
            "  2. confirm the HAT EEPROM is readable: ls /proc/device-tree/hat\n"
            "  3. user running the service must be in the 'gpio' and 'spi' groups\n"
            "  4. reboot after enabling SPI"
        ) from exc


def _do_paint_test(_: Config) -> int:
    panel = _detect_panel()
    width, height = panel_resolution(panel)
    name = model_name(panel)
    log.info("detected panel %s (%dx%d)", name, width, height)
    img = stripe_test_image(width, height)
    log.info("painting stripe test pattern")
    paint(panel, img, saturation=0.5)
    log.info("paint-test complete")
    return 0


def _do_run(config: Config) -> int:
    panel = _detect_panel()
    panel_size = panel_resolution(panel)
    name = model_name(panel)
    log.info("detected panel %s (%dx%d)", name, panel_size[0], panel_size[1])

    device_id = config.mqtt.device_id
    frame_t = frame_topic(device_id)
    status_t = status_topic(device_id)
    log.info("device_id=%s frame_topic=%s status_topic=%s", device_id, frame_t, status_t)

    status = Status(panel=name, panel_w=panel_size[0], panel_h=panel_size[1])

    def paint_fn(img: Any, saturation: float) -> None:
        paint(panel, img, saturation)

    # client_holder gives us a way for the heartbeat publisher to reach the
    # paho client even though the client is constructed *after* the heartbeat
    # (because the heartbeat needs a publisher to be constructed). The
    # placeholder is fine — heartbeat.publish_now() just no-ops until the
    # client is wired in below.
    client_holder: dict[str, Any] = {}

    class _ClientPublisher:
        def publish(
            self,
            topic: str,
            payload: bytes,
            qos: int = 0,
            retain: bool = False,
        ) -> Any:
            client = client_holder.get("client")
            if client is None:
                return None
            return client.publish(topic, payload, qos=qos, retain=retain)

    heartbeat = Heartbeat(status=status, publisher=_ClientPublisher(), topic=status_t)
    dispatcher = FrameDispatcher(
        config=config,
        paint_fn=paint_fn,
        panel_size=panel_size,
        status=status,
        heartbeat=heartbeat,
    )
    handler = MessageHandler(
        dispatcher=dispatcher, status=status, heartbeat=heartbeat, frame_topic=frame_t
    )
    client = make_mqtt_loop(
        config=config, handler=handler, frame_topic=frame_t, status_topic=status_t
    )
    client_holder["client"] = client

    shutdown = threading.Event()

    def _signal_handler(signum: int, frame: Any) -> None:
        log.info("signal %d received; shutting down", signum)
        shutdown.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    dispatcher.start()
    heartbeat.start()
    log.info(
        "connecting to mqtt %s:%d as %s",
        config.mqtt.host,
        config.mqtt.port,
        config.mqtt.client_id,
    )
    client.connect_async(config.mqtt.host, config.mqtt.port, config.mqtt.keepalive)
    client.loop_start()

    try:
        while not shutdown.is_set():
            time.sleep(0.5)
    finally:
        log.info("publishing offline and disconnecting")
        try:
            heartbeat.publish_offline()
        except Exception:  # noqa: BLE001
            log.exception("failed publishing offline status")
        heartbeat.stop()
        dispatcher.stop()
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:  # noqa: BLE001
            log.exception("error during MQTT shutdown")
    return 0


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tesserae-pi-png-client",
        description="Subscribe to a Tesserae server and paint PNG frames "
        "onto a Pimoroni e-ink panel via the inky library.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"config path (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--paint-test",
        action="store_true",
        help="paint a colour stripe pattern and exit (no MQTT)",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    _setup_logging(config.logging.level)

    if args.paint_test:
        return _do_paint_test(config)
    return _do_run(config)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
