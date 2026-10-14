from pathlib import Path

from pydantic import Field, IPvAnyAddress
from pydantic_settings import BaseSettings, SettingsConfigDict
from uuid import UUID


class Settings(BaseSettings):
    """Non-secret device configuration supplied by its runtime environment."""

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore")

    ROBOT_ID: UUID
    ROBOT_SERIAL_NUM: str = Field(min_length=1)
    ROBOT_IP_ADDRESS: IPvAnyAddress
    MQTT_HOST: str = "localhost"
    MQTT_PORT: int = 1883
    MQTT_USERNAME: str = ""
    MQTT_PASSWORD: str = ""
    ROBOT_INITIAL_SEQUENCE: int = Field(default=0, ge=0)
    ROBOT_STATE_PATH: Path = Path("/data/sequence.txt")


settings = Settings()
