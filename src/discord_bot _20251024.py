import asyncio
import io
import logging
from typing import Optional
from .sheets_client import append_product

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
        self.keepa_key = keepa_key
        self.channel_map = channel_map

    async def on_ready(self):
        log.info(f"✅ Logged in as {self.user} (id={self.user.id}) at {now_jst()}")
        # イベントループが立った後にスケジューラを開始
        await ensure_scheduler_started(self)        

    async def on_message(self, message: discord.Message):
        # 自分やBotには反応しない
        if message.author.bot:
            return

        # 抽出対象：テキスト＋（のちほどOCRで画像も）
        text = message.content or ""
        ids = extract_ids(text)

        # 画像値札は次ステップでOCRします（今日はテキストだけ拾う）
        price_candidate = extract_price_candidate_from_text(text)

        # チャンネル名からチェーン補完（例："ヤマダ" -> "ヤマダデンキ"）
        channel_name = message.channel.name
        store_chain_from_channel = normalize_store_by_channel(channel_name, self.channel_map)

        # コメントからも店名探索（あればコメントを優先）
        store_chain_from_comment, store_branch = extract_store_from_comment(text)
        store_chain = store_chain_from_comment or store_chain_from_channel

        asin = ids.get("asin")
        jan = ids.get("jan")
        title = None
        amazon_price = None
        if not asin and not jan:
            # ASIN/JANない場合はスルー（メンション返信などは別で）
            return

        if asin or jan:
            try:
                keepa = await asyncio.to_thread(fetch_product_from_keepa, asin, self.keepa_key, jan)
                title = keepa.get("title")
                amazon_price = keepa.get("amazon_price")
                # JAN検索のときにASINが得られたら埋める
                asin = asin or keepa.get("asin")
            except Exception as e:
                log.exception(f"Keepa fetch failed for ASIN={asin} JAN={jan}: {e}")

        try:
            append_product({
                "asin": asin,
                "jan": jan,
                "title": title,
                "amazon_price": amazon_price,
                "store_chain": store_chain,
                "store_branch": store_branch,
                "buy_price": price_candidate,  # OCR実装後ここに入れる
                "user": f"{message.author.name}#{message.author.discriminator}",
                "channel": message.channel.name,
            })
        except Exception as e:
            log.exception(f"append_product failed: {e}")

        # ここでは「動いてるか」を見せるために軽く返信（あとでEmbedに改造）
        lines = []
        lines.append("🧾 **ナギサが見つけたよ**")
        if title: lines.append(f"・商品名：{title}")
        if asin:  lines.append(f"・ASIN：`{asin}`")
        if jan:   lines.append(f"・JAN：`{jan}`")
        if amazon_price: lines.append(f"・Amazon参考価格：¥{amazon_price:,}")
        if price_candidate: lines.append(f"・仕入れ値（テキスト候補）：¥{price_candidate:,}")
        if store_chain: lines.append(f"・店舗：{store_chain}" + (f"（{store_branch}）" if store_branch else ""))

        reply = "\n".join(lines) if len(lines) > 1 else "見つけた情報を記録しました。"
        try:
            await message.reply(reply, mention_author=False)
        except Exception as e:
            log.warning(f"reply failed: {e}")
