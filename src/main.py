import logging
import sys
import discord

from src.config import load_settings
from src.discord_bot import NagisaDiscordBot
from src.digest_job import setup_scheduler
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(), override=True)

def setup_logging():
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    h = logging.StreamHandler(sys.stdout)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s")
    h.setFormatter(fmt)
    root.addHandler(h)

def main():
    setup_logging()
    st = load_settings()

    intents = discord.Intents.default()
    intents.message_content = True  # 重要：Discordの開発者ポータルで有効化も必要

    bot = NagisaDiscordBot(intents=intents, keepa_key=st.keepa_key, channel_map=st.channel_map)

    # スケジューラ起動
    #setup_scheduler(bot)  

    bot.run(st.discord_token)

if __name__ == "__main__":
    main()
