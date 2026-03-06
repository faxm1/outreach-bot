import sqlite3

conn = sqlite3.connect('outreach.db')
cursor = conn.execute(
    "UPDATE requests SET scheduled_send_time = datetime('now') WHERE status = 'confirmed'"
)
conn.commit()
print(f"Updated {cursor.rowcount} request(s) — scheduler will send within 30 seconds")
conn.close()
