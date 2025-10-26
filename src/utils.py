from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

def now_jst() -> datetime:
    return datetime.now(tz=JST)
