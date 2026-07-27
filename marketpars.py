import asyncio
import logging
import sqlite3
import json
import os
import ssl
import re
import sys
import time
import html
import random
import warnings
warnings.filterwarnings("ignore", message="Using async sessions support is an experimental feature")
from datetime import datetime, timedelta

from telethon import TelegramClient, functions, types, errors
from telethon.network.connection import ConnectionTcpMTProxyRandomizedIntermediate
from telethon.tl.functions.users import GetFullUserRequest
import aiohttp
from aiohttp import web
import certifi

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

PAID_DM_CACHE_TTL = 600  # кэш платных ЛС: 10 минут

DATA_DIR = os.getenv("DATA_DIR", "data")
if not os.path.isabs(DATA_DIR):
    DATA_DIR = os.path.abspath(DATA_DIR)
os.makedirs(DATA_DIR, exist_ok=True)

def ensure_data_path(path: str) -> str:
    if not path:
        return path
    if os.path.isabs(path):
        dir_path = os.path.dirname(path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        return path
    target = os.path.join(DATA_DIR, path)
    dir_path = os.path.dirname(target)
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)
    return target


def ensure_session_path(session_name: str) -> str:
    if not session_name:
        session_name = "gift_parser_session"
    if not session_name.endswith(".session"):
        session_name = session_name + ".session"
    return ensure_data_path(session_name)

# ============================================================
# НАСТРОЙКИ
# ============================================================

# Данные берутся из .env / переменных окружения.
# Не держи токены прямо в коде.
API_ID = int(os.getenv("API_ID", "37665478"))
API_HASH = os.getenv("API_HASH", "e5305ff832253dfe2d74fdbb530c3b65")
BOT_TOKEN = os.getenv("BOT_TOKEN", "8971114087:AAHHyrCbf88GcCBCSDV5ixJU-WyESzKQRKk")
NOTIFY_CHAT_ID = int(os.getenv("NOTIFY_CHAT_ID", "-1004331005672"))

# ID топика/темы "Tracker" в группе. 0 = общий чат (НЕ то что нужно).
# Как узнать: добавь бота в группу админом -> напиши любое сообщение в теме Tracker ->
# открой в браузере https://api.telegram.org/bot<ТВОЙ_ТОКЕН>/getUpdates ->
# найди в JSON поле "message_thread_id" внутри этого сообщения -> вставь число сюда.
NOTIFY_THREAD_ID = int(os.getenv("NOTIFY_THREAD_ID", "0"))  # <-- ПОДСТАВЬ РЕАЛЬНЫЙ ID ТЕМЫ

# Бот работает только с этой группой — все остальные апдейты игнорируются.
ALLOWED_CHAT_ID = NOTIFY_CHAT_ID

# Telegram user id (или несколько через запятую), кому разрешена команда /session.
# Узнать свой ID: напиши @userinfobot.
ADMIN_IDS = set()
for _x in os.getenv("ADMIN_IDS", "8575075839").split(","):
    _x = _x.strip()
    if _x.isdigit():
        ADMIN_IDS.add(int(_x))


CREATOR = "svigno"


STARS_PER_TON = 130


CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))

# Пауза между отправкой отдельных лотов в группу (чтобы не спамить пачкой).
POST_DELAY_MIN = int(os.getenv("POST_DELAY_MIN", "30"))
POST_DELAY_MAX = int(os.getenv("POST_DELAY_MAX", "45"))


MIN_PRICE_STARS = 0
MAX_PRICE_STARS = 0


TRACK_GIFTS = [
    "Plush Pepe",
    "Heart Locket",
    "Durov's Cap",
    "Precious Peach",
    "Heroic Helmet",
    "Scared Cat",
    "Astral Shard",
    "Mighty Arm",
    "Loot Bag",
    "Nail Bracelet",
    "Westside Sign",
    "Mini Oscar",
    "Perfume Bottle",
    "Ion Gem",
    "Gem Signet",
    "Magic Potion",
    "Artisan Brick",
    "Low Rider",
    "Swiss Watch",
    "Sharp Tongue",
    "Kissed Frog",
    "Bonded Ring",
    "Toy Bear",
    "Genie Lamp",
    "Neko Helmet",
    "Vintage Cigar",
    "Voodoo Doll",
    "Signet Ring",
    "Diamond Ring",
    "Electric Skull",
    "Eternal Rose",
    "Rare Bird",
    "Khabib's Papakha",
    "Bling Binky",
    "Cupid Charm",
    "Sky Stilettos",
    "UFC Strike",
    "Ionic Dryer",
    "Love Potion",
    "Trapped Heart",
    "Record Player",
    "Crystal Ball",
    "Snoop Cigar",
    "Flying Broom",
    "Mad Pumpkin",
    "Skull Flower",
    "Valentine Box",
    "Top Hat",
]


EXCLUDE_GIFTS = []


MAX_RARITY_PERCENT = 0


ONLY_FREE_DM = True


MAX_SELLER_LEVEL = 2


MAX_SELLER_GIFTS = 15

SESSION_NAME = ensure_session_path(os.getenv("SESSION_NAME", "gift_parser_session"))
DB_PATH = ensure_data_path(os.getenv("DB_PATH", "gift_market.db"))


logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger("GiftParser")

WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("PORT", os.getenv("WEB_PORT", "10000")))

ACTIVE_LISTINGS = {}

#
BOT_USERNAME = ""

async def stats_handler(request: web.Request):
    parser = request.app["parser"]
    stats = parser.stats
    active_count = len(ACTIVE_LISTINGS)
    html_text = f"""
    <html>
      <head>
        <title>Gift Parser Status</title>
        <style>
          body {{ font-family: Arial, sans-serif; background: #121212; color: #f5f5f5; }}
          .container {{ max-width: 760px; margin: 40px auto; padding: 24px; background: #1d1d1d; border-radius: 16px; box-shadow: 0 16px 40px rgba(0,0,0,0.3); }}
          h1 {{ margin-top: 0; }} .stat {{ padding: 16px; background: #282828; border-radius: 12px; margin: 12px 0; }}
          .label {{ color: #8ab4f8; }} .value {{ font-size: 2.4rem; margin-top: 8px; }}
        </style>
      </head>
      <body>
        <div class="container">
          <h1>Gift Parser Status</h1>
          <p>Статистика бота на текущий момент.</p>
          <div class="stat"><div class="label">Отправлено уведомлений о подарках</div><div class="value">{stats["notifications_sent"]}</div></div>
          <div class="stat"><div class="label">Новых найденных подарков</div><div class="value">{stats["new_listings"]}</div></div>
          <div class="stat"><div class="label">Подарков в обработке / активных списков</div><div class="value">{active_count}</div></div>
          <div class="stat"><div class="label">Ошибок</div><div class="value">{stats["errors"]}</div></div>
          <div class="stat"><div class="label">Проверок рынка</div><div class="value">{stats["checks"]}</div></div>
          <p>Сервис запущен как <strong>{CREATOR}</strong>.</p>
        </div>
      </body>
    </html>
    """
    return web.Response(text=html_text, content_type="text/html")

