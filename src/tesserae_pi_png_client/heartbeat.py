from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Protocol

OFFLINE_WILL_PAYLOAD = json.dumps({"state": "offline"}).encode("utf-8")
HEARTBEAT_INTERVAL_S = 60.0

# Discovery: Tesserae watches tesserae/+/status and uses this to pre-fill the
# device kind in its one-click register flow.
KIND = "pi_png_client"


def status_topic(device_id: str) -> str:
    return f"tesserae/{device_id}/status"


def _fw_version() -> str:
    try:
        return version("tesserae-pi-png-client")
    except PackageNotFoundError:
        return "0.0.0+unknown"


def _primary_ip() -> str:
    """Best-effort primary outbound IPv4. Blank if it can't be determined.

    The UDP connect sends no packets — it just asks the kernel which local
    address would route to a public host, which is the interface Tesserae
    would reach this device on.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return str(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        return ""


class Publisher(Protocol):
    def publish(
        self, topic: str, payload: bytes, qos: int = 0, retain: bool = False
    ) -> Any: ...


@dataclass
class Status:
    state: str = "idle"
    last_paint_at: float | None = None
    last_error: str | None = None
    last_digest: str | None = None
    panel: str = "unknown"
    started_at: float = field(default_factory=time.time)
    kind: str = KIND
    panel_w: int = 0
    panel_h: int = 0
    fw_version: str = field(default_factory=_fw_version)
    ip: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "last_paint_at": self.last_paint_at,
            "last_error": self.last_error,
            "last_digest": self.last_digest,
            "uptime_s": time.time() - self.started_at,
            "fw_version": self.fw_version,
            "panel": self.panel,
            "kind": self.kind,
            "panel_w": self.panel_w,
            "panel_h": self.panel_h,
            "ip": self.ip,
        }

    def to_json(self) -> bytes:
        return json.dumps(self.payload()).encode("utf-8")


class Heartbeat:
    """Background thread that re-publishes Status retained every interval.

    External callers (mqtt_loop / dispatcher) mutate the Status object and
    call kick() to flush immediately after a state change. The thread loop
    flushes on its own at least every HEARTBEAT_INTERVAL_S so the broker
    sees a fresh retained message even during long idle periods.
    """

    def __init__(
        self,
        status: Status,
        publisher: Publisher,
        interval: float = HEARTBEAT_INTERVAL_S,
        *,
        topic: str,
    ) -> None:
        self._status = status
        self._publisher = publisher
        self._interval = interval
        self._topic = topic
        self._stop_event = threading.Event()
        self._kick_event = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="tesserae-heartbeat", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._kick_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)
            self._thread = None

    def publish_now(self) -> None:
        with self._lock:
            self._publisher.publish(
                self._topic, self._status.to_json(), qos=1, retain=True
            )

    def publish_offline(self) -> None:
        with self._lock:
            self._publisher.publish(
                self._topic, OFFLINE_WILL_PAYLOAD, qos=1, retain=True
            )

    def kick(self) -> None:
        self._kick_event.set()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self.publish_now()
            self._kick_event.wait(timeout=self._interval)
            self._kick_event.clear()


def status_summary(status: Status) -> str:
    """Compact rendering for logs."""
    payload = status.payload()
    return (
        f"state={payload['state']} "
        f"digest={payload['last_digest']} "
        f"err={payload['last_error']} "
        f"uptime={payload['uptime_s']:.0f}s"
    )


__all__ = [
    "HEARTBEAT_INTERVAL_S",
    "KIND",
    "OFFLINE_WILL_PAYLOAD",
    "Heartbeat",
    "Publisher",
    "Status",
    "status_summary",
    "status_topic",
]
