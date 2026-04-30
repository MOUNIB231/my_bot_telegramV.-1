import logging
import sqlite3
import random
import string
import base64
import json
import hashlib
from datetime import datetime, timedelta
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    ApplicationBuilder,
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
ADMIN_ID = 7745757216
ENCRYPTION_KEY = bytes.fromhex('3859e4386916208894d1ebef53182792f02498eed478def11b46eef7435eebfb')

# ==================== إعدادات Groq ====================
GROQ_API_KEY = "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"
MODEL_NAME = "llama-3.1-8b-instant"
USE_MOCK_AI = False

# ==================== نظام الصلاحيات ====================
LEVEL_NAMES = {'basic': '🥉 عادي', 'premium': '🥈 مميز', 'pro': '🥇 محترف', 'admin': '👑 أدمن'}
LEVEL_LIMITS = {'basic': 10, 'premium': 50, 'pro': 999, 'admin': 9999}

# ==================== أوضاع الذكاء الاصطناعي ====================
AI_MODES = {
    'general': "أنت مساعد ذكي ومفيد. أجب بالعربية بأسلوب واضح ومباشر.",
    'coder': "أنت خبير برمجة محترف. أجب بالعربية مع أمثلة برمجية كاملة.",
    'writer': "أنت كاتب محترف. أجب بالعربية بأسلوب أدبي وجذاب.",
    'creative': "أنت مبدع. قدم أفكاراً مبتكرة وحلولاً إبداعية بالعربية.",
    'translator': "أنت مترجم محترف. ترجم النص بدقة إلى العربية."
}
MODE_NAMES = {'general': '🧠 عام', 'coder': '💻 مبرمج', 'writer': '📝 كاتب', 'creative': '🎨 مبدع', 'translator': '🌍 مترجم'}

