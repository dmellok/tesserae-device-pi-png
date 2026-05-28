from __future__ import annotations

import json
from typing import Any

from tesserae_pi_png_client.heartbeat import (
    OFFLINE_WILL_PAYLOAD,
    Heartbeat,
    Status,
    status_summary,
    status_topic,
)

DEVICE_ID = "pi_png"
STATUS_TOPIC = status_topic(DEVICE_ID)


class FakePublisher:
    def __init__(self) -> None:
        self.publishes: list[tuple[str, bytes, int, bool]] = []

    def publish(
        self, topic: str, payload: bytes, qos: int = 0, retain: bool = False
    ) -> Any:
        self.publishes.append((topic, payload, qos, retain))
        return None


def test_status_json_round_trips() -> None:
    status = Status(panel="inky_impression_7_3", last_digest="abc123")
    obj = json.loads(status.to_json())
    assert obj["state"] == "idle"
    assert obj["panel"] == "inky_impression_7_3"
    assert obj["last_digest"] == "abc123"
    assert obj["last_paint_at"] is None
    assert obj["last_error"] is None
    assert "uptime_s" in obj
    assert "fw_version" in obj


def test_publish_now_uses_retained_qos1_on_status_topic() -> None:
    status = Status(panel="x")
    publisher = FakePublisher()
    heartbeat = Heartbeat(status=status, publisher=publisher, topic=STATUS_TOPIC)
    heartbeat.publish_now()
    assert len(publisher.publishes) == 1
    topic, payload, qos, retain = publisher.publishes[0]
    assert topic == STATUS_TOPIC
    assert qos == 1
    assert retain is True
    assert json.loads(payload)["state"] == "idle"


def test_publish_offline_sends_offline_payload_retained() -> None:
    status = Status(panel="x")
    publisher = FakePublisher()
    heartbeat = Heartbeat(status=status, publisher=publisher, topic=STATUS_TOPIC)
    heartbeat.publish_offline()
    assert len(publisher.publishes) == 1
    topic, payload, qos, retain = publisher.publishes[0]
    assert topic == STATUS_TOPIC
    assert qos == 1
    assert retain is True
    assert payload == OFFLINE_WILL_PAYLOAD
    assert json.loads(payload)["state"] == "offline"


def test_status_summary_is_human_readable() -> None:
    status = Status(panel="x", last_digest="deadbeef")
    summary = status_summary(status)
    assert "state=idle" in summary
    assert "digest=deadbeef" in summary


def test_status_topic_builds_from_device_id() -> None:
    assert status_topic("pi_png") == "tesserae/pi_png/status"
    assert status_topic("pi_lounge") == "tesserae/pi_lounge/status"


def test_status_discovery_fields_round_trip() -> None:
    status = Status(
        panel="inky_impression_7_3",
        kind="pi_png_client",
        panel_w=800,
        panel_h=480,
        fw_version="1.2.3",
        ip="192.168.1.42",
    )
    obj = json.loads(status.to_json())
    assert obj["kind"] == "pi_png_client"
    assert obj["panel_w"] == 800
    assert obj["panel_h"] == 480
    assert obj["fw_version"] == "1.2.3"
    assert obj["ip"] == "192.168.1.42"


def test_status_kind_defaults_to_pi_png_client() -> None:
    obj = json.loads(Status(panel="x").to_json())
    assert obj["kind"] == "pi_png_client"
