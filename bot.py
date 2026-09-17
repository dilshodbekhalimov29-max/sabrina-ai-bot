import asyncio
import hashlib
import html
import logging
import os
import secrets
import sqlite3
from contextlib import closing
from urllib.parse import urlencode

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup
)
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "market.db")

CLICK_SERVICE_ID = os.getenv("CLICK_SERVICE_ID", "")
CLICK_MERCHANT_ID = os.getenv("CLICK_MERCHANT_ID", "")
CLICK_SECRET_KEY = os.getenv("CLICK_SECRET_KEY", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
RETURN_URL = os.getenv("RETURN_URL", "")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))
AUTO_APPROVE_PRODUCTS = os.getenv("AUTO_APPROVE_PRODUCTS", "true").lower() == "true"

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)


# ---------- Database ----------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with closing(db()) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id INTEGER NOT NULL,
            file_id TEXT NOT NULL,
            file_name TEXT NOT NULL,
            description TEXT NOT NULL,
            price INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'approved',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            buyer_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            click_trans_id TEXT UNIQUE,
            merchant_prepare_id TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            paid_at DATETIME
        );
        """)
        c.commit()

def save_user(user):
    with closing(db()) as c:
        c.execute("""
            INSERT INTO users(id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              username=excluded.username,
              first_name=excluded.first_name
        """, (user.id, user.username, user.first_name))
        c.commit()

def add_product(seller_id, file_id, file_name, description, price):
    status = "approved" if AUTO_APPROVE_PRODUCTS else "pending"
    with closing(db()) as c:
        cur = c.execute("""
            INSERT INTO products
            (seller_id, file_id, file_name, description, price, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (seller_id, file_id, file_name, description, price, status))
        c.commit()
        return cur.lastrowid

def get_product(product_id):
    with closing(db()) as c:
        return c.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()

def get_products(limit=30):
    with closing(db()) as c:
        return c.execute("""
            SELECT * FROM products
            WHERE status='approved'
            ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()

def create_order(buyer_id, product_id, amount):
    order_id = secrets.token_hex(12)
    with closing(db()) as c:
        c.execute("""
            INSERT INTO orders(id, buyer_id, product_id, amount)
            VALUES (?, ?, ?, ?)
        """, (order_id, buyer_id, product_id, amount))
        c.commit()
    return order_id

def get_order(order_id):
    with closing(db()) as c:
        return c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()

def update_order_prepare(order_id, click_trans_id, prepare_id):
    with closing(db()) as c:
        c.execute("""
            UPDATE orders
            SET click_trans_id=?, merchant_prepare_id=?
            WHERE id=? AND status='pending'
        """, (str(click_trans_id), str(prepare_id), order_id))
        c.commit()

def mark_paid(order_id, click_trans_id, prepare_id):
    with closing(db()) as c:
        cur = c.execute("""
            UPDATE orders
            SET status='paid', click_trans_id=?, merchant_prepare_id=?,
                paid_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='pending'
        """, (str(click_trans_id), str(prepare_id), order_id))
        c.commit()
        return cur.rowcount == 1

def user_products(user_id):
    with closing(db()) as c:
        return c.execute("""
            SELECT * FROM products WHERE seller_id=?
            ORDER BY id DESC
        """, (user_id,)).fetchall()

def user_orders(user_id):
    with closing(db()) as c:
        return c.execute("""
            SELECT o.*, p.file_name FROM orders o
            JOIN products p ON p.id=o.product_id
            WHERE o.buyer_id=?
            ORDER BY o.created_at DESC
        """, (user_id,)).fetchall()


# ---------- UI ----------
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛍 Xarid qilish"), KeyboardButton(text="📤 Fayl sotish")],
            [KeyboardButton(text="📁 Mening fayllarim"), KeyboardButton(text="🧾 Xaridlarim")],
            [KeyboardButton(text="👤 Profil"), KeyboardButton(text="ℹ️ Yordam")],
        ],
        resize_keyboard=True
    )

def cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
        resize_keyboard=True
    )

def product_kb(product_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 CLICK ORQALI SOTIB OLISH", callback_data=f"buy:{product_id}")]
    ])

# ---------- Seller FSM ----------
class SellState(StatesGroup):
    file = State()
    name = State()
    description = State()
    price = State()


# ---------- Handlers ----------
@router.message(CommandStart())
async def start(message: Message):
    save_user(message.from_user)
    await message.answer(
        "🤖 <b>FILE MARKET</b>\n\n"
        "Assalomu alaykum! 👋\n"
        "📁 Fayl soting yoki 🛍 kerakli faylni xarid qiling.\n\n"
        "🔐 To‘lovdan keyin fayl avtomatik yuboriladi.",
        reply_markup=main_kb(),
        parse_mode="HTML"
    )

@router.message(F.text == "❌ Bekor qilish")
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Amal bekor qilindi.", reply_markup=main_kb())

@router.message(F.text == "📤 Fayl sotish")
async def sell_start(message: Message, state: FSMContext):
    await state.set_state(SellState.file)
    await message.answer(
        "📤 <b>FAYL SOTISH</b>\n\n"
        "1️⃣ Sotmoqchi bo‘lgan faylingizni <b>Document</b> ko‘rinishida yuboring 📎",
        reply_markup=cancel_kb(),
        parse_mode="HTML"
    )

@router.message(SellState.file, F.document)
async def sell_file(message: Message, state: FSMContext):
    doc = message.document
    await state.update_data(file_id=doc.file_id, file_name=doc.file_name or "file")
    await state.set_state(SellState.name)
    await message.answer("✏️ Fayl uchun chiroyli nom kiriting:", reply_markup=cancel_kb())

@router.message(SellState.file)
async def sell_file_wrong(message: Message):
    await message.answer("📎 Iltimos, faylni <b>Document</b> qilib yuboring.", parse_mode="HTML")

@router.message(SellState.name, F.text)
async def sell_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 3 or len(name) > 100:
        return await message.answer("⚠️ Nom 3–100 belgi oralig‘ida bo‘lsin.")
    await state.update_data(name=name)
    await state.set_state(SellState.description)
    await message.answer("📝 Mahsulot haqida qisqacha tavsif yozing:", reply_markup=cancel_kb())

@router.message(SellState.description, F.text)
async def sell_description(message: Message, state: FSMContext):
    desc = message.text.strip()
    if len(desc) < 5 or len(desc) > 1000:
        return await message.answer("⚠️ Tavsif 5–1000 belgi oralig‘ida bo‘lsin.")
    await state.update_data(description=desc)
    await state.set_state(SellState.price)
    await message.answer(
        "💰 Narxni so‘mda kiriting.\n\nMasalan: <code>35000</code>",
        reply_markup=cancel_kb(),
        parse_mode="HTML"
    )

@router.message(SellState.price, F.text)
async def sell_price(message: Message, state: FSMContext):
    raw = message.text.replace(" ", "").replace(",", "")
    if not raw.isdigit():
        return await message.answer("⚠️ Faqat raqam kiriting. Masalan: 35000")
    price = int(raw)
    if price < 1000 or price > 100_000_000:
        return await message.answer("⚠️ Narx 1 000–100 000 000 so‘m oralig‘ida bo‘lsin.")

    data = await state.get_data()
    product_id = add_product(
        message.from_user.id,
        data["file_id"],
        data["file_name"],
        data["description"],
        price
    )
    await state.clear()

    status_text = "🟢 E'lon do‘konga qo‘shildi." if AUTO_APPROVE_PRODUCTS else "⏳ E'lon admin tasdig‘ini kutmoqda."
    await message.answer(
        f"✅ <b>MAHSULOT QO‘SHILDI!</b>\n\n"
        f"📄 <b>{html.escape(data['name'])}</b>\n"
        f"📝 {html.escape(data['description'])}\n"
        f"💰 {price:,} so‘m\n\n"
        f"{status_text}\n"
        f"🆔 ID: <code>{product_id}</code>",
        reply_markup=main_kb(),
        parse_mode="HTML"
    )

    if ADMIN_ID:
        try:
            await bot.send_message(
                ADMIN_ID,
                f"📥 <b>Yangi mahsulot</b>\n\n"
                f"🆔 #{product_id}\n"
                f"👤 {message.from_user.id}\n"
                f"📄 {html.escape(data['name'])}\n"
                f"💰 {price:,} so‘m",
                parse_mode="HTML"
            )
        except Exception:
            pass

@router.message(F.text == "🛍 Xarid qilish")
async def shop(message: Message):
    products = get_products()
    if not products:
        return await message.answer("📭 Hozircha sotuvda fayllar yo‘q.")
    await message.answer("🛍 <b>DO‘KON</b>\n\nKerakli mahsulotni tanlang:", parse_mode="HTML")
    for p in products:
        await message.answer(
            f"📄 <b>{html.escape(p['file_name'])}</b>\n\n"
            f"📝 {html.escape(p['description'])}\n\n"
            f"💰 <b>{p['price']:,} so‘m</b>",
            reply_markup=product_kb(p["id"]),
            parse_mode="HTML"
        )

@router.callback_query(F.data.startswith("buy:"))
async def buy(callback: CallbackQuery):
    product_id = int(callback.data.split(":")[1])
    product = get_product(product_id)
    if not product or product["status"] != "approved":
        return await callback.answer("❌ Mahsulot mavjud emas.", show_alert=True)

    order_id = create_order(callback.from_user.id, product_id, product["price"])

    if not CLICK_SERVICE_ID or not CLICK_MERCHANT_ID:
        return await callback.message.answer(
            "⚠️ Click hali sozlanmagan.\n"
            "Admin `.env` fayliga CLICK_SERVICE_ID va CLICK_MERCHANT_ID ni kiritishi kerak."
        )

    params = {
        "service_id": CLICK_SERVICE_ID,
        "merchant_id": CLICK_MERCHANT_ID,
        "amount": product["price"],
        "transaction_param": order_id,
    }
    if RETURN_URL:
        params["return_url"] = RETURN_URL
    pay_url = "https://my.click.uz/services/pay?" + urlencode(params)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 CLICK BILAN TO‘LASH", url=pay_url)]
    ])
    await callback.message.answer(
        f"💳 <b>TO‘LOV</b>\n\n"
        f"📄 {html.escape(product['file_name'])}\n"
        f"💰 <b>{product['price']:,} so‘m</b>\n\n"
        f"🔐 To‘lov Click serveri orqali amalga oshiriladi.\n"
        f"✅ To‘lov tasdiqlangach, fayl avtomatik yuboriladi.",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(F.text == "📁 Mening fayllarim")
async def my_products(message: Message):
    products = user_products(message.from_user.id)
    if not products:
        return await message.answer("📭 Sizda hali fayllar yo‘q.")
    text = "📁 <b>MENING FAYLLARIM</b>\n\n"
    for p in products:
        icon = "🟢" if p["status"] == "approved" else "🟡"
        text += f"{icon} #{p['id']} — {html.escape(p['file_name'])} — {p['price']:,} so‘m\n"
    await message.answer(text, parse_mode="HTML")

@router.message(F.text == "🧾 Xaridlarim")
async def my_orders(message: Message):
    orders = user_orders(message.from_user.id)
    if not orders:
        return await message.answer("🧾 Xaridlar tarixi hozircha bo‘sh.")
    text = "🧾 <b>XARIDLARIM</b>\n\n"
    for o in orders:
        status = "✅ To‘langan" if o["status"] == "paid" else "⏳ Kutilmoqda"
        text += f"📄 {html.escape(o['file_name'])}\n💰 {o['amount']:,} so‘m\n{status}\n\n"
    await message.answer(text, parse_mode="HTML")

@router.message(F.text == "👤 Profil")
async def profile(message: Message):
    await message.answer(
        f"👤 <b>PROFIL</b>\n\n"
        f"🆔 ID: <code>{message.from_user.id}</code>\n"
        f"👤 @{html.escape(message.from_user.username or 'username yo‘q')}",
        parse_mode="HTML"
    )

@router.message(F.text == "ℹ️ Yordam")
async def help_message(message: Message):
    await message.answer(
        "ℹ️ <b>YORDAM</b>\n\n"
        "📤 Fayl sotish — o‘zingizning faylingizni joylang.\n"
        "🛍 Xarid qilish — katalogdan fayl tanlang.\n"
        "💳 Click — to‘lovni amalga oshiring.\n"
        "📥 To‘lov tasdiqlangach fayl avtomatik yuboriladi.\n\n"
        "🔐 To‘lovni faqat Click callback tasdiqlagandan keyin xarid yakunlanadi.",
        parse_mode="HTML"
    )


# ---------- Click SHOP API ----------
def click_signature(data: dict) -> str:
    click_trans_id = str(data.get("click_trans_id", ""))
    service_id = str(data.get("service_id", ""))
    merchant_trans_id = str(data.get("merchant_trans_id", ""))
    amount = str(data.get("amount", ""))
    action = str(data.get("action", ""))
    sign_time = str(data.get("sign_time", ""))
    merchant_prepare_id = str(data.get("merchant_prepare_id", ""))

    if action == "0":
        raw = (
            click_trans_id + service_id + CLICK_SECRET_KEY +
            merchant_trans_id + amount + action + sign_time
        )
    else:
        raw = (
            click_trans_id + service_id + CLICK_SECRET_KEY +
            merchant_trans_id + merchant_prepare_id +
            amount + action + sign_time
        )
    return hashlib.md5(raw.encode()).hexdigest()

def click_response(data, error=0, note="Success", prepare_id=None):
    result = {
        "click_trans_id": str(data.get("click_trans_id", "")),
        "merchant_trans_id": str(data.get("merchant_trans_id", "")),
        "error": error,
        "error_note": note,
    }
    if prepare_id is not None:
        result["merchant_prepare_id"] = str(prepare_id)
    return web.json_response(result)

async def click_webhook(request: web.Request):
    data = await request.post()
    d = dict(data)

    if not CLICK_SECRET_KEY:
        return click_response(d, -8, "Click secret key is not configured")

    expected = click_signature(d)
    received = str(d.get("sign_string", ""))
    if not secrets.compare_digest(expected.lower(), received.lower()):
        return click_response(d, -1, "SIGN CHECK FAILED")

    if str(d.get("service_id", "")) != str(CLICK_SERVICE_ID):
        return click_response(d, -8, "Invalid service_id")

    order_id = str(d.get("merchant_trans_id", ""))
    order = get_order(order_id)
    if not order:
        return click_response(d, -5, "User does not exist")

    try:
        amount = int(float(str(d.get("amount", "0"))))
    except ValueError:
        return click_response(d, -2, "Incorrect parameter amount")

    if amount != int(order["amount"]):
        return click_response(d, -2, "Incorrect parameter amount")

    action = str(d.get("action", ""))

    if action == "0":
        if order["status"] == "paid":
            return click_response(d, -4, "Already paid", order["merchant_prepare_id"] or order_id)

        prepare_id = order["merchant_prepare_id"] or f"prep_{order_id}"
        update_order_prepare(order_id, d.get("click_trans_id"), prepare_id)
        return click_response(d, 0, "Success", prepare_id)

    if action == "1":
        prepare_id = str(d.get("merchant_prepare_id", ""))
        if order["merchant_prepare_id"] and prepare_id != str(order["merchant_prepare_id"]):
            return click_response(d, -6, "Transaction does not exist")

        if order["status"] == "paid":
            return click_response(d, -4, "Already paid", prepare_id or order_id)

        if int(d.get("error", 0)) != 0:
            return click_response(d, -9, "Transaction cancelled", prepare_id or order_id)

        changed = mark_paid(order_id, d.get("click_trans_id"), prepare_id or order_id)
        if not changed:
            return click_response(d, -4, "Already paid", prepare_id or order_id)

        product = get_product(order["product_id"])
        if product:
            try:
                await bot.send_message(
                    order["buyer_id"],
                    "✅ <b>TO‘LOV MUVAFFAQIYATLI!</b>\n\n"
                    f"📄 {html.escape(product['file_name'])}\n"
                    f"💰 {product['price']:,} so‘m\n\n"
                    "📥 Faylingiz quyida yuborildi. Rahmat! ❤️",
                    parse_mode="HTML"
                )
                await bot.send_document(order["buyer_id"], product["file_id"])
            except Exception:
                logging.exception("Could not deliver purchased file")

        return click_response(d, 0, "Success", prepare_id or order_id)

    return click_response(d, -3, "Action not found")


async def health(request):
    return web.Response(text="OK")

async def run_web_server():
    app = web.Application()
    app.router.add_post("/click", click_webhook)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEBHOOK_HOST, WEBHOOK_PORT)
    await site.start()
    logging.info("Click webhook server: http://%s:%s/click", WEBHOOK_HOST, WEBHOOK_PORT)

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN .env faylida ko‘rsatilmagan.")
    init_db()
    await run_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
