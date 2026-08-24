from typing import Optional, Any
from zoneinfo import ZoneInfo
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Discord Configuration
    DISCORD_TOKEN: str = Field(default="", description="Discord bot token")
    ALLOWED_USER_ID: Optional[int] = Field(default=None, description="Your Discord User ID for DMs")
    DISCORD_CHANNEL_ID: Optional[int] = Field(default=None, description="Optional channel fallback ID")

    # Groq Configuration
    GROQ_API_KEY: str = Field(default="", description="Groq API key")
    GROQ_MODEL: str = Field(default="openai/gpt-oss-120b", description="Groq model ID")

    # Storage & Timezone
    DATABASE_PATH: str = Field(default="tracker.db", description="Path to SQLite database")
    TIMEZONE: str = Field(default="Asia/Kuala_Lumpur", description="IANA timezone name")
    DAILY_SUMMARY_TIME: str = Field(default="22:00", description="Daily summary dispatch time HH:MM")
    MORNING_BRIEFING_TIME: str = Field(default="08:30", description="Morning briefing dispatch time HH:MM")

    @field_validator("ALLOWED_USER_ID", "DISCORD_CHANNEL_ID", mode="before")
    @classmethod
    def parse_optional_int(cls, value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            val_clean = value.strip()
            if not val_clean:
                return None
            if val_clean.isdigit():
                return int(val_clean)
        if isinstance(value, int):
            return value
        return None

    @property
    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.TIMEZONE)
        except Exception:
            return ZoneInfo("UTC")

    @property
    def summary_hour_minute(self) -> tuple[int, int]:
        parts = self.DAILY_SUMMARY_TIME.split(":")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return int(parts[0]), int(parts[1])
        return 22, 0

    @property
    def morning_hour_minute(self) -> tuple[int, int]:
        parts = self.MORNING_BRIEFING_TIME.split(":")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return int(parts[0]), int(parts[1])
        return 8, 30


settings = Settings()
