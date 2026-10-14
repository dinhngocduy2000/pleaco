import signal
import time

from app.config import settings
from app.mqtt_client import MockRobotMqttClient


def main() -> None:
    client = MockRobotMqttClient(settings)
    should_stop = False

    def stop(_signal, _frame) -> None:
        nonlocal should_stop
        should_stop = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    client.connect()
    try:
        while not should_stop:
            time.sleep(0.5)
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
