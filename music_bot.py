import os
import re
import uuid
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path

import telebot
import yt_dlp
from telebot import apihelper
from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)

# ============================================================
# SOZLAMALAR
# ============================================================
TOKEN = "9085817383:AAFun6Svuf3nSHd0OnSUjzQilOg3Hnclnjs"
ADMIN_ID = 8077522227

if not TOKEN:
    raise RuntimeError(
        "BOT_TOKEN topilmadi. Tokenni kiriting."
    )

bot = telebot.TeleBot(TOKEN)
BOT_USER = bot.get_me().username

PAGE = 10
REF_STARS = 5
DAILY_REF_LIMIT = 10
GIFT_NEED = 2000
GIFT_MONTHS = 3
GIFT_REAL = 1000

DB = "bot.db"
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("music_bot")

# Har bir chat uchun qidiruv natijalari va Instagram havolasi.
results = {}
links = {}

# ============================================================
# BAZA
# ============================================================
def q(sql, args=(), fetch=False):
    conn = sqlite3.connect(DB, timeout=20)
    try:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.fetchall() if fetch else cur.rowcount
    finally:
        conn.close()


q(
    """CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        stars INTEGER NOT NULL DEFAULT 0,
        invited_by INTEGER,
        phone TEXT UNIQUE,
        active INTEGER NOT NULL DEFAULT 1,
        joined_at TEXT,
        searches INTEGER NOT NULL DEFAULT 0,
        rewarded INTEGER NOT NULL DEFAULT 0,
        premium_until TEXT,
        dl_count INTEGER NOT NULL DEFAULT 0,
        dl_date TEXT
    )"""
)
q(
    """CREATE TABLE IF NOT EXISTS referrals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invited_user_id INTEGER UNIQUE,
        inviter_id INTEGER,
        created_at TEXT
    )"""
)
q(
    """CREATE TABLE IF NOT EXISTS songs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT UNIQUE,
        nomi TEXT,
        ijrochi TEXT,
        file_id TEXT
    )"""
)


def register(uid, ref=None):
    if ref == uid:
        ref = None
    if ref and not q("SELECT 1 FROM users WHERE user_id=?", (ref,), True):
        ref = None

    q(
        """INSERT OR IGNORE INTO users(user_id, invited_by, joined_at)
           VALUES(?,?,?)""",
        (uid, ref, datetime.now().isoformat()),
    )
    q("UPDATE users SET active=1 WHERE user_id=?", (uid,))


def user_row(uid):
    rows = q("SELECT * FROM users WHERE user_id=?", (uid,), True)
    return rows[0] if rows else None


def can_download(uid):
    # Kunlik yuklash cheklovi butunlay olib tashlandi
    return True


def count_download(uid):
    pass


# ============================================================
# ASOSIY MENYU
# ============================================================
def main_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        KeyboardButton("🎵 Qo'shiq qidirish"),
        KeyboardButton("📹 Instagram yuklash"),
    )
    kb.add(
        KeyboardButton("⭐ Mening balansim"),
        KeyboardButton("👥 Do'st taklif qilish"),
    )
    kb.add(
        KeyboardButton("🎁 Premium sovg'asi"),
    )
    return kb


WELCOME_TEXT = (
    "🎵 MUSIQA BOTIGA XUSH KELIBSIZ!\n\n"
    "✨ Qo'shiqlarni qidiring va cheksiz yuklab oling.\n"
    "📹 Instagram videosini yoki to'liq musiqasini yuklang.\n"
    "⭐ Do'stlaringizni taklif qilib yulduz yig'ing va Premium sovg'a oling.\n\n"
    "👇 Kerakli bo'limni tanlang yoki qo'shiq nomini yozing."
)


