# src/keepa_client.py
import requests
from typing import Optional, Dict, Any

KEEPA_ENDPOINT = "https://api.keepa.com/product"

def _clean_price(value: Optional[int], *, domain: int = 5) -> Optional[int]:
    """
    Keepaの価格: domain=5(JP)はすでに円単位。
    -1 / 0 は「価格なし」扱いにして None を返す。
    """
    if value is None:
        return None
    try:
        iv = int(value)
    except Exception:
        return None
    if iv <= 0:  # -1, 0 は無効
        return None
    # JPはそのまま
    return iv if domain == 5 else iv // 100

def yen_from_keepa_price(v: Optional[int]) -> Optional[int]:
    """
    Keepaの価格単位は国ごとに異なる。
    domain=5 (Japan) はすでに円単位で返るので除算しない。
    """
    if v is None:
        return None
    try:
        # 日本Amazonはそのままの値でOK（除算しない）
        return int(v)
    except Exception:
        return None

def _last_valid_int(seq: Any) -> Optional[int]:
    """list/intから末尾側の有効(>0)値を拾う。"""
    if isinstance(seq, list):
        for x in reversed(seq):
            if isinstance(x, int) and x > 0:
                return x
    elif isinstance(seq, int) and seq > 0:
        return seq
    return None

def _pick_last_valid_int(seq: Any) -> Optional[int]:
    """list等から末尾側の有効(>0)intを拾う"""
    if isinstance(seq, list):
        for x in reversed(seq):
            if isinstance(x, int) and x > 0:
                return x
    if isinstance(seq, int) and seq > 0:
        return seq
    return None

def fetch_product_from_keepa(asin: Optional[str],api_key: str,jan: Optional[str] = None) -> Dict[str, Optional[str]]:
    """
    Keepaから商品名と参考価格を取得。
    - asin が無ければ jan(EAN/JAN) で検索（param: code）
    - 価格は amazon→buyBox→new の順にフォールバック
    - 無効値(-1/0)は None で返す
    """
    params = {
        "key": api_key,
        "domain": 5,   # Amazon.co.jp
        "stats": 1,
        "history": 0,
    }
    if asin:
        params["asin"] = asin
    elif jan:
        params["code"] = jan   # EAN/JAN/UPC
    else:
        raise ValueError("asin or jan is required")

    r = requests.get(KEEPA_ENDPOINT, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data.get("products"):
        return {"title": None, "amazon_price": None, "asin": asin}

    p = data["products"][0]
    title = p.get("title")
    asin_from_keepa = p.get("asin") or asin

    stats = p.get("stats") or {}
    current = stats.get("current")

    raw = None
    if isinstance(current, dict):
        # dict ならキーを優先順位で
        raw = current.get("amazon") or current.get("buyBox") or current.get("new")
    elif isinstance(current, list):
        # list なら 0=amazon, 1=new が多い
        for idx in (0, 1):
            if len(current) > idx and isinstance(current[idx], int) and current[idx] > 0:
                raw = current[idx]
                break

    # まだ無ければ buyBox / buyBoxPrice / data(BUY_BOX_SHIPPING) を最後に
    if raw is None:
        raw = _last_valid_int(stats.get("buyBox")) \
              or _last_valid_int(stats.get("buyBoxPrice")) \
              or _last_valid_int((p.get("data") or {}).get("BUY_BOX_SHIPPING"))

    price = _clean_price(raw, domain=5)
    return {"title": title, "amazon_price": price, "asin": asin_from_keepa}
