import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from pydantic import ValidationError

from app.actions import OUTCOME_TTL_SECONDS, RobotActionProcessor
from app.config import Settings
from app.models import RobotAcknowledgement, RobotCommand
from app.mqtt_client import MockRobotMqttClient


class ManualTimer:
    def __init__(self, delay, callback):
        self.delay = delay
        self.callback = callback
        self.cancelled = False
        self.started = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        # Deliberately allow a callback racing with cancellation.
        self.callback()


class ActionsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.robot_id = uuid4()
        self.path = Path(self.directory.name) / "commands.json"
        self.acks = []
        self.timers = []
        self.now = 1000.0
        self.processor = self.make_processor()
        self.processor.connect()

    def timer_factory(self, delay, callback):
        timer = ManualTimer(delay, callback)
        self.timers.append(timer)
        return timer

    def make_processor(self, **kwargs):
        return RobotActionProcessor(
            robot_id=self.robot_id,
            state_path=self.path,
            publish_ack=self.acks.append,
            timer_factory=self.timer_factory,
            clock=lambda: self.now,
            **kwargs,
        )

    def command(self, **kwargs):
        return {
            "robot_id": str(self.robot_id),
            "command_id": str(uuid4()),
            "attempt": 1,
            "action": {
                "type": "CLEANING",
                "is_start": True,
                "is_finish": False,
                "direction": "NORTH",
                "distance": "1m",
            },
            **kwargs,
        }

    def receive(self, command, *, processor=None, topic=None):
        return (processor or self.processor).handle_message(
            topic or f"pleco/robots/{self.robot_id}/commands",
            json.dumps(command).encode(),
        )

    def test_exact_echo_after_nonblocking_twenty_second_timer(self):
        command = self.command()
        self.assertTrue(self.receive(command))
        self.assertEqual(self.acks, [])
        self.assertEqual(self.timers[0].delay, 20)
        self.assertTrue(self.timers[0].started)
        self.timers[0].fire()
        self.assertEqual(self.acks[0].model_dump(mode="json"), {**command, "ack": True})

    def test_defaults_and_decimal_distances(self):
        command = self.command()
        command["action"].pop("type")
        command["action"].pop("distance")
        parsed = RobotCommand.model_validate(command)
        self.assertEqual(parsed.action.type, "CLEANING")
        self.assertEqual(parsed.action.distance, "1m")
        for distance in ("0.5m", "0.001m", "12m", "1.0m"):
            with self.subTest(distance=distance):
                command["action"]["distance"] = distance
                self.assertTrue(self.receive({**command, "command_id": str(uuid4())}))

    def test_rejects_invalid_distances(self):
        for distance in ("0m", "0.00m", "-1m", "1cm", "NaNm", "1e3m", "01m", "1.m", "1" * 64 + "m", 1, None):
            with self.subTest(distance=distance):
                command = self.command()
                command["action"]["distance"] = distance
                self.assertFalse(self.receive(command))
        self.assertEqual(self.timers, [])

    def test_rejects_malformed_identity_topics_and_extra_fields(self):
        command = self.command()
        self.assertFalse(self.receive({**command, "robot_id": str(uuid4())}))
        self.assertFalse(self.receive(command, topic=f"pleco/robots/{uuid4()}/commands"))
        self.assertFalse(self.receive(command, topic=f"pleco/robots/{self.robot_id}/acks"))
        self.assertFalse(self.receive({**command, "group_id": str(uuid4())}))
        self.assertFalse(self.receive({**command, "ack": True}))
        self.assertFalse(self.receive({**command, "action": {**command["action"], "extra": 1}}))
        self.assertFalse(self.processor.handle_message(f"pleco/robots/{self.robot_id}/commands", b"invalid"))
        self.assertEqual(self.timers, [])

    def test_requires_strict_boolean_and_integer_fields(self):
        for attempt in (True, "1", 1.0, 0, 5):
            self.assertFalse(self.receive(self.command(attempt=attempt)))
        for flag in ("is_start", "is_finish"):
            for value in (0, 1, "true", None):
                command = self.command()
                command["action"][flag] = value
                self.assertFalse(self.receive(command))
            command = self.command()
            command["action"].pop(flag)
            self.assertFalse(self.receive(command))
        with self.assertRaises(ValidationError):
            RobotAcknowledgement.model_validate({**self.command(), "ack": 1})

    def test_pending_duplicate_shares_timer_and_completed_duplicate_replays(self):
        command = self.command()
        self.receive(command)
        self.receive(command)
        self.assertEqual(len(self.timers), 1)
        self.timers[0].fire()
        self.receive(command)
        self.assertEqual(len(self.timers), 1)
        self.assertEqual(len(self.acks), 2)
        self.assertEqual(self.acks[0], self.acks[1])
        self.timers[0].fire()
        self.assertEqual(len(self.acks), 2)

    def test_reused_identity_with_changed_action_is_rejected(self):
        command = self.command()
        self.receive(command)
        changed = {**command, "action": {**command["action"], "direction": "EAST"}}
        self.assertFalse(self.receive(changed))
        self.timers[0].fire()
        self.assertFalse(self.receive(changed))
        self.assertEqual(len(self.acks), 1)

    def test_failure_outcome_and_distinct_retry_attempts(self):
        processor = self.make_processor(force_failure=True)
        processor.connect()
        command = self.command()
        for attempt in range(1, 5):
            self.receive({**command, "attempt": attempt}, processor=processor)
            self.timers[-1].fire()
        self.assertEqual([ack.attempt for ack in self.acks], [1, 2, 3, 4])
        self.assertTrue(all(ack.ack is False for ack in self.acks))

    def test_completed_result_survives_restart_and_expires_after_twelve_hours(self):
        command = self.command()
        self.receive(command)
        self.timers[0].fire()
        processor = self.make_processor()
        processor.connect()
        self.receive(command, processor=processor)
        self.assertEqual(len(self.timers), 1)
        self.assertEqual(len(self.acks), 2)
        self.now += OUTCOME_TTL_SECONDS
        self.receive(command, processor=processor)
        self.assertEqual(len(self.timers), 2)

    def test_disconnect_cancels_timer_even_if_callback_races(self):
        command = self.command()
        self.receive(command)
        self.processor.disconnect()
        self.assertTrue(self.timers[0].cancelled)
        self.timers[0].fire()
        self.assertEqual(self.acks, [])
        self.assertFalse(self.receive(self.command()))
        self.processor.connect()
        self.receive(command)
        self.timers[0].fire()
        self.assertEqual(self.acks, [])
        self.assertEqual(len(self.timers), 1)
        self.assertTrue(self.receive(self.command()))

    def test_interrupted_receipt_is_not_restarted_after_process_restart(self):
        command = self.command()
        self.receive(command)
        self.processor.disconnect()
        processor = self.make_processor()
        processor.connect()
        self.assertTrue(self.receive(command, processor=processor))
        self.assertEqual(len(self.timers), 1)
        self.assertEqual(self.acks, [])

    def test_persistence_failure_does_not_ack_success(self):
        command = self.command()
        self.receive(command)
        with patch.object(self.processor, "_save", side_effect=OSError):
            self.timers[0].fire()
        self.assertEqual(self.acks, [])
        self.receive(command)
        self.assertEqual(len(self.timers), 1)

    def test_invalid_saved_state_fails_closed(self):
        self.path.write_text("invalid")
        with self.assertRaisesRegex(RuntimeError, "Invalid robot command state"):
            self.make_processor()


