import json
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Settings:
    discord_token: str
    keepa_key: str
    app_timezone: str = "Asia/Tokyo"
    digest_time: str = "08:30"
    channel_map: dict = None

def load_settings() -> Settings:
    token = os.getenv("DISCORD_BOT_TOKEN", "")
    keepa = os.getenv("KEEPA_API_KEY", "")
    tz = os.getenv("APP_TIMEZONE", "Asia/Tokyo")
    digest = os.getenv("DIGEST_TIME", "08:30")

    with open(os.path.join(os.path.dirname(__file__), "channel_map.json"), "r", encoding="utf-8") as f:
        channel_map = json.load(f)

    return Settings(
        discord_token=token,
        keepa_key=keepa,
        app_timezone=tz,
        digest_time=digest,
        channel_map=channel_map
    )
