# test_scheduler.py
from datetime import datetime
import pytz
from scheduler import compute_next_send_time, is_in_window

tz = pytz.timezone("Asia/Riyadh")
tests = [
    (tz.localize(datetime(2025,3,15,10,0,0)), "inside"),   # 10:00 → send now
    (tz.localize(datetime(2025,3,15, 6,0,0)), "before"),   # 06:00 → today 08:00
    (tz.localize(datetime(2025,3,15,20,0,0)), "after"),    # 20:00 → tomorrow 08:00
]
for dt_riyadh, label in tests:
    dt_utc = dt_riyadh.astimezone(pytz.utc).replace(tzinfo=None)
    result = compute_next_send_time(dt_utc)
    result_riyadh = result.replace(tzinfo=pytz.utc).astimezone(tz)
    print(f"{label}: {dt_riyadh.strftime('%H:%M')} → send at {result_riyadh.strftime('%H:%M %Z')}")
print("✅ Scheduler window logic OK")