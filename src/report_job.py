# src/report_job.py
from datetime import datetime, timedelta, timezone, time as dtime
import asyncio
import discord
import logging
import os
from typing import List
import re
from .openai_client import chat_complete
from .persona import EDITOR_SYSTEM_PROMPT as SYSTEM_PROMPT

log = logging.getLogger(__name__)
JST = timezone(timedelta(hours=9))

def _find_text_channel(bot: discord.Client, token: str):
    """名前 or ID でテキストチャンネルを解決する。戻り値: (channel, how|None)"""
    token = (token or "").strip()
    if token.isdigit():
        ch = bot.get_channel(int(token))
        return (ch, "id") if isinstance(ch, discord.TextChannel) else (None, None)
    # 完全一致
    ch = discord.utils.get(bot.get_all_channels(), name=token)
    if isinstance(ch, discord.TextChannel):
        return ch, "exact"
    # 前方一致（末尾に絵文字・装飾がつくケース用）
    for c in bot.get_all_channels():
        if isinstance(c, discord.TextChannel) and c.name.startswith(token):
            return c, "prefix"
    return None, None

def _get_report_channel(bot: discord.Client):
    # REPORT_CHANNEL_ID > DIGEST_CHANNEL_ID > #bot-log
    for key in ("REPORT_CHANNEL_ID", "DIGEST_CHANNEL_ID"):
        val = os.getenv(key)
        if val and val.isdigit():
            ch = bot.get_channel(int(val))
            if ch:
                return ch
    return discord.utils.get(bot.get_all_channels(), name="bot-log")

def _select_window():
    """
    REPORT_WINDOW に応じて対象期間を返す。
    - yesterday (既定): 昨日 00:00–23:59:59
    - today: 今日 00:00–今
    - last24h: 直近24時間
    """
    mode = (os.getenv("REPORT_WINDOW", "yesterday") or "yesterday").lower()
    now = datetime.now(JST)
    if mode == "today":
        y = now.date()
        start = datetime.combine(y, dtime(0,0), JST)
        end = now
        label = f"{y.strftime('%Y/%m/%d')}(本日)"
    elif mode == "last24h":
        end = now
        start = now - timedelta(hours=24)
        label = f"{start.strftime('%Y/%m/%d %H:%M')}–{end.strftime('%m/%d %H:%M')}"
    else:  # yesterday
        y = (now - timedelta(days=1)).date()
        start = datetime.combine(y, dtime(0,0), JST)
        end   = datetime.combine(y, dtime(23,59,59), JST)
        label = y.strftime('%Y/%m/%d')
    return label, start, end

async def _collect_logs(bot: discord.Client, after: datetime, before: datetime, *, debug: bool=False) -> List[str]:
    names = [s.strip() for s in os.getenv("SUMMARY_CHANNELS","").split(",") if s.strip()]
    fallback_all = os.getenv("REPORT_FALLBACK_ALL", "0") == "1"
    if not names and not fallback_all:
        log.info("[report] SUMMARY_CHANNELS is empty -> nothing to summarize")
        return []
    if not names and fallback_all:
        channels = [ch for ch in bot.get_all_channels() if isinstance(ch, discord.TextChannel)]
        if debug: log.info(f"[report] fallback_all on -> scanning {len(channels)} channels")
        iter_items = [(ch.name, ch) for ch in channels]
    else:
        iter_items = []
        for name in names:
            ch, how = _find_text_channel(bot, name)
            if not isinstance(ch, discord.TextChannel):
                log.info(f"[report] channel not found: '{name}'")
                continue
            log.info(f"[report] target resolved: '{name}' -> #{ch.name} ({how})")
            iter_items.append((name, ch))
    lines: List[str] = []
    for name, ch in iter_items:
        if not isinstance(ch, discord.TextChannel):
            continue
        try:
            # 権限ダンプ（debug時）
            if debug:
                me = ch.guild.me
                p = ch.permissions_for(me)
                log.info(f"[report] perms #{ch.name}: view={p.view_channel}, read_history={p.read_message_history}, send={p.send_messages}, embed={p.embed_links}")
            async for msg in ch.history(after=after, before=before, oldest_first=True, limit=None):
                if msg.author.bot:
                    continue
                content = (msg.content or "").strip()
                if not content and not msg.attachments:
                    continue
                if not content and msg.attachments:
                    content = "[添付あり]"
                content = content.replace("\n", " ").strip()
                who = msg.author.display_name or msg.author.name
                lines.append(f"[#{ch.name}] {who}: {content}")
        except Exception as e:
            log.warning(f"[report] fetch history failed on #{name}: {e}")
            await asyncio.sleep(1.0)
    return lines

