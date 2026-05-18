from datetime import datetime, timezone
JST = timezone.utc if 'JST' not in globals() else JST  # keep your existing JST

def to_iso_z(dt_utc: datetime) -> str:
    # Ensure UTC and serialize with trailing Z for JS Date correctness
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    else:
        dt_utc = dt_utc.astimezone(timezone.utc)
    return dt_utc.isoformat().replace("+00:00", "Z")
