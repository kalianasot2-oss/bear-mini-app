import os, json, hmac, hashlib, secrets, sqlite3, asyncio, random
from urllib.parse import parse_qsl
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import Message, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
import uvicorn

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "YOUR_BOT_USERNAME")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://YOUR-DOMAIN.example")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "peredoz08")
DB_PATH = os.getenv("DB_PATH", "bear_bot.sqlite3")

app = FastAPI()
router = Router()
bot = Bot(BOT_TOKEN) if BOT_TOKEN else None
dp = Dispatcher()
dp.include_router(router)

PRIZES = [
    {"id": "bear", "name": "Мишка", "emoji": "🧸", "weight": 50.0},
    {"id": "gift", "name": "Подарок", "emoji": "🎁", "weight": 25.0},
    {"id": "rose", "name": "Роза", "emoji": "🌹", "weight": 15.0},
    {"id": "cake", "name": "Торт", "emoji": "🎂", "weight": 9.989},
    {"id": "nft", "name": "NFT-подарок", "emoji": "💎", "weight": 0.01},
    {"id": "premium", "name": "Telegram Premium", "emoji": "⭐", "weight": 0.001},
]
SPIN_COST_REFERRALS = 3

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS users(
        tg_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
        referrals INTEGER DEFAULT 0, spins INTEGER DEFAULT 0
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS referrals(
        id INTEGER PRIMARY KEY AUTOINCREMENT, referrer INTEGER, invited INTEGER UNIQUE
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS wins(
        id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, prize_id TEXT,
        prize_name TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit()
    return conn

def ensure_user(tg_id, username="", first_name=""):
    c = db()
    c.execute("""INSERT INTO users(tg_id,username,first_name) VALUES(?,?,?)
                 ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username,
                 first_name=excluded.first_name""", (tg_id, username, first_name))
    c.commit(); c.close()

def verify_init_data(init_data: str):
    if not BOT_TOKEN or not init_data:
        raise HTTPException(401, "Telegram authorization required")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", None)
    if not received:
        raise HTTPException(401, "Invalid Telegram data")
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise HTTPException(401, "Invalid Telegram signature")
    user = json.loads(pairs.get("user", "{}"))
    if not user.get("id"):
        raise HTTPException(401, "User not found")
    return user

def weighted_prize():
    return random.choices(PRIZES, weights=[p["weight"] for p in PRIZES], k=1)[0]

@router.message(CommandStart())
async def start(message: Message):
    tg_id = message.from_user.id
    ensure_user(tg_id, message.from_user.username or "", message.from_user.first_name or "")
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].startswith("ref_"):
        try:
            referrer = int(parts[1][4:])
            if referrer != tg_id:
                c = db()
                if c.execute("SELECT 1 FROM referrals WHERE invited=?", (tg_id,)).fetchone() is None:
                    c.execute("INSERT INTO referrals(referrer, invited) VALUES(?,?)", (referrer, tg_id))
                    c.execute("UPDATE users SET referrals=referrals+1, spins=spins+1 WHERE tg_id=?", (referrer,))
                    c.commit()
                c.close()
        except Exception:
            pass
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎡 Открыть рулетку", web_app=WebAppInfo(url=WEBAPP_URL))
    ]])
    await message.answer("🧸 Bear Bot\n\nКрути колесо и выигрывай подарки бесплатно!", reply_markup=kb)

@app.get("/")
async def index():
    return FileResponse(Path(__file__).parent / "web" / "index.html")

@app.post("/api/state")
async def state(request: Request):
    data = await request.json()
    user = verify_init_data(data.get("initData",""))
    ensure_user(user["id"], user.get("username",""), user.get("first_name",""))
    c = db(); row = c.execute("SELECT * FROM users WHERE tg_id=?", (user["id"],)).fetchone(); c.close()
    return {
        "referrals": row["referrals"],
        "spins": row["spins"],
        "refLink": f"https://t.me/{BOT_USERNAME}?start=ref_{user['id']}",
        "prizes": PRIZES
    }

@app.post("/api/spin")
async def spin(request: Request):
    data = await request.json()
    user = verify_init_data(data.get("initData",""))
    c = db()
    row = c.execute("SELECT spins FROM users WHERE tg_id=?", (user["id"],)).fetchone()
    if not row or row["spins"] < 1:
        c.close()
        raise HTTPException(400, "NO_SPINS")
    prize = weighted_prize()
    c.execute("UPDATE users SET spins=spins-1 WHERE tg_id=?", (user["id"],))
    c.execute("INSERT INTO wins(tg_id,prize_id,prize_name) VALUES(?,?,?)",
              (user["id"], prize["id"], prize["name"]))
    c.commit(); c.close()
    return {"prize": prize}

@app.get("/health")
async def health():
    return {"ok": True}

async def main():
    db().close()
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")
    polling = asyncio.create_task(dp.start_polling(bot))
    config = uvicorn.Config(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")), log_level="info")
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        polling.cancel()

if __name__ == "__main__":
    asyncio.run(main())
