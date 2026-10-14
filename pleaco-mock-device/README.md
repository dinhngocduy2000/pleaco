# Pleco mock device

Python simulator for exactly one registered Pleco robot. It talks only to Mosquitto using
`pleco/robots/{ROBOT_ID}/status` and publishes `ONLINE` on connection, `OFFLINE` on graceful
shutdown, and an MQTT last-will `OFFLINE` event on unexpected disconnect.

Set `ROBOT_ID`, `ROBOT_SERIAL_NUM`, `ROBOT_IP_ADDRESS`, `MQTT_HOST`, and optionally MQTT
credentials in the runtime environment, then run `python -m app.main`.

The simulator persists its sequence number at `ROBOT_STATE_PATH` so restarts cannot emit stale
status events. `ROBOT_INITIAL_SEQUENCE` is used only when no state file exists.