async def stats_api_handler(request: web.Request):
    parser = request.app["parser"]
    return web.json_response({
        "notifications_sent": parser.stats["notifications_sent"],
        "new_listings": parser.stats["new_listings"],
        "active_listings": len(ACTIVE_LISTINGS),
        "errors": parser.stats["errors"],
        "checks": parser.stats["checks"],
    })

async def health_handler(request: web.Request):
    return web.json_response({
        "status": "ok",
        "active_listings": len(ACTIVE_LISTINGS),
        "notifications_sent": request.app["parser"].stats["notifications_sent"],
    })

async def active_listings_handler(request: web.Request):
    listing_data = []
    for token, item in ACTIVE_LISTINGS.items():
        listing = item.get("listing", {})
        listing_data.append({
            "token": token,
            "title": listing.get("gift_name") or listing.get("title"),
            "price": listing.get("price"),
            "seller_id": listing.get("seller_id"),
            "seller_name": listing.get("seller_name"),
            "message_id": item.get("message_id"),
            "claimed_by": item.get("claimed_by"),
            "claimed_by_display": item.get("claimed_by_display"),
        })
    return web.json_response({
        "active_listings": len(listing_data),
        "listings": listing_data,
    })

async def run_http_server(parser):
    app = web.Application()
    app["parser"] = parser
    app.router.add_get("/", stats_handler)
    app.router.add_get("/api/stats", stats_api_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/active", active_listings_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEB_HOST, WEB_PORT)
    await site.start()
    log.info(f"HTTP stats page available at http://{WEB_HOST}:{WEB_PORT}/")
    while True:
        await asyncio.sleep(3600)

#
BOT_USERNAME = ""

DISABLE_SSL_VERIFY = os.getenv("DISABLE_SSL_VERIFY", "0") == "1"

# Прокси для РФ (Telethon = MTProxy, Bot API = SOCKS5/HTTP).
MTPROXY_HOST = ""
MTPROXY_PORT = 443
MTPROXY_SECRET = ""
# Примеры: socks5://127.0.0.1:1080  или  http://127.0.0.1:7890
BOT_PROXY = os.getenv("BOT_PROXY", "").strip()

def get_telethon_proxy():
    proxy = os.getenv("TG_PROXY", "").strip()

    if not proxy:
        return None

    host, port = proxy.replace("socks5://", "").split(":")

    return (
        "socks5",
        host,
        int(port)
    )
    
def make_aiohttp_connector():
    if BOT_PROXY:
        from aiohttp_socks import ProxyConnector
        return ProxyConnector.from_url(BOT_PROXY, ssl=SSL_CONTEXT)
    return aiohttp.TCPConnector(ssl=SSL_CONTEXT)

def make_ssl_context():
    if DISABLE_SSL_VERIFY:
        log.warning("SSL verification отключён через DISABLE_SSL_VERIFY=1. Используй только временно.")
        return False
    return ssl.create_default_context(cafile=certifi.where())

SSL_CONTEXT = make_ssl_context()

def validate_config():
    missing = []
    if not API_ID:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not NOTIFY_CHAT_ID:
        missing.append("NOTIFY_CHAT_ID")
    if missing:
        raise RuntimeError(
            "Не хватает настроек: " + ", ".join(missing) +
            "\nСоздай .env рядом с файлом или задай переменные окружения."
        )
    if NOTIFY_THREAD_ID == 0:
        log.warning(
            "NOTIFY_THREAD_ID не задан (0) — сообщения уйдут в общий чат группы, "
            "а не в тему 'Tracker'. Смотри инструкцию в коде рядом с NOTIFY_THREAD_ID."
        )
    if not ADMIN_IDS:
        log.warning(
            "ADMIN_IDS не задан — команду /session не сможет использовать никто. "
            "Узнай свой Telegram ID через @userinfobot и пропиши в .env: ADMIN_IDS=123456789"
        )
    if get_telethon_proxy():
        log.info(f"Telethon MTProxy: {MTPROXY_HOST}:{MTPROXY_PORT}")
    else:
        log.warning("MTPROXY не задан — Telethon может не подключиться из РФ.")
    if BOT_PROXY:
        log.info(f"Bot API proxy: {BOT_PROXY.split('@')[-1]}")
    else:
        log.warning("BOT_PROXY не задан — Bot API (уведомления, /session) может не работать из РФ.")

# ============================================================
# БАЗА ДАННЫХ
# ============================================================

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS seen_listings (
            gift_unique_id TEXT PRIMARY KEY,
            gift_name TEXT,
            gift_num INTEGER,
            price_stars INTEGER,
            seller_id INTEGER,
            first_seen TEXT,
            notified INTEGER DEFAULT 0
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS base_gifts (
            gift_id INTEGER PRIMARY KEY,
            star_count INTEGER,
            total_count INTEGER,
            remaining_count INTEGER,
            title TEXT,
            last_updated TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS active_listings (
            token TEXT PRIMARY KEY,
            listing_json TEXT NOT NULL,
            message_id INTEGER,
            claimed_by INTEGER,
            claimed_by_display TEXT,
            created_at TEXT
        )
    ''')
    c.execute('''
        CREATE INDEX IF NOT EXISTS idx_seen_gift_name
        ON seen_listings(gift_name)
    ''')
    conn.commit()
    conn.close()
    log.info("БД инициализирована")


def is_listing_seen(gift_unique_id: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM seen_listings WHERE gift_unique_id = ?", (gift_unique_id,))
    result = c.fetchone()
    conn.close()
    return result is not None


def save_listing(gift_unique_id: str, gift_name: str, gift_num: int,
                 price_stars: int, seller_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT OR IGNORE INTO seen_listings 
        (gift_unique_id, gift_name, gift_num, price_stars, seller_id, first_seen, notified)
        VALUES (?, ?, ?, ?, ?, ?, 1)
    ''', (gift_unique_id, gift_name, gift_num, price_stars, seller_id,
          datetime.now().isoformat()))
    conn.commit()
    conn.close()


def save_active_listing(token: str, listing: dict, message_id: int = None):
    """Сохраняет активный лот, чтобы кнопка «Занять» работала даже после рестарта."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO active_listings
        (token, listing_json, message_id, claimed_by, claimed_by_display, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        token,
        json.dumps(listing, ensure_ascii=False),
        message_id,
        None,
        None,
        datetime.now().isoformat(),
    ))
    conn.commit()
    conn.close()


def update_active_claim(token: str, claimed_by=None, claimed_by_display=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "UPDATE active_listings SET claimed_by = ?, claimed_by_display = ? WHERE token = ?",
        (claimed_by, claimed_by_display, token),
    )
    conn.commit()
    conn.close()


def load_active_listing(token: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT listing_json, message_id, claimed_by, claimed_by_display FROM active_listings WHERE token = ?",
        (token,),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    try:
        listing = json.loads(row[0])
    except Exception:
        return None
    return {
        "listing": listing,
        "message_id": row[1],
        "claimed_by": row[2],
        "claimed_by_display": row[3],
    }


def save_base_gift(gift_id: int, star_count: int, total_count: int,
                   remaining_count: int, title: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO base_gifts 
        (gift_id, star_count, total_count, remaining_count, title, last_updated)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (gift_id, star_count, total_count, remaining_count, title,
          datetime.now().isoformat()))
    conn.commit()
    conn.close()


def get_base_gifts():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT gift_id, star_count, total_count, remaining_count, title FROM base_gifts")
    rows = c.fetchall()
    conn.close()
    return rows


def cleanup_old_listings(days: int = 7):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    c.execute("DELETE FROM seen_listings WHERE first_seen < ?", (cutoff,))
    deleted = c.rowcount
    c.execute("DELETE FROM active_listings WHERE created_at < ?", (cutoff,))
    conn.commit()
    conn.close()
    if deleted > 0:
        log.info(f"Очищено {deleted} старых записей")


# ============================================================
# КАСТОМНЫЕ TELEGRAM-ЭМОДЗИ (tg-emoji для текста сообщений)
# ============================================================
# Пак "The Open Emojis" — 27 премиум-эмодзи, заданных пользователем.
# ВАЖНО: у Telegram кастомного эмодзи "показываемый" символ обязан совпадать
# с тем, что зашито в самом premium-эмодзи документе, иначе Telegram может
# вернуть DOCUMENT_INVALID. Поэтому глиф в теге строго соответствует эмодзи
# из присланного списка, а не "как было раньше".
_E = {
    "confetti":     '<tg-emoji emoji-id="5258332798409783582">🚀</tg-emoji>',   # заголовок "новый лот"
    "gift":         '<tg-emoji emoji-id="5203977968644288289">🖼</tg-emoji>',   # визуал гифта
    "money":        '<tg-emoji emoji-id="5388774339623540025">🪙</tg-emoji>',   # цена
    "star":         '<tg-emoji emoji-id="5402104393396931859">⭐️</tg-emoji>',  # статус/звёзды
    "diamond":      '<tg-emoji emoji-id="5296742257146241213">💎</tg-emoji>',   # модель/редкость
    "person":       '<tg-emoji emoji-id="5246734896356936944">📱</tg-emoji>',   # продавец
    "chart":        '<tg-emoji emoji-id="5260343246831237239">⚙️</tg-emoji>',  # level
    "megaphone":    '<tg-emoji emoji-id="5257952710983955418">📰</tg-emoji>',   # сообщения / лог
    "arrow":        '<tg-emoji emoji-id="5240428351063081133">🌉</tg-emoji>',   # ссылка на лот
    "clock":        '<tg-emoji emoji-id="5303400229549135579">🌅</tg-emoji>',   # время
    "lock":         '<tg-emoji emoji-id="5386534612962915793">🥷</tg-emoji>',   # занято
    "no_entry":     '<tg-emoji emoji-id="5397982951369622729">🏴‍☠️</tg-emoji>', # платный ЛС / фильтр
    "search":       '<tg-emoji emoji-id="5411558372329667998">🌐</tg-emoji>',   # интервал/поиск
    "bell":         '<tg-emoji emoji-id="5424912684078348533">❤️</tg-emoji>',  # акцент/уведомление
    "top":          '<tg-emoji emoji-id="5390997973041701983">🔝</tg-emoji>',   # топ-лот / бейдж
    "robot":        '<tg-emoji emoji-id="5197252827247841976">🤖</tg-emoji>',   # подпись бота/автора
    "wallet":       '<tg-emoji emoji-id="5424976816530014958">👛</tg-emoji>',   # TON-цена
    "snow":         '<tg-emoji emoji-id="5201741854051156616">❄️</tg-emoji>',  # декоративный разделитель
    "block":        '<tg-emoji emoji-id="5303242028723753471">⬛️</tg-emoji>',  # маркер списка
    "bolt":         '<tg-emoji emoji-id="5260249805522744465">🔩</tg-emoji>',   # тех-детали
    # Функциональные статусы оставлены обычными юникод-эмодзи намеренно:
    # они должны рендериться одинаково в 100% клиентов Telegram (в т.ч. без
    # премиума у получателя), т.к. несут смысл "занято/свободно/ошибка".
    "check":        '✅',
    "cross":        '❌',
    "cancel":       '✗',
    "take":         '✓',
}

e = _E  # короткий алиас


def strip_custom_emoji(text: str) -> str:
    """Убирает теги <tg-emoji ...>X</tg-emoji>, оставляя обычный эмодзи X.
    Используется как фоллбэк, если Telegram отклонил кастомные эмодзи."""
    return re.sub(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', r'\1', text, flags=re.DOTALL)


def topic_id(thread_id: int = None):
    """Возвращает message_thread_id для отправки в топик группы или None для общего чата."""
    if thread_id is None:
        thread_id = NOTIFY_THREAD_ID
    try:
        thread_id = int(thread_id or 0)
    except (TypeError, ValueError):
        return None
    return thread_id if thread_id > 0 else None


def tg_emoji(emoji_id: str, fallback: str = "✨") -> str:
    """Безопасный fallback для emoji из атрибутов NFT.
    Важно: document.id из Telethon-атрибутов часто НЕ подходит для Bot API как custom_emoji_id.
    Если вставить его в <tg-emoji>, Telegram может вернуть DOCUMENT_INVALID.
    Поэтому для динамических emoji от модели возвращаем обычный fallback-emoji.
    """
    return fallback


def should_retry_without_custom_emoji(data: dict) -> bool:
    """Понимает, что Telegram отклонил HTML/custom emoji и надо повторить без <tg-emoji>."""
    if not data or data.get("ok"):
        return False
    desc = (data.get("description") or "").lower()
    bad_markers = (
        "emoji",
        "entity",
        "document_invalid",
        "document invalid",
        "can't parse",
        "bad request: document",
    )
    return any(x in desc for x in bad_markers)


# ============================================================
# TELEGRAM BOT API (отправка / редактирование / ответы на callback)
# ============================================================

async def _bot_request(method: str, payload: dict, timeout: int = 30):
    """Низкоуровневый вызов Bot API. Возвращает dict ответа или None."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        connector = make_aiohttp_connector()
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(url, json=payload, timeout=timeout) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    log.error(f"{method} -> {resp.status}: {data.get('description')}")
                return data
    except Exception as ex:
        log.error(f"{method} ошибка запроса: {ex}")
        return None


async def send_notification(text: str, buttons: list = None, thread_id: int = None):
    """Отправка сообщения через Bot API. Возвращает message_id или None.
    Если NOTIFY_THREAD_ID > 0 — сообщение уйдёт в нужный топик группы.
    При ошибке кастомных эмодзи повторяет отправку без них."""
    payload = {
        "chat_id": NOTIFY_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    t_id = topic_id(thread_id)
    if t_id:
        payload["message_thread_id"] = t_id
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}

    data = await _bot_request("sendMessage", payload)

    # Фоллбэк: если Telegram отклонил custom emoji / HTML entity / DOCUMENT_INVALID — шлём без <tg-emoji>
    if should_retry_without_custom_emoji(data):
        payload["text"] = strip_custom_emoji(text)
        data = await _bot_request("sendMessage", payload)

    if data and data.get("ok"):
        log.info("Уведомление отправлено")
        return data["result"]["message_id"]
    return None


async def edit_message(message_id: int, text: str, buttons: list = None):
    payload = {
        "chat_id": NOTIFY_CHAT_ID,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if buttons is not None:
        payload["reply_markup"] = {"inline_keyboard": buttons}

    data = await _bot_request("editMessageText", payload)
    if should_retry_without_custom_emoji(data):
        payload["text"] = strip_custom_emoji(text)
        await _bot_request("editMessageText", payload)




async def delete_message(message_id: int, chat_id: int = None) -> bool:
    """Удаляет сообщение. По умолчанию — из группы (лог о занятом лоте).
    С явным chat_id — из любого чата (используется, чтобы подчистить номер
    телефона/код/пароль из истории ЛС во время входа через /session)."""
    if not message_id:
        return False

    data = await _bot_request("deleteMessage", {
        "chat_id": chat_id or NOTIFY_CHAT_ID,
        "message_id": message_id,
    })
    return bool(data and data.get("ok"))

async def answer_callback(callback_id: str, text: str = "", alert: bool = False):
    await _bot_request("answerCallbackQuery", {
        "callback_query_id": callback_id,
        "text": text,
        "show_alert": alert,
    })


async def send_dm(user_id: int, text: str, buttons: list = None) -> bool:
    """Личное сообщение пользователю. True — успех, False — бот не может писать первым."""
    payload = {
        "chat_id": user_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}

    data = await _bot_request("sendMessage", payload)

    if should_retry_without_custom_emoji(data):
        payload["text"] = strip_custom_emoji(text)
        data = await _bot_request("sendMessage", payload)

    return bool(data and data.get("ok"))


# ============================================================
# ФОРМАТ УВЕДОМЛЕНИЯ (дизайн как на скринах)
# ============================================================

def build_buttons(token: str, gift_link: str, seller_url: str) -> list:
    # Важно: inline-кнопки Telegram НЕ поддерживают заливку цветом (ни через
    # Bot API, ни через HTML/<tg-emoji> — там текст кнопки строго plain-text).
    # Единственный рабочий способ обозначить цвет — цветной кружок-эмодзи
    # в начале подписи кнопки: 🟢 занять / 🔵 открыть лот / 🔴 продавец.
    return [
        [{"text": "🟢 Занять", "callback_data": f"claim:{token}"}],
        [
            {"text": "🔵 Открыть лот", "url": gift_link},
            {"text": "🔴 Продавец", "url": seller_url},
        ],
    ]


def format_notification(listing: dict) -> tuple:
    """Возвращает (текст, кнопки) для нового листинга."""
    title = listing.get("title") or listing.get("gift_name") or "Unknown"
    slug = listing.get("slug") or ""
    gift_num = listing.get("gift_num", 0)
    stars = listing.get("price", 0)
    ton = listing.get("price_ton", 0)
    model = listing.get("model", "")
    level = listing.get("seller_level", 0)
    premium = listing.get("seller_premium", False)
    is_paid_dm = listing.get("seller_paid_dm", False)
    paid_stars = listing.get("seller_paid_stars", 0)
    seller_username = listing.get("seller_username", "")
    seller_id = listing.get("seller_id", 0)
    token = listing.get("listing_key") or listing.get("unique_id", "")

    if not slug:
        slug = f"{title.replace(' ', '')}-{gift_num}"
    gift_link = f"https://t.me/nft/{slug}"

    if seller_username:
        seller_disp = f"@{seller_username} ({seller_id})"
        seller_url = f"https://t.me/{seller_username}"
    else:
        seller_disp = f"{listing.get('seller_name') or 'скрыт'} ({seller_id})"
        seller_url = f"tg://user?id={seller_id}"

    msg_status = "бесплатно" if not is_paid_dm else f"платно ({paid_stars} ⭐)"
    prem_status = "Premium" if premium else "Обычный"
    date_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    text = f"{e['confetti']} <b>НОВЫЙ ЛИСТИНГ</b>\n\n"
    text += f"{e['gift']} <b>Гифт:</b> {title}\n"
    text += f"{e['money']} <b>Цена:</b> {stars} {e['star']} / {ton} TON\n"
    model_emoji = tg_emoji(listing.get("model_emoji_id", ""), "🎨")
    if model:
        text += f"{model_emoji} <b>Модель:</b> {model}\n"
    if listing.get("model_rarity"):
        text += f"{e['diamond']} <b>Редкость модели:</b> {listing['model_rarity']}%\n"
    text += f"{e['person']} <b>Продавец:</b> {seller_disp}\n"
    text += f"{e['chart']} <b>Level:</b> {level}\n"
    text += f"{e['megaphone']} <b>Сообщения:</b> {msg_status}\n"
    text += f"{e['star']} <b>Статус:</b> {prem_status}\n"
    text += f"{e['arrow']} <a href=\"{gift_link}\">{slug}</a>\n"
    text += f"{e['clock']} {date_str}\n\n"
    text += f"<i>Creator By {CREATOR}</i>"

    buttons = build_buttons(token, gift_link, seller_url)
    return text, buttons


def format_claimed(listing: dict, claimer_display: str) -> tuple:
    """Текст и кнопки для занятого лота."""
    base_text, _ = format_notification(listing)
    text = base_text + f"\n\n{e['lock']} <b>Данный лог занят</b>\n"
    text += f"{e['person']} Взято: {claimer_display}"
    token = listing.get("listing_key") or listing.get("unique_id", "")
    buttons = [[{"text": "🔴 Отменить", "callback_data": f"cancel:{token}"}]]
    return text, buttons


def format_claim_dm(listing: dict) -> str:
    """Сообщение в ЛС тому, кто занял лот."""
    title = listing.get("title") or listing.get("gift_name") or "Unknown"
    slug = listing.get("slug") or ""
    gift_num = listing.get("gift_num", 0)
    stars = listing.get("price", 0)
    ton = listing.get("price_ton", 0)
    seller_username = listing.get("seller_username", "")
    seller_id = listing.get("seller_id", 0)
    if not slug:
        slug = f"{title.replace(' ', '')}-{gift_num}"
    gift_link = f"https://t.me/nft/{slug}"
    seller_disp = f"@{seller_username}" if seller_username else f"ID {seller_id}"

    text = f"{e['check']} <b>Вы заняли лот!</b>\n\n"
    text += f"{e['gift']} <b>Гифт:</b> {title}\n"
    text += f"{e['money']} <b>Цена:</b> {stars} {e['star']} / {ton} TON\n"
    text += f"{e['person']} <b>Продавец:</b> {seller_disp} ({seller_id})\n"
    text += f"{e['arrow']} <a href=\"{gift_link}\">{slug}</a>\n\n"
    return text


def build_claim_dm_buttons(listing: dict) -> list:
    """Кнопки в ЛС после занятия лота: открыть NFT и написать продавцу."""
    title = listing.get("title") or listing.get("gift_name") or "Unknown"
    slug = listing.get("slug") or ""
    gift_num = listing.get("gift_num", 0)
    seller_username = listing.get("seller_username", "")
    seller_id = listing.get("seller_id", 0)

    if not slug:
        slug = f"{title.replace(' ', '')}-{gift_num}"

    gift_link = f"https://t.me/nft/{slug}"
    if seller_username:
        seller_url = f"https://t.me/{seller_username}"
    elif seller_id:
        seller_url = f"tg://user?id={seller_id}"
    else:
        seller_url = gift_link

    return [[
        {"text": "🔵 Открыть NFT", "url": gift_link},
        {"text": "🔴 Написать продавцу", "url": seller_url},
    ]]


# ============================================================
# ОБРАБОТКА НАЖАТИЙ КНОПОК (Bot API long polling)
# ============================================================

def _claimer_display(from_user: dict) -> str:
    username = from_user.get("username")
    if username:
        return f"@{username}"
    name = from_user.get("first_name", "")
    if from_user.get("last_name"):
        name += f" {from_user['last_name']}"
    return name or f"ID {from_user.get('id')}"


async def handle_callback(cq: dict):
    data = cq.get("data", "")
    callback_id = cq["id"]
    from_user = cq.get("from", {})
    message = cq.get("message", {})
    message_id = message.get("message_id")

    chat_id = message.get("chat", {}).get("id")
    if chat_id != ALLOWED_CHAT_ID:
        # Бот работает только в целевой группе — любые нажатия из других чатов игнорируются.
        await answer_callback(callback_id, "Бот работает только в закреплённой группе.", alert=True)
        return

    if ":" not in data:
        await answer_callback(callback_id)
        return

    action, token = data.split(":", 1)
    entry = ACTIVE_LISTINGS.get(token) or load_active_listing(token)
    if entry:
        ACTIVE_LISTINGS[token] = entry

    if not entry:
        await answer_callback(callback_id, "Лот не найден или устарел.", alert=True)
        return

    listing = entry["listing"]

    if action == "claim":
        if entry.get("claimed_by"):
            who = entry.get("claimed_by_display", "кто-то")
            await answer_callback(callback_id, f"Лот уже занят: {who}", alert=True)
            return

        claimer_id = from_user.get("id")
        claimer_display = _claimer_display(from_user)
        entry["claimed_by"] = claimer_id
        entry["claimed_by_display"] = claimer_display
        update_active_claim(token, claimer_id, claimer_display)

        # Пишем в ЛС занявшему
        dm_ok = await send_dm(claimer_id, format_claim_dm(listing), build_claim_dm_buttons(listing))

        # Удаляем лог из группы/топика после занятия.
        # Если у бота нет прав на удаление — fallback: редактируем сообщение как занятое.
        deleted = await delete_message(message_id)
        if not deleted:
            log.warning("Не удалось удалить сообщение с логом, пробую отредактировать как занятое")
            text, buttons = format_claimed(listing, claimer_display)
            await edit_message(message_id, text, buttons)

        if dm_ok:
            await answer_callback(callback_id, "Вы заняли лот ✅ Детали — в личке у бота.")
        else:
            hint = f"@{BOT_USERNAME}" if BOT_USERNAME else "бота"
            await answer_callback(
                callback_id,
                f"Лот занят за вами ✅\nЧтобы получать детали в ЛС — откройте {hint} и нажмите Start.",
                alert=True,
            )

    elif action == "cancel":
        # Отменить может только тот, кто занял
        if entry.get("claimed_by") and from_user.get("id") != entry["claimed_by"]:
            await answer_callback(callback_id, "Отменить может только тот, кто занял лот.", alert=True)
            return

        entry["claimed_by"] = None
        entry["claimed_by_display"] = None
        update_active_claim(token, None, None)
        text, buttons = format_notification(listing)
        await edit_message(message_id, text, buttons)
        await answer_callback(callback_id, "Лот снова свободен.")
    else:
        await answer_callback(callback_id)





# ============================================================
# ПАРСЕР АТРИБУТОВ ГИФТА
# ============================================================

def extract_gift_attributes(gift) -> dict:
    """Извлекает атрибуты коллекционного гифта (модель, бэкдроп, паттерн) + emoji ID"""
    attrs = {
        "model": "",
        "model_rarity": "",
        "model_emoji_id": "",
        "backdrop": "",
        "backdrop_rarity": "",
        "pattern": "",
        "pattern_rarity": "",
        "pattern_emoji_id": "",
    }

    if not hasattr(gift, 'attributes'):
        return attrs

    for attr in gift.attributes:
        attr_type = type(attr).__name__

        if 'Model' in attr_type and 'Id' not in attr_type:
            if hasattr(attr, 'name'):
                attrs["model"] = attr.name
            if hasattr(attr, 'document') and attr.document:
                attrs["model_emoji_id"] = str(attr.document.id)
            if hasattr(attr, 'rarity') and attr.rarity:
                rarity_obj = attr.rarity
                if hasattr(rarity_obj, 'permille'):
                    attrs["model_rarity"] = str(round(rarity_obj.permille / 10, 1))

        if 'Backdrop' in attr_type and 'Id' not in attr_type:
            if hasattr(attr, 'name'):
                attrs["backdrop"] = attr.name
            if hasattr(attr, 'rarity') and attr.rarity:
                rarity_obj = attr.rarity
                if hasattr(rarity_obj, 'permille'):
                    attrs["backdrop_rarity"] = str(round(rarity_obj.permille / 10, 1))

        if 'Pattern' in attr_type and 'Id' not in attr_type:
            if hasattr(attr, 'name'):
                attrs["pattern"] = attr.name
            if hasattr(attr, 'document') and attr.document:
                attrs["pattern_emoji_id"] = str(attr.document.id)
            if hasattr(attr, 'rarity') and attr.rarity:
                rarity_obj = attr.rarity
                if hasattr(rarity_obj, 'permille'):
                    attrs["pattern_rarity"] = str(round(rarity_obj.permille / 10, 1))

        if 'Original' in attr_type:
            if hasattr(attr, 'name') and not attrs["model"]:
                attrs["model"] = attr.name

    return attrs


def extract_resell_price(gift) -> tuple:
    """Возвращает (цена_в_звёздах, цена_в_TON). TON берётся из маркета,
    иначе считается по курсу STARS_PER_TON."""
    stars = 0
    ton = 0.0
    if hasattr(gift, 'resell_amount') and gift.resell_amount:
        for sa in gift.resell_amount:
            sa_type = type(sa).__name__
            amount = getattr(sa, 'amount', 0) or 0
            nanos = getattr(sa, 'nanos', 0) or 0
            if 'Ton' in sa_type:
                # amount в нанотонах (1 TON = 1e9)
                ton = round(amount / 1_000_000_000, 2)
            else:
                stars = int(amount + nanos / 1_000_000_000)

    if stars == 0:
        if getattr(gift, 'resale_stars', 0):
            stars = gift.resale_stars
        elif getattr(gift, 'convert_stars', 0):
            stars = gift.convert_stars

    if ton == 0 and stars and STARS_PER_TON > 0:
        ton = round(stars / STARS_PER_TON, 2)

    return stars, ton


# ============================================================
# ОСНОВНОЙ ПАРСЕР
# ============================================================

class GiftMarketParser:

    def __init__(self):
        self.client: TelegramClient = None
        self.running = False
        self.base_gift_ids = {}
        self.paid_dm_cache = {}
        self.login_states = {}
        self.stats = {
            "checks": 0,
            "new_listings": 0,
            "notifications_sent": 0,
            "skipped_paid_dm": 0,
            "errors": 0,
            "started_at": None
        }


    async def start(self):
        global BOT_USERNAME

        log.info("=" * 50)
        log.info("  GIFT MARKET PARSER v3.0 - /session LOGIN")
        log.info("=" * 50)

        validate_config()
        init_db()

        me_data = await _bot_request("getMe", {})
        if me_data and me_data.get("ok"):
            BOT_USERNAME = me_data["result"].get("username", "")
            log.info(f"Бот: @{BOT_USERNAME}")

        telethon_proxy = get_telethon_proxy()

        self.client = TelegramClient(
            SESSION_NAME,
            API_ID,
            API_HASH,
            proxy=telethon_proxy
        )

        await self.client.connect()

        if await self.client.is_user_authorized():
            me = await self.client.get_me()
            log.info(f"Сессия уже авторизована: {me.first_name} (ID: {me.id})")
        else:
            log.warning(
                "Сессия не авторизована. Используй /session в ЛС боту."
            )

        await asyncio.gather(
            self.session_watcher(),
            self.bot_update_loop(),
        )


    async def session_watcher(self):
        ...
        """Ждёт, пока сессия не будет авторизована через /session, затем ОДИН РАЗ
        запускает мониторинг маркета и шлёт единственное уведомление о включении."""
        while True:
            try:
                authorized = await self.client.is_user_authorized()
            except Exception as ex:
                log.error(f"Ошибка проверки авторизации: {ex}")
                authorized = False

            if authorized:
                await self._launch_monitoring()
                return
            await asyncio.sleep(3)

    async def _launch_monitoring(self):
        """Запускается ровно один раз, сразу после успешной авторизации сессии."""
        me = await self.client.get_me()
        log.info(f"Авторизован как: {me.first_name} (ID: {me.id})")

        await self.load_base_gifts()

        gift_filter_text = ", ".join(TRACK_GIFTS) if TRACK_GIFTS else "ВСЕ"
        dm_info = f"\n{e['no_entry']} Фильтр: только бесплатные ЛС" if ONLY_FREE_DM else ""
        topic_info = f"{e['megaphone']} Топик логов: {NOTIFY_THREAD_ID}\n" if topic_id() else ""

        # Единственное уведомление о включении — шлётся один раз за жизнь процесса.
        await send_notification(
            f"{e['confetti']} <b>Parser запущен!</b>\n\n"
            f"{e['person']} Сессия: {me.first_name} (ID: {me.id})\n"
            f"{e['gift']} Отслеживание: {gift_filter_text}\n"
            f"{e['clock']} Интервал: {CHECK_INTERVAL} сек"
            f"{dm_info}\n"
            f"{topic_info}"
            f"{e['chart']} Базовых гифтов: {len(self.base_gift_ids)}\n\n"
            f"<i>Creator By {CREATOR}</i>"
        )

        self.running = True
        self.stats["started_at"] = datetime.now()
        await self.monitor_loop()

    async def bot_update_loop(self):
        """Long polling getUpdates — принимает нажатия inline-кнопок И личные
        сообщения (для команды /session). Групповые текстовые сообщения (кроме
        нажатий кнопок) бот не обрабатывает — см. handle_message."""
        log.info("Запущен обработчик кнопок и команд (getUpdates)")
        offset = 0
        while True:
            try:
                data = await _bot_request("getUpdates", {
                    "offset": offset,
                    "timeout": 25,
                    "allowed_updates": ["callback_query", "message"],
                }, timeout=35)

                if not data or not data.get("ok"):
                    await asyncio.sleep(3)
                    continue

                for upd in data["result"]:
                    offset = upd["update_id"] + 1
                    if "callback_query" in upd:
                        try:
                            await handle_callback(upd["callback_query"])
                        except Exception as ex:
                            log.error(f"Ошибка обработки callback: {ex}")
                    elif "message" in upd:
                        try:
                            await self.handle_message(upd["message"])
                        except Exception as ex:
                            log.error(f"Ошибка обработки сообщения: {ex}")
            except Exception as ex:
                log.error(f"Ошибка getUpdates: {ex}")
                await asyncio.sleep(3)

    async def handle_message(self, msg: dict):
        """Обрабатывает ЛИЧНЫЕ сообщения боту: команды /session, /cancel и шаги
        пошагового входа (телефон -> код -> пароль). Всё остальное игнорируется —
        бот не реагирует на текст в группе, только на нажатия кнопок."""
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        chat_type = chat.get("type")
        text = (msg.get("text") or "").strip()
        from_user = msg.get("from", {})
        user_id = from_user.get("id")
        message_id = msg.get("message_id")

        if chat_type != "private" or not user_id or not text:
            return  # групповые сообщения и служебные апдейты не обрабатываем

        if user_id not in ADMIN_IDS:
            if text.startswith("/"):
                await send_dm(
                    user_id,
                    f"{e['no_entry']} <b>Доступ ограничен.</b>\n"
                    f"Управление парсером доступно только администратору."
                )
            return

        if text == "/cancel":
            had_state = self.login_states.pop(user_id, None) is not None
            await send_dm(user_id, f"{e['cross']} " + ("Ввод отменён." if had_state else "Нечего отменять."))
            return

        if text == "/session":
            if await self.client.is_user_authorized():
                me = await self.client.get_me()
                await send_dm(
                    user_id,
                    f"{e['check']} <b>Сессия уже активна</b>\n\n"
                    f"{e['person']} Авторизовано как: {me.first_name} (ID: {me.id})\n\n"
                    f"Чтобы сменить аккаунт — останови процесс, удали файл "
                    f"<code>{SESSION_NAME}.session</code> и запусти заново."
                )
                return
            self.login_states[user_id] = {"step": "phone"}
            await send_dm(
                user_id,
                f"{e['robot']} <b>Подключение сессии парсера</b>\n\n"
                f"{e['bell']} Рекомендуется отдельный (запасной) аккаунт — не основной!\n\n"
                f"{e['person']} Введи номер телефона в международном формате:\n"
                f"<code>+79991234567</code>\n\n"
                f"{e['search']} Отменить в любой момент — /cancel"
            )
            return

        state = self.login_states.get(user_id)
        if not state:
            if text.startswith("/"):
                await send_dm(
                    user_id,
                    f"{e['search']} <b>Команды:</b>\n"
                    f"/session — подключить сессию для мониторинга\n"
                    f"/cancel — отменить текущий ввод"
                )
            return

        # Сообщение содержит номер/код/пароль — подчищаем его из истории ЛС.
        try:
            await delete_message(message_id, chat_id=user_id)
        except Exception:
            pass

        step = state["step"]

        if step == "phone":
            phone = text.replace(" ", "")
            if not phone.startswith("+") or not phone[1:].replace(" ", "").isdigit():
                await send_dm(user_id, f"{e['cross']} Формат неверный. Пример: <code>+79991234567</code>")
                return
            try:
                result = await self.client.send_code_request(phone)
                state["phone"] = phone
                state["phone_code_hash"] = result.phone_code_hash
                state["step"] = "code"
                await send_dm(
                    user_id,
                    f"{e['megaphone']} Код отправлен в Telegram на {phone}.\n"
                    f"Введи код цифрами (например: 12345):"
                )
            except errors.FloodWaitError as ex:
                await send_dm(user_id, f"{e['no_entry']} Telegram просит подождать {ex.seconds} сек. перед повтором.")
                self.login_states.pop(user_id, None)
            except errors.PhoneNumberInvalidError:
                await send_dm(user_id, f"{e['cross']} Номер невалиден. Попробуй ещё раз или /cancel")
            except Exception as ex:
                log.error(f"send_code_request ошибка: {ex}")
                await send_dm(user_id, f"{e['cross']} Ошибка: {ex}\nНачни заново — /session")
                self.login_states.pop(user_id, None)
            return

        if step == "code":
            code = re.sub(r"\D", "", text)
            try:
                await self.client.sign_in(phone=state["phone"], code=code, phone_code_hash=state["phone_code_hash"])
                await self._finish_login(user_id)
            except errors.SessionPasswordNeededError:
                state["step"] = "password"
                await send_dm(
                    user_id,
                    f"{e['lock']} На аккаунте включена двухфакторная защита (облачный пароль).\n"
                    f"Введи пароль:"
                )
            except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError):
                await send_dm(user_id, f"{e['cross']} Код неверный или устарел. Начни заново — /session")
                self.login_states.pop(user_id, None)
            except Exception as ex:
                log.error(f"sign_in ошибка: {ex}")
                await send_dm(user_id, f"{e['cross']} Ошибка: {ex}")
                self.login_states.pop(user_id, None)
            return

        if step == "password":
            try:
                await self.client.sign_in(password=text)
                await self._finish_login(user_id)
            except errors.PasswordHashInvalidError:
                await send_dm(user_id, f"{e['cross']} Пароль неверный. Попробуй ещё раз или /cancel")
            except Exception as ex:
                log.error(f"sign_in(password) ошибка: {ex}")
                await send_dm(user_id, f"{e['cross']} Ошибка: {ex}")
                self.login_states.pop(user_id, None)
            return

    async def _finish_login(self, user_id: int):
        """Успешный вход: чистим состояние, шлём подтверждение.
        Сам мониторинг подхватит session_watcher — он поллит is_user_authorized()."""
        self.login_states.pop(user_id, None)
        me = await self.client.get_me()
        await send_dm(
            user_id,
            f"{e['check']} <b>Сессия подключена!</b>\n\n"
            f"{e['person']} Авторизован как: {me.first_name} (ID: {me.id})\n"
            f"{e['top']} Мониторинг маркета запускается автоматически — "
            f"жди уведомление в теме Tracker."
        )
        log.info(f"Сессия авторизована через /session как {me.first_name} (ID: {me.id})")

    async def stop(self):
        self.running = False
        if self.client:
            await self.client.disconnect()

        uptime = ""
        if self.stats["started_at"]:
            delta = datetime.now() - self.stats["started_at"]
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime = f"{hours}ч {minutes}м {seconds}с"

        await send_notification(
            f"{e['cross']} <b>Parser остановлен</b>\n\n"
            f"{e['clock']} Аптайм: {uptime}\n"
            f"{e['search']} Проверок: {self.stats['checks']}\n"
            f"{e['bell']} Новых: {self.stats['new_listings']}\n"
            f"{e['megaphone']} Уведомлений: {self.stats['notifications_sent']}\n"
            f"{e['no_entry']} Пропущено (платный ЛС): {self.stats['skipped_paid_dm']}\n"
            f"{e['cross']} Ошибок: {self.stats['errors']}\n\n"
            f"<i>Creator By {CREATOR}</i>"
        )
        log.info("Парсер остановлен")

    async def load_base_gifts(self):
        log.info("Загрузка базовых гифтов...")
        try:
            result = await self.client(functions.payments.GetStarGiftsRequest(hash=0))

            if hasattr(result, 'gifts'):
                for gift in result.gifts:
                    gift_id = gift.id
                    star_count = getattr(gift, 'stars', 0) or getattr(gift, 'star_count', 0)
                    total = getattr(gift, 'availability_total', 0) or getattr(gift, 'total_count', 0)
                    remaining = getattr(gift, 'availability_remains', 0) or getattr(gift, 'remaining_count', 0)

                    title = ""
                    if hasattr(gift, 'title') and gift.title:
                        title = gift.title
                    elif hasattr(gift, 'slug') and gift.slug:
                        title = gift.slug

                    if not title and hasattr(gift, 'sticker') and gift.sticker:
                        for attr in gift.sticker.attributes:
                            if hasattr(attr, 'alt'):
                                title = attr.alt
                                break

                    self.base_gift_ids[gift_id] = title or f"Gift_{gift_id}"
                    save_base_gift(gift_id, star_count, total, remaining, title)

                log.info(f"Загружено {len(self.base_gift_ids)} базовых гифтов")
            else:
                self._load_from_cache()

        except Exception as ex:
            log.error(f"Ошибка загрузки базовых гифтов: {ex}")
            self._load_from_cache()

    def _load_from_cache(self):
        cached = get_base_gifts()
        for row in cached:
            self.base_gift_ids[row[0]] = row[4] or f"Gift_{row[0]}"
        log.info(f"Загружено {len(self.base_gift_ids)} гифтов из кэша")

    async def get_seller_info(self, seller_id: int) -> dict:
        """Платный ЛС, уровень, кол-во гифтов. Кэш на PAID_DM_CACHE_TTL сек."""
        now = time.time()

        if seller_id in self.paid_dm_cache:
            info, cached_at = self.paid_dm_cache[seller_id]
            if now - cached_at < PAID_DM_CACHE_TTL:
                return info

        info = {"is_paid_dm": False, "paid_stars": 0, "level": 0, "gifts_count": 0}

        try:
            full = await self.client(GetFullUserRequest(id=seller_id))
            full_user = full.full_user

            stars = getattr(full_user, 'send_paid_messages_stars', None)
            info["is_paid_dm"] = stars is not None and stars > 0
            info["paid_stars"] = stars or 0

            gifts_count = getattr(full_user, 'stargifts_count', None)
            if gifts_count:
                info["gifts_count"] = gifts_count

            rating = getattr(full_user, 'stars_rating', None)
            if rating:
                level = getattr(rating, 'level', 0)
                if level:
                    info["level"] = level

            self.paid_dm_cache[seller_id] = (info, now)
        except Exception as ex:
            log.warning(f"Не удалось проверить пользователя {seller_id}: {ex}")
            self.paid_dm_cache[seller_id] = (info, now)

        return info

    async def check_resale_gifts(self, gift_id: int, gift_title: str):
        new_listings = []

        try:
            result = await self.client(functions.payments.GetResaleStarGiftsRequest(
                gift_id=gift_id,
                offset='',
                limit=50,
                sort_by_price=True
            ))

            if not hasattr(result, 'gifts'):
                return new_listings

            users_map = {}
            if hasattr(result, 'users'):
                for user in result.users:
                    users_map[user.id] = user

            for gift in result.gifts:
                unique_id = str(getattr(gift, 'id', 0))
                gift_num = 0
                seller_id = 0
                slug = ""

                price, price_ton = extract_resell_price(gift)

                if hasattr(gift, 'num'):
                    gift_num = gift.num

                if hasattr(gift, 'owner_id'):
                    owner = gift.owner_id
                    if hasattr(owner, 'user_id'):
                        seller_id = owner.user_id
                    elif isinstance(owner, int):
                        seller_id = owner

                seller_name = ""
                seller_username = ""
                seller_premium = False
                if seller_id and seller_id in users_map:
                    user = users_map[seller_id]
                    seller_name = user.first_name or ""
                    if hasattr(user, 'last_name') and user.last_name:
                        seller_name += f" {user.last_name}"
                    seller_username = user.username or ""
                    seller_premium = bool(getattr(user, 'premium', False))

                if hasattr(gift, 'slug') and gift.slug:
                    slug = gift.slug

                # Имя гифта для фильтра исключений (по slug)
                filter_name = slug or gift_title
                if EXCLUDE_GIFTS:
                    if any(x.lower() in filter_name.lower() for x in EXCLUDE_GIFTS):
                        continue

                attrs = extract_gift_attributes(gift)

                if MIN_PRICE_STARS > 0 and price < MIN_PRICE_STARS:
                    continue
                if MAX_PRICE_STARS > 0 and price > MAX_PRICE_STARS:
                    continue

                if MAX_RARITY_PERCENT > 0 and attrs["model_rarity"]:
                    try:
                        rarity = float(attrs["model_rarity"])
                        if rarity > MAX_RARITY_PERCENT:
                            continue
                    except ValueError:
                        pass

                # Информация о продавце (нужна и для фильтров, и для дизайна)
                seller_info = {"is_paid_dm": False, "paid_stars": 0, "level": 0, "gifts_count": 0}
                if seller_id:
                    seller_info = await self.get_seller_info(seller_id)

                    if ONLY_FREE_DM and seller_info["is_paid_dm"]:
                        self.stats["skipped_paid_dm"] += 1
                        log.debug(f"Пропущен {filter_name} #{gift_num} — платный ЛС")
                        continue

                    if MAX_SELLER_LEVEL > 0 and seller_info["level"] > MAX_SELLER_LEVEL:
                        self.stats.setdefault("skipped_high_level", 0)
                        self.stats["skipped_high_level"] += 1
                        continue

                    if MAX_SELLER_GIFTS > 0 and seller_info["gifts_count"] > MAX_SELLER_GIFTS:
                        self.stats.setdefault("skipped_many_gifts", 0)
                        self.stats["skipped_many_gifts"] += 1
                        continue

                listing_key = f"{gift_id}_{unique_id}_{gift_num}"
                if not is_listing_seen(listing_key):
                    save_listing(listing_key, gift_title, gift_num, price, seller_id)
                    new_listings.append({
                        "title": gift_title,
                        "gift_name": gift_title,
                        "slug": slug,
                        "gift_num": gift_num,
                        "price": price,
                        "price_ton": price_ton,
                        "seller_id": seller_id,
                        "seller_name": seller_name,
                        "seller_username": seller_username,
                        "seller_premium": seller_premium,
                        "seller_level": seller_info["level"],
                        "seller_paid_dm": seller_info["is_paid_dm"],
                        "seller_paid_stars": seller_info["paid_stars"],
                        "unique_id": unique_id,
                        "listing_key": listing_key,
                        "model": attrs["model"],
                        "model_rarity": attrs["model_rarity"],
                        "model_emoji_id": attrs["model_emoji_id"],
                    })

        except Exception as ex:
            err_str = str(ex)
            if "STARGIFT_INVALID" not in err_str:
                log.error(f"Ошибка проверки {gift_title} (ID:{gift_id}): {ex}")
                self.stats["errors"] += 1

        return new_listings

    async def send_listing(self, listing: dict):
        """Форматирует и отправляет лот в тему Tracker текстом (без файлов/фото),
        сохраняя его как активный (для кнопки «Занять»)."""
        text, buttons = format_notification(listing)
        token = listing.get("listing_key") or listing["unique_id"]

        message_id = await send_notification(text, buttons)

        if message_id:
            ACTIVE_LISTINGS[token] = {
                "listing": listing,
                "message_id": message_id,
                "claimed_by": None,
                "claimed_by_display": None,
            }
            save_active_listing(token, listing, message_id)
            self.stats["notifications_sent"] += 1

    async def monitor_loop(self):
        log.info(f"Мониторинг запущен (интервал: {CHECK_INTERVAL}с)")

        cleanup_counter = 0

        while self.running:
            try:
                self.stats["checks"] += 1
                total_new = 0

                for gift_id, gift_title in list(self.base_gift_ids.items()):
                    if EXCLUDE_GIFTS:
                        if any(x.lower() in gift_title.lower() for x in EXCLUDE_GIFTS):
                            continue

                    if TRACK_GIFTS:
                        if not any(t.lower() in gift_title.lower() for t in TRACK_GIFTS):
                            continue

                    new_listings = await self.check_resale_gifts(gift_id, gift_title)

                    for listing in new_listings:
                        total_new += 1
                        self.stats["new_listings"] += 1
                        await self.send_listing(listing)
                        # Не спамим пачкой — держим паузу между постами в группу.
                        await asyncio.sleep(random.uniform(POST_DELAY_MIN, POST_DELAY_MAX))

                    await asyncio.sleep(0.5)

                if total_new > 0:
                    log.info(f"Найдено {total_new} новых листингов")

                cleanup_counter += 1
                if cleanup_counter >= 100:
                    cleanup_old_listings()
                    cleanup_counter = 0

                if self.stats["checks"] % 50 == 0:
                    await self.load_base_gifts()

                check_num = self.stats["checks"]
                if check_num % 10 == 0:
                    log.info(
                        f"Проверка #{check_num} | "
                        f"Новых: {self.stats['new_listings']} | "
                        f"Ошибок: {self.stats['errors']}"
                    )

            except Exception as ex:
                log.error(f"Ошибка в цикле мониторинга: {ex}")
                self.stats["errors"] += 1
                await asyncio.sleep(5)

            await asyncio.sleep(CHECK_INTERVAL)


# ============================================================
# ЗАПУСК
# ============================================================

async def main():
    parser = GiftMarketParser()
    await asyncio.gather(
        parser.start(),
        run_http_server(parser),
    )


if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════╗
║      TELEGRAM GIFT MARKET PARSER v3.0 /session    ║
║                                                  ║
║  Вход в сессию — командой /session в ЛС боту     ║
║  Фильтр: только бесплатные ЛС                   ║
║  «Занять» + ЛС + удаление лога из группы        ║
╚══════════════════════════════════════════════════╝
    """)

    asyncio.run(main())