def show_balance(chat_id, uid):
    row = q("SELECT stars FROM users WHERE user_id=?", (uid,), True)
    stars = row[0][0] if row else 0
    invited = q(
        "SELECT COUNT(*) FROM referrals WHERE inviter_id=?", (uid,), True
    )[0][0]
    invite_link = f"https://t.me/{BOT_USER}?start=ref_{uid}"

    bot.send_message(
        chat_id,
        f"⭐ Yulduzlaringiz: {stars}\n"
        f"👥 Mukofot berilgan takliflar: {invited}\n\n"
        f"🔗 Shaxsiy havolangiz:\n{invite_link}\n\n"
        f"🎁 Har bir tasdiqlangan do'st uchun +{REF_STARS} ⭐\n"
        f"Premium sovg'a olish uchun sizga {GIFT_NEED} ⭐ yulduz kerak bo'ladi.",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


# ============================================================
# REFERAL TIZIMI
# Telefon raqami + kamida 2 ta qidiruv talab qilinadi.
# Kunlik limitga yetilsa, mukofot keyingi kun qayta tekshirilishi mumkin.
# ============================================================
def try_reward(uid):
    row = q(
        """SELECT invited_by, phone, rewarded, searches
           FROM users WHERE user_id=?""",
        (uid,),
        True,
    )
    if not row:
        return

    inviter, phone, rewarded, searches = row[0]
    if not inviter or rewarded or searches < 2:
        return

    if not phone:
        kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.add(KeyboardButton("📱 Raqamni tasdiqlash", request_contact=True))
        bot.send_message(
            uid,
            "🎁 Referal mukofotini olish uchun telefon raqamingizni tasdiqlang.",
            reply_markup=kb,
        )
        return

    today = datetime.now().strftime("%Y-%m-%d")
    rewarded_today = q(
        """SELECT COUNT(*) FROM referrals
           WHERE inviter_id=? AND substr(created_at,1,10)=?""",
        (inviter, today),
        True,
    )[0][0]

    if rewarded_today >= DAILY_REF_LIMIT:
        return

    try:
        q(
            """INSERT INTO referrals(invited_user_id, inviter_id, created_at)
               VALUES(?,?,?)""",
            (uid, inviter, datetime.now().isoformat()),
        )
    except sqlite3.IntegrityError:
        # Ushbu foydalanuvchi uchun mukofot avval berilgan.
        q("UPDATE users SET rewarded=1 WHERE user_id=?", (uid,))
        return

    q("UPDATE users SET rewarded=1 WHERE user_id=?", (uid,))
    q(
        "UPDATE users SET stars=stars+? WHERE user_id=?",
        (REF_STARS, inviter),
    )
    try:
        bot.send_message(
            inviter,
            f"🎉 Do'stingiz talablarni bajardi! Sizga +{REF_STARS} ⭐ berildi.",
        )
    except Exception:
        logger.exception("Referal egasiga xabar yuborilmadi")


@bot.message_handler(content_types=["contact"])
def on_contact(message):
    uid = message.from_user.id
    register(uid)

    contact = message.contact
    if not contact.user_id or contact.user_id != uid:
        bot.send_message(
            message.chat.id,
            "❌ Iltimos, faqat o'zingizning Telegram raqamingizni yuboring.",
            reply_markup=main_menu(),
        )
        return

    phone = re.sub(r"\s+", "", contact.phone_number or "")
    if not phone.startswith("+"):
        phone = "+" + phone

    if not phone.startswith("+998"):
        bot.send_message(
            message.chat.id,
            "❌ Faqat O'zbekiston (+998) raqamlari qabul qilinadi.",
            reply_markup=main_menu(),
        )
        return

    try:
        q("UPDATE users SET phone=? WHERE user_id=?", (phone, uid))
    except sqlite3.IntegrityError:
        bot.send_message(
            message.chat.id,
            "❌ Bu raqam boshqa akkauntda ishlatilgan.",
            reply_markup=main_menu(),
        )
        return

    bot.send_message(
        message.chat.id,
        "✅ Telefon raqami tasdiqlandi.",
        reply_markup=main_menu(),
    )
    try_reward(uid)


# ============================================================
# TELEGRAM PREMIUM SOVG'ASI
# Bu Bot API metodlari botingiz uchun mavjud bo'lishi kerak.
# ============================================================
def real_balance():
    try:
        response = apihelper._make_request(TOKEN, "getMyStarBalance", method="get")
        if isinstance(response, dict):
            result = response.get("result", response)
            if isinstance(result, dict):
                amount = result.get("amount", 0)
                return int(amount or 0)
        return 0
    except Exception:
        logger.exception("Telegram Stars balansini olishda xato")
        return 0


def gift(uid):
    row = q("SELECT stars FROM users WHERE user_id=?", (uid,), True)
    if not row or row[0][0] < GIFT_NEED:
        current = row[0][0] if row else 0
        return f"🎁 Sovg'a uchun {GIFT_NEED} ⭐ kerak. Sizda {current} ⭐ bor. Ko'proq do'st taklif qiling!"

    if real_balance() < GIFT_REAL:
        if ADMIN_ID:
            try:
                bot.send_message(
                    ADMIN_ID,
                    f"⚠️ {uid} Premium sovg'a so'radi, lekin bot balansida "
                    f"{GIFT_REAL} Telegram Stars yetarli emas.",
                )
            except Exception:
                logger.exception("Adminga sovg'a haqida xabar yuborilmadi")
        return "⏳ Sovg'a hozircha mavjud emas. Yulduzlaringiz saqlab qolindi."

    changed = q(
        "UPDATE users SET stars=stars-? WHERE user_id=? AND stars>=?",
        (GIFT_NEED, uid, GIFT_NEED),
    )
    if changed == 0:
        return "❌ Yulduzlaringiz yetarli emas."

    try:
        apihelper._make_request(
            TOKEN,
            "giftPremiumSubscription",
            method="post",
            params={
                "user_id": uid,
                "month_count": GIFT_MONTHS,
                "star_count": GIFT_REAL,
            },
        )
    except Exception:
        q("UPDATE users SET stars=stars+? WHERE user_id=?", (GIFT_NEED, uid))
        logger.exception("Telegram Premium sovg'asini yuborishda xato")
        return (
            "❌ Sovg'a yuborilmadi. Yulduzlaringiz hisobingizga qaytarildi. "
            "Keyinroq qayta urinib ko'ring."
        )
    return f"🎉 Telegram Premium {GIFT_MONTHS} oyga sovg'a qilindi!"


# ============================================================
# QIDIRUV VA YUKLASH
# ============================================================
def search_songs(query):
    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(f"ytsearch30:{query}", download=False)
    entries = (info or {}).get("entries") or []
    return [entry for entry in entries if entry and entry.get("url")]


def duration_text(seconds):
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        seconds = 0
    return f" {seconds // 60}:{seconds % 60:02d}"


def show_results(chat_id, page=0, message_id=None):
    entries = results.get(chat_id, [])
    if not entries:
        bot.send_message(chat_id, "😕 Natija topilmadi. Boshqa nom bilan urinib ko'ring.")
        return

    start = page * PAGE
    part = entries[start:start + PAGE]
    if not part:
        bot.send_message(chat_id, "Bu sahifada natija yo'q.")
        return

    lines = ["🎵 Qidiruv natijalari\n"]
    for index, entry in enumerate(part, start=1):
        title = (entry.get("title") or "Noma'lum qo'shiq").replace("<", "<").replace(">", ">")
        lines.append(
            f"{index}. {title[:75]}  {duration_text(entry.get('duration'))}"
        )
    text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup(row_width=5)
    buttons = [
        InlineKeyboardButton(
            str(index),
            callback_data=f"s{start + index - 1}",
        )
        for index in range(1, len(part) + 1)
    ]
    keyboard.add(*buttons)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Oldingi", callback_data=f"p{page - 1}"))
    if (page + 1) * PAGE < len(entries):
        nav.append(InlineKeyboardButton("Keyingi ➡️", callback_data=f"p{page + 1}"))
    if nav:
        keyboard.row(*nav)

    if message_id:
        try:
            bot.edit_message_text(
                text,
                chat_id,
                message_id,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except Exception:
            bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
    else:
        bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")


def locate_downloaded_file(info, ydl):
    candidates = []

    requested = info.get("requested_downloads") or []
    for item in requested:
        filepath = item.get("filepath")
        if filepath:
            candidates.append(Path(filepath))

    try:
        candidates.append(Path(ydl.prepare_filename(info)))
    except Exception:
        pass

    for path in candidates:
        if path.is_file():
            return path

    title = info.get("id")
    if title:
        matches = list(DOWNLOAD_DIR.glob(f"*{title}*"))
        for path in matches:
            if path.is_file():
                return path

    raise FileNotFoundError("Yuklangan fayl topilmadi.")


def download_media(chat_id, url, kind):
    if kind == "a" and "instagram.com" in url.lower():
        options = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
            "noplaylist": True,
        }
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
                track = info.get("track")
                artist = info.get("artist")
                title = info.get("title") or ""
                query = f"{artist or ''} {track or ''}".strip() or title
                query = re.sub(r"#\w+|@\w+|instagram|reel|video|post", "", query, flags=re.IGNORECASE).strip()
                if query:
                    found = search_songs(query)
                    if found:
                        return download_media(chat_id, found[0]["url"], "a")
        except Exception:
            logger.exception("Instagram audio uchun to'liq musiqani qidirishda xato")

    if kind == "a":
        cached = q("SELECT file_id FROM songs WHERE url=?", (url,), True)
        if cached and cached[0][0]:
            bot.send_audio(chat_id, cached[0][0])
            return {"cached": True}

    unique = uuid.uuid4().hex
    options = {
        "format": "best[ext=mp4]/best" if kind == "v" else "bestaudio[ext=m4a]/bestaudio",
        "outtmpl": str(DOWNLOAD_DIR / f"{unique}.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 2,
    }

    path = None
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            path = locate_downloaded_file(info, ydl)

        with path.open("rb") as media_file:
            if kind == "v":
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("🎵 Qo'shiqni yuklash", callback_data="dl_insta_audio"))
                bot.send_video(chat_id, media_file, supports_streaming=True, reply_markup=kb)
            else:
                sent = bot.send_audio(
                    chat_id,
                    media_file,
                    title=(info.get("track") or info.get("title") or "Audio")[:64],
                    performer=(info.get("artist") or info.get("uploader") or "")[:64] or None,
                )
                if sent.audio:
                    q(
                        """INSERT OR IGNORE INTO songs(url, nomi, ijrochi, file_id)
                           VALUES(?,?,?,?)""",
                        (
                            url,
                            info.get("title"),
                            info.get("uploader"),
                            sent.audio.file_id,
                        ),
                    )
        return info
    finally:
        if path and path.exists():
            try:
                path.unlink()
            except OSError:
                logger.exception("Vaqtinchalik faylni o'chirib bo'lmadi: %s", path)


def ask_phone(chat_id):
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(KeyboardButton("📱 Raqamni tasdiqlash", request_contact=True))
    bot.send_message(
        chat_id,
        "📱 Referal mukofoti uchun telefon raqamingizni tasdiqlang.",
        reply_markup=kb,
    )


def process_search(message, query_text):
    uid = message.from_user.id
    query_text = query_text.strip()
    if not query_text:
        bot.send_message(message.chat.id, "Qo'shiq nomini yozing.")
        return

    status = bot.send_message(message.chat.id, "🔎 Qidirilmoqda, biroz kuting...")
    try:
        found = search_songs(query_text)
        results[message.chat.id] = found
        q("UPDATE users SET searches=searches+1 WHERE user_id=?", (uid,))
        try:
            bot.delete_message(message.chat.id, status.message_id)
        except Exception:
            pass

        if not found:
            bot.send_message(message.chat.id, "😕 Hech narsa topilmadi. Boshqa nom yozib ko'ring.")
            return

        show_results(message.chat.id, 0)
        try_reward(uid)
    except Exception:
        logger.exception("Qo'shiq qidirishda xato")
        try:
            bot.edit_message_text(
                "❌ Qidirib bo'lmadi. Internet yoki qidiruv xizmatini tekshirib, qayta urinib ko'ring.",
                message.chat.id,
                status.message_id,
            )
        except Exception:
            bot.send_message(message.chat.id, "❌ Qidirib bo'lmadi. Keyinroq urinib ko'ring.")


# ============================================================
# BUYRUQLAR
# ============================================================
@bot.message_handler(commands=["start"])
def start(message):
    ref = None
    parts = (message.text or "").split()
    if len(parts) > 1 and parts[1].startswith("ref_"):
        value = parts[1][4:]
        if value.isdigit():
            ref = int(value)

    register(message.from_user.id, ref)
    bot.send_message(
        message.chat.id,
        WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


@bot.message_handler(commands=["balance"])
def balance_command(message):
    register(message.from_user.id)
    show_balance(message.chat.id, message.from_user.id)


@bot.message_handler(commands=["help"])
def help_command(message):
    bot.send_message(
        message.chat.id,
        "ℹ️ Botdan foydalanish\n\n"
        "• Qo'shiq nomini yozing — qidiruv boshlanadi.\n• Instagram havolasini yuboring — video yoki audio tanlang.\n"
        "• Do'st taklif qilib 5 ⭐ yig'ing va Premium sovg'a oling.\n"
        "• Referal mukofoti uchun raqamni tasdiqlash va kamida 2 ta qidiruv kerak.\n",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


# ============================================================
# MATNLI XABARLAR VA MENYU
# ============================================================
@bot.message_handler(
    func=lambda message: bool(message.text) and not message.text.startswith("/")
)
def on_text(message):
    uid = message.from_user.id
    register(uid)
    text = message.text.strip()
    lower_text = text.lower()

    if text == "🎵 Qo'shiq qidirish":
        bot.send_message(
            message.chat.id,
            "🎵 Qo'shiq nomini yoki ijrochi nomini yozing:",
            reply_markup=main_menu(),
        )
        return

    if text == "📹 Instagram yuklash":
        bot.send_message(
            message.chat.id,
            "📹 Instagram video havolasini yuboring.",
            reply_markup=main_menu(),
        )
        return

    if text == "⭐ Mening balansim":
        show_balance(message.chat.id, uid)
        return

    if text == "👥 Do'st taklif qilish":
        show_balance(message.chat.id, uid)
        return

    if text == "🎁 Premium sovg'asi":
        bot.send_message(message.chat.id, gift(uid), reply_markup=main_menu())
        return

    if re.search(r"https?://(?:www\.)?instagram\.com/", text, re.IGNORECASE):
        links[message.chat.id] = text
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("🎬 Video", callback_data="v"),
            InlineKeyboardButton("🎵 To'liq audio qidirish", callback_data="a"),
        )
        bot.send_message(
            message.chat.id,
            "📥 Nimani yuklamoqchisiz?",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    if text.startswith("http://") or text.startswith("https://"):
        bot.send_message(
            message.chat.id,
            "⚠️ Hozircha Instagram havolasini yoki qo'shiq nomini yuboring.",
            reply_markup=main_menu(),
        )
        return

    process_search(message, text)


# ============================================================
# INLINE TUGMALAR
# ============================================================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    if not call.message:
        bot.answer_callback_query(call.id)
        return

    chat_id = call.message.chat.id
    uid = call.from_user.id
    data = call.data or ""
    register(uid)

    if data.startswith("p") and data[1:].isdigit():
        bot.answer_callback_query(call.id)
        show_results(chat_id, int(data[1:]), call.message.message_id)
        return

    if data == "dl_insta_audio":
        url = links.get(chat_id)
        if not url:
            bot.answer_callback_query(call.id, "Instagram havolasi topilmadi.")
            return
        bot.answer_callback_query(call.id, "To'liq audio qidirilmoqda...")
        try:
            download_media(chat_id, url, "a")
        except Exception:
            bot.send_message(chat_id, "❌ To'liq musiqani topib bo'lmadi.")
        return

    if data not in ("a", "v") and not (data.startswith("s") and data[1:].isdigit()):
        bot.answer_callback_query(call.id, "Noma'lum tugma.")
        return

    bot.answer_callback_query(call.id, "⏳ Yuklanmoqda...")

    try:
        if data.startswith("s"):
            entries = results.get(chat_id, [])
            index = int(data[1:])
            if index < 0 or index >= len(entries):
                bot.send_message(chat_id, "Qidiruv natijasi eskirgan. Qayta qidiring.")
                return
            url = entries[index].get("url")
            if not url:
                bot.send_message(chat_id, "Bu natijadan yuklab bo'lmadi.")
                return
            download_media(chat_id, url, "a")
            return

        if data in ("a", "v"):
            url = links.get(chat_id)
            if not url:
                bot.send_message(chat_id, "Instagram havolasi topilmadi. Qayta yuboring.")
                return

            download_media(chat_id, url, data)
            return

    except Exception as exc:
        logger.exception("Yuklashda xato")
        bot.send_message(
            chat_id,
            "❌ Yuklash amalga oshmadi. Havola yoki formatni tekshirib, "
            "qayta urinib ko'ring.",
            reply_markup=main_menu(),
        )


if __name__ == "__main__":
    logger.info("Bot ishga tushmoqda: @%s", BOT_USER)
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)

     

#!python music_bot.py
     
2026-10-28 17:09:41,917 | INFO | Bot ishga tushmoqda: @xonlar_bot
ERROR: [youtube] Du9_AnolyPA: This video is not available
2026-10-28 17:17:33,341 | ERROR | Instagram audio uchun to'liq musiqani qidirishda xato
Traceback (most recent call last):
  File "/usr/local/lib/python3.13/dist-packages/yt_dlp/YoutubeDL.py", line 1728, in wrapper
    return func(self, *args, **kwargs)
  File "/usr/local/lib/python3.13/dist-packages/yt_dlp/YoutubeDL.py", line 1863, in __extract_info
    ie_result = ie.extract(url)
  File "/usr/local/lib/python3.13/dist-packages/yt_dlp/extractor/common.py", line 764, in extract
    ie_result = self._real_extract(url)
  File "/usr/local/lib/python3.13/dist-packages/yt_dlp/extractor/youtube/_video.py", line 4083, in _real_extract
    self.raise_no_formats(reason, expected=True)
    ~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.13/dist-packages/yt_dlp/extractor/common.py", line 1271, in raise_no_formats
    raise ExtractorError(msg, expected=expected, video_id=video_id)
yt_dlp.utils.ExtractorError: [youtube] Du9_AnolyPA: This video is not available

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/content/music_bot.py", line 479, in download_media
    return download_media(chat_id, found[0]["url"], "a")
  File "/content/music_bot.py", line 503, in download_media
    info = ydl.extract_info(url, download=True)
  File "/usr/local/lib/python3.13/dist-packages/yt_dlp/YoutubeDL.py", line 1717, in extract_info
    return self.__extract_info(url, self.get_info_extractor(key), download, extra_info, process)
           ~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.13/dist-packages/yt_dlp/YoutubeDL.py", line 1746, in wrapper
    self.report_error(str(e), e.format_traceback())
    ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.13/dist-packages/yt_dlp/YoutubeDL.py", line 1162, in report_error
    self.trouble(f'{self._format_err("ERROR:", self.Styles.ERROR)} {message}', *args, **kwargs)
    ~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.13/dist-packages/yt_dlp/YoutubeDL.py", line 1100, in trouble
    raise DownloadError(message, exc_info if exc_info and exc_info[0] else sys.exc_info())
yt_dlp.utils.DownloadError: ERROR: [youtube] Du9_AnolyPA: This video is not available
