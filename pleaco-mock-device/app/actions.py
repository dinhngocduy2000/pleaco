"""Delayed, idempotent command outcomes, independent of the MQTT transport."""

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models import RobotAcknowledgement, RobotCommand

logger = logging.getLogger(__name__)
OUTCOME_TTL_SECONDS = 12 * 60 * 60


class StoredOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: RobotCommand
    expires_at: float = Field(allow_inf_nan=False)
    # None is a persisted receipt: interrupted work must not restart on replay.
    ack: bool | None


class RobotActionProcessor:
    def __init__(
        self,
        *,
        robot_id: UUID,
        state_path: Path,
        publish_ack: Callable[[RobotAcknowledgement], None],
        delay_seconds: float = 20,
        force_failure: bool = False,
        clock: Callable[[], float] = time.time,
        timer_factory: Callable = threading.Timer,
    ) -> None:
        self.robot_id = robot_id
        self.state_path = state_path
        self._publish_ack = publish_ack
        self._delay_seconds = delay_seconds
        self._force_failure = force_failure
        self._clock = clock
        self._timer_factory = timer_factory
        self._lock = threading.RLock()
        self._connected = False
        self._pending: dict[tuple[UUID, int], threading.Timer] = {}
        self._outcomes: dict[tuple[UUID, int], StoredOutcome] = {}
        self._load()

    @staticmethod
    def _key(command: RobotCommand) -> tuple[UUID, int]:
        return command.command_id, command.attempt

    def connect(self) -> None:
        with self._lock:
            self._connected = True

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False
            for timer in self._pending.values():
                timer.cancel()
            self._pending.clear()

    def handle_message(self, topic: str, payload: bytes) -> bool:
        """Accept one validated command, returning False for ignored deliveries."""
        if topic != f"pleco/robots/{self.robot_id}/commands":
            return False
        try:
            command = RobotCommand.model_validate_json(payload)
        except ValidationError:
            return False
        if command.robot_id != self.robot_id:
            return False
        key = self._key(command)
        with self._lock:
            if not self._connected:
                return False
            self._prune()
            previous = self._outcomes.get(key)
            if previous is not None:
                if previous.command != command:
                    return False
                logger.info(
                    "Robot action duplicate robot_id=%s command_id=%s attempt=%s completed=%s",
                    self.robot_id, command.command_id, command.attempt, previous.ack is not None,
                )
                if previous.ack is not None:
                    self._publish_ack(self._ack(previous))
                return True
            self._outcomes[key] = StoredOutcome(
                command=command,
                expires_at=self._clock() + OUTCOME_TTL_SECONDS,
                ack=None,
            )
            # Persist receipt before starting: a restart cannot execute it twice.
            try:
                self._save()
            except OSError:
                self._outcomes.pop(key, None)
                raise
            timer = self._timer_factory(self._delay_seconds, lambda: self._complete(key))
            timer.daemon = True
            self._pending[key] = timer
            timer.start()
            logger.info(
                "Robot action accepted robot_id=%s command_id=%s attempt=%s",
                self.robot_id, command.command_id, command.attempt,
            )
            return True

    def _complete(self, key: tuple[UUID, int]) -> None:
        with self._lock:
            if not self._connected or key not in self._pending:
                return
            self._pending.pop(key)
            outcome = self._outcomes[key]
            outcome.ack = not self._force_failure
            outcome.expires_at = self._clock() + OUTCOME_TTL_SECONDS
            try:
                self._save()
            except OSError:
                # No ACK without a persistent result. Leave the receipt tombstone.
                outcome.ack = None
                logger.error("Unable to persist robot command outcome")
                return
            logger.info(
                "Robot action completed robot_id=%s command_id=%s attempt=%s ack=%s",
                self.robot_id, outcome.command.command_id, outcome.command.attempt, outcome.ack,
            )
            self._publish_ack(self._ack(outcome))

    @staticmethod
    def _ack(outcome: StoredOutcome) -> RobotAcknowledgement:
        return RobotAcknowledgement(**outcome.command.model_dump(), ack=outcome.ack)

    def _prune(self) -> None:
        now = self._clock()
        self._outcomes = {
            key: outcome for key, outcome in self._outcomes.items()
            if outcome.expires_at > now or key in self._pending
        }

    def _load(self) -> None:
        try:
            raw = json.loads(self.state_path.read_text())
        except FileNotFoundError:
            return
        except (ValueError, UnicodeError) as error:
            raise RuntimeError("Invalid robot command state") from error
        try:
            if not isinstance(raw, dict) or raw.get("version") != 1:
                raise ValueError("Unsupported state format")
            for record in raw["outcomes"]:
                outcome = StoredOutcome.model_validate(record)
                if outcome.command.robot_id != self.robot_id:
                    raise ValueError("Robot identity mismatch in command state")
                self._outcomes[self._key(outcome.command)] = outcome
            self._prune()
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("Invalid robot command state") from error

    def _save(self) -> None:
        self._prune()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(json.dumps({
            "version": 1,
            "outcomes": [outcome.model_dump(mode="json") for outcome in self._outcomes.values()],
        }))
        temporary.replace(self.state_path)
