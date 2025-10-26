import re
from typing import Optional, Tuple, Dict

ASIN_RE = re.compile(r"\b([A-Z0-9]{10})\b")
JAN13_RE = re.compile(r"\b(\d{13})\b")
AMZ_URL_RE = re.compile(r"amazon\.(?:co\.jp|com)/(?:dp|gp/product)/([A-Z0-9]{10})", re.IGNORECASE)
PRICE_RE = re.compile(r"(?:¥\s*|￥\s*)?([1-9]\d{2,5})(?:\s*円)?")


def _fix_common_b0(asin: Optional[str]) -> Optional[str]:
    """B0 を BO と打った誤入力を自動補正（2文字目が 'O' の場合）。"""
    if asin and len(asin) == 10 and asin[0] == "B" and asin[1] == "O":
        return "B0" + asin[2:]
    return asin

def extract_ids(text: str) -> Dict[str, Optional[str]]:
    if not text:
        return {"asin": None, "jan": None}
    m = AMZ_URL_RE.search(text)
    if m:
        return {"asin": _fix_common_b0(m.group(1).upper()), "jan": None}
    m = ASIN_RE.search(text)
    asin = _fix_common_b0(m.group(1).upper()) if m else None
    m2 = JAN13_RE.search(text)
    jan = m2.group(1) if m2 else None
    return {"asin": asin, "jan": jan}

def extract_price_candidate_from_text(text: str) -> Optional[int]:
    """テキストから仕入れ値候補（円）を抽出。JAN/数量などの誤検出を極力回避。"""
    if not text:
        return None
    # 「個」「台」「%」直前は価格じゃないことが多いので除外
    cleaned = re.sub(r"\d+\s*(個|台|%)", "", text)
    for m in PRICE_RE.finditer(cleaned):
        val = int(m.group(1).replace(",", ""))
        # あり得る価格帯（例：300円〜200,000円）
        if 300 <= val <= 200000:
            return val
    return None

def normalize_store_by_channel(channel_name: str, channel_map: dict) -> Optional[str]:
    """チャンネル名からstore_chainを補完。見つからなければNone。"""
    for category, mapping in channel_map.items():
        for key, brand in mapping.items():
            if key.lower() == (channel_name or "").lower():
                return brand
    return None

STORE_SYNONYMS = {
    "ヤマダデンキ": ["ヤマダ", "YAMADA", "テックランド", "LABI", "ヤマダ電機"],
    "ビックカメラ・コジマ": ["ビック", "コジマ", "ビックカメラ", "ビック・コジマ"],
    "ヨドバシカメラ": ["ヨドバシ"],
    "ケーズデンキ": ["ケーズ", "ケーズ", "K's"],
    "ドン・キホーテ": ["ドンキ", "ドン・キホーテ", "MEGAドンキ"],
    "ココカラファイン": ["ココカラ", "ココカラファイン"],
    "マツモトキヨシ": ["マツキヨ"],
    "スギ薬局": ["スギ"],
    "クリエイトSD": ["クリエイト"],
    "クスリのアオキ": ["アオキ"],
    "サンドラッグ": ["サンドラッグ", "サンドラ"]
}

def extract_store_from_comment(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    コメントからチェーンと支店名っぽいものを抽出。
    例：「ヤマダです。テック川崎で10個」→ ("ヤマダデンキ", "テック川崎")
    """
    if not text:
        return None, None

    chain = None
    for norm, synonyms in STORE_SYNONYMS.items():
        for s in synonyms:
            if s.lower() in text.lower():
                chain = norm
                break
        if chain:
            break

    # 支店名: 「◯◯店」「◯◯センター」「テック◯◯」などを軽く拾う
    branch = None
    branch_patterns = [
        r"([^\s、。!！?？]{1,16}店)",
        r"(テック[^\s、。!！?？]{1,16})",
        r"([^\s、。!！?？]{1,16}センター)"
    ]
    for pat in branch_patterns:
        m = re.search(pat, text)
        if m:
            branch = m.group(1)
            break

    return chain, branch
