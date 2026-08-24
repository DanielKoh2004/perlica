from typing import Optional
from zoneinfo import ZoneInfo
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Discord Configuration
    DISCORD_TOKEN: str = Field(default="", description="Discord bot token")
    DISCORD_CHANNEL_ID: Optional[int] = Field(default=None, description="Designated private channel ID")
    ALLOWED_USER_ID: Optional[int] = Field(default=None, description="Restricted user ID")

    # Groq Configuration
    GROQ_API_KEY: str = Field(default="", description="Groq API key")
    GROQ_MODEL: str = Field(default="llama-3.3-70b-versatile", description="Groq model ID")

    # Storage & Timezone
    DATABASE_PATH: str = Field(default="tracker.db", description="Path to SQLite database")
    TIMEZONE: str = Field(default="Asia/Kuala_Lumpur", description="IANA timezone name")
    DAILY_SUMMARY_TIME: str = Field(default="22:00", description="Daily summary dispatch time HH:MM")

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


settings = Settings()
