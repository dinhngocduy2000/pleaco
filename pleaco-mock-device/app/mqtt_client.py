import json
import threading
from pathlib import Path

import paho.mqtt.client as mqtt

from app.config import Settings
from app.models import heartbeat_message, status_message


class MockRobotMqttClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sequence_number = self._load_sequence_number()
        self.topic = f"pleco/robots/{settings.ROBOT_ID}/status"
        self.connected = threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=str(settings.ROBOT_ID))
        if settings.MQTT_USERNAME:
            self.client.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD or None)
        self.client.on_connect = self._on_connect
        self.client.on_publish = self._on_publish
        self.client.on_disconnect = self._on_disconnect
        self._configure_last_will()

    def _configure_last_will(self) -> None:
        # This is prepared before failure; the backend uses receive time for effective liveness.
        will = status_message(
            robot_id=self.settings.ROBOT_ID,
            ip_address=self.settings.ROBOT_IP_ADDRESS,
            event="robot.offline",
            sequence_number=self.sequence_number + 2,
        )
        self.client.will_set(self.topic, will.model_dump_json(), qos=1, retain=False)
        # Reserve the will sequence before connecting. A crashed process may publish it,
        # so the next process must continue after it rather than repeat an older value.
        self._save_sequence_number(self.sequence_number + 2)

    def connect(self) -> None:
        print(
            f"Connecting robot {self.settings.ROBOT_ID} to MQTT at "
            f"{self.settings.MQTT_HOST}:{self.settings.MQTT_PORT}",
            flush=True,
        )
        self.client.connect(self.settings.MQTT_HOST, self.settings.MQTT_PORT, keepalive=30)
        self.client.loop_start()

    def disconnect(self) -> None:
        self.connected.clear()
        self.publish("robot.offline", wait_for_confirmation=True)
        self.client.disconnect()
        self.client.loop_stop()

    def publish(self, event: str, *, wait_for_confirmation: bool = False) -> None:
        self.sequence_number += 1
        self._save_sequence_number(self.sequence_number)
        message = status_message(
            robot_id=self.settings.ROBOT_ID,
            ip_address=self.settings.ROBOT_IP_ADDRESS,
            event=event,
            sequence_number=self.sequence_number,
        )
        info = self.client.publish(self.topic, message.model_dump_json(), qos=1, retain=False)
        if wait_for_confirmation:
            info.wait_for_publish()
        print(
            f"Queued {event} sequence={self.sequence_number} topic={self.topic}",
            flush=True,
        )

    def publish_heartbeat(self) -> None:
        if not self.connected.is_set():
            return
        message = heartbeat_message(robot_id=self.settings.ROBOT_ID)
        info = self.client.publish(self.topic, message.model_dump_json(), qos=1, retain=False)
        print(f"Queued robot.heartbeat topic={self.topic}", flush=True)

    def _on_connect(self, _client, _userdata, _flags, reason_code, _properties) -> None:
        if reason_code == 0:
            print("Connected to MQTT broker", flush=True)
            self.connected.set()
            self.publish("robot.online")
        else:
            print(f"MQTT broker rejected connection: {reason_code}", flush=True)

    def _on_publish(self, _client, _userdata, message_id, _reason_code, _properties) -> None:
        print(f"MQTT publish confirmed message_id={message_id}", flush=True)

    def _on_disconnect(self, _client, _userdata, disconnect_flags, reason_code, _properties) -> None:
        self.connected.clear()
        if reason_code != 0:
            print(
                f"Unexpected MQTT disconnect: {reason_code} flags={disconnect_flags}",
                flush=True,
            )

    def _load_sequence_number(self) -> int:
        path = self.settings.ROBOT_STATE_PATH
        try:
            return max(int(path.read_text().strip()), self.settings.ROBOT_INITIAL_SEQUENCE)
        except FileNotFoundError:
            return self.settings.ROBOT_INITIAL_SEQUENCE
        except ValueError:
            raise RuntimeError(f"Invalid robot sequence state at {path}")

    def _save_sequence_number(self, sequence_number: int) -> None:
        path: Path = self.settings.ROBOT_STATE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = self._load_sequence_number() if path.exists() else 0
        if sequence_number > existing:
            path.write_text(str(sequence_number))