def _chunk_lines(lines: List[str], max_chars: int = 9000) -> List[str]:
    chunks = []
    buf = []
    size = 0
    for ln in lines:
        l = len(ln) + 1
        if size + l > max_chars and buf:
            chunks.append("\n".join(buf))
            buf = [ln]
            size = l
        else:
            buf.append(ln)
            size += l
    if buf:
        chunks.append("\n".join(buf))
    return chunks

async def _summarize_chunks(chunks: List[str], ydate_str: str) -> str:
    # Map
    partials: List[str] = []
    for i, ck in enumerate(chunks, 1):
        user = (
            f"以下はDiscordサロンの {ydate_str} の投稿ログ（分割 {i}/{len(chunks)}）です。\n"
            "次の4項目で、端的に日本語で要約してください：\n"
            "1) 主要トピック（カテゴリ・商材・店舗）\n"
            "2) 会話の流れ・共有された知見\n"
            "3) トレンド/仕入れに繋がる兆し\n"
            "4) キーワード（最大10件、#ハッシュタグ形式）\n"
            "※箇条書き中心で、具体名はそのまま残す。\n"
            "---ログ---\n" + ck
        )
        text = await chat_complete(SYSTEM_PROMPT, user, max_tokens=900, temperature=0.3)
        partials.append(text)

    # Reduce
    joined = "\n\n---\n\n".join(partials)
    final_user = (
        f"以下は {ydate_str} のサロン要約（部分）です。重複を統合し、1つの『ナギサ日報』として仕上げてください。\n"
        "出力フォーマット：\n"
        "## 主要トピック\n"
        "・\n"
        "## 会話の流れ/注目\n"
        "・\n"
        "## トレンド/気づき\n"
        "・\n"
        "## ナギサのひとこと\n"
        "2文以内。やさしく、鼓舞するトーンで。\n"
        "――要約素材――\n" + joined
    )
    final = await chat_complete(SYSTEM_PROMPT, final_user, max_tokens=1000, temperature=0.35)
    return final

async def post_daily_report(bot: discord.Client):
    label, after, before = _select_window()
    target = _get_report_channel(bot)
    if not target:
        log.warning("[report] target channel not found -> skip")
        return

    debug = os.getenv("REPORT_DEBUG", "0") == "1"
    lines = await _collect_logs(bot, after, before, debug=debug)
    if not lines:
        log.info("[report] no logs -> skip")
        if debug and target:
            await target.send("🛠️ 日報デバッグ: 集計対象メッセージが見つかりませんでした。\n"
                              "・`SUMMARY_CHANNELS` の設定を確認してください。\n"
                              "・`REPORT_FALLBACK_ALL=1` を指定すると全テキストチャンネルを走査します。")
        return

    chunks = _chunk_lines(lines)
    if debug:
        sample = "\n".join(lines[:25])
        await target.send(f"🛠️ 日報デバッグ: {len(lines)}件拾えました。サンプル25件↓\n```\n{sample[:1800]}\n```")
    text = await _summarize_chunks(chunks, label)

    title = f"📰 ナギサ日報（{label}）"
    parts = [text[i:i+1900] for i in range(0, len(text), 1900)]
    if len(parts) == 1:
        embed = discord.Embed(
            title=title,
            description=parts[0],
            color=0xFFD700,
            timestamp=datetime.now(JST),
        )
        await target.send(embed=embed)
    else:
        for i, p in enumerate(parts, 1):
            await target.send(f"{title}（{i}/{len(parts)}）\n{p}")
            await asyncio.sleep(1.5)
    log.info("[report] posted daily report")
