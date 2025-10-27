import asyncio
import io
import logging
import time
from dataclasses import dataclass, field
from typing import Optional,List, Dict, Tuple
from .sheets_client import append_product
from .openai_client import chat_simple
from .persona import SYSTEM_PROMPT, role_address
import os
import discord

BUNDLE_INACTIVITY_SEC = 20
BUNDLE_MAX_WINDOW_SEC = 120

from .extract import (
    extract_ids,
    extract_price_candidate_from_text,
    normalize_store_by_channel,
    extract_store_from_comment
)
from .keepa_client import fetch_product_from_keepa
from .utils import now_jst
from .digest_job import ensure_scheduler_started

log = logging.getLogger(__name__)

@dataclass
class Bundle:
    channel_id: int
    user_id: int
    messages: List[discord.Message] = field(default_factory=list)
    created_at: float = field(default_factory=lambda: time.time())
    last_at: float = field(default_factory=lambda: time.time())
    task: Optional[asyncio.Task] = None


class NagisaDiscordBot(discord.Client):
    def __init__(self, *, intents: discord.Intents, keepa_key: str, channel_map: dict):
        super().__init__(intents=intents)
        owner_ids = set(int(x) for x in os.getenv("NAGISA_OWNER_IDS","").split(",") if x.strip().isdigit())
        self.owner_ids = owner_ids
        self.keepa_key = keepa_key
        self.channel_map = channel_map
        self.bundles: Dict[Tuple[int, int], Bundle] = {}

    async def on_ready(self):
        log.info(f"✅ Logged in as {self.user} (id={self.user.id}) at {now_jst()}")
        # イベントループが立った後にスケジューラを開始
        await ensure_scheduler_started(self)



    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        content = (message.content or "").strip()
        mentioned_me = self.user.mentioned_in(message)
        called_name = ("ナギサ" in content) or content.lower().startswith("nagisa:")

        if mentioned_me or called_name:
            who = role_address(message.author.id, self.owner_ids)
            # 会話の前提（必要なら短く追加）
            user_prompt = f"{who}からのメッセージ:\n{content}\n\n返答は3行以内で。必要なら箇条書き。"
            try:
                reply = await chat_simple(SYSTEM_PROMPT, user_prompt)
                await message.reply(reply, mention_author=False)
            except Exception as e:
                log.warning(f"chat reply failed: {e}")
                fallback = "いまナギサのおしゃべり頭脳に接続が集中してるみたい…💦 抽出や記録は動いてるから、もう少ししたらまた呼んでねっ。"
                await message.reply(fallback, mention_author=False)
                # 管理者（お兄さま）にはDMで詳細通知してもOK
                # 会話のときはここで終了（商材抽出とは独立）
            # 商材投稿と会話を混ぜる場合はこのreturnを外してOK
            return

        key = (message.channel.id, message.author.id)
        b = self.bundles.get(key)
        now = time.time()

        # 既存のbundleがなければ新規作成
        if not b:
            b = Bundle(channel_id=message.channel.id, user_id=message.author.id)
            self.bundles[key] = b
        b.messages.append(message)
        b.last_at = now

        # 古いタスクをキャンセルして再タイマー
        if b.task and not b.task.done():
            b.task.cancel()
        b.task = asyncio.create_task(self._bundle_timer(key))

    async def _bundle_timer(self, key: Tuple[int, int]):
        try:
            while True:
                await asyncio.sleep(1)
                b = self.bundles.get(key)
                if not b:
                    return
                now = time.time()
                if (now - b.last_at) >= BUNDLE_INACTIVITY_SEC or (now - b.created_at) >= BUNDLE_MAX_WINDOW_SEC:
                    await self.flush_bundle(key)
                    return
        except asyncio.CancelledError:
            return

    async def flush_bundle(self, key: Tuple[int, int]):
        b = self.bundles.pop(key, None)
        if not b or not b.messages:
            return

        # 全メッセージ結合
        texts = [m.content for m in b.messages if m.content]
        combined = "\n".join(texts)
        log.info(f"[bundle] flush user={b.user_id} ch={b.channel_id} lines={len(texts)}")

        ids = extract_ids(combined)
        asin = ids.get("asin")
        jan = ids.get("jan")

        channel_obj = self.get_channel(b.channel_id)
        store_chain_from_channel = normalize_store_by_channel(channel_obj.name if channel_obj else "", self.channel_map)
        store_chain_from_comment, store_branch = extract_store_from_comment(combined)
        store_chain = store_chain_from_comment or store_chain_from_channel
        price_candidate = extract_price_candidate_from_text(combined)

        title, amazon_price = None, None
        if asin or jan:
            try:
                keepa = await asyncio.to_thread(fetch_product_from_keepa, asin, self.keepa_key, jan)
                title = keepa.get("title")
                amazon_price = keepa.get("amazon_price")
                asin = asin or keepa.get("asin")
            except Exception as e:
                log.exception(f"Keepa fetch failed (bundle) for ASIN={asin} JAN={jan}: {e}")

        if not (asin or jan):
        lines = ["🧾 **ナギサが調べたよ！**"]
        if title: lines.append(f"・商品名：{title}")
        if asin: lines.append(f"・ASIN：`{asin}`")
        if jan: lines.append(f"・JAN：`{jan}`")
        lines.append(f"・Amazon参考価格：{'—' if amazon_price is None else f'¥{amazon_price:,}'}")
        if price_candidate: lines.append(f"・仕入れ値（候補）：¥{price_candidate:,}")
        if store_chain: lines.append(f"・店舗：{store_chain}" + (f"（{store_branch}）" if store_branch else ""))

        reply = "\n".join(lines)
        try:
            await b.messages[-1].reply(reply, mention_author=False)
        except Exception as e:
            log.warning(f"reply failed (bundle): {e}")

        # Sheets 書き込みはバックグラウンドで実行（ボットを止めない）
        payload = {
            "asin": asin,
            "jan": jan,
            "title": title,
            "amazon_price": amazon_price,
            "store_chain": store_chain,
            "store_branch": store_branch,
            "buy_price": price_candidate,
            "user": f"{b.messages[0].author.name}#{b.messages[0].author.discriminator}",
            "channel": channel_obj.name if channel_obj else "",
        }
        asyncio.create_task(self._append_to_sheets(payload))

    async def _append_to_sheets(self, payload: dict):
        """Sheets への書き込みをイベントループから切り離して実行。失敗はログのみ。"""
        if os.getenv("NAGISA_DISABLE_SHEETS") == "1":
            log.info("[bundle] sheets disabled; skip append")
            return
        try:
            t0 = time.time()
            await asyncio.wait_for(asyncio.to_thread(append_product, payload), timeout=12)
            log.info(f"[bundle] sheets appended in {time.time()-t0:.2f}s")
        except asyncio.TimeoutError:
            log.warning("[bundle] Sheets append timed out (background)")
        except Exception as e:
            log.exception(f"[bundle] append_product failed (background): {e}")