class MqttWiringTest(unittest.TestCase):
    def test_default_settings_preserve_command_and_connectivity_defaults(self):
        settings = Settings.model_construct(
            ROBOT_ID=uuid4(), ROBOT_SERIAL_NUM="test-robot", ROBOT_IP_ADDRESS="127.0.0.1",
        )
        self.assertEqual(settings.ROBOT_COMMAND_ACK_DELAY_SECONDS, 20)
        self.assertFalse(settings.ROBOT_COMMAND_FORCE_FAILURE)
        self.assertIsNone(settings.ROBOT_COMMAND_STATE_PATH)
        self.assertEqual(settings.ROBOT_STATE_PATH, Path("/data/sequence.txt"))
        self.assertEqual(settings.ROBOT_INITIAL_SEQUENCE, 0)
        self.assertEqual(settings.MQTT_PORT, 1883)

    def test_subscription_publish_heartbeat_and_disconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            robot_id = uuid4()
            # model_construct avoids all runtime configuration/environment sources.
            settings = Settings.model_construct(
                ROBOT_ID=robot_id,
                ROBOT_SERIAL_NUM="test-robot",
                ROBOT_IP_ADDRESS="127.0.0.1",
                ROBOT_STATE_PATH=Path(directory) / "sequence.txt",
            )
            mqtt = Mock()
            with patch("app.mqtt_client.mqtt.Client", return_value=mqtt):
                client = MockRobotMqttClient(settings)
            client._on_connect(mqtt, None, None, 0, None)
            mqtt.subscribe.assert_called_once_with(f"pleco/robots/{robot_id}/commands", qos=1)
            client.publish_heartbeat()
            self.assertEqual(json.loads(mqtt.publish.call_args.args[1])["event"], "robot.heartbeat")
            command = RobotCommand(
                robot_id=robot_id, command_id=uuid4(), attempt=1,
                action={"is_start": True, "is_finish": False, "direction": "WEST"},
            )
            ack = RobotAcknowledgement(**command.model_dump(), ack=True)
            client._publish_ack(ack)
            args, kwargs = mqtt.publish.call_args
            self.assertEqual(args[0], f"pleco/robots/{robot_id}/acks")
            self.assertEqual(json.loads(args[1]), ack.model_dump(mode="json"))
            self.assertEqual(kwargs, {"qos": 1, "retain": False})
            with patch.object(client.actions, "handle_message") as handle:
                message = SimpleNamespace(topic=client.command_topic, payload=command.model_dump_json().encode(), retain=True)
                client._on_message(mqtt, None, message)
                handle.assert_not_called()
                message.retain = False
                client._on_message(mqtt, None, message)
                handle.assert_called_once_with(message.topic, message.payload)
            client._on_disconnect(mqtt, None, None, 1, None)
            mqtt.publish.reset_mock()
            client._publish_ack(ack)
            client.publish_heartbeat()
            mqtt.publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
