# src/openai_client.py
import os, asyncio
from openai import OpenAI

_client = None
def get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client

async def chat_simple(system: str, user: str, model: str = "gpt-4o-mini"):
    """単発チャット: 非同期で叩けるようにexecutorで包む"""
    client = get_client()
    def _call():
        return client.chat.completions.create(
            model=model,
            messages=[{"role":"system","content":system},
                      {"role":"user","content":user}],
            temperature=0.6,
            max_tokens=220,
            timeout=30,  # タイムアウト保険            
        )
    last_err = None
    for i in range(3):
        try:
            resp = await asyncio.to_thread(_call)
            return resp.choices[0].message.content.strip()
        except Exception as e:
            last_err = e
            await asyncio.sleep(0.8 * (i+1))  # 簡易バックオフ
    raise last_err
    resp = await asyncio.to_thread(_call)
    return resp.choices[0].message.content.strip()

async def chat_complete(system: str, user: str, *, model: str = None, max_tokens: int = 1200, temperature: float = 0.4):
    """
    長文要約・日報向け。chat_simpleよりもmax_tokensを広く取りたいケースに使う。
    modelは .env の NAGISA_MODEL_DAILY を優先し、未指定なら gpt-4o。
    """
    client = get_client()
    model = model or os.getenv("NAGISA_MODEL_DAILY", "gpt-4o")
    def _call():
        return client.chat.completions.create(
            model=model,
            messages=[{"role":"system","content":system},
                      {"role":"user","content":user}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
    last_err = None
    for i in range(3):
        try:
            resp = await asyncio.to_thread(_call)
            return resp.choices[0].message.content.strip()
        except Exception as e:
            last_err = e
            await asyncio.sleep(0.8 * (i+1))
    raise last_err
