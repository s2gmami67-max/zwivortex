import asyncio
import logging
import os
import sqlite3

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

def init_db():
    with sqlite3.connect("bot.db") as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT);
            CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, description TEXT, price REAL, active INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS keys_pool (id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER, key_value TEXT, sold INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, product_id INTEGER, status TEXT DEFAULT 'pending', proof TEXT);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        """)

def db_execute(query, params=()):
    with sqlite3.connect("bot.db") as conn:
        cur = conn.cursor()
        cur.execute(query, params)
        conn.commit()
        return cur

def db_fetch(query, params=()):
    with sqlite3.connect("bot.db") as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(query, params)
        return cur.fetchall()

class AddProduct(StatesGroup):
    name = State()
    description = State()
    price = State()

class AddStock(StatesGroup):
    product_id = State()
    keys = State()

class SendProof(StatesGroup):
    proof = State()

class SetPayment(StatesGroup):
    text = State()

def is_admin(uid): return uid == ADMIN_ID
def fmt_price(p): return f"{p:.2f}₺".replace(".00₺", "₺")

@dp.message()
async def log_user(message: Message):
    if message.from_user:
        db_execute("INSERT OR REPLACE INTO users (user_id, username, first_name) VALUES (?, ?, ?)", 
                   (message.from_user.id, message.from_user.username, message.from_user.first_name))

@dp.message(Command("start"))
async def cmd_start(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛍 Ürünler", callback_data="shop")],
        [InlineKeyboardButton(text="📦 Siparişlerim", callback_data="my_orders")],
        [InlineKeyboardButton(text="❓ Yardım", callback_data="help")],
    ])
    await message.answer(f"Merhaba <b>{message.from_user.first_name}</b>! 👋\n\nHoş geldin. Buradan anahtar satın alabilirsin.", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "help")
async def cb_help(cb: CallbackQuery):
    await cb.answer()
    txt = "📖 <b>Komutlar</b>\n\n/start — Menü\n/siparislerim — Siparişlerin\n"
    if is_admin(cb.from_user.id):
        txt += "\n🛠 <b>Admin Komutları</b>\n/admin — Panel\n/urun_ekle\n/stok_ekle\n/siparisler\n/odeme_ayarla"
    await cb.message.answer(txt, parse_mode="HTML")

@dp.callback_query(F.data == "shop")
async def cb_shop(cb: CallbackQuery):
    await cb.answer()
    products = db_fetch("SELECT * FROM products WHERE active = 1")
    if not products:
        return await cb.message.answer("Şu an ürün yok 😔")
    rows = []
    for p in products:
        stock = db_fetch("SELECT COUNT(*) as c FROM keys_pool WHERE product_id=? AND sold=0", (p['id'],))[0]['c']
        rows.append([InlineKeyboardButton(text=f"{p['name']} — {fmt_price(p['price'])} (Stok: {stock})", callback_data=f"view:{p['id']}")])
    await cb.message.answer("🛍 <b>Ürünler</b>\nBir ürüne tıkla:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@dp.callback_query(F.data.startswith("view:"))
async def cb_view_product(cb: CallbackQuery):
    await cb.answer()
    pid = int(cb.data.split(":")[1])
    p = db_fetch("SELECT * FROM products WHERE id=?", (pid,))[0]
    stock = db_fetch("SELECT COUNT(*) as c FROM keys_pool WHERE product_id=? AND sold=0", (pid,))[0]['c']
    text = f"<b>{p['name']}</b>\n\n{p['description'] or '—'}\n\n💰 Fiyat: <b>{fmt_price(p['price'])}</b>\n📦 Stok: {stock} adet"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Satın Al", callback_data=f"buy:{pid}")], [InlineKeyboardButton(text="⬅️ Geri", callback_data="shop")]])
    await cb.message.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("buy:"))
async def cb_buy(cb: CallbackQuery):
    await cb.answer()
    pid = int(cb.data.split(":")[1])
    p = db_fetch("SELECT * FROM products WHERE id=?", (pid,))[0]
    stock = db_fetch("SELECT COUNT(*) as c FROM keys_pool WHERE product_id=? AND sold=0", (pid,))[0]['c']
    if stock == 0: return await cb.message.answer("Maalesef stokta yok 😔")
    
    cur = db_execute("INSERT INTO orders (user_id, product_id) VALUES (?, ?)", (cb.from_user.id, pid))
    order_id = cur.lastrowid
    payment_info = db_fetch("SELECT value FROM settings WHERE key='payment'")
    payment_info = payment_info[0]['value'] if payment_info else "Ödeme bilgisi henüz ayarlanmadı."
    
    text = f"🧾 <b>Sipariş #{order_id}</b>\nÜrün: {p['name']}\nTutar: <b>{fmt_price(p['price'])}</b>\n\n💳 <b>Ödeme Bilgisi (IBAN)</b>\n<code>{payment_info}</code>\n\nÖdemeyi yaptıktan sonra dekont gönder."
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📤 Ödeme Yaptım, Dekont Gönder", callback_data=f"proof:{order_id}")]])
    await cb.message.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("proof:"))
async def cb_proof(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    order_id = int(cb.data.split(":")[1])
    await state.set_state(SendProof.proof)
    await state.update_data(order_id=order_id)
    await cb.message.answer("📤 Lütfen ödeme dekontunu (fotoğraf) veya işlem numarasını gönder:")

@dp.message(SendProof.proof)
async def receive_proof(message: Message, state: FSMContext):
    data = await state.get_data()
    order_id = data["order_id"]
    await state.clear()
    proof_text = "📷 Fotoğraf" if message.photo else f"📝 {message.text}"
    db_execute("UPDATE orders SET status='waiting', proof=? WHERE id=?", (proof_text, order_id))
    await message.answer("✅ Dekontun alındı! Admin onayından sonra anahtarın gönderilecek.")

    order = db_fetch("SELECT * FROM orders WHERE id=?", (order_id,))[0]
    product = db_fetch("SELECT * FROM products WHERE id=?", (order['product_id'],))[0]
    caption = f"🔔 <b>Yeni Sipariş #{order_id}</b>\n👤 {message.from_user.first_name} (@{message.from_user.username or '—'})\n🆔 <code>{message.from_user.id}</code>\n🛍 {product['name']}\n📝 {proof_text}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Onayla", callback_data=f"approve:{order_id}"), InlineKeyboardButton(text="❌ Reddet", callback_data=f"reject:{order_id}")]])
    
    try:
        if message.photo: await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=caption, reply_markup=kb, parse_mode="HTML")
        else: await bot.send_message(ADMIN_ID, caption, reply_markup=kb, parse_mode="HTML")
    except: pass

@dp.callback_query(F.data == "my_orders")
async def cb_my_orders(cb: CallbackQuery):
    await cb.answer()
    orders = db_fetch("SELECT o.*, p.name as pname FROM orders o JOIN products p ON p.id=o.product_id WHERE o.user_id=? ORDER BY o.id DESC", (cb.from_user.id,))
    if not orders: return await cb.message.answer("Henüz siparişin yok.")
    icons = {"pending": "🕓", "waiting": "⏳", "approved": "✅", "rejected": "❌"}
    lines = ["📦 <b>Siparişlerin</b>\n"] + [f"{icons.get(o['status'], '•')} #{o['id']} — {o['pname']} ({o['status']})" for o in orders]
    await cb.message.answer("\n".join(lines), parse_mode="HTML")

@dp.callback_query(F.data.startswith("approve:"))
async def cb_approve(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return await cb.answer("Yetkin yok.", show_alert=True)
    order_id = int(cb.data.split(":")[1])
    order = db_fetch("SELECT * FROM orders WHERE id=? AND status='waiting'", (order_id,))
    if not order: return await cb.answer("İşlendi veya bulunamadı.", show_alert=True)
    key = db_fetch("SELECT * FROM keys_pool WHERE product_id=? AND sold=0 LIMIT 1", (order[0]['product_id'],))
    if not key: return await cb.answer("Stok yok!", show_alert=True)
    
    db_execute("UPDATE keys_pool SET sold=1 WHERE id=?", (key[0]['id'],))
    db_execute("UPDATE orders SET status='approved' WHERE id=?", (order_id,))
    p = db_fetch("SELECT name FROM products WHERE id=?", (order[0]['product_id'],))[0]['name']
    try: await bot.send_message(order[0]['user_id'], f"🎉 <b>Onaylandı!</b>\n\n🧾 #{order_id} — {p}\n\n🔑 <b>Anahtar:</b>\n<code>{key[0]['key_value']}</code>", parse_mode="HTML")
    except: pass
    await cb.message.edit_text(cb.message.html_text + "\n\n✅ <b>ONAYLANDI</b>", parse_mode="HTML")
    await cb.answer("Onaylandı ✅")

@dp.callback_query(F.data.startswith("reject:"))
async def cb_reject(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return await cb.answer("Yetkin yok.", show_alert=True)
    order_id = int(cb.data.split(":")[1])
    db_execute("UPDATE orders SET status='rejected' WHERE id=?", (order_id,))
    order = db_fetch("SELECT user_id FROM orders WHERE id=?", (order_id,))[0]
    try: await bot.send_message(order['user_id'], f"❌ Siparişiniz (#{order_id}) reddedildi.", parse_mode="HTML")
    except: pass
    await cb.message.edit_text(cb.message.html_text + "\n\n❌ <b>REDDEDİLDİ</b>", parse_mode="HTML")
    await cb.answer("Reddedildi")

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id): return
    await message.answer("🛠 <b>Admin Paneli</b>\n\n/odeme_ayarla\n/urun_ekle\n/stok_ekle\n/siparisler", parse_mode="HTML")

@dp.message(Command("odeme_ayarla"))
async def cmd_set_payment(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.set_state(SetPayment.text)
    await message.answer("Müşterinin göreceği yeni ödeme bilgisini (IBAN, Papara vb.) yazın:")

@dp.message(SetPayment.text)
async def save_payment(message: Message, state: FSMContext):
    db_execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('payment', ?)", (message.text,))
    await state.clear()
    await message.answer("✅ Ödeme bilgisi güncellendi.")

@dp.message(Command("urun_ekle"))
async def cmd_add_product(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.set_state(AddProduct.name)
    await message.answer("1/3 — Ürün adı:")

@dp.message(AddProduct.name)
async def addp_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(AddProduct.description)
    await message.answer("2/3 — Açıklama:")

@dp.message(AddProduct.description)
async def addp_desc(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    await state.set_state(AddProduct.price)
    await message.answer("3/3 — Fiyat (Örn: 150):")

@dp.message(AddProduct.price)
async def addp_price(message: Message, state: FSMContext):
    try: price = float(message.text.replace(",", "."))
    except: return await message.answer("Geçerli bir sayı girin!")
    data = await state.get_data()
    db_execute("INSERT INTO products (name, description, price) VALUES (?, ?, ?)", (data['name'], data['description'], price))
    await state.clear()
    await message.answer(f"✅ Ürün eklendi: {data['name']}")

@dp.message(Command("stok_ekle"))
async def cmd_add_stock(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    products = db_fetch("SELECT * FROM products")
    if not products: return await message.answer("Önce /urun_ekle ile ürün ekleyin.")
    rows = [[InlineKeyboardButton(text=p['name'], callback_data=f"addstock:{p['id']}")] for p in products]
    await state.set_state(AddStock.product_id)
    await message.answer("Hangi ürüne stok ekleyeceksiniz?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@dp.callback_query(AddStock.product_id, F.data.startswith("addstock:"))
async def stock_pick(cb: CallbackQuery, state: FSMContext):
    await state.update_data(product_id=int(cb.data.split(":")[1]))
    await state.set_state(AddStock.keys)
    await cb.message.answer("Anahtarları gönderin (Her satıra 1 tane):")
    await cb.answer()

@dp.message(AddStock.keys)
async def stock_add(message: Message, state: FSMContext):
    data = await state.get_data()
    keys = [k.strip() for k in message.text.splitlines() if k.strip()]
    for k in keys: db_execute("INSERT INTO keys_pool (product_id, key_value) VALUES (?, ?)", (data['product_id'], k))
    await state.clear()
    await message.answer(f"✅ {len(keys)} anahtar eklendi.")

@dp.message(Command("siparisler"))
async def cmd_orders(message: Message):
    if not is_admin(message.from_user.id): return
    orders = db_fetch("SELECT o.*, p.name as pname, p.price FROM orders o JOIN products p ON p.id=o.product_id WHERE o.status='waiting'")
    if not orders: return await message.answer("Bekleyen sipariş yok ✅")
    for o in orders:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Onayla", callback_data=f"approve:{o['id']}"), InlineKeyboardButton(text="❌ Reddet", callback_data=f"reject:{o['id']}")]])
        await message.answer(f"🧾 <b>#{o['id']}</b>\n🛍 {o['pname']} - {fmt_price(o['price'])}\n👤 <code>{o['user_id']}</code>\n📝 {o['proof']}", reply_markup=kb, parse_mode="HTML")

@dp.message(F.text)
async def fallback(message: Message):
    await message.answer("Bunu anlamadım 🤔 /help yazın.")

async def main():
    init_db()
    print("Bot çalışıyor...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
