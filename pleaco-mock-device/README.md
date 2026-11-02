# Pleco mock device

Python simulator for exactly one registered Pleco robot. It talks to Mosquitto using
`pleco/robots/{ROBOT_ID}/status` and publishes `ONLINE` on connection, `OFFLINE` on graceful
shutdown, and an MQTT last-will `OFFLINE` event on unexpected disconnect. While connected, it
publishes a `robot.heartbeat` event every 10 seconds. Heartbeats refresh backend `last_seen_at`
without changing status or consuming the online/offline sequence number.

Set `ROBOT_ID`, `ROBOT_SERIAL_NUM`, `ROBOT_IP_ADDRESS`, `MQTT_HOST`, and optionally MQTT
credentials in the runtime environment, then run `python -m app.main`.

The simulator persists its sequence number at `ROBOT_STATE_PATH` so restarts cannot emit stale
status events. `ROBOT_INITIAL_SEQUENCE` is used only when no state file exists.

See [HEARTBEAT.md](HEARTBEAT.md) for MQTT event payloads, timing, and backend handling.

## Cleaning commands

The simulator subscribes to `pleco/robots/{ROBOT_ID}/commands` at QoS 1. It validates
the topic, its own UUID, and the strict command schema before scheduling a result.
Commands for another robot, retained commands, and malformed payloads are ignored.
Each command is one movement result; start/finish flags are echoed metadata.

```json
{
  "robot_id": "d67e006a-c9bd-4dbf-852d-15224a5a283d",
  "command_id": "ea58a802-e59b-42f0-b511-7852ef7e7110",
  "attempt": 1,
  "action": {
    "type": "CLEANING",
    "is_start": true,
    "is_finish": false,
    "direction": "NORTH",
    "distance": "1m"
  }
}
```

After 20 seconds, it publishes the same validated command plus top-level `"ack": true`
to `pleco/robots/{ROBOT_ID}/acks` at QoS 1, without retaining it. This timer never
blocks heartbeats. `direction` accepts NORTH/SOUTH/EAST/WEST; distance accepts positive
decimal meter strings (`"1m"`, `"0.5m"`) and defaults to `"1m"`. The action type defaults
to CLEANING; both boolean flags are required. Attempts must be integers from 1 through 4.
The backend owns coordinate calculation and position caching; the simulator emits no
additional position telemetry for this first command flow.

| Setting | Default | Purpose |
|---|---|---|
| `ROBOT_COMMAND_ACK_DELAY_SECONDS` | `20` | Positive acknowledgement delay in seconds. |
| `ROBOT_COMMAND_FORCE_FAILURE` | `false` | Return `ack=false` for every attempt to exercise retries. |
| `ROBOT_COMMAND_STATE_PATH` | `commands.json` beside `ROBOT_STATE_PATH` | Persistent deduplication state, separate from status sequence state. |

In-flight duplicates with the same command ID and attempt share a timer. Completed
duplicates replay the saved result immediately, including after a restart. Results
expire 12 hours after completion. Reusing an ID/attempt with a changed action is ignored.
Disconnect and shutdown cancel pending timers. Persisted receipt tombstones prevent
interrupted attempts from restarting on redelivery or after a process restart; a fresh
backend command ID is required. Reconnection does not automatically publish results.

Run deterministic simulator tests without brokers or runtime credentials:

```sh
python -m unittest discover -s tests -v
```
