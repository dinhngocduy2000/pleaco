from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress, StrictBool, field_validator


class Direction(str, Enum):
    NORTH = "NORTH"
    SOUTH = "SOUTH"
    EAST = "EAST"
    WEST = "WEST"


class ActionType(str, Enum):
    CLEANING = "CLEANING"


class BaseAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ActionType = ActionType.CLEANING
    is_start: StrictBool
    is_finish: StrictBool


class CleaningAction(BaseAction):
    type: Literal[ActionType.CLEANING] = ActionType.CLEANING
    direction: Direction
    distance: str = Field(default="1m", max_length=64, pattern=r"^(?:0|[1-9]\d*)(?:\.\d+)?m$")

    @field_validator("distance")
    @classmethod
    def positive_distance(cls, value: str) -> str:
        if Decimal(value[:-1]) <= 0:
            raise ValueError("Distance must be positive")
        return value


class RobotCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    robot_id: UUID
    command_id: UUID
    attempt: int = Field(strict=True, ge=1, le=4)
    action: CleaningAction


class RobotAcknowledgement(RobotCommand):
    ack: StrictBool


class RobotStatusMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: UUID
    robot_id: UUID
    ip_address: IPvAnyAddress
    event: Literal["robot.online", "robot.offline"]
    sequence_number: int
    timestamp: datetime
    data: dict[str, str]


class RobotHeartbeatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: UUID
    robot_id: UUID
    event: Literal["robot.heartbeat"]
    timestamp: datetime


def status_message(
    *,
    robot_id: UUID,
    ip_address: IPvAnyAddress,
    event: Literal["robot.online", "robot.offline"],
    sequence_number: int,
) -> RobotStatusMessage:
    status = "ONLINE" if event == "robot.online" else "OFFLINE"
    return RobotStatusMessage(
        message_id=uuid4(),
        robot_id=robot_id,
        ip_address=ip_address,
        event=event,
        sequence_number=sequence_number,
        timestamp=datetime.now(timezone.utc),
        data={"connection_status": status},
    )


def heartbeat_message(*, robot_id: UUID) -> RobotHeartbeatMessage:
    return RobotHeartbeatMessage(
        message_id=uuid4(),
        robot_id=robot_id,
        event="robot.heartbeat",
        timestamp=datetime.now(timezone.utc),
    )
