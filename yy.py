import logging
import sqlite3
import random
import string
import base64
import json
from datetime import datetime, timedelta
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode
import httpx

# ==================== الإعدادات العامة ====================
TOKEN = "8612117067:AAGGTV8QLmuEi7m3xGkNbuiZn5UJNtTwiwY"
ADMIN_ID = 7745757216                            # ايدي حسابك الرقمي
ENCRYPTION_KEY = bytes.fromhex('3859e4386916208894d1ebef53182792f02498eed478def11b46eef7435eebfb')

# ==================== إعدادات Groq (LLaMA) ====================
GROQ_API_KEY = "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"                        # ضع مفتاح Groq الخاص بك هنا
MODEL_NAME = "llama-3.1-8b-instant"                   # أو llama3-70b-8192
USE_MOCK_AI = False

# ==================== قاعدة البيانات ====================
DB_NAME = "monoaihub.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        full_name TEXT,
        activated INTEGER DEFAULT 0,
        activation_code TEXT,
        activation_date TEXT,
        expiry_date TEXT,
        blocked INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS activation_codes (
        code TEXT PRIMARY KEY,
        created_by INTEGER,
        validity_days INTEGER,
        used_by INTEGER,
        used_at TEXT,
        created_at TEXT,
        is_used INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS pending_activations (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        full_name TEXT,
        request_date TEXT
    )''')
    conn.commit()
    conn.close()

def db_execute(query, params=()):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(query, params)
    conn.commit()
    conn.close()

def db_fetchone(query, params=()):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(query, params)
    row = c.fetchone()
    conn.close()
    return row

def db_fetchall(query, params=()):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    return rows

# ==================== دوال مساعدة ====================
def generate_code(length=8):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def is_user_admin(user_id):
    return user_id == ADMIN_ID

def get_user_status(user_id):
    return db_fetchone("SELECT * FROM users WHERE user_id=?", (user_id,))

def user_is_activated(user_id):
    user = get_user_status(user_id)
    if not user:
        return False
    if user[7]:  # blocked
        return False
    if not user[3]:  # activated flag
        return False
    if user[6] and datetime.strptime(user[6], "%Y-%m-%d %H:%M:%S") < datetime.now():
        db_execute("UPDATE users SET activated=0 WHERE user_id=?", (user_id,))
        return False
    return True

def activate_user(user_id, code, validity_days=None):
    now = datetime.now()
    expiry = None
    if validity_days:
        expiry = now + timedelta(days=validity_days)
        expiry_str = expiry.strftime("%Y-%m-%d %H:%M:%S")
    else:
        expiry_str = None
    db_execute("UPDATE users SET activated=1, activation_code=?, activation_date=?, expiry_date=? WHERE user_id=?",
               (code, now.strftime("%Y-%m-%d %H:%M:%S"), expiry_str, user_id))
    db_execute("UPDATE activation_codes SET is_used=1, used_by=?, used_at=? WHERE code=?",
               (user_id, now.strftime("%Y-%m-%d %H:%M:%S"), code))
    db_execute("DELETE FROM pending_activations WHERE user_id=?", (user_id,))

# ==================== تشفير AES ====================
def aes_encrypt(plaintext: str) -> str:
    cipher = AES.new(ENCRYPTION_KEY, AES.MODE_CBC)
    ct_bytes = cipher.encrypt(pad(plaintext.encode('utf-8'), AES.block_size))
    iv = base64.b64encode(cipher.iv).decode('utf-8')
    ct = base64.b64encode(ct_bytes).decode('utf-8')
    return json.dumps({'iv': iv, 'ciphertext': ct})

def aes_decrypt(encrypted_text: str) -> str:
    try:
        b64 = json.loads(encrypted_text)
        iv = base64.b64decode(b64['iv'])
        ct = base64.b64decode(b64['ciphertext'])
        cipher = AES.new(ENCRYPTION_KEY, AES.MODE_CBC, iv)
        pt = unpad(cipher.decrypt(ct), AES.block_size)
        return pt.decode('utf-8')
    except Exception as e:
        return "❌ فشل فك التشفير"

# ==================== الذكاء الاصطناعي (Groq عبر httpx) ====================
def ask_ai(prompt: str) -> str:
    if USE_MOCK_AI:
        return "🧠 الذكاء الاصطناعي في وضع التجربة حالياً. فعّل المفتاح للاستخدام الحقيقي."
    try:
        response = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json={
                "model": MODEL_NAME,
                "messages": [{"role": "user", "content": prompt}]
            },
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            timeout=60
        )
        data = response.json()
        
        # التحقق من وجود choices
        if "choices" in data and len(data["choices"]) > 0:
            return data["choices"][0]["message"]["content"]
        elif "error" in data:
            return f"❌ خطأ: {data['error']['message']}"
        else:
            return f"❌ رد غير متوقع. تأكد من صحة مفتاح Groq."
    except Exception as e:
        return f"❌ خطأ في الاتصال: {str(e)}"

# ==================== حالات المستخدم ====================
USER_STATES = {}

# ==================== القوائم والأزرار ====================
def main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("🧠 الذكاء الاصطناعي", callback_data="menu_ai")],
        [InlineKeyboardButton("🔐 تشفير نص", callback_data="menu_encrypt"),
         InlineKeyboardButton("🔓 فك تشفير", callback_data="menu_decrypt")],
        [InlineKeyboardButton("💬 تواصل مع المطور", callback_data="menu_contact")],
    ]
    return InlineKeyboardMarkup(keyboard)

def admin_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats")],
        [InlineKeyboardButton("📝 طلبات التفعيل", callback_data="admin_requests")],
        [InlineKeyboardButton("🎫 إنشاء كود تفعيل", callback_data="admin_create_code")],
        [InlineKeyboardButton("🚫 حظر / فك حظر", callback_data="admin_block")],
        [InlineKeyboardButton("📣 إرسال إعلان", callback_data="admin_broadcast")],
    ]
    return InlineKeyboardMarkup(keyboard)

def back_to_main_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]])

# ==================== معالجات البوت ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    existing = get_user_status(user_id)
    if not existing:
        db_execute("INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?,?,?)",
                   (user_id, user.username, user.full_name))

    if user_is_activated(user_id):
        await update.message.reply_text(
            "🤖 *أهلاً بك في MonoAIHub* 🚀\n\n📌 اختر الخدمة التي تريدها:",
            reply_markup=main_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        keyboard = [
            [InlineKeyboardButton("🔑 طلب تفعيل", callback_data="request_activation")],
            [InlineKeyboardButton("🔹 لدي كود تفعيل", callback_data="use_code")],
        ]
        await update.message.reply_text(
            "🔒 *حسابك غير مفعّل*\n\nاختر أحد الخيارين:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

async def request_activation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    user_id = user.id

    if user_is_activated(user_id):
        await query.edit_message_text("✅ حسابك مفعّل مسبقاً", reply_markup=main_menu_keyboard())
        return

    db_execute("INSERT OR REPLACE INTO pending_activations (user_id, username, full_name, request_date) VALUES (?,?,?,?)",
               (user_id, user.username, user.full_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

    text = f"📩 *طلب تفعيل جديد*\n\n👤 المستخدم: @{user.username}\n📛 الاسم: {user.full_name}\n🆔 ID: `{user_id}`"
    admin_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ موافقة", callback_data=f"approve_{user_id}"),
         InlineKeyboardButton("❌ رفض", callback_data=f"reject_{user_id}")]
    ])
    await context.bot.send_message(ADMIN_ID, text, reply_markup=admin_kb, parse_mode=ParseMode.MARKDOWN)
    await query.edit_message_text("✅ *تم إرسال طلبك إلى المشرف*\n\n⏳ يرجى الانتظار...", parse_mode=ParseMode.MARKDOWN)

async def admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    action, target_id_str = data.split("_")
    target_id = int(target_id_str)

    if action == "approve":
        code = generate_code()
        validity = 7
        db_execute("INSERT INTO activation_codes (code, created_by, validity_days, created_at) VALUES (?,?,?,?)",
                   (code, ADMIN_ID, validity, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        try:
            await context.bot.send_message(target_id,
                f"🎉 *تمت الموافقة على تفعيلك!*\n\n🔑 الكود: `{code}`\n📅 الصلاحية: {validity} يوم\n\nاستخدم زر *لدي كود تفعيل* لإدخاله.",
                parse_mode=ParseMode.MARKDOWN)
        except:
            pass
        await query.edit_message_text(f"✅ تمت الموافقة على المستخدم `{target_id}`", parse_mode=ParseMode.MARKDOWN)
        db_execute("DELETE FROM pending_activations WHERE user_id=?", (target_id,))
    elif action == "reject":
        try:
            await context.bot.send_message(target_id, "❌ *تم رفض طلب التفعيل*\n\nيمكنك المحاولة مرة أخرى لاحقاً.", parse_mode=ParseMode.MARKDOWN)
        except:
            pass
        await query.edit_message_text(f"❌ تم رفض طلب المستخدم `{target_id}`", parse_mode=ParseMode.MARKDOWN)
        db_execute("DELETE FROM pending_activations WHERE user_id=?", (target_id,))

async def use_code_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔑 *أدخل كود التفعيل:*", parse_mode=ParseMode.MARKDOWN)
    USER_STATES[query.from_user.id] = "awaiting_code"

async def handle_code_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if USER_STATES.get(user_id) != "awaiting_code":
        return
    code = update.message.text.strip().upper()
    code_row = db_fetchone("SELECT * FROM activation_codes WHERE code=? AND is_used=0", (code,))
    if not code_row:
        await update.message.reply_text("❌ الكود غير صحيح أو مستخدم بالفعل.")
        return
    validity_days = code_row[2]
    activate_user(user_id, code, validity_days)
    USER_STATES.pop(user_id, None)
    await update.message.reply_text(
        f"🎉 *تم التفعيل بنجاح!*\n📅 الصلاحية: {validity_days} يوم",
        reply_markup=main_menu_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )

async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if not user_is_activated(user_id):
        await query.edit_message_text("🔒 حسابك غير مفعّل. استخدم /start للتفعيل.")
        return

    if data == "menu_ai":
        await query.edit_message_text("🧠 *أرسل سؤالك:*", reply_markup=back_to_main_button(), parse_mode=ParseMode.MARKDOWN)
        USER_STATES[user_id] = "ai_question"
    elif data == "menu_encrypt":
        await query.edit_message_text("🔐 *أرسل النص للتشفير:*", reply_markup=back_to_main_button(), parse_mode=ParseMode.MARKDOWN)
        USER_STATES[user_id] = "encrypt_text"
    elif data == "menu_decrypt":
        await query.edit_message_text("🔓 *أرسل النص المشفر:*", reply_markup=back_to_main_button(), parse_mode=ParseMode.MARKDOWN)
        USER_STATES[user_id] = "decrypt_text"
    elif data == "menu_contact":
        await query.edit_message_text("💬 *للتواصل مع المطور:*\n\n📧 @AdminUsername", reply_markup=back_to_main_button(), parse_mode=ParseMode.MARKDOWN)
    elif data == "back_main":
        await query.edit_message_text("📌 *القائمة الرئيسية:*", reply_markup=main_menu_keyboard(), parse_mode=ParseMode.MARKDOWN)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    state = USER_STATES.get(user_id)

    if state == "ai_question":
        await update.message.reply_text("⏳ *جاري التفكير...*", parse_mode=ParseMode.MARKDOWN)
        prompt = update.message.text
        reply = ask_ai(prompt)
        await update.message.reply_text(reply, reply_markup=main_menu_keyboard())
        USER_STATES.pop(user_id)
    elif state == "encrypt_text":
        plaintext = update.message.text
        encrypted = aes_encrypt(plaintext)
        await update.message.reply_text(f"🔐 *النص المشفر:*\n\n`{encrypted}`", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard())
        USER_STATES.pop(user_id)
    elif state == "decrypt_text":
        encrypted = update.message.text
        decrypted = aes_decrypt(encrypted)
        await update.message.reply_text(f"🔓 *النص المفكوك:*\n\n`{decrypted}`", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard())
        USER_STATES.pop(user_id)

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_admin(user_id):
        await update.message.reply_text("❌ غير مصرح.")
        return
    await update.message.reply_text("🛡️ *لوحة تحكم الأدمن:*", reply_markup=admin_menu_keyboard(), parse_mode=ParseMode.MARKDOWN)

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not is_user_admin(user_id):
        await query.edit_message_text("❌ غير مصرح.")
        return

    data = query.data

    if data == "admin_stats":
        total_users = db_fetchone("SELECT COUNT(*) FROM users")[0]
        active_users = db_fetchone("SELECT COUNT(*) FROM users WHERE activated=1 AND blocked=0")[0]
        blocked_users = db_fetchone("SELECT COUNT(*) FROM users WHERE blocked=1")[0]
        codes_total = db_fetchone("SELECT COUNT(*) FROM activation_codes")[0]
        codes_used = db_fetchone("SELECT COUNT(*) FROM activation_codes WHERE is_used=1")[0]
        stats_text = (
            "📊 *الإحصائيات*\n\n"
            f"👥 إجمالي المستخدمين: {total_users}\n"
            f"✅ المفعلين: {active_users}\n"
            f"🚫 المحظورين: {blocked_users}\n"
            f"🎫 الأكواد المنشأة: {codes_total}\n"
            f"♻️ المستخدمة: {codes_used}"
        )
        await query.edit_message_text(stats_text, reply_markup=admin_menu_keyboard(), parse_mode=ParseMode.MARKDOWN)

    elif data == "admin_requests":
        requests = db_fetchall("SELECT * FROM pending_activations")
        if not requests:
            await query.edit_message_text("📝 لا توجد طلبات معلقة.", reply_markup=admin_menu_keyboard())
        else:
            text = "📝 *الطلبات المعلقة:*\n"
            for req in requests:
                text += f"• @{req[1]} (ID: {req[0]})\n"
            await query.edit_message_text(text, reply_markup=admin_menu_keyboard(), parse_mode=ParseMode.MARKDOWN)

    elif data == "admin_create_code":
        code = generate_code()
        validity = 7
        db_execute("INSERT INTO activation_codes (code, created_by, validity_days, created_at) VALUES (?,?,?,?)",
                   (code, ADMIN_ID, validity, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        await query.edit_message_text(
            f"🎫 *تم إنشاء كود جديد:*\n\n`{code}`\n📅 الصلاحية: {validity} أيام",
            reply_markup=admin_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )

    elif data == "admin_block":
        USER_STATES[user_id] = "admin_block_id"
        await query.edit_message_text("🆔 *أرسل ID المستخدم لحظره أو فك حظره:*", reply_markup=back_to_main_button(), parse_mode=ParseMode.MARKDOWN)

    elif data == "admin_broadcast":
        USER_STATES[user_id] = "admin_broadcast_msg"
        await query.edit_message_text("📣 *أرسل نص الإعلان:*", reply_markup=back_to_main_button(), parse_mode=ParseMode.MARKDOWN)

    elif data == "back_main":
        await query.edit_message_text("📌 *القائمة الرئيسية:*", reply_markup=main_menu_keyboard(), parse_mode=ParseMode.MARKDOWN)

async def admin_block_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if USER_STATES.get(user_id) != "admin_block_id":
        return
    try:
        target_id = int(update.message.text)
    except:
        await update.message.reply_text("❌ يرجى إرسال رقم ID صحيح.")
        return
    user = db_fetchone("SELECT user_id, blocked FROM users WHERE user_id=?", (target_id,))
    if not user:
        await update.message.reply_text("❌ المستخدم غير موجود.")
    else:
        new_status = 0 if user[1] else 1
        db_execute("UPDATE users SET blocked=? WHERE user_id=?", (new_status, target_id))
        status = "🚫 حظر" if new_status else "✅ فك حظر"
        await update.message.reply_text(f"{status} المستخدم `{target_id}`", parse_mode=ParseMode.MARKDOWN)
    USER_STATES.pop(user_id, None)

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if USER_STATES.get(user_id) != "admin_broadcast_msg":
        return
    msg = update.message.text
    users = db_fetchall("SELECT user_id FROM users WHERE activated=1 AND blocked=0")
    count = 0
    for u in users:
        try:
            await context.bot.send_message(u[0], f"📣 *إعلان:*\n\n{msg}", parse_mode=ParseMode.MARKDOWN)
            count += 1
        except:
            pass
    await update.message.reply_text(f"✅ تم الإرسال إلى {count} مستخدم.")
    USER_STATES.pop(user_id, None)

# ==================== التشغيل ====================
def main():
    init_db()
    logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))

    app.add_handler(CallbackQueryHandler(request_activation, pattern="^request_activation$"))
    app.add_handler(CallbackQueryHandler(use_code_start, pattern="^use_code$"))
    app.add_handler(CallbackQueryHandler(admin_approve, pattern="^(approve|reject)_"))
    app.add_handler(CallbackQueryHandler(main_menu_handler, pattern="^menu_|^back_main$"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_code_input), group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text), group=2)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_block_id), group=3)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast), group=4)

    print("✅ MonoAIHub bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