# ==================== قاعدة البيانات ====================
DB_NAME = "monoaihub.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT,
        activated INTEGER DEFAULT 0, activation_code TEXT,
        activation_date TEXT, expiry_date TEXT, blocked INTEGER DEFAULT 0,
        level TEXT DEFAULT 'basic', referral_code TEXT,
        referred_by INTEGER, referral_count INTEGER DEFAULT 0,
        join_date TEXT, last_active TEXT, total_ai_queries INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS activation_codes (
        code TEXT PRIMARY KEY, created_by INTEGER, validity_days INTEGER,
        used_by INTEGER, used_at TEXT, created_at TEXT,
        is_used INTEGER DEFAULT 0, level TEXT DEFAULT 'basic'
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS pending_activations (
        user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, request_date TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS ai_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
        question TEXT, answer TEXT, mode TEXT DEFAULT 'general', timestamp TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS daily_stats (
        date TEXT PRIMARY KEY, total_ai_requests INTEGER DEFAULT 0,
        total_encrypt INTEGER DEFAULT 0, total_decrypt INTEGER DEFAULT 0,
        new_users INTEGER DEFAULT 0, new_activations INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_daily_usage (
        user_id INTEGER, date TEXT, ai_count INTEGER DEFAULT 0,
        encrypt_count INTEGER DEFAULT 0, decrypt_count INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, date)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS scheduled_broadcasts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, message TEXT,
        scheduled_time TEXT, sent INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS broadcast_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, message TEXT,
        recipients_count INTEGER, sent_time TEXT
    )''')
    conn.commit()
    conn.close()

# ==================== دوال قاعدة البيانات ====================
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

def generate_referral_code(user_id):
    return f"REF{user_id}{random.randint(1000,9999)}"

def is_user_admin(user_id):
    return user_id == ADMIN_ID

def get_user_status(user_id):
    return db_fetchone("SELECT * FROM users WHERE user_id=?", (user_id,))

def user_is_activated(user_id):
    user = get_user_status(user_id)
    if not user: return False
    if len(user) > 7 and user[7]: return False
    if not user[3]: return False
    if len(user) > 6 and user[6] and datetime.strptime(user[6], "%Y-%m-%d %H:%M:%S") < datetime.now():
        db_execute("UPDATE users SET activated=0 WHERE user_id=?", (user_id,))
        return False
    return True

def get_user_level(user_id):
    user = get_user_status(user_id)
    return user[8] if user and len(user) > 8 else 'basic'

def get_daily_ai_usage(user_id):
    today = datetime.now().strftime("%Y-%m-%d")
    row = db_fetchone("SELECT ai_count FROM user_daily_usage WHERE user_id=? AND date=?", (user_id, today))
    return row[0] if row else 0

def check_ai_limit(user_id):
    level = get_user_level(user_id)
    if level in ['pro', 'admin']: return True, 999
    limit = LEVEL_LIMITS.get(level, 10)
    usage = get_daily_ai_usage(user_id)
    remaining = limit - usage
    return remaining > 0, remaining

def increment_ai_usage(user_id):
    today = datetime.now().strftime("%Y-%m-%d")
    db_execute("INSERT OR REPLACE INTO user_daily_usage (user_id, date, ai_count) VALUES (?, ?, COALESCE((SELECT ai_count FROM user_daily_usage WHERE user_id=? AND date=?), 0) + 1)", (user_id, today, user_id, today))
    db_execute("INSERT OR REPLACE INTO daily_stats (date, total_ai_requests) VALUES (?, COALESCE((SELECT total_ai_requests FROM daily_stats WHERE date=?), 0) + 1)", (today, today))
    db_execute("UPDATE users SET total_ai_queries = total_ai_queries + 1, last_active = ? WHERE user_id=?", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user_id))

def add_ai_history(user_id, question, answer, mode='general'):
    db_execute("INSERT INTO ai_history (user_id, question, answer, mode, timestamp) VALUES (?,?,?,?,?)",
               (user_id, question, answer, mode, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

def activate_user(user_id, code, validity_days=None, level='basic'):
    now = datetime.now()
    expiry = now + timedelta(days=validity_days) if validity_days else None
    expiry_str = expiry.strftime("%Y-%m-%d %H:%M:%S") if expiry else None
    db_execute("UPDATE users SET activated=1, activation_code=?, activation_date=?, expiry_date=?, level=? WHERE user_id=?",
               (code, now.strftime("%Y-%m-%d %H:%M:%S"), expiry_str, level, user_id))
    db_execute("UPDATE activation_codes SET is_used=1, used_by=?, used_at=? WHERE code=?",
               (user_id, now.strftime("%Y-%m-%d %H:%M:%S"), code))
    db_execute("DELETE FROM pending_activations WHERE user_id=?", (user_id,))
    today = now.strftime("%Y-%m-%d")
    db_execute("INSERT OR REPLACE INTO daily_stats (date, new_activations) VALUES (?, COALESCE((SELECT new_activations FROM daily_stats WHERE date=?), 0) + 1)", (today, today))

def process_referral(user_id, referral_code):
    if not referral_code: return
    referrer = db_fetchone("SELECT user_id, referral_count, activated FROM users WHERE referral_code=?", (referral_code,))
    if referrer and referrer[0] != user_id:
        db_execute("UPDATE users SET referral_count = referral_count + 1 WHERE user_id=?", (referrer[0],))
        db_execute("UPDATE users SET referred_by = ? WHERE user_id=?", (referrer[0], user_id))
        if referrer[1] + 1 >= 3 and referrer[2]:
            current_expiry = db_fetchone("SELECT expiry_date FROM users WHERE user_id=?", (referrer[0],))
            if current_expiry and current_expiry[0]:
                try:
                    new_expiry = datetime.strptime(current_expiry[0], "%Y-%m-%d %H:%M:%S") + timedelta(days=1)
                    db_execute("UPDATE users SET expiry_date=? WHERE user_id=?", (new_expiry.strftime("%Y-%m-%d %H:%M:%S"), referrer[0]))
                except: pass

# ==================== التشفير ====================
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
    except: return "❌ فشل فك التشفير"

def base64_encode(text: str) -> str: return base64.b64encode(text.encode()).decode()
def base64_decode(text: str) -> str:
    try: return base64.b64decode(text).decode()
    except: return "❌ نص غير صالح"
def md5_hash(text: str) -> str: return hashlib.md5(text.encode()).hexdigest()
def sha256_hash(text: str) -> str: return hashlib.sha256(text.encode()).hexdigest()

# ==================== الذكاء الاصطناعي ====================
def ask_ai(prompt: str, mode: str = 'general') -> str:
    if USE_MOCK_AI: return "🧠 الذكاء الاصطناعي في وضع التجربة."
    try:
        system_msg = AI_MODES.get(mode, AI_MODES['general'])
        response = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json={"model": MODEL_NAME, "messages": [{"role": "system", "content": system_msg}, {"role": "user", "content": prompt}]},
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            timeout=60
        )
        data = response.json()
        if "choices" in data and len(data["choices"]) > 0:
            return data["choices"][0]["message"]["content"]
        elif "error" in data: return f"❌ {data['error']['message']}"
        else: return "❌ رد غير متوقع"
    except Exception as e: return f"❌ خطأ: {str(e)}"

# ==================== حالات المستخدم ====================
USER_STATES = {}
USER_AI_MODE = {}

# ==================== القوائم والأزرار ====================
def main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("🧠 الذكاء الاصطناعي", callback_data="menu_ai")],
        [InlineKeyboardButton("🔄 وضع AI الحالي", callback_data="menu_ai_mode")],
        [InlineKeyboardButton("🔐 تشفير AES", callback_data="menu_encrypt"), InlineKeyboardButton("🔓 فك AES", callback_data="menu_decrypt")],
        [InlineKeyboardButton("📊 أدوات متقدمة", callback_data="menu_advanced")],
        [InlineKeyboardButton("👥 نظام الإحالة", callback_data="menu_referral")],
        [InlineKeyboardButton("📜 سجل الذكاء", callback_data="menu_history")],
        [InlineKeyboardButton("👤 حسابي", callback_data="menu_profile")],
        [InlineKeyboardButton("💬 تواصل", callback_data="menu_contact")],
    ]
    return InlineKeyboardMarkup(keyboard)

def admin_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("📊 إحصائيات شاملة", callback_data="admin_stats")],
        [InlineKeyboardButton("📝 طلبات التفعيل", callback_data="admin_requests")],
        [InlineKeyboardButton("🎫 إنشاء كود تفعيل", callback_data="admin_create_code")],
        [InlineKeyboardButton("🚫 حظر/فك مستخدم", callback_data="admin_block")],
        [InlineKeyboardButton("👑 تغيير صلاحية", callback_data="admin_change_level")],
        [InlineKeyboardButton("📣 إعلان فوري", callback_data="admin_broadcast")],
        [InlineKeyboardButton("📅 جدولة إعلان", callback_data="admin_schedule")],
    ]
    return InlineKeyboardMarkup(keyboard)

def back_to_main_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]])

def ai_modes_keyboard():
    keyboard = [
        [InlineKeyboardButton("🧠 وضع عام", callback_data="aimode_general")],
        [InlineKeyboardButton("💻 مبرمج", callback_data="aimode_coder"), InlineKeyboardButton("📝 كاتب", callback_data="aimode_writer")],
        [InlineKeyboardButton("🎨 مبدع", callback_data="aimode_creative"), InlineKeyboardButton("🌍 مترجم", callback_data="aimode_translator")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(keyboard)

def advanced_tools_keyboard():
    keyboard = [
        [InlineKeyboardButton("📦 Base64 تشفير", callback_data="tool_base64enc"), InlineKeyboardButton("📦 Base64 فك", callback_data="tool_base64dec")],
        [InlineKeyboardButton("🔑 MD5 Hash", callback_data="tool_md5")],
        [InlineKeyboardButton("🔒 SHA256 Hash", callback_data="tool_sha256")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(keyboard)

# ==================== معالجات البوت ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    existing = get_user_status(user_id)
    if not existing:
        ref_code = generate_referral_code(user_id)
        db_execute("INSERT OR IGNORE INTO users (user_id, username, full_name, referral_code, join_date) VALUES (?,?,?,?,?)",
                   (user_id, user.username, user.full_name, ref_code, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        today = datetime.now().strftime("%Y-%m-%d")
        db_execute("INSERT OR REPLACE INTO daily_stats (date, new_users) VALUES (?, COALESCE((SELECT new_users FROM daily_stats WHERE date=?), 0) + 1)", (today, today))
        if context.args: process_referral(user_id, context.args[0])
    else:
        db_execute("UPDATE users SET last_active=? WHERE user_id=?", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user_id))

    if user_is_activated(user_id):
        await update.message.reply_text("🤖 *MonoAIHub*\n\n🎯 اختر الخدمة:", reply_markup=main_menu_keyboard(), parse_mode=ParseMode.MARKDOWN)
    else:
        keyboard = [[InlineKeyboardButton("🔑 طلب تفعيل", callback_data="request_activation")], [InlineKeyboardButton("🔹 لدي كود", callback_data="use_code")]]
        await update.message.reply_text("🔒 *حسابك غير مفعّل*\nاختر أحد الخيارين:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def request_activation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    user_id = user.id
    if user_is_activated(user_id):
        await query.edit_message_text("✅ مفعّل مسبقاً", reply_markup=main_menu_keyboard())
        return
    db_execute("INSERT OR REPLACE INTO pending_activations VALUES (?,?,?,?)", (user_id, user.username, user.full_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    text = f"📩 *طلب تفعيل*\n👤 @{user.username}\n🆔 `{user_id}`"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ موافقة", callback_data=f"approve_{user_id}"), InlineKeyboardButton("❌ رفض", callback_data=f"reject_{user_id}")]])
    await context.bot.send_message(ADMIN_ID, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    await query.edit_message_text("✅ تم الإرسال للمشرف")

async def admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    action, target_id_str = data.split("_")
    target_id = int(target_id_str)
    if action == "approve":
        keyboard = [[InlineKeyboardButton("🥉 يوم - عادي", callback_data=f"approvewith_{target_id}_1_basic")],
                    [InlineKeyboardButton("🥈 7 أيام - مميز", callback_data=f"approvewith_{target_id}_7_premium")],
                    [InlineKeyboardButton("🥇 30 يوم - محترف", callback_data=f"approvewith_{target_id}_30_pro")]]
        await query.edit_message_text("📅 اختر المدة والمستوى:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        try: await context.bot.send_message(target_id, "❌ تم رفض طلبك")
        except: pass
        await query.edit_message_text(f"❌ تم رفض {target_id}")
        db_execute("DELETE FROM pending_activations WHERE user_id=?", (target_id,))

async def admin_approve_with_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, target_id_str, days_str, level = query.data.split("_")
    target_id, days = int(target_id_str), int(days_str)
    code = generate_code()
    db_execute("INSERT INTO activation_codes VALUES (?,?,?,NULL,NULL,?,0,?)", (code, ADMIN_ID, days, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), level))
    try: await context.bot.send_message(target_id, f"🎉 *تمت الموافقة!*\n🔑 الكود: `{code}`\n📅 {days} يوم\n⭐ {LEVEL_NAMES.get(level, level)}", parse_mode=ParseMode.MARKDOWN)
    except: pass
    await query.edit_message_text(f"✅ تم إنشاء كود لـ {target_id}")
    db_execute("DELETE FROM pending_activations WHERE user_id=?", (target_id,))

async def use_code_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔑 *أدخل الكود:*", parse_mode=ParseMode.MARKDOWN)
    USER_STATES[query.from_user.id] = "awaiting_code"

async def handle_code_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if USER_STATES.get(user_id) != "awaiting_code": return
    code = update.message.text.strip().upper()
    code_row = db_fetchone("SELECT * FROM activation_codes WHERE code=? AND is_used=0", (code,))
    if not code_row:
        await update.message.reply_text("❌ كود غير صحيح"); return
    validity_days = code_row[2]
    level = code_row[7] if len(code_row) > 7 else 'basic'
    activate_user(user_id, code, validity_days, level)
    USER_STATES.pop(user_id, None)
    await update.message.reply_text(f"🎉 *تم التفعيل!*\n📅 {validity_days} يوم\n⭐ {LEVEL_NAMES.get(level, level)}", reply_markup=main_menu_keyboard(), parse_mode=ParseMode.MARKDOWN)

# ==================== القائمة الرئيسية ====================
async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    if not user_is_activated(user_id):
        await query.edit_message_text("🔒 غير مفعّل"); return

    if data == "menu_ai":
        mode = USER_AI_MODE.get(user_id, 'general')
        can_ask, remaining = check_ai_limit(user_id)
        if not can_ask:
            try: await context.bot.send_message(user_id, f"⚠️ وصلت حد الأسئلة اليومي.\n⭐ مستواك: {LEVEL_NAMES.get(get_user_level(user_id))}")
            except: pass
            await query.edit_message_text(f"⚠️ وصلت حد الأسئلة اليومي", reply_markup=main_menu_keyboard()); return
        await query.edit_message_text(f"🧠 *اسأل الذكاء الاصطناعي*\n🎯 الوضع: {MODE_NAMES.get(mode, mode)}\n📊 المتبقي: {remaining}", reply_markup=back_to_main_button(), parse_mode=ParseMode.MARKDOWN)
        USER_STATES[user_id] = "ai_question"

    elif data == "menu_ai_mode":
        await query.edit_message_text("🔄 *اختر وضع الذكاء:*", reply_markup=ai_modes_keyboard(), parse_mode=ParseMode.MARKDOWN)
    elif data.startswith("aimode_"):
        mode = data.split("_")[1]
        USER_AI_MODE[user_id] = mode
        await query.edit_message_text(f"✅ تم التغيير إلى: *{MODE_NAMES.get(mode, mode)}*", reply_markup=main_menu_keyboard(), parse_mode=ParseMode.MARKDOWN)
    elif data == "menu_encrypt":
        await query.edit_message_text("🔐 *أرسل النص للتشفير:*", reply_markup=back_to_main_button(), parse_mode=ParseMode.MARKDOWN)
        USER_STATES[user_id] = "encrypt_text"
    elif data == "menu_decrypt":
        await query.edit_message_text("🔓 *أرسل النص المشفر:*", reply_markup=back_to_main_button(), parse_mode=ParseMode.MARKDOWN)
        USER_STATES[user_id] = "decrypt_text"
    elif data == "menu_advanced":
        await query.edit_message_text("📊 *أدوات متقدمة:*", reply_markup=advanced_tools_keyboard(), parse_mode=ParseMode.MARKDOWN)
    elif data.startswith("tool_"):
        tool = data.split("_")[1]
        names = {'base64enc': 'Base64 تشفير', 'base64dec': 'Base64 فك', 'md5': 'MD5', 'sha256': 'SHA256'}
        states = {'base64enc': 'base64_enc', 'base64dec': 'base64_dec', 'md5': 'md5_hash', 'sha256': 'sha256_hash'}
        await query.edit_message_text(f"📦 *{names.get(tool, tool)}*\nأرسل النص:", reply_markup=back_to_main_button(), parse_mode=ParseMode.MARKDOWN)
        USER_STATES[user_id] = states.get(tool)
    elif data == "menu_referral":
        user = get_user_status(user_id)
        ref_code = user[9] if len(user) > 9 else "غير متوفر"
        ref_count = user[11] if len(user) > 11 else 0
        link = f"https://t.me/{context.bot.username}?start={ref_code}"
        await query.edit_message_text(f"👥 *نظام الإحالة*\n\n🔗 رابطك:\n`{link}`\n\n👤 المدعوين: {ref_count}\n🎁 3 مدعوين = يوم مجاني!", reply_markup=main_menu_keyboard(), parse_mode=ParseMode.MARKDOWN)
    elif data == "menu_history":
        history = db_fetchall("SELECT question, answer, mode, timestamp FROM ai_history WHERE user_id=? ORDER BY id DESC LIMIT 5", (user_id,))
        if not history: await query.edit_message_text("📜 لا يوجد سجل", reply_markup=main_menu_keyboard())
        else:
            text = "📜 *آخر 5 محادثات:*\n\n"
            for q, a, m, t in history: text += f"❓ {q[:40]}...\n💬 {a[:60]}...\n🎯 {MODE_NAMES.get(m, m)} | 📅 {t}\n\n"
            await query.edit_message_text(text, reply_markup=main_menu_keyboard(), parse_mode=ParseMode.MARKDOWN)
    elif data == "menu_profile":
        user = get_user_status(user_id)
        level = get_user_level(user_id)
        limit = LEVEL_LIMITS.get(level, 10)
        usage = get_daily_ai_usage(user_id)
        expiry = user[6] if len(user) > 6 and user[6] else "غير محدد"
        total_ai = user[13] if len(user) > 13 else 0
        await query.edit_message_text(f"👤 *ملفك الشخصي*\n\n🆔 ID: {user_id}\n⭐ المستوى: {LEVEL_NAMES.get(level, level)}\n🧠 أسئلة اليوم: {usage}/{limit}\n📊 إجمالي الأسئلة: {total_ai}\n📅 الصلاحية: {expiry}", reply_markup=main_menu_keyboard(), parse_mode=ParseMode.MARKDOWN)
    elif data == "menu_contact":
        await query.edit_message_text("💬 *تواصل مع المطور*\n📧 @AdminUsername", reply_markup=back_to_main_button(), parse_mode=ParseMode.MARKDOWN)
    elif data == "back_main":
        await query.edit_message_text("📌 *القائمة الرئيسية:*", reply_markup=main_menu_keyboard(), parse_mode=ParseMode.MARKDOWN)

# ==================== معالجة النصوص ====================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    state = USER_STATES.get(user_id)
    if not state: return

    if state == "ai_question":
        can_ask, _ = check_ai_limit(user_id)
        if not can_ask:
            await update.message.reply_text("⚠️ وصلت حد الأسئلة اليومي", reply_markup=main_menu_keyboard())
            USER_STATES.pop(user_id); return
        await update.message.reply_text("⏳ *جاري التفكير...*", parse_mode=ParseMode.MARKDOWN)
        prompt = update.message.text
        mode = USER_AI_MODE.get(user_id, 'general')
        reply = ask_ai(prompt, mode)
        increment_ai_usage(user_id)
        add_ai_history(user_id, prompt, reply, mode)
        await update.message.reply_text(reply, reply_markup=main_menu_keyboard())
        USER_STATES.pop(user_id)
    elif state == "encrypt_text":
        result = aes_encrypt(update.message.text)
        await update.message.reply_text(f"🔐 *مشفر:*\n`{result}`", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard())
        USER_STATES.pop(user_id)
    elif state == "decrypt_text":
        result = aes_decrypt(update.message.text)
        await update.message.reply_text(f"🔓 *مفكوك:*\n`{result}`", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard())
        USER_STATES.pop(user_id)
    elif state in ["base64_enc", "base64_dec", "md5_hash", "sha256_hash"]:
        funcs = {'base64_enc': (base64_encode, "📦 *Base64:*"), 'base64_dec': (base64_decode, "📦 *مفكوك:*"), 'md5_hash': (md5_hash, "🔑 *MD5:*"), 'sha256_hash': (sha256_hash, "🔒 *SHA256:*")}
        func, label = funcs[state]
        result = func(update.message.text)
        await update.message.reply_text(f"{label}\n`{result}`", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard())
        USER_STATES.pop(user_id)

# ==================== لوحة تحكم الأدمن ====================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user_admin(update.effective_user.id):
        await update.message.reply_text("❌ غير مصرح"); return
    await update.message.reply_text("🛡️ *لوحة الأدمن:*", reply_markup=admin_menu_keyboard(), parse_mode=ParseMode.MARKDOWN)

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not is_user_admin(user_id): await query.edit_message_text("❌ غير مصرح"); return
    data = query.data

    if data == "admin_stats":
        total = db_fetchone("SELECT COUNT(*) FROM users")[0]
        active = db_fetchone("SELECT COUNT(*) FROM users WHERE activated=1 AND blocked=0")[0]
        blocked = db_fetchone("SELECT COUNT(*) FROM users WHERE blocked=1")[0]
        codes = db_fetchone("SELECT COUNT(*) FROM activation_codes")[0]
        used = db_fetchone("SELECT COUNT(*) FROM activation_codes WHERE is_used=1")[0]
        today = datetime.now().strftime("%Y-%m-%d")
        today_ai = db_fetchone("SELECT total_ai_requests FROM daily_stats WHERE date=?", (today,))
        today_ai = today_ai[0] if today_ai else 0
        top = db_fetchall("SELECT user_id, ai_count FROM user_daily_usage WHERE date=? ORDER BY ai_count DESC LIMIT 5", (today,))
        top_text = "\n".join([f"• {u}: {c} سؤال" for u, c in top]) if top else "لا يوجد"
        text = f"📊 *إحصائيات شاملة*\n\n👥 الكلي: {total}\n✅ النشط: {active}\n🚫 المحظور: {blocked}\n🎫 الأكواد: {codes}\n♻️ المستخدم: {used}\n🧠 أسئلة اليوم: {today_ai}\n\n📈 *الأكثر نشاطاً:*\n{top_text}"
        await query.edit_message_text(text, reply_markup=admin_menu_keyboard(), parse_mode=ParseMode.MARKDOWN)

    elif data == "admin_requests":
        reqs = db_fetchall("SELECT * FROM pending_activations")
        if not reqs: await query.edit_message_text("📝 لا طلبات", reply_markup=admin_menu_keyboard())
        else:
            text = "📝 *الطلبات:*\n" + "\n".join([f"• @{r[1]} ({r[0]})" for r in reqs])
            await query.edit_message_text(text, reply_markup=admin_menu_keyboard(), parse_mode=ParseMode.MARKDOWN)

    elif data == "admin_create_code":
        keyboard = [[InlineKeyboardButton("🥉 يوم - عادي", callback_data="gencode_1_basic")],
                    [InlineKeyboardButton("🥈 7 أيام - مميز", callback_data="gencode_7_premium")],
                    [InlineKeyboardButton("🥇 30 يوم - محترف", callback_data="gencode_30_pro")]]
        await query.edit_message_text("🎫 *اختر نوع الكود:*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    elif data.startswith("gencode_"):
        _, days, level = data.split("_")
        code = generate_code()
        db_execute("INSERT INTO activation_codes VALUES (?,?,?,NULL,NULL,?,0,?)", (code, ADMIN_ID, int(days), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), level))
        await query.edit_message_text(f"🎫 *كود جديد:*\n`{code}`\n📅 {days} يوم\n⭐ {LEVEL_NAMES.get(level, level)}", reply_markup=admin_menu_keyboard(), parse_mode=ParseMode.MARKDOWN)

    elif data == "admin_block":
        USER_STATES[user_id] = "admin_block_id"
        await query.edit_message_text("🆔 *أرسل ID المستخدم:*", reply_markup=back_to_main_button(), parse_mode=ParseMode.MARKDOWN)
    elif data == "admin_change_level":
        USER_STATES[user_id] = "admin_change_level_id"
        await query.edit_message_text("🆔 *أرسل ID المستخدم:*", reply_markup=back_to_main_button(), parse_mode=ParseMode.MARKDOWN)
    elif data == "admin_broadcast":
        USER_STATES[user_id] = "admin_broadcast_msg"
        await query.edit_message_text("📣 *أرسل نص الإعلان:*", reply_markup=back_to_main_button(), parse_mode=ParseMode.MARKDOWN)
    elif data == "admin_schedule":
        USER_STATES[user_id] = "admin_schedule_msg"
        await query.edit_message_text("📅 *أرسل نص الإعلان المجدول:*", reply_markup=back_to_main_button(), parse_mode=ParseMode.MARKDOWN)
    elif data == "back_main":
        await query.edit_message_text("📌 *القائمة:*", reply_markup=main_menu_keyboard(), parse_mode=ParseMode.MARKDOWN)

async def admin_block_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if USER_STATES.get(user_id) != "admin_block_id": return
    try: target = int(update.message.text)
    except: await update.message.reply_text("❌ رقم غير صالح"); return
    user = db_fetchone("SELECT blocked FROM users WHERE user_id=?", (target,))
    if not user: await update.message.reply_text("❌ غير موجود")
    else:
        new = 0 if user[0] else 1
        db_execute("UPDATE users SET blocked=? WHERE user_id=?", (new, target))
        await update.message.reply_text(f"{'🚫 حظر' if new else '✅ فك'} {target}")
    USER_STATES.pop(user_id, None)

async def admin_change_level_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if USER_STATES.get(user_id) != "admin_change_level_id": return
    try: target = int(update.message.text)
    except: await update.message.reply_text("❌ رقم غير صالح"); return
    keyboard = [[InlineKeyboardButton("🥉 عادي", callback_data=f"setlevel_{target}_basic")],
                [InlineKeyboardButton("🥈 مميز", callback_data=f"setlevel_{target}_premium")],
                [InlineKeyboardButton("🥇 محترف", callback_data=f"setlevel_{target}_pro")]]
    await update.message.reply_text("⭐ اختر المستوى:", reply_markup=InlineKeyboardMarkup(keyboard))
    USER_STATES.pop(user_id, None)

async def set_level_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_user_admin(query.from_user.id): return
    _, target, level = query.data.split("_")
    db_execute("UPDATE users SET level=? WHERE user_id=?", (level, int(target)))
    await query.edit_message_text(f"✅ تم تغيير مستوى {target} إلى {LEVEL_NAMES.get(level, level)}")

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if USER_STATES.get(user_id) != "admin_broadcast_msg": return
    msg = update.message.text
    users = db_fetchall("SELECT user_id FROM users WHERE activated=1 AND blocked=0")
    c = 0
    for u in users:
        try: await context.bot.send_message(u[0], f"📣 *إعلان:*\n{msg}", parse_mode=ParseMode.MARKDOWN); c += 1
        except: pass
    db_execute("INSERT INTO broadcast_log (message, recipients_count, sent_time) VALUES (?,?,?)", (msg, c, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    await update.message.reply_text(f"✅ أُرسل إلى {c}")
    USER_STATES.pop(user_id, None)

async def admin_schedule_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if USER_STATES.get(user_id) != "admin_schedule_msg": return
    USER_STATES[user_id] = "admin_schedule_time"
    USER_STATES["admin_schedule_msg_text"] = update.message.text
    await update.message.reply_text("⏰ *أرسل وقت الإرسال*\nبصيغة: YYYY-MM-DD HH:MM", parse_mode=ParseMode.MARKDOWN)

async def admin_schedule_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if USER_STATES.get(user_id) != "admin_schedule_time": return
    time_str = update.message.text
    msg = USER_STATES.pop("admin_schedule_msg_text", "")
    db_execute("INSERT INTO scheduled_broadcasts (message, scheduled_time) VALUES (?,?)", (msg, time_str))
    await update.message.reply_text(f"✅ تمت جدولة الإعلان في {time_str}", reply_markup=admin_menu_keyboard())
    USER_STATES.pop(user_id, None)

# ==================== المهام المجدولة ====================
async def check_expiring_accounts(context: ContextTypes.DEFAULT_TYPE):
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    users = db_fetchall("SELECT user_id, level, expiry_date FROM users WHERE activated=1 AND blocked=0 AND expiry_date BETWEEN ? AND ?", (now, tomorrow))
    for u_id, level, expiry in users:
        try:
            remaining = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S") - datetime.now()
            hours = int(remaining.total_seconds() / 3600)
            await context.bot.send_message(u_id, f"⚠️ *تنبيه الصلاحية*\n\n📅 تنتهي صلاحيتك خلال {hours} ساعة\n⭐ {LEVEL_NAMES.get(level, level)}", parse_mode=ParseMode.MARKDOWN)
        except: pass

async def check_scheduled_broadcasts(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    broadcasts = db_fetchall("SELECT id, message FROM scheduled_broadcasts WHERE scheduled_time <= ? AND sent=0", (now,))
    for b_id, msg in broadcasts:
        users = db_fetchall("SELECT user_id FROM users WHERE activated=1 AND blocked=0")
        c = 0
        for u in users:
            try: await context.bot.send_message(u[0], f"📣 *إعلان مجدول:*\n\n{msg}", parse_mode=ParseMode.MARKDOWN); c += 1
            except: pass
        db_execute("UPDATE scheduled_broadcasts SET sent=1 WHERE id=?", (b_id,))
        db_execute("INSERT INTO broadcast_log (message, recipients_count, sent_time) VALUES (?,?,?)", (msg, c, now))

# ==================== التشغيل ====================
def main():
    init_db()
    logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

    # استخدام ApplicationBuilder (متوافق مع v21.x و Python 3.13)
    app = ApplicationBuilder().token(TOKEN).build()

    # المهام المجدولة
    job_queue = app.job_queue
    job_queue.run_repeating(check_expiring_accounts, interval=3600, first=60)
    job_queue.run_repeating(check_scheduled_broadcasts, interval=300, first=30)

    # المعالجات
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(request_activation, pattern="^request_activation$"))
    app.add_handler(CallbackQueryHandler(use_code_start, pattern="^use_code$"))
    app.add_handler(CallbackQueryHandler(admin_approve, pattern="^(approve|reject)_"))
    app.add_handler(CallbackQueryHandler(admin_approve_with_days, pattern="^approvewith_"))
    app.add_handler(CallbackQueryHandler(set_level_callback, pattern="^setlevel_"))
    app.add_handler(CallbackQueryHandler(main_menu_handler, pattern="^menu_|^aimode_|^tool_|^back_main$"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_|^gencode_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_code_input), group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text), group=2)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_block_id), group=3)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_change_level_id), group=4)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast), group=5)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_schedule_msg), group=6)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_schedule_time), group=7)

    print("✅ MonoAIHub bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
