from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, IPvAnyAddress


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
