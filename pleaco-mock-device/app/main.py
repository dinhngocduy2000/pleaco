import signal
import time

from app.config import settings
from app.mqtt_client import MockRobotMqttClient

HEARTBEAT_INTERVAL_SECONDS = 10


def main() -> None:
    client = MockRobotMqttClient(settings)
    should_stop = False

    def stop(_signal, _frame) -> None:
        nonlocal should_stop
        should_stop = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    client.connect()
    next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL_SECONDS
    try:
        while not should_stop:
            now = time.monotonic()
            if now >= next_heartbeat:
                client.publish_heartbeat()
                next_heartbeat = now + HEARTBEAT_INTERVAL_SECONDS
            time.sleep(min(0.5, max(0, next_heartbeat - now)))
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
