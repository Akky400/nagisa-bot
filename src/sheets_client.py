import json
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timezone, timedelta
import os

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
JST = timezone(timedelta(hours=9))

def get_gspread_client():
    path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    creds = Credentials.from_service_account_file(path, scopes=SCOPES)
    return gspread.authorize(creds)

def open_sheet():
    gc = get_gspread_client()
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    return gc.open_by_key(sheet_id)

def _open():
    creds = Credentials.from_service_account_file(
        os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"),
        scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(os.getenv("GOOGLE_SHEET_ID"))

def append_product(record: dict):
    """products シートに1行追加"""
    wb = open_sheet()
    ws = wb.worksheet("products")
    ts = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    row = [
        "id",
        record.get("asin") or "",
        record.get("jan") or "",
        record.get("title") or "",
        record.get("amazon_price") or "",
        record.get("store_chain") or "",
        record.get("store_branch") or "",
        record.get("buy_price") or "",
        record.get("user") or "",
        record.get("channel") or "",
        ts,
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")

def fetch_yesterday_records():
    wb = _open()
    ws = wb.worksheet("products")
    values = ws.get_all_values()  # 2次元配列で取得（型ブレ回避）

    if not values:
        return []

    header = values[0]
    rows = values[1:]

    # ヘッダから timestamp 列を動的に特定（大小/全角半角を無視）
    def norm(s): return (s or "").strip().lower()
    try:
        ts_idx = next(i for i, h in enumerate(header) if norm(h) in ("timestamp", "time", "日時"))
    except StopIteration:
        return []

    # “昨日”の文字列
    today = datetime.now(JST).date()
    y = today - timedelta(days=1)
    y_str = y.strftime("%Y-%m-%d")

    # 行を dict に戻しつつ、timestamp は文字列化して判定
    out = []
    for r in rows:
        # 行長がヘッダと違っても安全に扱う
        rec = {header[i]: (r[i] if i < len(r) else "") for i in range(len(header))}
        ts_val = rec.get(header[ts_idx], "")

        # Google側が日時型で持っていても、文字列化で YYYY-MM-DD を含めば合格にする
        ts_text = str(ts_val)
        if y_str in ts_text:   # “含む” で判定（書式ゆれ吸収）
            out.append({k: rec.get(k, "") for k in header})

    return out