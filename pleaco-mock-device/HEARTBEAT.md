# Mock Device Heartbeat and MQTT Status Events

The mock device publishes connection and heartbeat events to Mosquitto. The backend validates
these MQTT messages, forwards them through RabbitMQ, and updates the robot's persisted status or
`last_seen_at` value.

## MQTT topic

Every event uses the robot-specific status topic:

```text
pleco/robots/{ROBOT_ID}/status
```

The device publishes with QoS 1 and does not retain messages. The backend subscribes to
`pleco/robots/+/status`.

## Events

### `robot.online`

Published after the MQTT connection is established.

```json
{
  "message_id": "a-unique-uuid",
  "robot_id": "the-robot-uuid",
  "ip_address": "192.168.10.24",
  "event": "robot.online",
  "sequence_number": 1001,
  "timestamp": "2026-10-14T12:00:00+00:00",
  "data": {
    "connection_status": "ONLINE"
  }
}
```

The backend checks that the `robot_id` matches the ID in the topic, then handles this as a
connection status event. Its receive time becomes `last_seen_at`.

### `robot.heartbeat`

Published every 10 seconds while the MQTT client is connected.

```json
{
  "message_id": "a-unique-uuid",
  "robot_id": "the-robot-uuid",
  "event": "robot.heartbeat",
  "timestamp": "2026-10-14T12:00:10+00:00"
}
```

Heartbeats have their own message ID and do not include or consume the online/offline status
sequence number. The backend accepts heartbeats only for known robots currently marked online,
deduplicates each message ID in Redis, and updates `last_seen_at` using backend receive time. A
heartbeat does not change connection or operational status and does not emit a status-change
Socket.IO event.

### `robot.offline`

Published during graceful shutdown. The same offline payload is configured as the MQTT Last Will,
which the broker publishes if it detects an unexpected disconnection.

```json
{
  "message_id": "a-unique-uuid",
  "robot_id": "the-robot-uuid",
  "ip_address": "192.168.10.24",
  "event": "robot.offline",
  "sequence_number": 1002,
  "timestamp": "2026-10-14T12:05:00+00:00",
  "data": {
    "connection_status": "OFFLINE"
  }
}
```

The Last Will is configured before the device connects. Heartbeats do not advance the status
sequence, so the Will's sequence remains newer than the online event for that connection. The
backend continues to apply its existing sequence checks to online/offline status events.

## Timing and offline detection

- Heartbeat interval: 10 seconds.
- MQTT keepalive: 30 seconds.
- Unexpected disconnect handling: the broker publishes the configured Last Will after it detects
  the disconnected client.
- There is no separate backend timeout job for missed heartbeats. Heartbeats refresh freshness;
  online/offline events determine connection status.

## Configuration

The mock-device Compose file configures the broker host and port and supplies each robot's ID,
serial number, IP address, and sequence state path. The heartbeat interval is currently defined in
`app/main.py` as `HEARTBEAT_INTERVAL_SECONDS`.
