from datetime import datetime, timedelta, timezone
import asyncio
import discord
import logging
import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from .sheets_client import fetch_yesterday_records
from .openai_client import chat_simple
from .persona import SYSTEM_PROMPT

log = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

def _get_target_channel(bot: discord.Client):
    """DIGEST_CHANNEL_ID（優先）→ 'bot-log'（フォールバック）"""
    ch_id = os.getenv("DIGEST_CHANNEL_ID")
    if ch_id and ch_id.isdigit():
        ch = bot.get_channel(int(ch_id))
        if ch:
            return ch
    return discord.utils.get(bot.get_all_channels(), name="bot-log")

def _parse_hhmm(value: str, default: str) -> tuple[int,int]:
    s = (value or default)
    try:
        h, m = [int(x) for x in s.split(":")]
        return h, m
    except Exception:
        dh, dm = [int(x) for x in default.split(":")]
        return dh, dm

async def post_daily_digest(bot: discord.Client):
    try:
        records = await asyncio.to_thread(fetch_yesterday_records)
    except Exception as e:
        log.warning(f"[digest] sheets fetch failed: {e}")
        return
    if not records:
        log.info("[digest] no records for yesterday -> skip")
        return

    target = _get_target_channel(bot)
    if not target:
        log.warning("[digest] target channel 'bot-log' not found -> skip")
        return

    # 1ページあたり最大25件（Embedの仕様）
    PAGE_SIZE = 25
    total_pages = (len(records) + PAGE_SIZE - 1) // PAGE_SIZE

    log.info(f"[digest] posting {len(records)} records in {total_pages} pages")

    for page in range(total_pages):
        chunk = records[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        embed = discord.Embed(
            title=f"🌅 昨日の商材まとめ（{len(records)}件） - {page+1}/{total_pages}",
            description="お兄さま＆みなさま、昨日もおつかれさまでした！",
            color=0x4A90E2,
            timestamp=datetime.now(JST),
        )

        # 各商材をEmbedに追加
        for r in chunk:
            name  = r.get("title") or "不明"
            asin  = r.get("asin") or "—"
            price = r.get("amazon_price")
            store = r.get("store_chain") or "—"
            price_str = "—" if price in (None, "", "—") else f"¥{int(price):,}"
            embed.add_field(
                name=name[:256],  # Discord仕様上の安全対策
                value=f"ASIN: `{asin}`\nAmazon参考: {price_str}\n店舗: {store}",
                inline=False
            )

        # 1ページ目だけナギサの一言を生成してフッターにつける
        if page == 0:
            tops = [f"{(r.get('title') or '不明')}（{r.get('store_chain') or '—'}）" for r in records[:6]]
            context = "・" + "\n・".join(tops)
            user_prompt = (
                "昨日の商材トップ（抜粋）です。全体の雰囲気が伝わる一言コメントを、"
                "可愛く・励まし系で2行以内で。最後にハートか星を1個だけ付けてください。\n\n" + context
            )
            try:
                one_liner = await chat_simple(SYSTEM_PROMPT, user_prompt)
                log.info("[digest] GPT one-liner generated")
            except Exception as e:
                log.warning(f"[digest] GPT fallback: {e}")
                one_liner = "きのうもたくさんの投稿、ありがとうございます✨"
            embed.set_footer(text=one_liner)

        await target.send(embed=embed)
        await asyncio.sleep(1.5)  # Discord API制限回避（安全間隔）

async def ensure_scheduler_started(bot: discord.Client):
    """Discord のイベントループ上でスケジューラを起動（1回だけ）
    - 08:30 (env: DIGEST_TIME) 昨日の商材まとめ
    - 08:35 (env: REPORT_TIME) サロン日報
    """
    if getattr(bot, "_nagisa_sched", None):
        return
    loop = asyncio.get_running_loop()
    sched = AsyncIOScheduler(event_loop=loop, timezone=JST)

    # 商材まとめ
    h1, m1 = _parse_hhmm(os.getenv("DIGEST_TIME", "08:30"), "08:30")
    sched.add_job(post_daily_digest, "cron", hour=h1, minute=m1, args=[bot])

    # 日報（別モジュール）
    try:
        from .report_job import post_daily_report
        h2, m2 = _parse_hhmm(os.getenv("REPORT_TIME", "08:35"), "08:35")
        sched.add_job(post_daily_report, "cron", hour=h2, minute=m2, args=[bot])
    except Exception as e:
        log.warning(f"[scheduler] report_job not scheduled: {e}")

    sched.start()
    bot._nagisa_sched = sched

def setup_scheduler(bot: discord.Client):
    sched = AsyncIOScheduler(timezone=JST)
    sched.add_job(post_daily_digest, "cron", hour=0, minute=58, args=[bot])
    sched.start()
