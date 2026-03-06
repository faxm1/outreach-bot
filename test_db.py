# test_db.py
import asyncio, db

async def run():
    await db.init_db()
    print("✅ DB init OK")
    await db.add_to_opt_outs("block@test.com")
    result = await db.is_suppressed("block@test.com")
    assert result is not None
    print("✅ Suppression OK")
    rate = await db.check_rate_limits()
    assert rate["allowed"]
    print("✅ Rate limit check OK")

asyncio.run(run())