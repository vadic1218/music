# ============================================
# РњРЈР—Р«РљРђР›Р¬РќР«Р™ Р‘РћРў Р”Р›РЇ TELEGRAM
# РџРѕР»РЅР°СЏ РёРЅС‚РµРіСЂР°С†РёСЏ: YouTube + РЇРЅРґРµРєСЃ.РњСѓР·С‹РєР°
# РЎРёСЃС‚РµРјР° РїРѕРґРїРёСЃРѕРє Рё РїСЂРѕРјРѕРєРѕРґРѕРІ
# ============================================

# РРјРїРѕСЂС‚ Р±РёР±Р»РёРѕС‚РµРє
import database
from pathlib import Path
from config import (
    BOT_TOKEN,
    ADMIN_IDS,
    MAX_FILE_SIZE_MB,
    FFMPEG_THREADS,
    YANDEX_MUSIC_TOKEN,
    ENABLE_VK,
    OPENAI_API_KEY,
    OPENAI_TRANSCRIBE_MODEL,
    VK_LOGIN,
    VK_PASSWORD,
    VK_ACCESS_TOKEN,
    CACHE_DIR,
    DATA_DIR,
    SEARCH_RESULTS_PER_SOURCE,
    MINI_APP_URL,
)
import telebot
import os
import sys
import yt_dlp
import re
import time
import threading
import concurrent.futures
import requests
import json
import shutil
from dotenv import load_dotenv
from urllib.parse import urlparse, parse_qs, quote, unquote, urlencode, urlunparse
from yandex_music import Client
from yandex_music.exceptions import UnauthorizedError, NetworkError, NotFoundError
import subprocess
import math
from telebot import types
import traceback
from datetime import datetime, timedelta, timezone
import vk_api
from vk_api.audio import VkAudio
from vk_api.exceptions import AuthError
from bs4 import BeautifulSoup

# --- РќРђРЎРўР РћР™РљРђ Р‘РћРўРђ ---
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set. Put it into the project .env file before starting the bot.")

bot = telebot.TeleBot(BOT_TOKEN)

ADMIN_CONTACT_ID = ADMIN_IDS[0] if ADMIN_IDS else None

# Проверка базы данных
print("\n🔍 Проверка базы данных...")
try:
    # Пробуем добавить тестового пользователя
    test_result = database.add_user(999999, "test", "Test", "User", "ru", False)
    print(f"✅ База данных доступна: {test_result}")

    # Проверяем промокод
    promo_check = database.check_promo_code("WELCOME")
    print(f"✅ Промокод WELCOME: {promo_check}")

    # Проверяем созданные промокоды
    all_promos = database.get_all_promo_codes()
    print(f"✅ Всего промокодов в базе: {len(all_promos)}")
    for promo in all_promos[:3]:
        print(f"   - {promo['code']}: {promo['subscription_type']} (использовано: {promo['uses_count']}/{promo['max_uses']})")

except Exception as e:
    print(f"❌ Ошибка базы данных: {e}")
    traceback.print_exc()

# Инициализация клиента Яндекс.Музыки
YM_TOKEN = YANDEX_MUSIC_TOKEN
ym_client = None
if YM_TOKEN:
    try:
        ym_client = Client(YM_TOKEN).init()
        print("✅ Клиент Яндекс.Музыки успешно инициализирован.")
    except UnauthorizedError:
        print("❌ Ошибка авторизации Яндекс.Музыки: неверный токен.")
    except NetworkError:
        print("⚠️ Ошибка сети при подключении к Яндекс.Музыке.")
    except Exception as e:
        print(f"⚠️ Неизвестная ошибка инициализации Яндекс.Музыки: {e}")

# --- РћР‘Р©РР• РџР•Р Р•РњР•РќРќР«Р• ---
vk_session = None
vk_audio = None
vk_audio_lock = threading.Lock()
if ENABLE_VK and VK_LOGIN and VK_PASSWORD:
    try:
        vk_session = vk_api.VkApi(login=VK_LOGIN, password=VK_PASSWORD, token=VK_ACCESS_TOKEN or None)
        vk_session.auth(token_only=False)
        vk_audio = VkAudio(vk_session)
        print("VK Music client initialized.")
    except AuthError as e:
        print(f"[VK] Authorization error: {e}")
    except Exception as e:
        print(f"[VK] Initialization error: {e}")
elif ENABLE_VK and VK_ACCESS_TOKEN:
    try:
        vk_session = vk_api.VkApi(token=VK_ACCESS_TOKEN)
        vk_audio = VkAudio(vk_session)
        print("VK Music client initialized from access token.")
    except Exception as e:
        print(f"[VK] Token initialization error: {e}")
elif not ENABLE_VK:
    print("VK Music integration disabled.")

user_search_history = {}
user_files_state = {}
pending_transcription_requests = {}
pending_user_actions = {}
pending_lyrics_requests = {}
ym_client_lock = threading.Lock()
yandex_cache_index_lock = threading.RLock()
chat_library_index_lock = threading.RLock()
liked_sync_state_lock = threading.Lock()
active_liked_sync_users = set()

# --- РќРђРЎРўР РћР™РљР РџРђРџРћРљ ---
AUDIO_CACHE_DIR = str(CACHE_DIR)
MUSIC_DIR = os.path.join(AUDIO_CACHE_DIR, "music")
PODCASTS_DIR = os.path.join(AUDIO_CACHE_DIR, "podcasts")
TRANSCRIPTIONS_DIR = Path(DATA_DIR) / "transcriptions"
TRANSCRIPTION_MAX_BYTES = 25 * 1024 * 1024
TRANSCRIPTION_API_URL = "https://api.openai.com/v1/audio/transcriptions"
TRANSCRIPTION_ENABLED = bool(OPENAI_API_KEY)

os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)
os.makedirs(MUSIC_DIR, exist_ok=True)
os.makedirs(PODCASTS_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(TRANSCRIPTIONS_DIR, exist_ok=True)

YANDEX_CACHE_INDEX_PATH = Path(DATA_DIR) / "yandex_cache_index.json"
YANDEX_CHAT_LIBRARY_INDEX_PATH = Path(DATA_DIR) / "yandex_chat_library_index.json"
YANDEX_LIKED_SYNC_STATE_PATH = Path(DATA_DIR) / "yandex_liked_sync_state.json"


# --- Р’РЎРџРћРњРћР“РђРўР•Р›Р¬РќР«Р• Р¤РЈРќРљР¦РР ---

def escape_markdown(text):
    """Escape dynamic text for Telegram Markdown."""
    if text is None:
        return ""
    text = str(text)
    for char in ('\\', '_', '*', '`', '['):
        text = text.replace(char, f'\\{char}')
    return text


def sanitize_filename(text, fallback="unknown", max_length=80):
    if not text:
        return fallback

    sanitized = "".join(c for c in str(text) if c.isalnum() or c in (" ", "-", "_", ".", ",", "(", ")"))
    sanitized = " ".join(sanitized.split()).strip(" ._-")
    if not sanitized:
        sanitized = fallback
    return sanitized[:max_length]


def transcription_is_available():
    return TRANSCRIPTION_ENABLED


def save_telegram_file_locally(file_id, filename_hint):
    file_info = bot.get_file(file_id)
    raw_bytes = bot.download_file(file_info.file_path)
    safe_name = sanitize_filename(filename_hint, fallback="voice_message", max_length=80)
    if "." not in safe_name:
        suffix = Path(file_info.file_path).suffix or ".ogg"
        safe_name = f"{safe_name}{suffix}"

    local_path = TRANSCRIPTIONS_DIR / safe_name
    local_path.write_bytes(raw_bytes)
    return local_path


def transcribe_audio_file(file_path: Path):
    if not transcription_is_available():
        return None, "Р Р°СЃС€РёС„СЂРѕРІРєР° СЂРµС‡Рё РЅРµ РЅР°СЃС‚СЂРѕРµРЅР°."

    file_size = file_path.stat().st_size
    if file_size > TRANSCRIPTION_MAX_BYTES:
        return None, "Р¤Р°Р№Р» СЃР»РёС€РєРѕРј Р±РѕР»СЊС€РѕР№ РґР»СЏ СЂР°СЃС€РёС„СЂРѕРІРєРё. РћС‚РїСЂР°РІСЊС‚Рµ РіРѕР»РѕСЃРѕРІРѕРµ РґРѕ 25 РњР‘."

    try:
        with file_path.open("rb") as audio_file:
            response = requests.post(
                TRANSCRIPTION_API_URL,
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                data={
                    "model": OPENAI_TRANSCRIBE_MODEL,
                    "response_format": "json",
                },
                files={"file": (file_path.name, audio_file, "application/octet-stream")},
                timeout=(20, 300),
            )
        response.raise_for_status()
        payload = response.json()
        text = (payload.get("text") or "").strip()
        if not text:
            return None, "РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕР»СѓС‡РёС‚СЊ С‚РµРєСЃС‚ РёР· Р°СѓРґРёРѕ."
        return text, None
    except Exception as e:
        print(f"[Transcription] Error: {e}")
        return None, f"РћС€РёР±РєР° СЂР°СЃС€РёС„СЂРѕРІРєРё: {e}"


def format_transcription_text(text):
    text = text.strip()
    if len(text) <= 3500:
        return f"рџ“ќ *Р Р°СЃС€РёС„СЂРѕРІРєР° СЂРµС‡Рё:*\n\n{text}"
    short_text = text[:3500].rstrip()
    return f"рџ“ќ *Р Р°СЃС€РёС„СЂРѕРІРєР° СЂРµС‡Рё:*\n\n{short_text}\n\nвЂ¦С‚РµРєСЃС‚ СЃРѕРєСЂР°С‰РµРЅ."


def get_media_duration_seconds(file_path: Path):
    try:
        probe_cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            str(file_path),
        ]
        result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return 0
        return max(0, int(float((result.stdout or "0").strip() or 0)))
    except Exception as e:
        print(f"[Transcription] Duration probe error: {e}")
        return 0


def split_audio_for_transcription(file_path: Path, segment_seconds=480):
    file_size = file_path.stat().st_size
    duration_seconds = get_media_duration_seconds(file_path)

    if file_size <= TRANSCRIPTION_MAX_BYTES and duration_seconds <= segment_seconds:
        return [file_path]

    segment_dir = TRANSCRIPTIONS_DIR / f"{file_path.stem}_parts"
    segment_dir.mkdir(parents=True, exist_ok=True)
    segment_pattern = segment_dir / f"{file_path.stem}_part_%03d{file_path.suffix or '.ogg'}"

    split_cmd = [
        'ffmpeg', '-y',
        '-i', str(file_path),
        '-f', 'segment',
        '-segment_time', str(segment_seconds),
        '-c', 'copy',
        str(segment_pattern),
    ]

    result = subprocess.run(split_cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        print(f"[Transcription] Split error: {result.stderr}")
        return [file_path]

    parts = sorted(segment_dir.glob(f"{file_path.stem}_part_*{file_path.suffix or '.ogg'}"))
    return parts or [file_path]


def transcribe_audio_with_chunking(file_path: Path):
    chunk_paths = split_audio_for_transcription(file_path)
    texts = []
    try:
        for chunk_path in chunk_paths:
            text, error = transcribe_audio_file(chunk_path)
            if error:
                return None, error
            if text:
                texts.append(text.strip())
        if not texts:
            return None, "РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕР»СѓС‡РёС‚СЊ С‚РµРєСЃС‚ РёР· Р°СѓРґРёРѕ."
        return "\n\n".join(texts), None
    finally:
        for chunk_path in chunk_paths:
            if chunk_path != file_path and chunk_path.exists():
                try:
                    chunk_path.unlink()
                except OSError:
                    pass
        for chunk_path in chunk_paths:
            parent_dir = chunk_path.parent
            if parent_dir != TRANSCRIPTIONS_DIR and parent_dir.exists():
                try:
                    parent_dir.rmdir()
                except OSError:
                    pass


def register_transcription_request(message, media_type, file_id, filename_hint):
    token = f"{message.chat.id}_{message.message_id}"
    pending_transcription_requests[token] = {
        "chat_id": message.chat.id,
        "user_id": message.from_user.id,
        "file_id": file_id,
        "filename_hint": filename_hint,
        "media_type": media_type,
        "created_at": time.time(),
    }
    return token


def build_transcription_keyboard(token):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("рџ“ќ Р Р°СЃС€РёС„СЂРѕРІР°С‚СЊ", callback_data=f"transcribe_{token}"))
    return markup


def set_pending_action(chat_id, user_id, action_name):
    pending_user_actions[(chat_id, user_id)] = {
        "action": action_name,
        "created_at": time.time(),
    }


def pop_pending_action(chat_id, user_id):
    return pending_user_actions.pop((chat_id, user_id), None)


def get_pending_action(chat_id, user_id):
    return pending_user_actions.get((chat_id, user_id))


def register_lyrics_request(message, query):
    token = f"{message.chat.id}_{message.message_id}_{int(time.time())}"
    pending_lyrics_requests[token] = {
        "chat_id": message.chat.id,
        "user_id": message.from_user.id,
        "query": query.strip(),
        "created_at": time.time(),
    }
    return token


def pop_lyrics_request(token):
    return pending_lyrics_requests.pop(token, None)


def get_lyrics_request(token):
    return pending_lyrics_requests.get(token)


def build_lyrics_source_keyboard(token):
    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.add(
        types.InlineKeyboardButton("рџЊђ РђРІС‚Рѕ", callback_data=f"lyrics_auto_{token}"),
        types.InlineKeyboardButton("рџЋµ РЇРЅРґРµРєСЃ", callback_data=f"lyrics_yandex_{token}"),
        types.InlineKeyboardButton("рџ“љ Genius", callback_data=f"lyrics_genius_{token}"),
    )
    return markup


def normalize_match_text(value):
    value = (value or "").lower().replace("С‘", "Рµ")
    cleaned = []
    for char in value:
        if char.isalnum() or char.isspace():
            cleaned.append(char)
        else:
            cleaned.append(" ")
    return " ".join("".join(cleaned).split())


def score_song_match(query, title, artist=""):
    normalized_query = normalize_match_text(query)
    normalized_title = normalize_match_text(title)
    normalized_artist = normalize_match_text(artist)
    combined = f"{normalized_artist} {normalized_title}".strip()

    if not normalized_query:
        return 0

    score = 0
    if normalized_query == normalized_title:
        score += 120
    if normalized_query == combined:
        score += 180
    if normalized_query in combined:
        score += 80

    query_tokens = [token for token in normalized_query.split() if len(token) > 1]
    title_tokens = set(normalized_title.split())
    artist_tokens = set(normalized_artist.split())
    combined_tokens = title_tokens | artist_tokens

    for token in query_tokens:
        if token in title_tokens:
            score += 18
        elif token in artist_tokens:
            score += 12
        elif token in combined_tokens:
            score += 8
        elif token in combined:
            score += 4

    return score


def count_song_match_tokens(query, title, artist=""):
    query_tokens = [token for token in normalize_match_text(query).split() if len(token) > 1]
    if not query_tokens:
        return 0

    combined = f"{normalize_match_text(artist)} {normalize_match_text(title)}".strip()
    matched = 0
    for token in query_tokens:
        if token in combined:
            matched += 1
    return matched


def make_yandex_track_identity(track_id):
    return str(int(track_id))


def format_lyrics_text(title, artist, lyrics_text, source_name):
    text = (lyrics_text or "").strip()
    if len(text) > 3500:
        text = text[:3500].rstrip() + "\n\nвЂ¦С‚РµРєСЃС‚ СЃРѕРєСЂР°С‰РµРЅ."
    return (
        f"рџ“ќ *РўРµРєСЃС‚ РїРµСЃРЅРё*\n\n"
        f"рџЋµ *{escape_markdown(title)}*\n"
        f"рџ‘¤ *{escape_markdown(artist)}*\n"
        f"рџ“љ РСЃС‚РѕС‡РЅРёРє: *{escape_markdown(source_name)}*\n\n"
        f"{escape_markdown(text)}"
    )


def extract_genius_lyrics(page_html):
    soup = BeautifulSoup(page_html, "html.parser")
    containers = soup.select('[data-lyrics-container="true"]')
    if containers:
        parts = [container.get_text("\n", strip=True) for container in containers]
        return "\n".join(part for part in parts if part).strip()

    legacy_container = soup.select_one("div.lyrics")
    if legacy_container:
        return legacy_container.get_text("\n", strip=True).strip()

    return ""


def build_genius_headers():
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/137.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
        "Referer": "https://genius.com/",
        "Origin": "https://genius.com",
    }


def extract_genius_search_hits(page_html):
    soup = BeautifulSoup(page_html, "html.parser")
    hits = []

    for link in soup.select('a[href*="genius.com/"][href$="-lyrics"]'):
        href = link.get("href")
        if not href:
            continue

        title = link.get_text(" ", strip=True)
        if not title:
            continue

        hits.append((href, title))

    unique_hits = []
    seen_urls = set()
    for href, title in hits:
        if href in seen_urls:
            continue
        seen_urls.add(href)
        unique_hits.append((href, title))
    return unique_hits


def search_genius_urls_via_duckduckgo(query):
    headers = {
        "User-Agent": build_genius_headers()["User-Agent"],
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    }
    response = requests.get(
        "https://html.duckduckgo.com/html/",
        params={"q": f"site:genius.com {query} lyrics"},
        headers=headers,
        timeout=(10, 30),
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    hits = []
    for link in soup.select("a.result__a"):
        href = link.get("href")
        title = link.get_text(" ", strip=True)
        if not href or "genius.com" not in href or not href.endswith("-lyrics"):
            continue
        hits.append((href, title))
    return hits


def get_lyrics_from_genius(query):
    headers = build_genius_headers()
    try:
        try:
            search_response = requests.get(
                "https://genius.com/search",
                params={"q": query},
                headers=headers,
                timeout=(10, 30),
            )
            search_response.raise_for_status()
            hits = extract_genius_search_hits(search_response.text)
        except Exception as search_error:
            print(f"[Lyrics] Genius direct search failed, fallback to DuckDuckGo: {search_error}")
            hits = search_genius_urls_via_duckduckgo(query)

        ranked_hits = sorted(
            hits,
            key=lambda item: score_song_match(query, item[1], item[0].replace("https://genius.com/", "").replace("-lyrics", "").replace("-", " ")),
            reverse=True,
        )

        for song_url, fallback_title in ranked_hits[:10]:
            page_response = requests.get(song_url, headers=headers, timeout=(10, 30))
            page_response.raise_for_status()
            lyrics_text = extract_genius_lyrics(page_response.text)
            if not lyrics_text:
                continue

            title = fallback_title
            artist = "Unknown Artist"
            page_soup = BeautifulSoup(page_response.text, "html.parser")
            title_tag = page_soup.find("meta", property="og:title")
            if title_tag and title_tag.get("content"):
                meta_title = title_tag["content"].strip()
                if " Lyrics" in meta_title:
                    meta_title = meta_title.replace(" Lyrics", "").strip()
                if " by " in meta_title:
                    title_part, artist_part = meta_title.split(" by ", 1)
                    title = title_part.strip() or title
                    artist = artist_part.strip() or artist
                else:
                    title = meta_title or title

            score = score_song_match(query, title, artist)
            matched_tokens = count_song_match_tokens(query, title, artist)
            query_tokens = [token for token in normalize_match_text(query).split() if len(token) > 1]
            min_matches = 1 if len(query_tokens) <= 2 else 2

            if matched_tokens < min_matches or score < 40:
                continue

            return lyrics_text, title, artist, "Genius"

        return None, None, None, "РўРµРєСЃС‚ РЅР° Genius РЅРµ РЅР°Р№РґРµРЅ."
    except Exception as e:
        print(f"[Lyrics] Genius error: {e}")
        return None, None, None, f"Genius error: {e}"


def get_lyrics_from_yandex(query):
    if not ym_client:
        return None, None, None, "РЇРЅРґРµРєСЃ.РњСѓР·С‹РєР° РЅРµ РЅР°СЃС‚СЂРѕРµРЅР°."

    candidates = search_yandex_music(query, limit=20)
    ranked_candidates = sorted(
        candidates,
        key=lambda candidate: score_song_match(query, candidate.get("title"), candidate.get("artists")),
        reverse=True,
    )
    for candidate in ranked_candidates:
        try:
            lyrics_meta = ym_client.tracks_lyrics(candidate["track_id"], format="TEXT")
            if not lyrics_meta:
                continue
            lyrics_text = lyrics_meta.fetch_lyrics().strip()
            if lyrics_text:
                return lyrics_text, candidate["title"], candidate["artists"], "РЇРЅРґРµРєСЃ.РњСѓР·С‹РєР°"
        except NotFoundError:
            continue
        except Exception as e:
            print(f"[Lyrics] Yandex lyrics error for {candidate.get('track_id')}: {e}")
            continue

    return None, None, None, "РўРµРєСЃС‚ РІ РЇРЅРґРµРєСЃ.РњСѓР·С‹РєРµ РЅРµ РЅР°Р№РґРµРЅ."


def get_song_lyrics(query, preferred_source="auto"):
    preferred_source = (preferred_source or "auto").lower()

    if preferred_source == "yandex":
        lyrics_text, title, artist, source_name = get_lyrics_from_yandex(query)
        if lyrics_text:
            return lyrics_text, title, artist, source_name
        return None, None, None, source_name

    if preferred_source == "genius":
        lyrics_text, title, artist, source_name = get_lyrics_from_genius(query)
        if lyrics_text:
            return lyrics_text, title, artist, source_name
        return None, None, None, source_name

    lyrics_text, title, artist, source_name = get_lyrics_from_yandex(query)
    if lyrics_text:
        return lyrics_text, title, artist, source_name

    lyrics_text, title, artist, source_name = get_lyrics_from_genius(query)
    if lyrics_text:
        return lyrics_text, title, artist, source_name

    return None, None, None, "РўРµРєСЃС‚ РїРµСЃРЅРё РЅРµ РЅР°Р№РґРµРЅ РЅРё РІ РЇРЅРґРµРєСЃ.РњСѓР·С‹РєРµ, РЅРё РІ Genius."


def prompt_lyrics_source(message, query):
    clean_query = (query or "").strip()
    if not clean_query:
        bot.reply_to(
            message,
            "рџ“ќ *РўРµРєСЃС‚ РїРµСЃРЅРё*\n\n"
            "РћС‚РїСЂР°РІСЊС‚Рµ РЅР°Р·РІР°РЅРёРµ РїРµСЃРЅРё Рё РёСЃРїРѕР»РЅРёС‚РµР»СЏ.\n\n"
            "*РџСЂРёРјРµСЂС‹:*\n"
            "вЂў РћР№ РґР° Oxxxymiron\n"
            "вЂў РљРёРЅРѕ РіСЂСѓРїРїР° РєСЂРѕРІРё",
            parse_mode='Markdown'
        )
        return

    token = register_lyrics_request(message, clean_query)
    bot.reply_to(
        message,
        "рџ“ќ *РўРµРєСЃС‚ РїРµСЃРЅРё*\n\n"
        f"Р—Р°РїСЂРѕСЃ: *{escape_markdown(clean_query)}*\n\n"
        "Р’С‹Р±РµСЂРёС‚Рµ, РіРґРµ РёСЃРєР°С‚СЊ С‚РµРєСЃС‚:",
        parse_mode='Markdown',
        reply_markup=build_lyrics_source_keyboard(token)
    )


def process_lyrics_lookup(chat_id, message_id, query, preferred_source):
    source_label = {
        "auto": "РђРІС‚Рѕ",
        "yandex": "РЇРЅРґРµРєСЃ",
        "genius": "Genius",
    }.get(preferred_source, "РђРІС‚Рѕ")

    safe_edit_message_text(
        "рџ“ќ *РўРµРєСЃС‚ РїРµСЃРЅРё*\n\n"
        f"Р—Р°РїСЂРѕСЃ: *{escape_markdown(query)}*\n"
        f"РСЃС‚РѕС‡РЅРёРє: *{escape_markdown(source_label)}*\n\n"
        "РС‰Сѓ С‚РµРєСЃС‚...",
        chat_id=chat_id,
        message_id=message_id,
        parse_mode='Markdown'
    )

    lyrics_text, title, artist, source_name = get_song_lyrics(query, preferred_source=preferred_source)
    if not lyrics_text:
        safe_edit_message_text(
            "вќЊ *РўРµРєСЃС‚ РїРµСЃРЅРё РЅРµ РЅР°Р№РґРµРЅ*\n\n"
            f"Р—Р°РїСЂРѕСЃ: *{escape_markdown(query)}*\n"
            f"РСЃС‚РѕС‡РЅРёРє: *{escape_markdown(source_label)}*\n\n"
            f"{escape_markdown(source_name)}",
            chat_id=chat_id,
            message_id=message_id,
            parse_mode='Markdown'
        )
        return

    safe_edit_message_text(
        format_lyrics_text(title, artist, lyrics_text, source_name),
        chat_id=chat_id,
        message_id=message_id,
        parse_mode='Markdown'
    )


def make_yandex_cache_key(track_id, album_id):
    return f"{int(track_id)}:{int(album_id or 0)}"


def load_yandex_cache_index():
    with yandex_cache_index_lock:
        try:
            if not YANDEX_CACHE_INDEX_PATH.exists():
                return {}

            raw_data = json.loads(YANDEX_CACHE_INDEX_PATH.read_text(encoding="utf-8"))
            return raw_data if isinstance(raw_data, dict) else {}
        except Exception as e:
            print(f"[Yandex Cache] Failed to load cache index: {e}")
            return {}


def save_yandex_cache_index(index_data):
    with yandex_cache_index_lock:
        payload = json.dumps(index_data, ensure_ascii=False, indent=2)
        try:
            temp_path = YANDEX_CACHE_INDEX_PATH.with_suffix(".tmp")
            temp_path.write_text(payload, encoding="utf-8")
            temp_path.replace(YANDEX_CACHE_INDEX_PATH)
        except Exception as e:
            try:
                YANDEX_CACHE_INDEX_PATH.write_text(payload, encoding="utf-8")
            except Exception as fallback_error:
                print(f"[Yandex Cache] Failed to save cache index: {e}; fallback failed: {fallback_error}")


def load_yandex_liked_sync_state():
    try:
        if not YANDEX_LIKED_SYNC_STATE_PATH.exists():
            return {"synced_track_ids": [], "last_synced_at": None, "bootstrapped": False}
        raw_data = json.loads(YANDEX_LIKED_SYNC_STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw_data, dict):
            return {"synced_track_ids": [], "last_synced_at": None, "bootstrapped": False}
        raw_data.setdefault("synced_track_ids", [])
        raw_data.setdefault("last_synced_at", None)
        raw_data.setdefault("bootstrapped", False)
        return raw_data
    except Exception as e:
        print(f"[Yandex Likes] Failed to load sync state: {e}")
        return {"synced_track_ids": [], "last_synced_at": None, "bootstrapped": False}


def save_yandex_liked_sync_state(sync_state):
    try:
        payload = json.dumps(sync_state, ensure_ascii=False, indent=2)
        temp_path = YANDEX_LIKED_SYNC_STATE_PATH.with_suffix(".tmp")
        temp_path.write_text(payload, encoding="utf-8")
        temp_path.replace(YANDEX_LIKED_SYNC_STATE_PATH)
    except Exception as e:
        try:
            YANDEX_LIKED_SYNC_STATE_PATH.write_text(
                json.dumps(sync_state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as fallback_error:
            print(f"[Yandex Likes] Failed to save sync state: {e}; fallback failed: {fallback_error}")


def count_existing_cached_audio_files():
    total = 0
    for folder in (MUSIC_DIR, PODCASTS_DIR):
        try:
            total += sum(1 for path in Path(folder).glob("*.mp3") if path.is_file())
        except OSError:
            continue
    return total


def resolve_cached_yandex_track(track_id, album_id):
    cache_key = make_yandex_cache_key(track_id, album_id)
    index_data = load_yandex_cache_index()
    item = index_data.get(cache_key)
    if item:
        file_path = item.get("path")
        if file_path and os.path.exists(file_path):
            return file_path, item

    track_identity = make_yandex_track_identity(track_id)
    stale_keys = []
    for existing_key, existing_item in list(index_data.items()):
        if str(existing_item.get("track_id")) != track_identity:
            continue
        existing_path = existing_item.get("path")
        if existing_path and os.path.exists(existing_path):
            return existing_path, existing_item
        stale_keys.append(existing_key)

    if item and cache_key not in stale_keys:
        stale_keys.append(cache_key)
    if stale_keys:
        for stale_key in stale_keys:
            index_data.pop(stale_key, None)
        save_yandex_cache_index(index_data)

    file_pattern = f"ym_{int(track_id)}_*.mp3"
    for search_dir in (MUSIC_DIR, PODCASTS_DIR):
        try:
            matches = sorted(Path(search_dir).glob(file_pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            matches = []
        for match in matches:
            if not match.exists():
                continue
            synthetic_item = {
                "track_id": int(track_id),
                "album_id": int(album_id or 0),
                "path": str(match),
                "liked_synced": True,
                "chat_sent": True,
            }
            return str(match), synthetic_item

    return None, None


def update_yandex_cache_entry(track_id, album_id, file_path, title, performer, duration_seconds=0, liked_synced=False, chat_sent=None):
    cache_key = make_yandex_cache_key(track_id, album_id)
    index_data = load_yandex_cache_index()
    track_identity = make_yandex_track_identity(track_id)
    previous_item = index_data.get(cache_key, {})
    for existing_key, existing_item in list(index_data.items()):
        if existing_key == cache_key:
            continue
        if str(existing_item.get("track_id")) == track_identity:
            index_data.pop(existing_key, None)
    index_data[cache_key] = {
        "track_id": int(track_id),
        "album_id": int(album_id or 0),
        "path": file_path,
        "title": title,
        "performer": performer,
        "duration_seconds": duration_seconds or 0,
        "liked_synced": bool(liked_synced),
        "chat_sent": previous_item.get("chat_sent") if chat_sent is None else bool(chat_sent),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    save_yandex_cache_index(index_data)


def remove_yandex_cache_entry(track_id, album_id, delete_file=False):
    cache_key = make_yandex_cache_key(track_id, album_id)
    index_data = load_yandex_cache_index()
    item = index_data.pop(cache_key, None)
    save_yandex_cache_index(index_data)

    if delete_file and item:
        file_path = item.get("path")
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                print(f"[Yandex Cache] Failed to delete cached file {file_path}: {e}")


def load_chat_library_index():
    with chat_library_index_lock:
        try:
            if not YANDEX_CHAT_LIBRARY_INDEX_PATH.exists():
                return {}

            raw_data = json.loads(YANDEX_CHAT_LIBRARY_INDEX_PATH.read_text(encoding="utf-8"))
            return raw_data if isinstance(raw_data, dict) else {}
        except Exception as e:
            print(f"[Chat Library] Failed to load chat library index: {e}")
            return {}


def save_chat_library_index(index_data):
    with chat_library_index_lock:
        payload = json.dumps(index_data, ensure_ascii=False, indent=2)
        try:
            temp_path = YANDEX_CHAT_LIBRARY_INDEX_PATH.with_suffix(".tmp")
            temp_path.write_text(payload, encoding="utf-8")
            temp_path.replace(YANDEX_CHAT_LIBRARY_INDEX_PATH)
        except Exception as e:
            try:
                YANDEX_CHAT_LIBRARY_INDEX_PATH.write_text(payload, encoding="utf-8")
            except Exception as fallback_error:
                print(f"[Chat Library] Failed to save chat library index: {e}; fallback failed: {fallback_error}")


def normalize_chat_library_tracks(chat_tracks):
    normalized = {}
    changed = False

    for raw_key, item in (chat_tracks or {}).items():
        if not isinstance(item, dict):
            changed = True
            continue

        normalized_key = str(raw_key).split(":", 1)[0].strip()
        if not normalized_key:
            changed = True
            continue

        normalized_item = dict(item)
        normalized_item["track_id"] = normalized_key

        existing = normalized.get(normalized_key)
        if existing is None:
            normalized[normalized_key] = normalized_item
        else:
            existing_updated_at = str(existing.get("updated_at") or "")
            candidate_updated_at = str(normalized_item.get("updated_at") or "")
            if candidate_updated_at >= existing_updated_at:
                normalized[normalized_key] = normalized_item
            changed = True

        if normalized_key != str(raw_key):
            changed = True

    return normalized, changed


def get_chat_library_tracks(chat_id):
    index_data = load_chat_library_index()
    chat_key = str(chat_id)
    chat_tracks = index_data.setdefault(chat_key, {})
    normalized_tracks, changed = normalize_chat_library_tracks(chat_tracks)
    if changed:
        index_data[chat_key] = normalized_tracks
        save_chat_library_index(index_data)
    return normalized_tracks


def update_chat_library_track(chat_id, track_key, message_id, title, performer, album_id=0):
    index_data = load_chat_library_index()
    chat_key = str(chat_id)
    chat_tracks = index_data.setdefault(chat_key, {})
    normalized_tracks, changed = normalize_chat_library_tracks(chat_tracks)
    if changed:
        chat_tracks = normalized_tracks
        index_data[chat_key] = chat_tracks

    normalized_key = str(track_key).split(":", 1)[0].strip()
    chat_tracks[normalized_key] = {
        "message_id": int(message_id),
        "track_id": normalized_key,
        "album_id": int(album_id or 0),
        "title": title,
        "performer": performer,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_chat_library_index(index_data)


def remove_chat_library_track(chat_id, track_key):
    index_data = load_chat_library_index()
    chat_key = str(chat_id)
    chat_tracks = index_data.setdefault(chat_key, {})
    normalized_tracks, changed = normalize_chat_library_tracks(chat_tracks)
    if changed:
        chat_tracks = normalized_tracks
        index_data[chat_key] = chat_tracks

    normalized_key = str(track_key).split(":", 1)[0].strip()
    item = chat_tracks.pop(normalized_key, None)
    save_chat_library_index(index_data)
    return item


def send_track_to_chat_library(chat_id, audio_path, title, performer):
    with open(audio_path, 'rb') as audio_file:
        bot.send_chat_action(chat_id, 'upload_audio')
        message = bot.send_audio(
            chat_id=chat_id,
            audio=audio_file,
            title=title[:64] if title else None,
            performer=performer[:64] if performer else None,
            caption=f"рџЋµ {title}",
            timeout=300
        )
    return message.message_id


def get_yandex_liked_tracks():
    if not ym_client:
        return [], "РљР»РёРµРЅС‚ РЇРЅРґРµРєСЃ.РњСѓР·С‹РєРё РЅРµ РЅР°СЃС‚СЂРѕРµРЅ."

    try:
        with ym_client_lock:
            likes = ym_client.users_likes_tracks()

        if not likes:
            return [], None

        tracks = likes.fetch_tracks() or []
        valid_tracks = [track for track in tracks if track]
        return valid_tracks, None
    except Exception as e:
        print(f"[Yandex Likes] Failed to load liked tracks: {e}")
        return [], str(e)


def sync_yandex_liked_tracks(chat_id=None, progress_callback=None):
    tracks, error = get_yandex_liked_tracks()
    if error:
        return {
            "success": False,
            "message": f"РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕР»СѓС‡РёС‚СЊ Р»Р°Р№РєРё РЇРЅРґРµРєСЃ.РњСѓР·С‹РєРё: {error}"
        }

    total_tracks = len(tracks)
    print(f"[Yandex Likes] Sync started: total={total_tracks}, chat_id={chat_id}")
    sync_state = load_yandex_liked_sync_state()
    synced_track_ids = {str(track_id) for track_id in sync_state.get("synced_track_ids", [])}
    current_track_ids = {
        make_yandex_track_identity(track.id)
        for track in tracks
        if track and getattr(track, "id", None) is not None
    }

    if not synced_track_ids and count_existing_cached_audio_files() > 0:
        sync_state["synced_track_ids"] = sorted(current_track_ids)
        sync_state["last_synced_at"] = datetime.now(timezone.utc).isoformat()
        sync_state["bootstrapped"] = True
        save_yandex_liked_sync_state(sync_state)
        print(f"[Yandex Likes] Bootstrapped sync state with {len(current_track_ids)} track ids")
        return {
            "success": True,
            "message": (
                "РЎРѕСЃС‚РѕСЏРЅРёРµ Р»Р°Р№РєРѕРІ РІРѕСЃСЃС‚Р°РЅРѕРІР»РµРЅРѕ РёР· СѓР¶Рµ СЃСѓС‰РµСЃС‚РІСѓСЋС‰РµР№ РјРµРґРёР°С‚РµРєРё.\n"
                "РўРµРєСѓС‰РёРµ С‚СЂРµРєРё РїРѕРјРµС‡РµРЅС‹ РєР°Рє СѓР¶Рµ СЃРёРЅС…СЂРѕРЅРёР·РёСЂРѕРІР°РЅРЅС‹Рµ. "
                "РЎР»РµРґСѓСЋС‰РёРµ Р·Р°РїСѓСЃРєРё Р±СѓРґСѓС‚ РґРѕРєР°С‡РёРІР°С‚СЊ С‚РѕР»СЊРєРѕ РЅРѕРІС‹Рµ РїРµСЃРЅРё."
            ),
            "downloaded": 0,
            "reused": len(current_track_ids),
            "removed": 0,
            "failed": [],
            "tracks": tracks,
            "sent_to_chat": 0,
            "already_in_chat": len(current_track_ids),
            "removed_from_chat": 0,
        }

    if progress_callback:
        progress_callback(
            "start",
            total=total_tracks,
            processed=0,
            downloaded=0,
            reused=0,
            failed=0,
            removed=0,
            sent_to_chat=0,
            already_in_chat=0,
            removed_from_chat=0,
        )

    downloaded = 0
    reused = 0
    failed = []
    sent_to_chat = 0
    already_in_chat = 0
    removed_from_chat = 0
    chat_library_tracks = get_chat_library_tracks(chat_id) if chat_id is not None else {}

    for index, track in enumerate(tracks, start=1):
        try:
            album_id = track.albums[0].id if track.albums else 0
            cache_key = make_yandex_cache_key(track.id, album_id)
            track_identity = make_yandex_track_identity(track.id)
            performer = ", ".join(a.name for a in track.artists) if track.artists else "Unknown Artist"
            duration_seconds = (track.duration_ms or 0) / 1000 if hasattr(track, "duration_ms") else 0

            cached_path, cache_item = resolve_cached_yandex_track(track.id, album_id)
            if cached_path:
                update_yandex_cache_entry(
                    track.id,
                    album_id,
                    cached_path,
                    track.title,
                    performer,
                    duration_seconds,
                    liked_synced=True,
                    chat_sent=(cache_item or {}).get("chat_sent")
                )
                audio_path = cached_path
                title = track.title
                reused += 1
            elif track_identity in synced_track_ids:
                audio_path = None
                title = track.title
                reused += 1
            else:
                audio_path, title, performer, status = download_yandex_track_fast(track.id, album_id, liked_synced=True)
                if status == "success" and audio_path:
                    downloaded += 1
                else:
                    failed.append(f"{track.title}: {status}")
                    audio_path = None

            if audio_path and chat_id is not None:
                existing_chat_item = chat_library_tracks.get(track_identity)
                if existing_chat_item and existing_chat_item.get("message_id"):
                    already_in_chat += 1
                    update_yandex_cache_entry(
                        track.id,
                        album_id,
                        audio_path,
                        title,
                        performer,
                        duration_seconds,
                        liked_synced=True,
                        chat_sent=True,
                    )
                elif cache_item and (cache_item.get("chat_sent") or cache_item.get("liked_synced")):
                    already_in_chat += 1
                    update_yandex_cache_entry(
                        track.id,
                        album_id,
                        audio_path,
                        title,
                        performer,
                        duration_seconds,
                        liked_synced=True,
                        chat_sent=True,
                    )
                else:
                    try:
                        message_id = send_track_to_chat_library(chat_id, audio_path, title, performer)
                        update_chat_library_track(
                            chat_id,
                            track_identity,
                            message_id,
                            title,
                            performer,
                            album_id=album_id,
                        )
                        update_yandex_cache_entry(
                            track.id,
                            album_id,
                            audio_path,
                            title,
                            performer,
                            duration_seconds,
                            liked_synced=True,
                            chat_sent=True,
                        )
                        chat_library_tracks[track_identity] = {
                            "message_id": message_id,
                            "title": title,
                            "performer": performer,
                        }
                        sent_to_chat += 1
                    except Exception as e:
                        failed.append(f"{track.title}: РЅРµ СѓРґР°Р»РѕСЃСЊ СЃРѕС…СЂР°РЅРёС‚СЊ РІ С‡Р°С‚ ({e})")

            synced_track_ids.add(track_identity)
        except Exception as e:
            failed.append(f"{getattr(track, 'title', 'Unknown track')}: {e}")

        if progress_callback and (index == 1 or index % 10 == 0 or index == total_tracks):
            progress_callback(
                "progress",
                total=total_tracks,
                processed=index,
                downloaded=downloaded,
                reused=reused,
                failed=len(failed),
                removed=0,
                sent_to_chat=sent_to_chat,
                already_in_chat=already_in_chat,
                removed_from_chat=0,
            )

    index_data = load_yandex_cache_index()
    removed = 0
    for cache_key, item in list(index_data.items()):
        if not item.get("liked_synced"):
            continue
        if str(item.get("track_id")) in current_track_ids:
            continue
        remove_yandex_cache_entry(item["track_id"], item.get("album_id", 0), delete_file=True)
        removed += 1

    if chat_id is not None:
        for track_key, item in list(get_chat_library_tracks(chat_id).items()):
            if track_key in current_track_ids:
                continue
            removed_item = remove_chat_library_track(chat_id, track_key)
            if removed_item and removed_item.get("message_id"):
                try:
                    bot.delete_message(chat_id, removed_item["message_id"])
                    removed_from_chat += 1
                except Exception as e:
                    print(f"[Chat Library] Failed to delete message {removed_item['message_id']}: {e}")

    print(
        f"[Yandex Likes] Sync completed: total={total_tracks}, "
        f"downloaded={downloaded}, reused={reused}, removed={removed}, failed={len(failed)}, "
        f"sent_to_chat={sent_to_chat}, already_in_chat={already_in_chat}, removed_from_chat={removed_from_chat}"
    )
    sync_state["synced_track_ids"] = sorted(current_track_ids)
    sync_state["last_synced_at"] = datetime.now(timezone.utc).isoformat()
    save_yandex_liked_sync_state(sync_state)
    if progress_callback:
        progress_callback(
            "cleanup",
            total=total_tracks,
            processed=total_tracks,
            downloaded=downloaded,
            reused=reused,
            failed=len(failed),
            removed=removed,
            sent_to_chat=sent_to_chat,
            already_in_chat=already_in_chat,
            removed_from_chat=removed_from_chat,
        )

    summary = (
        f"РЎРёРЅС…СЂРѕРЅРёР·Р°С†РёСЏ Р·Р°РІРµСЂС€РµРЅР°.\n"
        f"Р›Р°Р№РєРѕРІ РЅР°Р№РґРµРЅРѕ: {total_tracks}\n"
        f"РќРѕРІС‹С… СЃРєР°С‡Р°РЅРѕ: {downloaded}\n"
        f"РЈР¶Рµ СЃРѕС…СЂР°РЅРµРЅРѕ Р»РѕРєР°Р»СЊРЅРѕ: {reused}\n"
        f"РЎРѕС…СЂР°РЅРµРЅРѕ РІ С‡Р°С‚: {sent_to_chat}\n"
        f"РЈР¶Рµ Р±С‹Р»Рѕ РІ С‡Р°С‚Рµ: {already_in_chat}\n"
        f"РЈРґР°Р»РµРЅРѕ Р»РѕРєР°Р»СЊРЅРѕ: {removed}\n"
        f"РЈРґР°Р»РµРЅРѕ РёР· С‡Р°С‚Р°: {removed_from_chat}"
    )
    if failed:
        summary += f"\nРћС€РёР±РѕРє: {len(failed)}"

    return {
        "success": True,
        "message": summary,
        "downloaded": downloaded,
        "reused": reused,
        "removed": removed,
        "failed": failed,
        "tracks": tracks,
        "sent_to_chat": sent_to_chat,
        "already_in_chat": already_in_chat,
        "removed_from_chat": removed_from_chat,
    }


def format_liked_sync_result(sync_result):
    tracks = sync_result.get("tracks", [])
    preview_lines = []
    for track in tracks[:10]:
        artist_names = ", ".join(a.name for a in track.artists) if getattr(track, "artists", None) else "Unknown Artist"
        preview_lines.append(f"вЂў {escape_markdown(artist_names)} - {escape_markdown(track.title)}")

    response_text = (
        "вњ… *Р Р°Р·РґРµР» В«РњРЅРµ РїРѕРЅСЂР°РІРёР»РѕСЃСЊВ» СЃРёРЅС…СЂРѕРЅРёР·РёСЂРѕРІР°РЅ*\n\n"
        f"вЂў Р’СЃРµРіРѕ Р»Р°Р№РєРѕРІ: {len(tracks)}\n"
        f"вЂў РќРѕРІС‹С… СЃРєР°С‡Р°РЅРѕ: {sync_result['downloaded']}\n"
        f"вЂў РЈР¶Рµ СЃРѕС…СЂР°РЅРµРЅРѕ Р»РѕРєР°Р»СЊРЅРѕ: {sync_result['reused']}\n"
        f"вЂў РЎРѕС…СЂР°РЅРµРЅРѕ РІ С‡Р°С‚: {sync_result.get('sent_to_chat', 0)}\n"
        f"вЂў РЈР¶Рµ Р±С‹Р»Рѕ РІ С‡Р°С‚Рµ: {sync_result.get('already_in_chat', 0)}\n"
        f"вЂў РЈРґР°Р»РµРЅРѕ Р»РѕРєР°Р»СЊРЅРѕ: {sync_result['removed']}\n"
        f"вЂў РЈРґР°Р»РµРЅРѕ РёР· С‡Р°С‚Р°: {sync_result.get('removed_from_chat', 0)}"
    )

    if preview_lines:
        response_text += "\n\n*РџРµСЂРІС‹Рµ С‚СЂРµРєРё:*\n" + "\n".join(preview_lines)

    if sync_result.get("failed"):
        response_text += f"\n\nвљ пёЏ РћС€РёР±РѕРє СЃРёРЅС…СЂРѕРЅРёР·Р°С†РёРё: {len(sync_result['failed'])}"

    return response_text


def format_liked_sync_progress(total, processed, downloaded, reused, failed, removed=0, sent_to_chat=0, already_in_chat=0, removed_from_chat=0):
    return (
        "рџЋµ *РЎРёРЅС…СЂРѕРЅРёР·РёСЂСѓСЋ С‚СЂРµРєРё РёР· СЂР°Р·РґРµР»Р° В«РњРЅРµ РїРѕРЅСЂР°РІРёР»РѕСЃСЊВ»...*\n\n"
        f"вЂў РћР±СЂР°Р±РѕС‚Р°РЅРѕ: {processed}/{total}\n"
        f"вЂў РќРѕРІС‹С… СЃРєР°С‡Р°РЅРѕ: {downloaded}\n"
        f"вЂў РЈР¶Рµ СЃРѕС…СЂР°РЅРµРЅРѕ Р»РѕРєР°Р»СЊРЅРѕ: {reused}\n"
        f"вЂў РЎРѕС…СЂР°РЅРµРЅРѕ РІ С‡Р°С‚: {sent_to_chat}\n"
        f"вЂў РЈР¶Рµ Р±С‹Р»Рѕ РІ С‡Р°С‚Рµ: {already_in_chat}\n"
        f"вЂў РћС€РёР±РѕРє: {failed}\n"
        f"вЂў РЈРґР°Р»РµРЅРѕ Р»РѕРєР°Р»СЊРЅРѕ: {removed}\n"
        f"вЂў РЈРґР°Р»РµРЅРѕ РёР· С‡Р°С‚Р°: {removed_from_chat}"
    )


def finish_liked_sync(user_id):
    with liked_sync_state_lock:
        active_liked_sync_users.discard(user_id)


def run_liked_sync(chat_id, user_id, wait_message_id, library_chat_id=None):
    try:
        target_library_chat_id = library_chat_id or chat_id

        def progress_callback(stage, total, processed, downloaded, reused, failed, removed, sent_to_chat, already_in_chat, removed_from_chat):
            print(
                f"[Yandex Likes] stage={stage} processed={processed}/{total} "
                f"downloaded={downloaded} reused={reused} failed={failed} removed={removed} "
                f"sent_to_chat={sent_to_chat} already_in_chat={already_in_chat} removed_from_chat={removed_from_chat}"
            )
            safe_edit_message_text(
                format_liked_sync_progress(
                    total,
                    processed,
                    downloaded,
                    reused,
                    failed,
                    removed,
                    sent_to_chat,
                    already_in_chat,
                    removed_from_chat
                ),
                chat_id=chat_id,
                message_id=wait_message_id,
                parse_mode='Markdown'
            )

        sync_result = sync_yandex_liked_tracks(chat_id=target_library_chat_id, progress_callback=progress_callback)
        if not sync_result["success"]:
            safe_edit_message_text(
                f"вќЊ *РќРµ СѓРґР°Р»РѕСЃСЊ СЃРёРЅС…СЂРѕРЅРёР·РёСЂРѕРІР°С‚СЊ Р»Р°Р№РєРё*\n\n{escape_markdown(sync_result['message'])}",
                chat_id=chat_id,
                message_id=wait_message_id,
                parse_mode='Markdown'
            )
            return

        safe_edit_message_text(
            format_liked_sync_result(sync_result),
            chat_id=chat_id,
            message_id=wait_message_id,
            parse_mode='Markdown'
        )
    except Exception as e:
        print(f"[Yandex Likes] Sync failed: {e}")
        traceback.print_exc()
        try:
            safe_edit_message_text(
                f"вќЊ *РћС€РёР±РєР° СЃРёРЅС…СЂРѕРЅРёР·Р°С†РёРё*\n\n{escape_markdown(str(e)[:300])}",
                chat_id=chat_id,
                message_id=wait_message_id,
                parse_mode='Markdown'
            )
        except Exception:
            pass
    finally:
        finish_liked_sync(user_id)


def build_admin_contact_button(label="Contact Admin"):
    if ADMIN_CONTACT_ID:
        return types.InlineKeyboardButton(label, url=f"tg://user?id={ADMIN_CONTACT_ID}")
    return None


def ensure_subscription_access(user_id, chat_id=None, reply_target=None, send_details=True):
    """Checks whether the user has access to search and downloads."""
    has_access, message = database.check_subscription(user_id)
    if has_access or chat_id is None:
        return has_access, message

    if send_details:
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("Get Access", callback_data="buy_subscription"),
            types.InlineKeyboardButton("Use Promo Code", callback_data="activate_promo")
        )
        contact_button = build_admin_contact_button()
        if contact_button:
            markup.add(contact_button)

        access_text = (
            "*Access is limited*\n\n"
            f"{message}\n\n"
            "*How to get access:*\n"
            "1. Activate a promo code\n"
            "2. Buy a subscription\n"
            "3. Contact admin if you need help"
        )

        if reply_target is not None:
            bot.reply_to(reply_target, access_text, parse_mode='Markdown', reply_markup=markup)
        else:
            bot.send_message(chat_id, access_text, parse_mode='Markdown', reply_markup=markup)

    return has_access, message


def build_main_menu_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton('🎵 Мне понравилось'),
        types.KeyboardButton('🔍 Поиск музыки')
    )
    keyboard.row(
        types.KeyboardButton('📁 Музыка'),
        types.KeyboardButton('🎙️ Подкасты'),
        types.KeyboardButton('🗑️ Очистить кэш')
    )
    keyboard.row(
        types.KeyboardButton('💎 Подписка'),
        types.KeyboardButton('📝 Текст песни')
    )
    if MINI_APP_URL:
        keyboard.row(types.KeyboardButton('🚀 Mini App', web_app=types.WebAppInfo(url=MINI_APP_URL)))
    keyboard.row(types.KeyboardButton('📋 Помощь'))
    if ENABLE_VK:
        keyboard.row(types.KeyboardButton('🎧 VK'))
    return keyboard


def build_mini_app_markup():
    if not MINI_APP_URL:
        return None

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('Открыть', web_app=types.WebAppInfo(url=MINI_APP_URL)))
    return markup


def safe_edit_message_text(text, chat_id, message_id, **kwargs):
    try:
        return bot.edit_message_text(
            text,
            chat_id=chat_id,
            message_id=message_id,
            **kwargs
        )
    except Exception as e:
        error_text = str(e)
        if "message is not modified" in error_text:
            return None
        raise


def is_menu_button_text(text):
    if not text:
        return False

    normalized_text = text.strip()
    menu_labels = [
        'Мне понравилось',
        'Поиск музыки',
        'Музыка',
        'Подкасты',
        'Очистить кэш',
        'Подписка',
        'Текст песни',
        'Помощь',
        'Mini App',
    ]
    if ENABLE_VK:
        menu_labels.append('VK')
    return any(label in normalized_text for label in menu_labels)


def check_access(user_id):
    """Checks whether the user has an active subscription."""
    has_access, message = database.check_subscription(user_id)
    return has_access, message

def is_youtube_playlist(url):
    """РџСЂРѕРІРµСЂСЏРµС‚, СЏРІР»СЏРµС‚СЃСЏ Р»Рё СЃСЃС‹Р»РєР° РїР»РµР№Р»РёСЃС‚РѕРј YouTube"""
    try:
        parsed = urlparse(url)
        if 'youtube.com' in parsed.netloc or 'youtu.be' in parsed.netloc:
            query_params = parse_qs(parsed.query)
            if 'list' in query_params or 'playlist' in query_params:
                return True
    except:
        pass
    return False


def extract_video_from_playlist(url):
    """РР·РІР»РµРєР°РµС‚ СЃСЃС‹Р»РєСѓ РЅР° РєРѕРЅРєСЂРµС‚РЅРѕРµ РІРёРґРµРѕ РёР· РїР»РµР№Р»РёСЃС‚Р° YouTube"""
    try:
        if 'youtube.com' in url or 'youtu.be' in url:
            parsed = urlparse(url)
            query_params = parse_qs(parsed.query)

            if 'list' in query_params and 'v' in query_params:
                base_url = f"https://www.youtube.com/watch?v={query_params['v'][0]}"
                return base_url
            elif 'list' in query_params:
                ydl_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'extract_flat': True,
                    'noplaylist': False,
                }

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if info and 'entries' in info and info['entries']:
                        first_video = info['entries'][0]
                        if first_video.get('url'):
                            return first_video['url']
                        elif first_video.get('id'):
                            return f"https://www.youtube.com/watch?v={first_video['id']}"
    except Exception as e:
        print(f"[YouTube] РћС€РёР±РєР° РёР·РІР»РµС‡РµРЅРёСЏ РІРёРґРµРѕ РёР· РїР»РµР№Р»РёСЃС‚Р°: {e}")

    try:
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        if 'list' in query_params:
            del query_params['list']
            new_query = urlencode(query_params, doseq=True)
            parsed = parsed._replace(query=new_query)
            url = urlunparse(parsed)
    except:
        pass

    return url


def get_target_folder(duration_seconds):
    """РћРїСЂРµРґРµР»СЏРµС‚ РїР°РїРєСѓ РґР»СЏ СЃРѕС…СЂР°РЅРµРЅРёСЏ С„Р°Р№Р»Р° РЅР° РѕСЃРЅРѕРІРµ РґР»РёС‚РµР»СЊРЅРѕСЃС‚Рё"""
    try:
        if isinstance(duration_seconds, str):
            try:
                duration_seconds = float(duration_seconds)
            except ValueError:
                return MUSIC_DIR

        if isinstance(duration_seconds, (int, float)):
            if duration_seconds > 1200:
                return PODCASTS_DIR
            else:
                return MUSIC_DIR
        else:
            return MUSIC_DIR
    except (ValueError, TypeError) as e:
        print(f"[!] РћС€РёР±РєР° РѕРїСЂРµРґРµР»РµРЅРёСЏ РїР°РїРєРё: {e}")
        return MUSIC_DIR


def compress_audio_if_needed_fast(audio_path, max_size_mb=MAX_FILE_SIZE_MB):
    """Р‘С‹СЃС‚СЂРѕРµ СЃР¶Р°С‚РёРµ Р°СѓРґРёРѕС„Р°Р№Р»Р° СЃ РёСЃРїРѕР»СЊР·РѕРІР°РЅРёРµРј РїРѕС‚РѕРєРѕРІ"""
    try:
        file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)

        if file_size_mb <= max_size_mb:
            return audio_path, False

        print(f"[!] Р¤Р°Р№Р» СЃР»РёС€РєРѕРј Р±РѕР»СЊС€РѕР№: {file_size_mb:.2f} РњР‘. Р‘С‹СЃС‚СЂРѕ СЃР¶РёРјР°СЋ...")

        compressed_path = audio_path.replace('.mp3', '_fast_compressed.mp3')

        if file_size_mb > 100:
            bitrate = '96k'
        elif file_size_mb > 50:
            bitrate = '128k'
        else:
            bitrate = '160k'

        cmd = [
            'ffmpeg', '-i', audio_path,
            '-b:a', bitrate,
            '-threads', str(FFMPEG_THREADS),
            '-preset', 'ultrafast',
            '-f', 'mp3',
            '-y',
            compressed_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)

        if result.returncode == 0 and os.path.exists(compressed_path):
            new_size_mb = os.path.getsize(compressed_path) / (1024 * 1024)
            print(f"[вњ“] Р¤Р°Р№Р» Р±С‹СЃС‚СЂРѕ СЃР¶Р°С‚: {file_size_mb:.2f} РњР‘ -> {new_size_mb:.2f} РњР‘")

            try:
                os.remove(audio_path)
            except:
                pass
            os.rename(compressed_path, audio_path)
            return audio_path, True
        else:
            print(f"[!] РќРµ СѓРґР°Р»РѕСЃСЊ Р±С‹СЃС‚СЂРѕ СЃР¶Р°С‚СЊ С„Р°Р№Р»: {result.stderr}")
            return audio_path, False

    except subprocess.TimeoutExpired:
        print(f"[!] РўР°Р№РјР°СѓС‚ РїСЂРё Р±С‹СЃС‚СЂРѕРј СЃР¶Р°С‚РёРё С„Р°Р№Р»Р°")
        return audio_path, False
    except Exception as e:
        print(f"[!] РћС€РёР±РєР° РїСЂРё Р±С‹СЃС‚СЂРѕРј СЃР¶Р°С‚РёРё С„Р°Р№Р»Р°: {e}")
        return audio_path, False


def split_large_audio_fast(audio_path, max_part_size_mb=MAX_FILE_SIZE_MB):
    """Р‘С‹СЃС‚СЂРѕРµ СЂР°Р·РґРµР»РµРЅРёРµ Р±РѕР»СЊС€РѕРіРѕ Р°СѓРґРёРѕС„Р°Р№Р»Р° РЅР° С‡Р°СЃС‚Рё"""
    try:
        cmd = ['ffprobe', '-v', 'error', '-show_entries',
               'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', audio_path]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[!] РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕР»СѓС‡РёС‚СЊ РґР»РёС‚РµР»СЊРЅРѕСЃС‚СЊ Р°СѓРґРёРѕ: {result.stderr}")
            return [audio_path]

        duration = float(result.stdout.strip())
        file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)

        num_parts = max(2, math.ceil(file_size_mb / max_part_size_mb))

        print(f"[!] Р‘С‹СЃС‚СЂРѕ СЂР°Р·РґРµР»СЏСЋ С„Р°Р№Р» РЅР° {num_parts} С‡Р°СЃС‚РµР№...")

        part_duration = duration / num_parts

        temp_dir = os.path.join(os.path.dirname(audio_path), "temp_parts")
        os.makedirs(temp_dir, exist_ok=True)

        parts = []
        base_name = os.path.splitext(os.path.basename(audio_path))[0]

        with concurrent.futures.ThreadPoolExecutor(max_workers=FFMPEG_THREADS) as executor:
            futures = []

            for i in range(num_parts):
                start_time = i * part_duration
                part_filename = f"{base_name}_part_{i + 1}.mp3"
                part_path = os.path.join(temp_dir, part_filename)

                cmd = [
                    'ffmpeg', '-i', audio_path,
                    '-ss', str(start_time),
                    '-t', str(part_duration),
                    '-c', 'copy',
                    '-threads', '2',
                    '-y',
                    part_path
                ]

                future = executor.submit(subprocess.run, cmd, capture_output=True, text=True, timeout=180)
                futures.append((future, part_path, i + 1))

            for future, part_path, part_num in futures:
                result = future.result()
                if result.returncode == 0 and os.path.exists(part_path):
                    part_size_mb = os.path.getsize(part_path) / (1024 * 1024)
                    print(f"[вњ“] Р§Р°СЃС‚СЊ {part_num} СЃРѕР·РґР°РЅР°: {part_size_mb:.2f} РњР‘")
                    parts.append(part_path)
                else:
                    print(f"[!] РћС€РёР±РєР° СЃРѕР·РґР°РЅРёСЏ С‡Р°СЃС‚Рё {part_num}: {result.stderr}")

        if parts:
            return parts
        else:
            return [audio_path]

    except Exception as e:
        print(f"[!] РћС€РёР±РєР° Р±С‹СЃС‚СЂРѕРіРѕ СЂР°Р·РґРµР»РµРЅРёСЏ С„Р°Р№Р»Р°: {e}")
        return [audio_path]


def is_podcast_file(audio_path):
    """РџСЂРѕРІРµСЂСЏРµС‚, СЏРІР»СЏРµС‚СЃСЏ Р»Рё С„Р°Р№Р» РїРѕРґРєР°СЃС‚РѕРј"""
    return audio_path.startswith(PODCASTS_DIR)


def send_audio_fast(chat_id, audio_path, title=None, performer=None, caption=None, max_retries=2):
    """Р‘С‹СЃС‚СЂР°СЏ РѕС‚РїСЂР°РІРєР° Р°СѓРґРёРѕС„Р°Р№Р»Р°"""
    for attempt in range(max_retries):
        try:
            file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)

            if file_size_mb > MAX_FILE_SIZE_MB:
                audio_path, compressed = compress_audio_if_needed_fast(audio_path)
                if compressed:
                    file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
                    if caption:
                        caption = f"{caption} (Р±С‹СЃС‚СЂРѕ СЃР¶Р°С‚Рѕ)"

            if file_size_mb > MAX_FILE_SIZE_MB:
                print(f"[!] Р¤Р°Р№Р» РІСЃРµ РµС‰Рµ Р±РѕР»СЊС€РѕР№ {file_size_mb:.1f}РњР‘. Р‘С‹СЃС‚СЂРѕ СЂР°Р·РґРµР»СЏСЋ...")
                parts = split_large_audio_fast(audio_path)

                if len(parts) > 1:
                    print(f"[вњ“] Р‘С‹СЃС‚СЂРѕ СЂР°Р·РґРµР»РµРЅ РЅР° {len(parts)} С‡Р°СЃС‚РµР№")

                    bot.send_message(chat_id,
                                     f"вљЎ Р¤Р°Р№Р» Р±С‹СЃС‚СЂРѕ СЂР°Р·РґРµР»РµРЅ РЅР° {len(parts)} С‡Р°СЃС‚РµР№...")

                    for i, part_path in enumerate(parts):
                        part_caption = f"{caption or ''} (С‡Р°СЃС‚СЊ {i + 1}/{len(parts)})".strip()

                        with open(part_path, 'rb') as audio_file:
                            bot.send_audio(
                                chat_id=chat_id,
                                audio=audio_file,
                                title=f"{title or ''} (С‡Р°СЃС‚СЊ {i + 1})"[:64] if title else None,
                                performer=performer[:64] if performer else None,
                                caption=part_caption,
                                timeout=300
                            )

                        try:
                            os.remove(part_path)
                        except:
                            pass

                    temp_dir = os.path.join(os.path.dirname(audio_path), "temp_parts")
                    if os.path.exists(temp_dir):
                        try:
                            shutil.rmtree(temp_dir)
                        except:
                            pass

                    return True
                else:
                    return send_document_fast(chat_id, audio_path, caption)

            with open(audio_path, 'rb') as audio_file:
                bot.send_chat_action(chat_id, 'upload_audio')
                bot.send_audio(
                    chat_id=chat_id,
                    audio=audio_file,
                    title=title[:64] if title else None,
                    performer=performer[:64] if performer else None,
                    caption=caption,
                    timeout=300
                )
            return True

        except telebot.apihelper.ApiTelegramException as e:
            print(f"[!] РћС€РёР±РєР° Telegram API: {e}")
            if "file is too big" in str(e) or "400" in str(e):
                return send_document_fast(chat_id, audio_path, caption)
            elif attempt < max_retries - 1:
                time.sleep(2)
            else:
                return False
        except Exception as e:
            print(f"[!] РћС€РёР±РєР° РїСЂРё РѕС‚РїСЂР°РІРєРµ Р°СѓРґРёРѕ: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                return False

    return False


def send_document_fast(chat_id, file_path, caption=None, max_retries=2):
    """Р‘С‹СЃС‚СЂР°СЏ РѕС‚РїСЂР°РІРєР° С„Р°Р№Р»Р° РєР°Рє РґРѕРєСѓРјРµРЅС‚"""
    for attempt in range(max_retries):
        try:
            with open(file_path, 'rb') as doc_file:
                bot.send_chat_action(chat_id, 'upload_document')
                bot.send_document(
                    chat_id=chat_id,
                    document=doc_file,
                    caption=f"рџ“Ѓ {caption or os.path.basename(file_path)}",
                    timeout=300,
                    visible_file_name=os.path.basename(file_path)
                )
            return True
        except Exception as e:
            print(f"[!] РћС€РёР±РєР° РѕС‚РїСЂР°РІРєРё РґРѕРєСѓРјРµРЅС‚Р°: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                return False


def send_file_from_folder(chat_id, file_path):
    """РћС‚РїСЂР°РІРєР° С„Р°Р№Р»Р° РёР· РїР°РїРєРё"""
    try:
        if not os.path.exists(file_path):
            return False

        if file_path.lower().endswith('.mp3'):
            filename = os.path.basename(file_path)
            title = None
            performer = None

            if ' - ' in filename:
                parts = filename.rsplit(' - ', 1)
                if len(parts) == 2:
                    performer = parts[0].replace('.mp3', '').strip()
                    title = parts[1].replace('.mp3', '').strip()

            return send_audio_fast(
                chat_id=chat_id,
                audio_path=file_path,
                title=title,
                performer=performer,
                caption=f"рџ“Ѓ {filename}"
            )
        else:
            return send_document_fast(chat_id, file_path, os.path.basename(file_path))

    except Exception as e:
        print(f"[!] РћС€РёР±РєР° РѕС‚РїСЂР°РІРєРё С„Р°Р№Р»Р° РёР· РїР°РїРєРё: {e}")
        return False


def clear_cache_folders():
    """РћС‡РёС‰Р°РµС‚ РІСЃРµ С„Р°Р№Р»С‹ РІ РїР°РїРєР°С… РєСЌС€Р°"""
    try:
        total_deleted = 0

        if os.path.exists(MUSIC_DIR):
            for filename in os.listdir(MUSIC_DIR):
                file_path = os.path.join(MUSIC_DIR, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        os.unlink(file_path)
                        total_deleted += 1
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception as e:
                    print(f'РќРµ СѓРґР°Р»РѕСЃСЊ СѓРґР°Р»РёС‚СЊ {file_path}. РџСЂРёС‡РёРЅР°: {e}')

        if os.path.exists(PODCASTS_DIR):
            for filename in os.listdir(PODCASTS_DIR):
                file_path = os.path.join(PODCASTS_DIR, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        os.unlink(file_path)
                        total_deleted += 1
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception as e:
                    print(f'РќРµ СѓРґР°Р»РѕСЃСЊ СѓРґР°Р»РёС‚СЊ {file_path}. РџСЂРёС‡РёРЅР°: {e}')

        save_yandex_cache_index({})
        return total_deleted
    except Exception as e:
        print(f"[!] РћС€РёР±РєР° РїСЂРё РѕС‡РёСЃС‚РєРµ РєСЌС€Р°: {e}")
        return 0


# --- РџРћРРЎРљ Р’ РЇРќР”Р•РљРЎ.РњРЈР—Р«РљР• ---
def search_yandex_music(query, search_type="all", limit=SEARCH_RESULTS_PER_SOURCE):
    """РС‰РµС‚ С‚СЂРµРєРё РІ РЇРЅРґРµРєСЃ.РњСѓР·С‹РєРµ."""
    if not ym_client:
        print("[Yandex] РљР»РёРµРЅС‚ РЅРµ РЅР°СЃС‚СЂРѕРµРЅ РґР»СЏ РїРѕРёСЃРєР°")
        return []

    try:
        print(f"[Yandex] РџРѕРёСЃРє: '{query}' (С‚РёРї: {search_type})")
        search_result = ym_client.search(query, type_='track', page=0)

        if not search_result or not search_result.tracks:
            print(f"[Yandex] РџРѕ Р·Р°РїСЂРѕСЃСѓ '{query}' РЅРёС‡РµРіРѕ РЅРµ РЅР°Р№РґРµРЅРѕ")
            return []

        tracks = search_result.tracks.results if limit is None else search_result.tracks.results[:limit]
        print(f"[Yandex] РќР°Р№РґРµРЅРѕ {len(tracks)} С‚СЂРµРєРѕРІ РїРѕ Р·Р°РїСЂРѕСЃСѓ '{query}'")

        formatted_results = []
        for track in tracks:
            try:
                title = track.title if hasattr(track, 'title') else ''

                if search_type == "artist":
                    artists = [artist.name for artist in track.artists] if hasattr(track,
                                                                                   'artists') and track.artists else []
                    if not any(query.lower() in artist.lower() for artist in artists):
                        continue
                elif search_type == "title":
                    if query.lower() not in title.lower():
                        continue

                artists_str = ', '.join(
                    [artist.name for artist in track.artists]) if track.artists else 'РќРµРёР·РІРµСЃС‚РЅС‹Р№ РёСЃРїРѕР»РЅРёС‚РµР»СЊ'
                album_name = track.albums[0].title if track.albums else 'РќРµРёР·РІРµСЃС‚РЅС‹Р№ Р°Р»СЊР±РѕРј'
                album_id = track.albums[0].id if track.albums else 0
                duration_ms = track.duration_ms if hasattr(track, 'duration_ms') else 0
                duration_str = f"{duration_ms // 60000}:{str((duration_ms % 60000) // 1000).zfill(2)}"

                formatted_results.append({
                    'title': title,
                    'artists': artists_str,
                    'album': album_name,
                    'track_id': track.id,
                    'album_id': album_id,
                    'duration': duration_str,
                    'duration_ms': duration_ms,
                    'duration_seconds': duration_ms / 1000 if duration_ms else 0,
                    'track_obj': track,
                    'source': 'yandex'
                })

            except Exception as e:
                print(f"[Yandex] РћС€РёР±РєР° С„РѕСЂРјР°С‚РёСЂРѕРІР°РЅРёСЏ С‚СЂРµРєР°: {e}")
                continue

        return formatted_results

    except Exception as e:
        print(f"[Yandex] РћС€РёР±РєР° РїРѕРёСЃРєР°: {e}")
        return []


# --- РџРћРРЎРљ Р’ YOUTUBE ---
def search_youtube_music(query, limit=SEARCH_RESULTS_PER_SOURCE):
    """РС‰РµС‚ С‚СЂРµРєРё РЅР° YouTube РїРѕ РЅР°Р·РІР°РЅРёСЋ."""
    try:
        print(f"[YouTube Search] РџРѕРёСЃРє: '{query}'")

        ydl_opts = build_ytdlp_base_options()
        ydl_opts.update({
            'extract_flat': True,
            'default_search': 'ytsearch',
            'noplaylist': True,
        })

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_string = f"ytsearch{limit}:{query}"
            info = ydl.extract_info(search_string, download=False)

            if not info or 'entries' not in info:
                print(f"[YouTube Search] РџРѕ Р·Р°РїСЂРѕСЃСѓ '{query}' РЅРёС‡РµРіРѕ РЅРµ РЅР°Р№РґРµРЅРѕ")
                return []

            videos = info['entries']
            formatted_results = []

            for i, video in enumerate(videos):
                try:
                    if not video:
                        continue

                    title = video.get('title', 'Р‘РµР· РЅР°Р·РІР°РЅРёСЏ')
                    uploader = video.get('uploader', 'РќРµРёР·РІРµСЃС‚РЅС‹Р№ Р°РІС‚РѕСЂ')
                    duration = video.get('duration', 0)
                    video_id = video.get('id', '')

                    if not video_id:
                        continue

                    url = f"https://www.youtube.com/watch?v={video_id}"

                    if duration > 0:
                        minutes = int(duration) // 60
                        seconds = int(duration) % 60
                        duration_str = f"{minutes}:{str(seconds).zfill(2)}"
                    else:
                        duration_str = "РќРµРёР·РІРµСЃС‚РЅРѕ"

                    formatted_results.append({
                        'index': i + 1,
                        'title': title,
                        'artist': uploader,
                        'full_title': f"{uploader} - {title}",
                        'duration': duration_str,
                        'duration_seconds': int(duration) if duration else 0,
                        'url': url,
                        'video_id': video_id,
                        'source': 'youtube'
                    })

                except Exception as e:
                    print(f"[YouTube Search] РћС€РёР±РєР° С„РѕСЂРјР°С‚РёСЂРѕРІР°РЅРёСЏ РІРёРґРµРѕ {i}: {e}")
                    continue

            print(f"[YouTube Search] РќР°Р№РґРµРЅРѕ {len(formatted_results)} РІРёРґРµРѕ РїРѕ Р·Р°РїСЂРѕСЃСѓ '{query}'")
            return formatted_results[:limit]

    except Exception as e:
        print(f"[YouTube Search] РћС€РёР±РєР° РїРѕРёСЃРєР°: {e}")
        return []


# --- РЎРљРђР§РР’РђРќРР• РР— YANDEX Р YOUTUBР• ---
def search_vk_music(query, limit=SEARCH_RESULTS_PER_SOURCE):
    """РС‰РµС‚ С‚СЂРµРєРё РІ VK Music С‡РµСЂРµР· С‚РµС…РЅРёС‡РµСЃРєРёР№ Р°РєРєР°СѓРЅС‚ Р±РѕС‚Р°."""
    if not vk_audio:
        print("[VK Search] Client is not configured")
        return []

    try:
        print(f"[VK Search] РџРѕРёСЃРє: '{query}'")
        with vk_audio_lock:
            tracks = list(vk_audio.search(query, count=limit))

        formatted_results = []
        for track in tracks[:limit]:
            try:
                title = track.get('title', 'Р‘РµР· РЅР°Р·РІР°РЅРёСЏ')
                artist = track.get('artist', 'РќРµРёР·РІРµСЃС‚РЅС‹Р№ РёСЃРїРѕР»РЅРёС‚РµР»СЊ')
                duration_seconds = int(track.get('duration') or 0)
                minutes = duration_seconds // 60
                seconds = duration_seconds % 60
                duration_str = f"{minutes}:{str(seconds).zfill(2)}" if duration_seconds else "РќРµРёР·РІРµСЃС‚РЅРѕ"

                formatted_results.append({
                    'title': title,
                    'artist': artist,
                    'artists': artist,
                    'duration': duration_str,
                    'duration_seconds': duration_seconds,
                    'track_id': int(track.get('id') or 0),
                    'owner_id': int(track.get('owner_id') or 0),
                    'url': track.get('url', ''),
                    'source': 'vk',
                })
            except Exception as e:
                print(f"[VK Search] РћС€РёР±РєР° С„РѕСЂРјР°С‚РёСЂРѕРІР°РЅРёСЏ С‚СЂРµРєР°: {e}")

        print(f"[VK Search] РќР°Р№РґРµРЅРѕ {len(formatted_results)} С‚СЂРµРєРѕРІ РїРѕ Р·Р°РїСЂРѕСЃСѓ '{query}'")
        return formatted_results
    except Exception as e:
        print(f"[VK Search] РћС€РёР±РєР° РїРѕРёСЃРєР°: {e}")
        return []


def download_yandex_track_fast(track_id, album_id, liked_synced=False):
    """????????? ???? ?? ??????.?????? ? ?????????????????? ?????????? ????."""
    if not ym_client:
        return None, None, None, "?????? ??????.?????? ?? ????????."

    try:
        cached_path, cache_item = resolve_cached_yandex_track(track_id, album_id)
        if cached_path:
            return (
                cached_path,
                cache_item.get("title") or "Unknown Title",
                cache_item.get("performer") or "Unknown Artist",
                "success"
            )

        with ym_client_lock:
            tracks = ym_client.tracks([f"{track_id}:{album_id}"])

        if not tracks:
            return None, None, None, "???? ?? ??????."

        track = tracks[0]

        with ym_client_lock:
            download_info = track.get_download_info()

        if not download_info:
            return None, None, None, "?????????? ??? ?????????? ??????????."

        best_info = min(
            [info for info in download_info if info.codec == 'mp3'],
            key=lambda x: x.bitrate_in_kbps,
            default=None
        )

        if not best_info:
            best_info = download_info[0] if download_info else None
            if not best_info:
                return None, None, None, "??? ??????????? ???????."

        artist_name = track.artists[0].name if track.artists else "Unknown Artist"
        performer = ", ".join(a.name for a in track.artists) if track.artists else "Unknown Artist"
        safe_title = sanitize_filename(track.title, fallback=f"track_{track_id}", max_length=70)
        safe_artist = sanitize_filename(artist_name, fallback="Unknown Artist", max_length=40)

        duration_seconds = track.duration_ms / 1000 if hasattr(track, 'duration_ms') and track.duration_ms else 0
        target_dir = get_target_folder(duration_seconds)
        os.makedirs(target_dir, exist_ok=True)

        filename = f"ym_{track_id}_{album_id}_{safe_artist} - {safe_title}.mp3"
        filepath = os.path.join(target_dir, filename)
        legacy_filename = f"{safe_artist} - {safe_title}.mp3"
        legacy_candidates = [
            os.path.join(MUSIC_DIR, legacy_filename),
            os.path.join(PODCASTS_DIR, legacy_filename),
            os.path.join(target_dir, legacy_filename),
        ]

        if not os.path.exists(filepath):
            for candidate in legacy_candidates:
                if os.path.exists(candidate):
                    filepath = candidate
                    break

        if not os.path.exists(filepath):
            track.download(filepath, codec='mp3', bitrate_in_kbps=best_info.bitrate_in_kbps)

        update_yandex_cache_entry(
            track_id,
            album_id,
            filepath,
            track.title,
            performer,
            duration_seconds,
            liked_synced=liked_synced
        )

        return filepath, track.title, performer, "success"

    except Exception as e:
        print(f"[Yandex] ?????? ??????????: {e}")
        return None, None, None, f"?????? ??????????: {str(e)}"


def download_vk_track_fast(owner_id, track_id, track_url=None, title_hint=None, artist_hint=None, duration_seconds=0):
    """РЎРєР°С‡РёРІР°РµС‚ С‚СЂРµРє РёР· VK Music РїРѕ РїСЂСЏРјРѕР№ СЃСЃС‹Р»РєРµ, РїРѕР»СѓС‡РµРЅРЅРѕР№ С‡РµСЂРµР· С‚РµС…РЅРёС‡РµСЃРєРёР№ Р°РєРєР°СѓРЅС‚."""
    if not vk_audio:
        return None, None, None, "VK Music РЅРµ РЅР°СЃС‚СЂРѕРµРЅ."

    try:
        track_data = None
        if not track_url:
            with vk_audio_lock:
                track_data = vk_audio.get_audio_by_id(owner_id, track_id)
            if track_data:
                track_url = track_data.get('url')
                title_hint = track_data.get('title') or title_hint
                artist_hint = track_data.get('artist') or artist_hint
                duration_seconds = int(track_data.get('duration') or duration_seconds or 0)

        if not track_url:
            return None, None, None, "РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕР»СѓС‡РёС‚СЊ СЃСЃС‹Р»РєСѓ РЅР° С‚СЂРµРє VK."

        safe_title = sanitize_filename(title_hint or f"track_{track_id}", fallback=f"track_{track_id}", max_length=70)
        safe_artist = sanitize_filename(artist_hint or "Unknown Artist", fallback="Unknown Artist", max_length=40)
        target_dir = get_target_folder(duration_seconds)
        os.makedirs(target_dir, exist_ok=True)

        filename = f"vk_{owner_id}_{track_id}_{safe_artist} - {safe_title}.mp3"
        filepath = os.path.join(target_dir, filename)

        if not os.path.exists(filepath):
            with requests.get(track_url, stream=True, timeout=(10, 120)) as response:
                response.raise_for_status()
                with open(filepath, 'wb') as output_file:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            output_file.write(chunk)

        return filepath, title_hint or safe_title, artist_hint or safe_artist, "success"
    except Exception as e:
        print(f"[VK] РћС€РёР±РєР° СЃРєР°С‡РёРІР°РЅРёСЏ: {e}")
        return None, None, None, f"РћС€РёР±РєР° СЃРєР°С‡РёРІР°РЅРёСЏ VK: {str(e)}"


def download_from_youtube_fast(query, is_url=False):
    """РЎРєР°С‡РёРІР°РµС‚ Р°СѓРґРёРѕ СЃ YouTube"""
    try:
        if is_url and is_youtube_playlist(query):
            print("[YouTube] РџРѕР»СѓС‡РµРЅР° СЃСЃС‹Р»РєР° РЅР° РїР»РµР№Р»РёСЃС‚, РёР·РІР»РµРєР°СЋ РїРµСЂРІРѕРµ РІРёРґРµРѕ...")
            query = extract_video_from_playlist(query)

        ydl_info_opts = build_ytdlp_base_options()
        ydl_info_opts.update({
            'extract_flat': False,
        })

        with yt_dlp.YoutubeDL(ydl_info_opts) as ydl:
            info = ydl.extract_info(query, download=False)

            if not info:
                return None, None, None, "no_info"

            if 'entries' in info:
                video = info['entries'][0] if info['entries'] else None
            else:
                video = info

            if not video:
                return None, None, None, "no_video"

            title = video.get('title', 'Р‘РµР· РЅР°Р·РІР°РЅРёСЏ')
            uploader = video.get('uploader', 'РќРµРёР·РІРµСЃС‚РЅС‹Р№ Р°РІС‚РѕСЂ')
            duration = video.get('duration', 0)
            video_id = video.get('id', '')

            try:
                duration_int = int(duration) if duration else 0
            except (ValueError, TypeError):
                duration_int = 0

            target_dir = get_target_folder(duration_int)
            os.makedirs(target_dir, exist_ok=True)

            ydl_opts = build_ytdlp_base_options()
            ydl_opts.update({
                'format': 'bestaudio[ext=m4a]/bestaudio/best',
                'outtmpl': os.path.join(target_dir, f'%(id)s.%(ext)s'),
                'socket_timeout': 60,
                'retries': 10,
                'fragment_retries': 10,
                'extractor_retries': 3,
                'nooverwrites': True,
                'continuedl': True,
                'noprogress': True,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'default_search': 'ytsearch1:' if not is_url else None,
                'noplaylist': True,
                'sleep_interval': 1,
                'max_sleep_interval': 5,
            })

            if duration_int > 7200:
                ydl_opts.update({
                    'retries': 20,
                    'fragment_retries': 20,
                    'buffersize': '1024M',
                    'http_chunk_size': 10485760,
                })

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([query])

            if video_id:
                import glob
                possible_patterns = [
                    os.path.join(target_dir, f"{video_id}.mp3"),
                    os.path.join(target_dir, f"{video_id}.webm"),
                    os.path.join(target_dir, f"{video_id}.m4a"),
                ]

                audio_path = None
                for pattern in possible_patterns:
                    if os.path.exists(pattern):
                        audio_path = pattern
                        break

                if not audio_path:
                    pattern = os.path.join(target_dir, f"{video_id}.*")
                    files = glob.glob(pattern)
                    if files:
                        audio_path = files[0]

                if not audio_path:
                    all_mp3_files = glob.glob(os.path.join(target_dir, "*.mp3"))
                    if all_mp3_files:
                        audio_path = max(all_mp3_files, key=os.path.getctime)

                if audio_path:
                    safe_uploader = "".join([c for c in uploader if c.isalnum() or c in (' ', '-', '_')]).strip()[:20]
                    safe_title = "".join([c for c in title if c.isalnum() or c in (' ', '-', '_')]).strip()[:30]
                    new_name = f"{safe_uploader} - {safe_title}.mp3"
                    new_path = os.path.join(target_dir, new_name)

                    try:
                        if os.path.exists(new_path):
                            os.remove(new_path)

                        if audio_path.endswith('.mp3'):
                            os.rename(audio_path, new_path)
                            audio_path = new_path
                        else:
                            import subprocess
                            temp_mp3 = audio_path.rsplit('.', 1)[0] + '.mp3'
                            try:
                                subprocess.run(
                                    ['ffmpeg', '-i', audio_path, '-codec:a', 'libmp3lame', '-qscale:a', '2', temp_mp3],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                if os.path.exists(temp_mp3):
                                    os.rename(temp_mp3, new_path)
                                    if os.path.exists(audio_path):
                                        os.remove(audio_path)
                                    audio_path = new_path
                            except:
                                os.rename(audio_path, new_path)
                                audio_path = new_path

                        return audio_path, title, uploader, "success"
                    except Exception as rename_error:
                        print(f"[YouTube] РћС€РёР±РєР° РїРµСЂРµРёРјРµРЅРѕРІР°РЅРёСЏ: {rename_error}")
                        return audio_path, title, uploader, "success"

            return None, title, uploader, "no_file"

    except Exception as e:
        print(f"[!] РћС€РёР±РєР° YouTube: {e}")
        return None, None, None, f"РћС€РёР±РєР°: {str(e)}"


# --- РЈРќРР’Р•Р РЎРђР›Р¬РќР«Р™ РџРћРРЎРљ ---
def universal_search_all(query, limit_per_service=SEARCH_RESULTS_PER_SOURCE):
    """РС‰РµС‚ РјСѓР·С‹РєСѓ РІ РЇРЅРґРµРєСЃ.РњСѓР·С‹РєРµ Рё YouTube."""
    all_results = []

    if ym_client:
        yandex_results = search_yandex_music(query, limit=limit_per_service)
        all_results.extend(yandex_results)

    youtube_results = search_youtube_music(query, limit=limit_per_service)
    all_results.extend(youtube_results)

    if ENABLE_VK and vk_audio:
        vk_results = search_vk_music(query, limit=limit_per_service)
        all_results.extend(vk_results)

    for i, result in enumerate(all_results):
        result['global_index'] = i + 1

    return all_results


def show_search_results(chat_id, query, results, page=0):
    """РџРѕРєР°Р·С‹РІР°РµС‚ СЂРµР·СѓР»СЊС‚Р°С‚С‹ РїРѕРёСЃРєР°"""
    if not results:
        return "вќЊ РџРѕ РІР°С€РµРјСѓ Р·Р°РїСЂРѕСЃСѓ РЅРёС‡РµРіРѕ РЅРµ РЅР°Р№РґРµРЅРѕ."

    history = user_search_history.get(chat_id, {})
    original_results = history.get('original_results')
    if original_results is None or history.get('query') != query:
        original_results = list(results)

    user_search_history[chat_id] = {
        'query': query,
        'results': list(results),
        'original_results': original_results,
        'timestamp': time.time()
    }

    start_idx = page * 5
    end_idx = start_idx + 5
    page_results = results[start_idx:end_idx]

    message_text = f"рџ”Ћ *Search results for: '{escape_markdown(query)}'*\n\n"

    yandex_count = len([r for r in results if r.get('source') == 'yandex'])
    youtube_count = len([r for r in results if r.get('source') == 'youtube'])
    source_counts = [f"рџЋµ РЇРЅРґРµРєСЃ: {yandex_count}", f"рџ“є YouTube: {youtube_count}"]
    if ENABLE_VK:
        vk_count = len([r for r in results if r.get('source') == 'vk'])
        source_counts.append(f"рџЋ§ VK: {vk_count}")

    message_text += f"*РќР°Р№РґРµРЅРѕ:* {len(results)} С‚СЂРµРєРѕРІ "
    message_text += f"({', '.join(source_counts)})\n"
    message_text += f"*РЎС‚СЂР°РЅРёС†Р°:* {page + 1}/{(len(results) + 4) // 5}\n\n"

    for track in page_results:
        idx = track.get('global_index', 0)
        title = escape_markdown(track.get('title', 'Unknown title'))
        source = track.get('source', 'unknown')

        if source == 'yandex':
            source_icon = "рџЋµ"
            artist_info = escape_markdown(track.get('artists', 'Unknown artist'))
        elif source == 'youtube':
            source_icon = "рџ“є"
            artist_info = escape_markdown(track.get('artist', 'Unknown channel'))
        else:
            source_icon = "рџ”Ќ"
            artist_info = 'РќРµРёР·РІРµСЃС‚РЅРѕ'

        if source == 'vk':
            source_icon = "рџЋ§"
            artist_info = escape_markdown(track.get('artist', 'Unknown artist'))

        message_text += f"{idx}. {source_icon} *{title}*\n"
        message_text += f"   рџ‘¤ {artist_info}\n"

        duration = track.get('duration', '0:00')
        message_text += f"   вЏ± {duration}\n\n"

    message_text += "Р’С‹Р±РµСЂРёС‚Рµ С‚СЂРµРє РґР»СЏ СЃРєР°С‡РёРІР°РЅРёСЏ:"

    return message_text


def create_search_keyboard(results, page=0, results_per_page=5, show_all_button=True):
    """РЎРѕР·РґР°РµС‚ РёРЅР»Р°Р№РЅ-РєР»Р°РІРёР°С‚СѓСЂСѓ РґР»СЏ СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ РїРѕРёСЃРєР°"""
    markup = types.InlineKeyboardMarkup(row_width=2)

    start_idx = page * results_per_page
    end_idx = start_idx + results_per_page
    page_results = results[start_idx:end_idx]

    for track in page_results:
        idx = track.get('global_index', 0)
        title = track.get('title', 'РўСЂРµРє')
        source = track.get('source', 'unknown')

        if source == 'yandex':
            source_icon = "рџЋµ"
        elif source == 'youtube':
            source_icon = "рџ“є"
        else:
            source_icon = "рџ”Ќ"

        if source == 'vk':
            source_icon = "рџЋ§"

        btn_text = f"{source_icon} {idx}. {title[:15]}..."

        if source == 'yandex':
            btn_data = f"ya_{track.get('track_id', 0)}_{track.get('album_id', 0)}_{page}"
        elif source == 'youtube':
            btn_data = f"yt_{track.get('video_id', '')}_{page}"
        elif source == 'vk':
            btn_data = f"vk_{track.get('owner_id', 0)}_{track.get('track_id', 0)}_{page}"
        else:
            btn_data = f"info_{idx}_{page}"

        markup.add(types.InlineKeyboardButton(btn_text, callback_data=btn_data))

    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton("в—ЂпёЏ РќР°Р·Р°Рґ", callback_data=f"page_{page - 1}"))

    if end_idx < len(results):
        nav_buttons.append(types.InlineKeyboardButton("Р’РїРµСЂРµРґ в–¶пёЏ", callback_data=f"page_{page + 1}"))

    if nav_buttons:
        markup.add(*nav_buttons)

    filter_buttons = []

    if show_all_button:
        filter_buttons.append(types.InlineKeyboardButton("рџЊђ Р’РµР·РґРµ", callback_data="filter_all"))

    filter_buttons.extend([
        types.InlineKeyboardButton("рџЋµ РЇРЅРґРµРєСЃ", callback_data="filter_yandex"),
        types.InlineKeyboardButton("рџ“є YouTube", callback_data="filter_youtube"),
    ])

    if ENABLE_VK:
        filter_buttons.append(types.InlineKeyboardButton("рџЋ§ VK", callback_data="filter_vk"))

    filter_buttons.append(types.InlineKeyboardButton("рџ”„ РќРѕРІС‹Р№ РїРѕРёСЃРє", callback_data="new_search"))

    markup.add(*filter_buttons)

    return markup


# --- Р¤РЈРќРљР¦РР Р”Р›РЇ Р РђР‘РћРўР« РЎ РџРђРџРљРђРњР ---
def get_folder_files(folder_path):
    """РџРѕР»СѓС‡Р°РµС‚ СЃРїРёСЃРѕРє С„Р°Р№Р»РѕРІ РІ РїР°РїРєРµ"""
    try:
        files = []
        if not os.path.exists(folder_path):
            print(f"[!] РџР°РїРєР° РЅРµ СЃСѓС‰РµСЃС‚РІСѓРµС‚: {folder_path}")
            return files

        for file in os.listdir(folder_path):
            if file.endswith('.mp3'):
                file_path = os.path.join(folder_path, file)
                file_size = os.path.getsize(file_path) / (1024 * 1024)
                files.append({
                    'name': file,
                    'path': file_path,
                    'size': round(file_size, 2),
                    'mtime': os.path.getmtime(file_path)
                })
        files.sort(key=lambda x: (-x['mtime'], x['name']))
        return files
    except Exception as e:
        print(f"[!] РћС€РёР±РєР° РїРѕР»СѓС‡РµРЅРёСЏ С„Р°Р№Р»РѕРІ РёР· РїР°РїРєРё {folder_path}: {e}")
        return []


def create_files_keyboard(files, page=0, files_per_page=10, folder_type="music"):
    """РЎРѕР·РґР°РµС‚ РєР»Р°РІРёР°С‚СѓСЂСѓ РґР»СЏ РІС‹Р±РѕСЂР° С„Р°Р№Р»РѕРІ РёР· РїР°РїРєРё"""
    markup = types.InlineKeyboardMarkup(row_width=2)

    start_idx = page * files_per_page
    end_idx = start_idx + files_per_page
    page_files = files[start_idx:end_idx]

    for i, file in enumerate(page_files):
        btn_text = f"рџ“„ {file['name'][:20]}..."
        btn_data = f"file_{folder_type}_{start_idx + i}_{page}"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=btn_data))

    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton("в—ЂпёЏ РќР°Р·Р°Рґ", callback_data=f"files_{folder_type}_{page - 1}"))

    if end_idx < len(files):
        nav_buttons.append(types.InlineKeyboardButton("Р’РїРµСЂРµРґ в–¶пёЏ", callback_data=f"files_{folder_type}_{page + 1}"))

    if nav_buttons:
        markup.row(*nav_buttons)

    markup.add(
        types.InlineKeyboardButton("рџ—‘пёЏ РћС‡РёСЃС‚РёС‚СЊ РєСЌС€", callback_data="clear_cache"),
        types.InlineKeyboardButton("рџ”™ РќР°Р·Р°Рґ Рє РјРµРЅСЋ", callback_data="back_to_menu")
    )

    return markup


# --- РћРЎРќРћР’РќРђРЇ Р¤РЈРќРљР¦РРЇ Р”Р›РЇ РђР’РўРћРњРђРўРР§Р•РЎРљРћР“Рћ РџРћРРЎРљРђ ---
def process_search_query(chat_id, query, is_command=False):
    """РћР±СЂР°Р±Р°С‚С‹РІР°РµС‚ РїРѕРёСЃРєРѕРІС‹Р№ Р·Р°РїСЂРѕСЃ (Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё РёР»Рё РїРѕ РєРѕРјР°РЅРґРµ)"""
    try:
        query = query.strip()

        if not query or len(query) < 2:
            if is_command:
                bot.send_message(chat_id, "вќЊ Р—Р°РїСЂРѕСЃ СЃР»РёС€РєРѕРј РєРѕСЂРѕС‚РєРёР№. Р’РІРµРґРёС‚Рµ РЅР°Р·РІР°РЅРёРµ РїРµСЃРЅРё РёР»Рё РёСЃРїРѕР»РЅРёС‚РµР»СЏ.")
            return

        if is_command:
            wait_msg = bot.send_message(chat_id, f"рџ”Ќ РС‰Сѓ '{query}' РІРѕ РІСЃРµС… РёСЃС‚РѕС‡РЅРёРєР°С…...")
        else:
            wait_msg = bot.send_message(chat_id, f"рџ”Ќ РђРІС‚РѕРјР°С‚РёС‡РµСЃРєРёР№ РїРѕРёСЃРє: '{query}'...")

        results = universal_search_all(query, limit_per_service=SEARCH_RESULTS_PER_SOURCE)

        if not results:
            bot.edit_message_text(f"вќЊ РџРѕ Р·Р°РїСЂРѕСЃСѓ '{query}' РЅРёС‡РµРіРѕ РЅРµ РЅР°Р№РґРµРЅРѕ.",
                                  chat_id=chat_id,
                                  message_id=wait_msg.message_id)
            return

        message_text = show_search_results(chat_id, query, results, page=0)
        keyboard = create_search_keyboard(results, page=0, show_all_button=True)

        try:
            bot.edit_message_text(message_text,
                                  chat_id=chat_id,
                                  message_id=wait_msg.message_id,
                                  parse_mode='Markdown',
                                  reply_markup=keyboard)
        except Exception as e:
            print(f"[!] РћС€РёР±РєР° РѕС‚РїСЂР°РІРєРё СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ: {e}")
            bot.edit_message_text(f"вњ… РќР°Р№РґРµРЅРѕ {len(results)} СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ. РСЃРїРѕР»СЊР·СѓР№С‚Рµ РєРЅРѕРїРєРё РЅРёР¶Рµ РґР»СЏ РІС‹Р±РѕСЂР°.",
                                  chat_id=chat_id,
                                  message_id=wait_msg.message_id,
                                  reply_markup=keyboard)

    except Exception as e:
        print(f"[!] РћС€РёР±РєР° РїСЂРё РѕР±СЂР°Р±РѕС‚РєРµ РїРѕРёСЃРєРѕРІРѕРіРѕ Р·Р°РїСЂРѕСЃР°: {e}")


# ============================================
# РћР‘Р РђР‘РћРўР§РРљР РљРћРњРђРќР” TELEGRAM
# ============================================

# РР·РјРµРЅРµРЅРЅС‹Р№ /promo handler
@bot.message_handler(commands=['promo'])
def handle_promo(message):
    """РђРєС‚РёРІР°С†РёСЏ РїСЂРѕРјРѕРєРѕРґР°"""
    try:
        user_id = message.from_user.id
        print(f"[DEBUG] /promo command from user {user_id}")

        # РџРѕР»СѓС‡Р°РµРј РїСЂРѕРјРѕРєРѕРґ РёР· РєРѕРјР°РЅРґС‹
        parts = message.text.split()
        if len(parts) < 2:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("рџЋЃ РђРєС‚РёРІРёСЂРѕРІР°С‚СЊ РїСЂРѕРјРѕРєРѕРґ", callback_data="activate_promo"))

            bot.reply_to(message,
                         "рџЋЃ *РђРєС‚РёРІР°С†РёСЏ РїСЂРѕРјРѕРєРѕРґР°*\n\n"
                         "Р”Р»СЏ Р°РєС‚РёРІР°С†РёРё РїСЂРѕРјРѕРєРѕРґР° РёСЃРїРѕР»СЊР·СѓР№С‚Рµ РєРѕРјР°РЅРґСѓ:\n"
                         "`/promo Р’РђРЁ_РџР РћРњРћРљРћР”`\n\n"
                         "РР»Рё РЅР°Р¶РјРёС‚Рµ РєРЅРѕРїРєСѓ РЅРёР¶Рµ РґР»СЏ РІРІРѕРґР° РїСЂРѕРјРѕРєРѕРґР°:",
                         parse_mode='Markdown',
                         reply_markup=markup)
            return

        promo_code = parts[1].strip()
        print(f"[DEBUG] Trying to use promo code: {promo_code} for user {user_id}")

        # РџСЂРѕРІРµСЂСЏРµРј РґР»РёРЅСѓ РїСЂРѕРјРѕРєРѕРґР°
        if len(promo_code) < 3:
            bot.reply_to(message, "вќЊ РџСЂРѕРјРѕРєРѕРґ СЃР»РёС€РєРѕРј РєРѕСЂРѕС‚РєРёР№. РњРёРЅРёРјР°Р»СЊРЅР°СЏ РґР»РёРЅР° - 3 СЃРёРјРІРѕР»Р°.")
            return

        # РџСЂРѕРІРµСЂСЏРµРј, СЏРІР»СЏРµС‚СЃСЏ Р»Рё РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂРѕРј
        if user_id in ADMIN_IDS:
            bot.reply_to(message,
                         "вљЎ *Р’С‹ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂ!*\n\n"
                         "Р’Р°Рј Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё РїСЂРµРґРѕСЃС‚Р°РІР»РµРЅР° Р±РµСЃРєРѕРЅРµС‡РЅР°СЏ РїРѕР»РЅР°СЏ РїРѕРґРїРёСЃРєР°.\n"
                         "РџСЂРѕРјРѕРєРѕРґС‹ РІР°Рј РЅРµ РЅСѓР¶РЅС‹!",
                         parse_mode='Markdown')
            return

        # РџРѕРєР°Р·С‹РІР°РµРј РѕР¶РёРґР°РЅРёРµ
        wait_msg = bot.reply_to(message, f"рџ”Ќ РџСЂРѕРІРµСЂСЏСЋ РїСЂРѕРјРѕРєРѕРґ `{promo_code}`...", parse_mode='Markdown')

        # РђРєС‚РёРІРёСЂСѓРµРј РїСЂРѕРјРѕРєРѕРґ
        print(f"[DEBUG] Calling database.use_promo_code...")
        result = database.use_promo_code(user_id, promo_code)
        print(f"[DEBUG] Promo code result: {result}")

        # РЈРґР°Р»СЏРµРј СЃРѕРѕР±С‰РµРЅРёРµ РѕР¶РёРґР°РЅРёСЏ
        try:
            bot.delete_message(message.chat.id, wait_msg.message_id)
        except:
            pass

        # РћС‚РїСЂР°РІР»СЏРµРј СЂРµР·СѓР»СЊС‚Р°С‚
        if result.get('success'):
            # РџРѕР»СѓС‡Р°РµРј РѕР±РЅРѕРІР»РµРЅРЅСѓСЋ РёРЅС„РѕСЂРјР°С†РёСЋ Рѕ РїРѕРґРїРёСЃРєРµ
            has_access, msg = database.check_subscription(user_id)

            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton("рџ“Љ РњРѕСЏ РїРѕРґРїРёСЃРєР°", callback_data="back_to_subscribe"),
                types.InlineKeyboardButton("рџЋµ РЎРєР°С‡Р°С‚СЊ РјСѓР·С‹РєСѓ", callback_data="new_search")
            )

            bot.reply_to(message,
                         f"рџЋ‰ *РЈСЃРїРµС€РЅРѕ!*\n\n"
                         f"{result['message']}\n\n"
                         f"рџљЂ *РќР°С‡РЅРёС‚Рµ РїСЂСЏРјРѕ СЃРµР№С‡Р°СЃ:*\n"
                         f"1. РћС‚РїСЂР°РІСЊС‚Рµ РЅР°Р·РІР°РЅРёРµ РїРµСЃРЅРё РІ С‡Р°С‚\n"
                         f"2. РСЃРїРѕР»СЊР·СѓР№С‚Рµ РїРѕРёСЃРє С‡РµСЂРµР· РєРЅРѕРїРєРё\n"
                         f"3. РћС‚РїСЂР°РІСЊС‚Рµ СЃСЃС‹Р»РєСѓ РЅР° С‚СЂРµРє",
                         parse_mode='Markdown',
                         reply_markup=markup)
        else:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("рџ”„ РџРѕРїСЂРѕР±РѕРІР°С‚СЊ РґСЂСѓРіРѕР№ РєРѕРґ", callback_data="activate_promo"))

            bot.reply_to(message,
                         f"вќЊ *РќРµ СѓРґР°Р»РѕСЃСЊ Р°РєС‚РёРІРёСЂРѕРІР°С‚СЊ РїСЂРѕРјРѕРєРѕРґ*\n\n"
                         f"*РљРѕРґ:* `{promo_code}`\n"
                         f"*РџСЂРёС‡РёРЅР°:* {result.get('message', 'РќРµРёР·РІРµСЃС‚РЅР°СЏ РѕС€РёР±РєР°')}\n\n"
                         f"рџ’Ў *РЎРѕРІРµС‚С‹:*\n"
                         f"вЂў РџСЂРѕРІРµСЂСЊС‚Рµ РїСЂР°РІРёР»СЊРЅРѕСЃС‚СЊ РЅР°РїРёСЃР°РЅРёСЏ\n"
                         f"вЂў РЈР±РµРґРёС‚РµСЃСЊ, С‡С‚Рѕ РїСЂРѕРјРѕРєРѕРґ РµС‰Рµ РґРµР№СЃС‚РІРёС‚РµР»РµРЅ\n"
                         f"вЂў РџРѕРјРЅРёС‚Рµ: РѕРґРёРЅ РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ = РѕРґРёРЅ РїСЂРѕРјРѕРєРѕРґ",
                         parse_mode='Markdown',
                         reply_markup=markup)

    except Exception as e:
        print(f"[ERROR] РћС€РёР±РєР° РѕР±СЂР°Р±РѕС‚РєРё РїСЂРѕРјРѕРєРѕРґР°: {e}")
        traceback.print_exc()
        bot.reply_to(message,
                     "вќЊ РџСЂРѕРёР·РѕС€Р»Р° РѕС€РёР±РєР° РїСЂРё РѕР±СЂР°Р±РѕС‚РєРµ РїСЂРѕРјРѕРєРѕРґР°. РџРѕРїСЂРѕР±СѓР№С‚Рµ РїРѕР·Р¶Рµ.")


# РћР±РЅРѕРІР»РµРЅРЅР°СЏ С„СѓРЅРєС†РёСЏ handle_subscribe (СѓР±СЂР°С‚СЊ РєРЅРѕРїРєРё РїСЂРѕРјРѕРєРѕРґРѕРІ)
@bot.message_handler(commands=['subscribe'])
def handle_subscribe(message):
    """Обработчик команды подписки."""
    try:
        user_id = message.from_user.id

        has_access, msg = database.check_subscription(user_id)
        markup = types.InlineKeyboardMarkup(row_width=1)

        if not has_access:
            markup.add(
                types.InlineKeyboardButton("💰 Купить подписку (49₽/месяц)", callback_data="buy_subscription"),
                types.InlineKeyboardButton("📞 Связаться с администратором", callback_data="contact_admin")
            )

            reply_text = (
                "🚫 *У вас нет активной подписки*\n\n"
                "📅 *Доступ ограничен:*\n"
                "• ❌ Скачивание музыки недоступно\n"
                "• ❌ Поиск работает с ограничениями\n\n"
                "💡 *Как получить доступ:*\n"
                "1. 💰 Купите подписку за 49₽/месяц\n"
                "2. 📞 Свяжитесь с администратором\n"
                "3. 🎁 Если у вас есть промокод, используйте `/promo КОД`\n\n"
                "✨ *После оформления подписки откроются все функции бота!*"
            )

            bot.reply_to(message, reply_text, parse_mode='Markdown', reply_markup=markup)
        else:
            markup.add(
                types.InlineKeyboardButton("📊 Статистика", callback_data="stats"),
            )

            if user_id in ADMIN_IDS:
                subscription_info = (
                    "✅ *АДМИНИСТРАТОРСКАЯ ПОДПИСКА*\n\n"
                    "⚡ *Вам доступны все функции бота!*\n\n"
                    "🚀 *Статус:* ВЕЧНАЯ АДМИНИСТРАТОРСКАЯ подписка\n"
                )
            else:
                subscription_info = "✅ *Подписка активна*\n\n"

            reply_text = f"{subscription_info}\n"
            reply_text += (
                "✨ *Ваши возможности:*\n"
                "• ✅ Скачивание музыки из YouTube\n"
                "• ✅ Скачивание из Яндекс.Музыки\n"
                "• ✅ Быстрая загрузка\n"
                "• ✅ Автоматическая сортировка\n\n"
                "Что вы хотите сделать?"
            )

            bot.reply_to(message, reply_text, parse_mode='Markdown', reply_markup=markup)

    except Exception as e:
        print(f"[ERROR] Ошибка в обработчике подписки: {e}")
        traceback.print_exc()
        try:
            bot.reply_to(message,
                         "⚠️ Произошла ошибка при проверке подписки.",
                         parse_mode='Markdown')
        except:
            pass


@bot.message_handler(func=lambda message: bool(message.text) and 'Mini App' in message.text)
def open_mini_app(message):
    markup = build_mini_app_markup()
    if not markup:
        bot.reply_to(message, 'Mini App пока не настроен.')
        return

    bot.reply_to(message, 'Откройте Mini App:', reply_markup=markup)


@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    """Начальное приветствие."""
    try:
        user_id = message.from_user.id
        username = message.from_user.username
        first_name = message.from_user.first_name
        safe_first_name = escape_markdown(first_name or 'друг')

        database.add_user(
            user_id=user_id,
            username=username,
            first_name=first_name,
            last_name=message.from_user.last_name,
            language_code=message.from_user.language_code,
            is_premium=message.from_user.is_premium if hasattr(message.from_user, 'is_premium') else False
        )

        keyboard = build_main_menu_keyboard()
        is_admin = user_id in ADMIN_IDS

        features = [
            '• 🔍 *Автопоиск* — отправьте название песни или исполнителя',
            '• 🎵 *Яндекс.Музыка* — поиск, скачивание и синхронизация лайков',
            '• 📺 *YouTube* — поиск и скачивание треков',
            '• 📝 *Текст песни* — поиск текста через Яндекс и Genius',
        ]
        if ENABLE_VK:
            features.append('• 🎧 *VK Music* — поиск и скачивание через VK')
        if MINI_APP_URL:
            features.append('• 🚀 *Mini App* — отдельный интерфейс с плеером, очередью и библиотекой')

        commands = [
            '• /subscribe — информация о подписке',
            '• /status — проверка подключений',
            '• /clear_cache — очистить кэш',
        ]
        if is_admin:
            commands.extend([
                '• /admin_create_promo — создать промокод',
                '• /admin_stats — статистика бота',
            ])

        header = '⚡ *Вы администратор!* Вам доступны все функции бота.\n\n' if is_admin else ''
        welcome_text = (
            f"🎵 *Привет, {safe_first_name}!*\n\n"
            f"{header}"
            "*Добро пожаловать в KSB Music Bot!*\n\n"
            "*Возможности:*\n"
            + "\n".join(features)
            + "\n\n*Команды:*\n"
            + "\n".join(commands)
            + "\n\n🚀 *Начните с поиска музыки!*"
        )

        bot.reply_to(
            message,
            welcome_text,
            parse_mode='Markdown',
            disable_web_page_preview=True,
            reply_markup=keyboard
        )

        mini_app_markup = build_mini_app_markup()
        if mini_app_markup:
            bot.send_message(
                message.chat.id,
                '🚀 *Mini App готов.*\n\nОткройте плеер и библиотеку в отдельном интерфейсе.',
                parse_mode='Markdown',
                reply_markup=mini_app_markup
            )

        has_access, _ = database.check_subscription(user_id)
        if not has_access and user_id not in ADMIN_IDS:
            time.sleep(1)
            bot.send_message(
                message.chat.id,
                '🔒 *Доступ к полному функционалу откроется после подписки или промокода.*\n\nИспользуйте /subscribe или /promo <код>.',
                parse_mode='Markdown'
            )

    except Exception as e:
        print(f"[ERROR] Ошибка в send_welcome: {e}")
        traceback.print_exc()
        bot.reply_to(
            message,
            'Добро пожаловать! Используйте кнопки меню для навигации.',
            reply_markup=build_main_menu_keyboard()
        )


@bot.message_handler(commands=['status', 'check'])
def handle_status(message):
    """Показывает статус подключения к сервисам."""
    status_text = "📊 *Статус подключений бота*\n\n"

    if ym_client:
        try:
            ym_client.me.account_status()
            status_text += "✅ *Яндекс.Музыка*: Авторизован\n"
        except Exception:
            status_text += "❌ *Яндекс.Музыка*: Ошибка авторизации\n"
    else:
        status_text += "⚠️ *Яндекс.Музыка*: Токен не указан\n"

    status_text += "✅ *YouTube*: Сервис доступен\n"

    if ENABLE_VK:
        if vk_audio:
            status_text += "✅ *VK Music*: Технический аккаунт подключен\n"
        else:
            status_text += "⚠️ *VK Music*: Не настроен\n"

    status_text += "✅ *Тексты песен*: Яндекс.Музыка -> Genius\n"

    music_files = len(get_folder_files(MUSIC_DIR))
    podcast_files = len(get_folder_files(PODCASTS_DIR))

    status_text += "\n📊 *Статистика файлов:*\n"
    status_text += f"• 🎵 Музыка: {music_files} файлов\n"
    status_text += f"• 🎙️ Подкасты: {podcast_files} файлов\n"

    status_text += "\n📁 *Пути к папкам:*\n"
    status_text += f"• Музыка: `{os.path.abspath(MUSIC_DIR)}`\n"
    status_text += f"• Подкасты: `{os.path.abspath(PODCASTS_DIR)}`\n"

    bot.reply_to(message, status_text, parse_mode='Markdown')


# ============================================
# РђР”РњРРќРРЎРўР РђРўРР’РќР«Р• РљРћРњРђРќР”Р«
# ============================================

@bot.message_handler(commands=['admin_create_promo'])
def handle_create_promo(message):
    """РЎРѕР·РґР°РЅРёРµ РїСЂРѕРјРѕРєРѕРґР° (С‚РѕР»СЊРєРѕ РґР»СЏ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂРѕРІ)"""
    try:
        # РџСЂРѕРІРµСЂРєР° РїСЂР°РІ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°
        user_id = message.from_user.id
        if user_id not in ADMIN_IDS:
            bot.reply_to(message, "вќЊ РЈ РІР°СЃ РЅРµС‚ РїСЂР°РІ РґР»СЏ РІС‹РїРѕР»РЅРµРЅРёСЏ СЌС‚РѕР№ РєРѕРјР°РЅРґС‹.")
            return

        # РџР°СЂСЃРёРЅРі РєРѕРјР°РЅРґС‹
        parts = message.text.split()

        if len(parts) < 3:
            bot.reply_to(message,
                         "рџ“ќ *РСЃРїРѕР»СЊР·РѕРІР°РЅРёРµ:*\n"
                         "`/admin_create_promo РљРћР” РўРРџ [РњРђРљРЎ_РРЎРџРћР›Р¬Р—РћР’РђРќРР™] [РћРџРРЎРђРќРР•]`\n\n"
                         "вљ пёЏ *Р’РЅРёРјР°РЅРёРµ:* Р’СЃРµ РїСЂРѕРјРѕРєРѕРґС‹ РґРµР№СЃС‚РІСѓСЋС‚ 30 РґРЅРµР№!\n\n"
                         "*РџСЂРёРјРµСЂС‹:*\n"
                         "вЂў `/admin_create_promo WELCOME premium 100 РџСЂРёРІРµС‚СЃС‚РІРµРЅРЅС‹Р№ РєРѕРґ`\n"
                         "вЂў `/admin_create_promo SPECIAL premium 10 РЎРїРµС†РёР°Р»СЊРЅР°СЏ Р°РєС†РёСЏ`\n"
                         "вЂў `/admin_create_promo TEST premium 5 РўРµСЃС‚РѕРІС‹Р№ РїСЂРѕРјРѕРєРѕРґ`\n\n"
                         "*Р•РґРёРЅСЃС‚РІРµРЅРЅС‹Р№ С‚РёРї РїРѕРґРїРёСЃРєРё:* premium\n"
                         "*РЎСЂРѕРє РґРµР№СЃС‚РІРёСЏ:* 30 РґРЅРµР№ (С„РёРєСЃРёСЂРѕРІР°РЅРѕ)",
                         parse_mode='Markdown')
            return

        promo_code = parts[1].upper()
        sub_type = parts[2].lower()

        # РџСЂРѕРІРµСЂСЏРµРј С‚РёРї РїРѕРґРїРёСЃРєРё
        if sub_type != 'premium':
            bot.reply_to(message, "вќЊ РќРµРІРµСЂРЅС‹Р№ С‚РёРї РїРѕРґРїРёСЃРєРё. Р”РѕРїСѓСЃС‚РёРјС‹Р№: premium")
            return

        max_uses = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1

        # Р’СЃРµРіРґР° 30 РґРЅРµР№, РЅРµ РїСЂРёРЅРёРјР°РµРј РїР°СЂР°РјРµС‚СЂ РґРЅРµР№
        # РЎРѕР±РёСЂР°РµРј РѕРїРёСЃР°РЅРёРµ
        description = ' '.join(parts[4:]) if len(parts) > 4 else None

        # РЎРѕР·РґР°РЅРёРµ РїСЂРѕРјРѕРєРѕРґР° (РІСЃРµРіРґР° РЅР° 30 РґРЅРµР№)
        result = database.create_promo_code(
            code=promo_code,
            subscription_type=sub_type,
            max_uses=max_uses,
            days_valid=30,  # Р¤РёРєСЃРёСЂРѕРІР°РЅРЅРѕРµ Р·РЅР°С‡РµРЅРёРµ
            description=description
        )

        if result['success']:
            bot.reply_to(message, result['message'], parse_mode='Markdown')
        else:
            bot.reply_to(message, result['message'], parse_mode='Markdown')

    except Exception as e:
        print(f"[ERROR] РћС€РёР±РєР° СЃРѕР·РґР°РЅРёСЏ РїСЂРѕРјРѕРєРѕРґР°: {e}")
        traceback.print_exc()
        bot.reply_to(message, f"вќЊ РћС€РёР±РєР°: {str(e)}")


@bot.message_handler(commands=['admin_stats'])
def handle_admin_stats(message):
    """РЎС‚Р°С‚РёСЃС‚РёРєР° Р±РѕС‚Р° (Р°РґРјРёРЅС‹)"""
    try:
        user_id = message.from_user.id
        if user_id not in ADMIN_IDS:
            bot.reply_to(message, "вќЊ РЈ РІР°СЃ РЅРµС‚ РїСЂР°РІ РґР»СЏ РІС‹РїРѕР»РЅРµРЅРёСЏ СЌС‚РѕР№ РєРѕРјР°РЅРґС‹.")
            return

        # РџРѕР»СѓС‡Р°РµРј СЃС‚Р°С‚РёСЃС‚РёРєСѓ
        active_users = database.get_active_users_count()
        total_downloads = database.get_total_downloads()
        all_promos = database.get_all_promo_codes()

        # РЎС‚Р°С‚РёСЃС‚РёРєР° РїРѕ РїСЂРѕРјРѕРєРѕРґР°Рј
        active_promos = [p for p in all_promos if p['is_active']]
        used_promos = sum(p['uses_count'] for p in all_promos)
        total_promos_created = len(all_promos)

        stats_text = (
            "рџ“Љ *РЎС‚Р°С‚РёСЃС‚РёРєР° Р±РѕС‚Р°*\n\n"
            f"рџ‘Ґ *РџРѕР»СЊР·РѕРІР°С‚РµР»Рё:*\n"
            f"вЂў РђРєС‚РёРІРЅС‹Рµ (30 РґРЅРµР№): {active_users}\n\n"
            f"рџ“Ґ *РЎРєР°С‡РёРІР°РЅРёСЏ:*\n"
            f"вЂў Р’СЃРµРіРѕ: {total_downloads}\n\n"
            f"рџЋЃ *РџСЂРѕРјРѕРєРѕРґС‹:*\n"
            f"вЂў Р’СЃРµРіРѕ СЃРѕР·РґР°РЅРѕ: {total_promos_created}\n"
            f"вЂў РђРєС‚РёРІРЅС‹С…: {len(active_promos)}\n"
            f"вЂў РСЃРїРѕР»СЊР·РѕРІР°РЅРѕ СЂР°Р·: {used_promos}\n\n"
            f"рџ’ѕ *РљСЌС€:*\n"
            f"вЂў РњСѓР·С‹РєР°: {len(get_folder_files(MUSIC_DIR))} С„Р°Р№Р»РѕРІ\n"
            f"вЂў РџРѕРґРєР°СЃС‚С‹: {len(get_folder_files(PODCASTS_DIR))} С„Р°Р№Р»РѕРІ\n\n"
            f"вЏ° *Р’СЂРµРјСЏ СЂР°Р±РѕС‚С‹:* {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )

        # Р”РѕР±Р°РІР»СЏРµРј СЃРїРёСЃРѕРє Р°РєС‚РёРІРЅС‹С… РїСЂРѕРјРѕРєРѕРґРѕРІ
        if active_promos:
            stats_text += "\n\nрџЋ« *РђРєС‚РёРІРЅС‹Рµ РїСЂРѕРјРѕРєРѕРґС‹:*\n"
            for promo in active_promos[:10]:  # РџРѕРєР°Р·С‹РІР°РµРј РїРµСЂРІС‹Рµ 10
                expiry = promo['expiry_date'].split()[0] if promo['expiry_date'] else "Р±РµСЃСЃСЂРѕС‡РЅРѕ"
                stats_text += f"вЂў `{promo['code']}` - {promo['subscription_type']} ({promo['uses_count']}/{promo['max_uses']}) РґРѕ {expiry}\n"
            if len(active_promos) > 10:
                stats_text += f"вЂў ... Рё РµС‰Рµ {len(active_promos) - 10}"

        bot.reply_to(message, stats_text, parse_mode='Markdown')

    except Exception as e:
        print(f"[ERROR] РћС€РёР±РєР° СЃС‚Р°С‚РёСЃС‚РёРєРё: {e}")
        traceback.print_exc()
        bot.reply_to(message, f"вќЊ РћС€РёР±РєР°: {str(e)}")


# ============================================
# РћРЎРќРћР’РќР«Р• РљРћРњРђРќР”Р« Р‘РћРўРђ
# ============================================

@bot.message_handler(commands=['clear_cache', 'clear'])
def handle_clear_cache(message):
    """РћС‡РёС‰Р°РµС‚ РєСЌС€ С„Р°Р№Р»РѕРІ"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("вњ… Р”Р°, РѕС‡РёСЃС‚РёС‚СЊ РІСЃС‘", callback_data="clear_cache_confirm"),
        types.InlineKeyboardButton("вќЊ РќРµС‚, РѕС‚РјРµРЅРёС‚СЊ", callback_data="clear_cache_cancel")
    )
    bot.reply_to(message,
                 "вљ пёЏ *Р’РЅРёРјР°РЅРёРµ!*\n\n"
                 "Р’С‹ СѓРІРµСЂРµРЅС‹, С‡С‚Рѕ С…РѕС‚РёС‚Рµ СѓРґР°Р»РёС‚СЊ Р’РЎР• С„Р°Р№Р»С‹ РёР· РєСЌС€Р°?\n\n"
                 "рџ—‘пёЏ *Р‘СѓРґРµС‚ СѓРґР°Р»РµРЅРѕ:*\n"
                 "вЂў Р’СЃРµ СЃРєР°С‡Р°РЅРЅС‹Рµ С‚СЂРµРєРё\n"
                 "вЂў Р’СЃРµ РїРѕРґРєР°СЃС‚С‹\n\n"
                 "вљЎ *Р­С‚Рѕ РґРµР№СЃС‚РІРёРµ РЅРµР»СЊР·СЏ РѕС‚РјРµРЅРёС‚СЊ!*",
                 parse_mode='Markdown',
                 reply_markup=markup)


@bot.message_handler(commands=['search_all', 'search'])
def handle_search_all(message):
    """Handles a combined search across all supported sources."""
    has_access, _ = ensure_subscription_access(message.from_user.id, message.chat.id, reply_target=message)
    if not has_access:
        return
    query = message.text.replace('/search_all', '').replace('/search', '').strip()
    process_search_query(message.chat.id, query, is_command=True)


@bot.message_handler(commands=['search_yandex'])
def handle_search_yandex(message):
    """Handles a search request in Yandex Music."""
    if not ym_client:
        bot.reply_to(message, "Yandex Music is not configured.")
        return

    has_access, _ = ensure_subscription_access(message.from_user.id, message.chat.id, reply_target=message)
    if not has_access:
        return

    query = message.text.replace('/search_yandex', '').strip()

    if not query:
        bot.reply_to(message, "рџ“ќ РСЃРїРѕР»СЊР·РѕРІР°РЅРёРµ: `/search_yandex <Р·Р°РїСЂРѕСЃ>`", parse_mode='Markdown')
        return

    wait_msg = bot.reply_to(message, f"рџЋµ РС‰Сѓ '{query}' РІ РЇРЅРґРµРєСЃ.РњСѓР·С‹РєРµ...")

    results = search_yandex_music(query, limit=SEARCH_RESULTS_PER_SOURCE)

    if not results:
        bot.edit_message_text(f"вќЊ РџРѕ Р·Р°РїСЂРѕСЃСѓ '{query}' РЅРёС‡РµРіРѕ РЅРµ РЅР°Р№РґРµРЅРѕ.",
                              chat_id=message.chat.id,
                              message_id=wait_msg.message_id)
        return

    message_text = show_search_results(message.chat.id, query, results, page=0)
    keyboard = create_search_keyboard(results, page=0, show_all_button=False)

    try:
        bot.edit_message_text(message_text,
                              chat_id=message.chat.id,
                              message_id=wait_msg.message_id,
                              parse_mode='Markdown',
                              reply_markup=keyboard)
    except Exception as e:
        print(f"[!] РћС€РёР±РєР° РѕС‚РїСЂР°РІРєРё СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ: {e}")
        bot.edit_message_text(f"вњ… РќР°Р№РґРµРЅРѕ {len(results)} СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ. РСЃРїРѕР»СЊР·СѓР№С‚Рµ РєРЅРѕРїРєРё РЅРёР¶Рµ РґР»СЏ РІС‹Р±РѕСЂР°.",
                              chat_id=message.chat.id,
                              message_id=wait_msg.message_id,
                              reply_markup=keyboard)


@bot.message_handler(commands=['search_youtube', 'youtube'])
def handle_search_youtube(message):
    """Handles a search request on YouTube."""
    has_access, _ = ensure_subscription_access(message.from_user.id, message.chat.id, reply_target=message)
    if not has_access:
        return

    query = message.text.replace('/search_youtube', '').replace('/youtube', '').strip()

    if not query:
        bot.reply_to(message, "рџ“ќ РСЃРїРѕР»СЊР·РѕРІР°РЅРёРµ: `/search_youtube <Р·Р°РїСЂРѕСЃ>`", parse_mode='Markdown')
        return

    wait_msg = bot.reply_to(message, f"рџ“є РС‰Сѓ '{query}' РЅР° YouTube...")

    results = search_youtube_music(query, limit=SEARCH_RESULTS_PER_SOURCE)

    if not results:
        bot.edit_message_text(f"вќЊ РџРѕ Р·Р°РїСЂРѕСЃСѓ '{query}' РЅРёС‡РµРіРѕ РЅРµ РЅР°Р№РґРµРЅРѕ РЅР° YouTube.",
                              chat_id=message.chat.id,
                              message_id=wait_msg.message_id)
        return

    message_text = show_search_results(message.chat.id, query, results, page=0)
    keyboard = create_search_keyboard(results, page=0, show_all_button=False)

    try:
        bot.edit_message_text(message_text,
                              chat_id=message.chat.id,
                              message_id=wait_msg.message_id,
                              parse_mode='Markdown',
                              reply_markup=keyboard)
    except Exception as e:
        print(f"[!] РћС€РёР±РєР° РѕС‚РїСЂР°РІРєРё СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ: {e}")
        bot.edit_message_text(f"вњ… РќР°Р№РґРµРЅРѕ {len(results)} СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ. РСЃРїРѕР»СЊР·СѓР№С‚Рµ РєРЅРѕРїРєРё РЅРёР¶Рµ РґР»СЏ РІС‹Р±РѕСЂР°.",
                              chat_id=message.chat.id,
                              message_id=wait_msg.message_id,
                              reply_markup=keyboard)


# ============================================
# РћР‘Р РђР‘РћРўР§РРљР РљРќРћРџРћРљ РњР•РќР®
# ============================================

@bot.message_handler(commands=['search_vk', 'vk'])
def handle_search_vk(message):
    """Handles a search request in VK Music."""
    if not ENABLE_VK:
        bot.reply_to(message, "VK-РїРѕРёСЃРє РѕС‚РєР»СЋС‡РµРЅ РІ СЌС‚РѕР№ РІРµСЂСЃРёРё Р±РѕС‚Р°.")
        return

    if not vk_audio:
        bot.reply_to(message, "VK Music is not configured.")
        return

    has_access, _ = ensure_subscription_access(message.from_user.id, message.chat.id, reply_target=message)
    if not has_access:
        return

    query = message.text.replace('/search_vk', '').replace('/vk', '').strip()

    if not query:
        bot.reply_to(message, "рџ“ќ РСЃРїРѕР»СЊР·РѕРІР°РЅРёРµ: `/search_vk <Р·Р°РїСЂРѕСЃ>`", parse_mode='Markdown')
        return

    wait_msg = bot.reply_to(message, f"рџЋ§ РС‰Сѓ '{query}' РІ VK Music...")
    results = search_vk_music(query, limit=SEARCH_RESULTS_PER_SOURCE)

    if not results:
        bot.edit_message_text(f"вќЊ РџРѕ Р·Р°РїСЂРѕСЃСѓ '{query}' РЅРёС‡РµРіРѕ РЅРµ РЅР°Р№РґРµРЅРѕ РІ VK Music.",
                              chat_id=message.chat.id,
                              message_id=wait_msg.message_id)
        return

    message_text = show_search_results(message.chat.id, query, results, page=0)
    keyboard = create_search_keyboard(results, page=0, show_all_button=False)

    try:
        bot.edit_message_text(message_text,
                              chat_id=message.chat.id,
                              message_id=wait_msg.message_id,
                              parse_mode='Markdown',
                              reply_markup=keyboard)
    except Exception as e:
        print(f"[!] РћС€РёР±РєР° РѕС‚РїСЂР°РІРєРё VK СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ: {e}")
        bot.edit_message_text(f"вњ… РќР°Р№РґРµРЅРѕ {len(results)} СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ. РСЃРїРѕР»СЊР·СѓР№С‚Рµ РєРЅРѕРїРєРё РЅРёР¶Рµ РґР»СЏ РІС‹Р±РѕСЂР°.",
                              chat_id=message.chat.id,
                              message_id=wait_msg.message_id,
                              reply_markup=keyboard)


def parse_yandex_playlist_url(url):
    if not url or 'music.yandex' not in url:
        return None

    parsed = urlparse(url.strip())
    match = re.search(r'/users/([^/]+)/playlists/(\d+)', parsed.path)
    if match:
        return match.group(1), match.group(2)

    query_params = parse_qs(parsed.query)
    owner = (query_params.get('owner') or [None])[0]
    kind = (query_params.get('kinds') or query_params.get('kind') or [None])[0]
    if owner and kind:
        return owner, str(kind)
    return None


def download_yandex_playlist_by_url(url, chat_id, user_id, progress_message_id=None):
    if not ym_client:
        return False, "Клиент Яндекс.Музыки не настроен."

    parsed_playlist = parse_yandex_playlist_url(url)
    if not parsed_playlist:
        return False, "Не удалось распознать ссылку на плейлист Яндекс.Музыки."

    owner, kind = parsed_playlist
    try:
        with ym_client_lock:
            playlist = ym_client.users_playlists(kind=kind, user_id=owner)
        if not playlist:
            return False, "Плейлист не найден."
        tracks = playlist.fetch_tracks() or []
    except Exception as e:
        return False, f"Не удалось загрузить плейлист: {e}"

    total_tracks = len(tracks)
    downloaded_count = 0
    failed_count = 0

    for index, item in enumerate(tracks, start=1):
        track = getattr(item, "track", None)
        if track is None and hasattr(item, "fetch_track"):
            try:
                track = item.fetch_track()
            except Exception:
                track = None

        if not track or not getattr(track, "id", None):
            failed_count += 1
            continue

        album_id = track.albums[0].id if getattr(track, "albums", None) else int(getattr(item, "album_id", 0) or 0)
        audio_path, title, performer, status = download_yandex_track_fast(int(track.id), int(album_id or 0))
        if status == "success" and audio_path:
            database.increment_download(user_id)
            file_type = "подкаст" if audio_path.startswith(PODCASTS_DIR) else "музыка"
            caption = f"🎵 {title} (Яндекс.Музыка) | 📁 {file_type}"
            if send_audio_fast(
                chat_id=chat_id,
                audio_path=audio_path,
                title=title[:64],
                performer=performer[:64],
                caption=caption,
            ):
                downloaded_count += 1
            else:
                failed_count += 1
        else:
            failed_count += 1

        if progress_message_id and (index == 1 or index % 5 == 0 or index == total_tracks):
            safe_edit_message_text(
                f"📦 Загружаю плейлист...\n\nОбработано: {index}/{total_tracks}\n"
                f"Успешно: {downloaded_count}\nОшибок: {failed_count}",
                chat_id=chat_id,
                message_id=progress_message_id,
            )

    playlist_title = getattr(playlist, "title", "Плейлист")
    return True, (
        f"✅ Плейлист загружен: {playlist_title}\n\n"
        f"Всего треков: {total_tracks}\n"
        f"Успешно отправлено: {downloaded_count}\n"
        f"Ошибок: {failed_count}"
    )


@bot.message_handler(func=lambda m: m.text and any(x in m.text for x in ['music.yandex', 'youtube.com', 'youtu.be']))
def handle_music_link(message):
    """РћР±СЂР°Р±Р°С‚С‹РІР°РµС‚ РїСЂСЏРјС‹Рµ СЃСЃС‹Р»РєРё РЅР° РјСѓР·С‹РєСѓ"""
    try:
        # РџСЂРѕРІРµСЂРєР° РґРѕСЃС‚СѓРїР°
        user_id = message.from_user.id
        has_access, msg = database.check_subscription(user_id)
        if not has_access:
            bot.reply_to(message,
                         f"рџљ« *Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰РµРЅ!*\n\n"
                         f"{msg}\n\n"
                         f"рџ’Ў Р”Р»СЏ РґРѕСЃС‚СѓРїР° Рє СЃРєР°С‡РёРІР°РЅРёСЋ:\n"
                         f"вЂў РСЃРїРѕР»СЊР·СѓР№С‚Рµ РєРѕРјР°РЅРґСѓ /subscribe\n"
                         f"вЂў РђРєС‚РёРІРёСЂСѓР№С‚Рµ РїСЂРѕРјРѕРєРѕРґ /promo РљРћР”\n"
                         f"вЂў РћР±СЂР°С‚РёС‚РµСЃСЊ Рє Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂСѓ",
                         parse_mode='Markdown')
            return

        wait_msg = bot.reply_to(message, "рџ”— РђРЅР°Р»РёР·РёСЂСѓСЋ СЃСЃС‹Р»РєСѓ...")
        url = message.text.strip()

        if 'music.yandex' in url:
            import re
            playlist_match = parse_yandex_playlist_url(url)
            if playlist_match:
                safe_edit_message_text("📦 Загружаю плейлист Яндекс.Музыки...", chat_id=message.chat.id, message_id=wait_msg.message_id)
                success, playlist_message = download_yandex_playlist_by_url(
                    url=url,
                    chat_id=message.chat.id,
                    user_id=user_id,
                    progress_message_id=wait_msg.message_id,
                )
                safe_edit_message_text(
                    playlist_message if success else f"❌ {playlist_message}",
                    chat_id=message.chat.id,
                    message_id=wait_msg.message_id,
                )
                return

            match = re.search(r'music\.yandex\.\w+/album/(\d+)/track/(\d+)', url)
            if match:
                album_id, track_id = match.groups()
                audio_path, title, performer, status = download_yandex_track_fast(int(track_id), int(album_id))
                if status == "success" and audio_path:
                    # РЈРІРµР»РёС‡РёРІР°РµРј СЃС‡РµС‚С‡РёРє СЃРєР°С‡РёРІР°РЅРёР№
                    database.increment_download(user_id)

                    file_type = "РїРѕРґРєР°СЃС‚" if audio_path.startswith(PODCASTS_DIR) else "РјСѓР·С‹РєР°"
                    caption = f"рџЋµ {title} (РЇРЅРґРµРєСЃ.РњСѓР·С‹РєР°) | рџ“Ѓ {file_type}"

                    success = send_audio_fast(
                        chat_id=message.chat.id,
                        audio_path=audio_path,
                        title=title[:64],
                        performer=performer[:64],
                        caption=caption
                    )

                    if success:
                        bot.delete_message(message.chat.id, wait_msg.message_id)
                    else:
                        bot.edit_message_text("вќЊ РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РїСЂР°РІРёС‚СЊ Р°СѓРґРёРѕ.",
                                              chat_id=message.chat.id,
                                              message_id=wait_msg.message_id)
                    return
            bot.edit_message_text(f"вќЊ РќРµ СѓРґР°Р»РѕСЃСЊ РѕР±СЂР°Р±РѕС‚Р°С‚СЊ РЇРЅРґРµРєСЃ-СЃСЃС‹Р»РєСѓ",
                                  chat_id=message.chat.id,
                                  message_id=wait_msg.message_id)

        elif 'youtube.com' in url or 'youtu.be' in url:
            bot.edit_message_text("рџ“Ґ РЎРєР°С‡РёРІР°СЋ СЃ YouTube...",
                                  chat_id=message.chat.id,
                                  message_id=wait_msg.message_id)

            audio_path, title, performer, status = download_from_youtube_fast(url, is_url=True)

            if status == "success" and audio_path:
                # РЈРІРµР»РёС‡РёРІР°РµРј СЃС‡РµС‚С‡РёРє СЃРєР°С‡РёРІР°РЅРёР№
                database.increment_download(user_id)

                file_type = "РїРѕРґРєР°СЃС‚" if audio_path.startswith(PODCASTS_DIR) else "РјСѓР·С‹РєР°"
                caption = f"рџЋµ {title} (YouTube) | рџ“Ѓ {file_type}"

                success = send_audio_fast(
                    chat_id=message.chat.id,
                    audio_path=audio_path,
                    title=title[:64],
                    performer=performer[:64],
                    caption=caption
                )

                if success:
                    bot.delete_message(message.chat.id, wait_msg.message_id)
                else:
                    bot.edit_message_text("вќЊ РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РїСЂР°РІРёС‚СЊ Р°СѓРґРёРѕ.",
                                          chat_id=message.chat.id,
                                          message_id=wait_msg.message_id)
                return
            else:
                error_msg = "вќЊ РћС€РёР±РєР° Р·Р°РіСЂСѓР·РєРё"
                bot.edit_message_text(f"{error_msg}: {status}",
                                      chat_id=message.chat.id,
                                      message_id=wait_msg.message_id)
        else:
            bot.edit_message_text(f"вќЊ Р¤РѕСЂРјР°С‚ СЃСЃС‹Р»РєРё РЅРµ РїРѕРґРґРµСЂР¶РёРІР°РµС‚СЃСЏ",
                                  chat_id=message.chat.id,
                                  message_id=wait_msg.message_id)
    except Exception as e:
        print(f"[!] РћС€РёР±РєР° РІ РѕР±СЂР°Р±РѕС‚С‡РёРєРµ СЃСЃС‹Р»РєРё: {e}")
        traceback.print_exc()
        try:
            bot.reply_to(message, f"вќЊ РћС€РёР±РєР°: {str(e)[:100]}")
        except:
            pass


@bot.message_handler(func=lambda message: message.text == '🎵 Мне понравилось')
def handle_liked_button(message):
    if not ym_client:
        bot.reply_to(
            message,
            "\u274c \u042f\u043d\u0434\u0435\u043a\u0441.\u041c\u0443\u0437\u044b\u043a\u0430 \u043d\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0435\u043d\u0430. \u0414\u043e\u0431\u0430\u0432\u044c\u0442\u0435 `YANDEX_MUSIC_TOKEN` \u0438 \u043f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u0441\u043d\u043e\u0432\u0430.",
            parse_mode='Markdown'
        )
        return

    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        bot.reply_to(
            message,
            "❌ Синхронизация раздела *«Мне понравилось»* доступна только администратору, потому что использует один общий аккаунт Яндекс.Музыки бота.",
            parse_mode='Markdown'
        )
        return

    library_chat_id = ADMIN_CONTACT_ID or message.chat.id
    with liked_sync_state_lock:
        if user_id in active_liked_sync_users:
            bot.reply_to(
                message,
                "\u23f3 \u0421\u0438\u043d\u0445\u0440\u043e\u043d\u0438\u0437\u0430\u0446\u0438\u044f \u0440\u0430\u0437\u0434\u0435\u043b\u0430 *\u00ab\u041c\u043d\u0435 \u043f\u043e\u043d\u0440\u0430\u0432\u0438\u043b\u043e\u0441\u044c\u00bb* \u0443\u0436\u0435 \u0438\u0434\u0435\u0442. \u0414\u043e\u0436\u0434\u0438\u0442\u0435\u0441\u044c \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u0438\u044f \u0442\u0435\u043a\u0443\u0449\u0435\u0439 \u0437\u0430\u0434\u0430\u0447\u0438.",
                parse_mode='Markdown'
            )
            return
        active_liked_sync_users.add(user_id)

    try:
        wait_msg = bot.reply_to(
            message,
            "\U0001F3B5 *\u0421\u0438\u043d\u0445\u0440\u043e\u043d\u0438\u0437\u0438\u0440\u0443\u044e \u0442\u0440\u0435\u043a\u0438 \u0438\u0437 \u0440\u0430\u0437\u0434\u0435\u043b\u0430 \u00ab\u041c\u043d\u0435 \u043f\u043e\u043d\u0440\u0430\u0432\u0438\u043b\u043e\u0441\u044c\u00bb...*\n\n"
            "\u042d\u0442\u043e \u043c\u043e\u0436\u0435\u0442 \u0437\u0430\u043d\u044f\u0442\u044c \u043d\u0435\u043a\u043e\u0442\u043e\u0440\u043e\u0435 \u0432\u0440\u0435\u043c\u044f, \u0435\u0441\u043b\u0438 \u043b\u0430\u0439\u043a\u043e\u0432 \u043c\u043d\u043e\u0433\u043e.",
            parse_mode='Markdown'
        )
    except Exception:
        finish_liked_sync(user_id)
        raise

    threading.Thread(
        target=run_liked_sync,
        args=(message.chat.id, user_id, wait_msg.message_id, library_chat_id),
        daemon=True
    ).start()

@bot.message_handler(func=lambda message: message.text == '🔍 Поиск музыки')
def handle_search_button(message):
    bot.reply_to(message,
                 "🔍 *Поиск музыки*\n\n"
                 "🎵 *Просто отправьте в чат:*\n"
                 "• название песни\n"
                 "• имя исполнителя\n"
                 "• ссылку на трек\n\n"
                 "⚡ *Автоматический поиск работает сразу по всем источникам.*\n\n"
                 "💡 *Примеры:*\n"
                 "• `Shape of You`\n"
                 "• `Imagine Dragons Believer`\n"
                 "• `https://youtube.com/...`\n\n"
                 "🎯 *Команды:*\n"
                 "• `/search_all <запрос>` — искать везде\n"
                 "• `/search_yandex <запрос>` — только Яндекс\n"
                 "• `/search_youtube <запрос>` — только YouTube"
                 + ("\n• `/search_vk <запрос>` — только VK" if ENABLE_VK else ""),
                 parse_mode='Markdown')


@bot.message_handler(func=lambda message: message.text == '📺 YouTube')
def handle_youtube_button(message):
    bot.reply_to(message,
                 "📺 *YouTube Музыка*\n\n"
                 "🎵 *Как использовать:*\n"
                 "1. Отправьте название песни в чат\n"
                 "2. Или отправьте ссылку на видео\n"
                 "3. Выберите трек из результатов\n"
                 "4. Скачайте аудиофайл\n\n"
                 "🔗 *Поддерживаемые ссылки:*\n"
                 "• Видео: `youtube.com/watch?...`\n"
                 "• Короткие: `youtu.be/...`\n"
                 "• Плейлисты: первое видео из плейлиста\n\n"
                 "⚡ *Пример:* `Shape of You`",
                 parse_mode='Markdown')


@bot.message_handler(func=lambda message: message.text == '🎧 VK')
def handle_vk_button(message):
    if not vk_audio:
        bot.reply_to(
            message,
            "🎧 *VK Music пока не настроен.*\n\nДобавьте `VK_LOGIN` и `VK_PASSWORD` технического аккаунта бота в переменные окружения.",
            parse_mode='Markdown'
        )
        return

    bot.reply_to(
        message,
        "🎧 *VK Музыка*\n\nИщите треки в VK так же, как и в других источниках.\n\n*Как использовать:*\n• Отправьте `/search_vk название песни`\n• Или напишите название трека в чат, чтобы бот искал сразу везде\n\n*Пример:*\n`/search_vk Кино Группа крови`",
        parse_mode='Markdown'
    )


def build_ytdlp_base_options():
    return {
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'geo_bypass': True,
        'nocheckcertificate': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        },
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'web'],
                'player_skip': ['configs', 'webpage'],
            }
        },
    }


@bot.message_handler(func=lambda message: message.text == '📁 Музыка')
def handle_music_folder(message):
    """Показывает список музыкальных файлов."""
    files = get_folder_files(MUSIC_DIR)

    if not files:
        bot.reply_to(message,
                     "🎵 *Папка с музыкой*\n\n"
                     "📭 Папка пуста\n\n"
                     "💡 *Совет:*\n"
                     "• Отправьте название песни в чат\n"
                     "• Скачайте треки из поиска\n"
                     "• Файлы появятся здесь автоматически",
                     parse_mode='Markdown')
        return

    total_size = sum(f['size'] for f in files)
    message_text = (f"🎵 *Папка с музыкой*\n\n"
                    f"📊 *Статистика:*\n"
                    f"• Файлов: {len(files)}\n"
                    f"• Общий размер: {total_size:.2f} MB\n\n"
                    f"📁 Выберите файл для отправки:")

    keyboard = create_files_keyboard(files, page=0, folder_type="music")
    bot.reply_to(message, message_text, parse_mode='Markdown', reply_markup=keyboard)


@bot.message_handler(func=lambda message: message.text == '🎙️ Подкасты')
def handle_podcasts_folder(message):
    """Показывает список подкастов."""
    files = get_folder_files(PODCASTS_DIR)

    if not files:
        bot.reply_to(message,
                     "🎙️ *Папка с подкастами*\n\n"
                     "📭 Папка пуста\n\n"
                     "💡 *Совет:*\n"
                     "• Скачайте длинные видео с YouTube\n"
                     "• Подкасты сохраняются сюда автоматически\n"
                     "• Файлы длиннее 20 минут считаются подкастами",
                     parse_mode='Markdown')
        return

    total_size = sum(f['size'] for f in files)
    message_text = (f"🎙️ *Папка с подкастами*\n\n"
                    f"📊 *Статистика:*\n"
                    f"• Файлов: {len(files)}\n"
                    f"• Общий размер: {total_size:.2f} MB\n\n"
                    f"📁 Выберите файл для отправки:")

    keyboard = create_files_keyboard(files, page=0, folder_type="podcasts")
    bot.reply_to(message, message_text, parse_mode='Markdown', reply_markup=keyboard)


@bot.message_handler(func=lambda message: message.text == '🗑️ Очистить кэш')
def handle_clear_cache_button(message):
    handle_clear_cache(message)


@bot.message_handler(func=lambda message: message.text == '💎 Подписка')
def handle_subscribe_button(message):
    handle_subscribe(message)


@bot.message_handler(commands=['lyrics'])
def handle_lyrics_command(message):
    has_access, _ = ensure_subscription_access(message.from_user.id, message.chat.id, reply_target=message)
    if not has_access:
        return

    query = message.text.replace('/lyrics', '', 1).strip()
    if not query:
        bot.reply_to(
            message,
            "📝 *Текст песни*\n\nОтправьте название песни вместе с исполнителем.\n\n*Примеры:*\n• `/lyrics ой да oxxxymiron`\n• `текст группа крови кино`",
            parse_mode='Markdown'
        )
        return

    prompt_lyrics_source(message, query)


@bot.message_handler(func=lambda message: message.text == '📝 Текст песни')
def handle_lyrics_button(message):
    set_pending_action(message.chat.id, message.from_user.id, "lyrics_lookup")
    bot.reply_to(
        message,
        "📝 *Текст песни*\n\nПросто отправьте следующим сообщением название песни и исполнителя.\n\nПосле этого бот предложит выбрать источник: *Авто*, *Яндекс* или *Genius*.\n\n*Примеры:*\n• ой да oxxxymiron\n• группа крови кино",
        parse_mode='Markdown'
    )


@bot.message_handler(func=lambda message: message.text == '📋 Помощь')
def handle_help_button(message):
    send_welcome(message)


@bot.message_handler(commands=['menu'])
def handle_menu_command(message):
    bot.reply_to(
        message,
        "Главное меню:",
        reply_markup=build_main_menu_keyboard()
    )


@bot.message_handler(func=lambda message: is_menu_button_text(message.text))
def handle_menu_buttons_fallback(message):
    normalized_text = message.text.strip()

    if 'Мне понравилось' in normalized_text:
        return handle_liked_button(message)
    if 'Поиск музыки' in normalized_text:
        return handle_search_button(message)
    if 'YouTube' in normalized_text:
        return handle_youtube_button(message)
    if 'VK' in normalized_text:
        return handle_vk_button(message)
    if 'Музыка' in normalized_text and 'Поиск' not in normalized_text:
        return handle_music_folder(message)
    if 'Подкасты' in normalized_text:
        return handle_podcasts_folder(message)
    if 'Очистить кэш' in normalized_text:
        return handle_clear_cache_button(message)
    if 'Подписка' in normalized_text:
        return handle_subscribe_button(message)
    if 'Текст песни' in normalized_text:
        return handle_lyrics_button(message)
    if 'РџРѕРјРѕС‰СЊ' in normalized_text:
        return handle_help_button(message)


# ============================================
# Р РђРЎРЁРР¤Р РћР’РљРђ Р Р•Р§Р
# ============================================

@bot.message_handler(content_types=['voice'])
def handle_voice_passthrough(message):
    return


@bot.message_handler(content_types=['audio', 'document'])
def handle_audio_passthrough(message):
    return


# ============================================
# РђР’РўРћРњРђРўРР§Р•РЎРљРР™ РџРћРРЎРљ РџРћ РўР•РљРЎРўРћР’РћРњРЈ РЎРћРћР‘Р©Р•РќРР®
# ============================================

@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_auto_search(message):
    """РђРІС‚РѕРјР°С‚РёС‡РµСЃРєРё РёС‰РµС‚ РјСѓР·С‹РєСѓ РїРѕ Р»СЋР±РѕРјСѓ С‚РµРєСЃС‚РѕРІРѕРјСѓ СЃРѕРѕР±С‰РµРЅРёСЋ"""
    try:
        if message.text.startswith('/'):
            return

        # РЎРїРёСЃРѕРє РєРЅРѕРїРѕРє РјРµРЅСЋ, РєРѕС‚РѕСЂС‹Рµ СѓР¶Рµ РѕР±СЂР°Р±РѕС‚Р°РЅС‹ РІС‹С€Рµ
        button_texts = [
            '🎵 Мне понравилось', '🔍 Поиск музыки',
            '📁 Музыка', '🎙️ Подкасты', '🗑️ Очистить кэш',
            '💎 Подписка', '📝 Текст песни', '📋 Помощь'
        ]
        if ENABLE_VK:
            button_texts.append('🎧 VK')
        if MINI_APP_URL:
            button_texts.append('🚀 Mini App')

        if message.text in button_texts:
            return

        if any(x in message.text for x in ['music.yandex', 'youtube.com', 'youtu.be']):
            return

        query = message.text.strip()
        if len(query) < 2:
            return

        pending_action = get_pending_action(message.chat.id, message.from_user.id)
        if pending_action and pending_action.get("action") == "lyrics_lookup":
            pop_pending_action(message.chat.id, message.from_user.id)
            return prompt_lyrics_source(message, query)

        if len(query) > 100:
            bot.reply_to(message, "❌ Запрос слишком длинный. Пожалуйста, укажите более короткое название.")
            return

        lowered_query = query.lower()
        if lowered_query.startswith('текст '):
            return prompt_lyrics_source(message, query[6:].strip())

        if query.lower() in ['РїРѕРёСЃРє', 'search', 'РёСЃРєР°С‚СЊ', 'РјСѓР·С‹РєР°', 'РїРµСЃРЅСЏ']:
            return

        # Check subscription access before automatic search/download.
        user_id = message.from_user.id
        has_access, _ = ensure_subscription_access(user_id, message.chat.id, reply_target=message)
        if not has_access:
            return
            return

        # Р•СЃР»Рё РґРѕСЃС‚СѓРї РµСЃС‚СЊ - РІС‹РїРѕР»РЅСЏРµРј РїРѕРёСЃРє
        process_search_query(message.chat.id, query, is_command=False)

    except Exception as e:
        print(f"[!] РћС€РёР±РєР° РІ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРѕРј РїРѕРёСЃРєРµ: {e}")
        traceback.print_exc()


# ============================================
# РћР‘Р РђР‘РћРўР§РРљ INLINE-РљРќРћРџРћРљ
# ============================================

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    """РЈРїСЂРѕС‰РµРЅРЅС‹Р№ РѕР±СЂР°Р±РѕС‚С‡РёРє callback-Р·Р°РїСЂРѕСЃРѕРІ"""
    try:
        chat_id = call.message.chat.id
        message_id = call.message.message_id
        data = call.data

        print(f"[DEBUG] Callback received from user {call.from_user.id}: {data}")

        # РћС‚РІРµС‡Р°РµРј СЃСЂР°Р·Сѓ, С‡С‚РѕР±С‹ СѓР±СЂР°С‚СЊ "С‡Р°СЃРёРєРё"
        try:
            bot.answer_callback_query(call.id)
        except Exception as e:
            print(f"[DEBUG] Error answering callback query: {e}")

        # Р Р°Р·Р±РёСЂР°РµРј РґР°РЅРЅС‹Рµ
        if data == "new_search":
            try:
                safe_edit_message_text(
                    "рџ”Ќ *РќРѕРІС‹Р№ РїРѕРёСЃРє*\n\n"
                    "РџСЂРѕСЃС‚Рѕ РѕС‚РїСЂР°РІСЊС‚Рµ РЅР°Р·РІР°РЅРёРµ РїРµСЃРЅРё РёР»Рё РёСЃРїРѕР»РЅРёС‚РµР»СЏ РІ С‡Р°С‚!\n\n"
                    "рџЋµ *РџСЂРёРјРµСЂС‹:*\n"
                    "вЂў Shape of You\n"
                    "вЂў Imagine Dragons\n"
                    "вЂў Queen Bohemian Rhapsody\n\n"
                    "вљЎ РџРѕРёСЃРє СЂР°Р±РѕС‚Р°РµС‚ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё!",
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='Markdown'
                )
            except Exception as e:
                print(f"[ERROR] Failed to edit message for new_search: {e}")
            return

        elif data.startswith("transcribe_"):
            safe_edit_message_text(
                "рџ“ќ Р¤СѓРЅРєС†РёСЏ СЂР°СЃС€РёС„СЂРѕРІРєРё СѓР±СЂР°РЅР°. РСЃРїРѕР»СЊР·СѓР№С‚Рµ СЂР°Р·РґРµР» *РўРµРєСЃС‚ РїРµСЃРЅРё* РґР»СЏ РїРѕРёСЃРєР° С‚РµРєСЃС‚Р° РїРѕ РЅР°Р·РІР°РЅРёСЋ.",
                chat_id=chat_id,
                message_id=message_id,
                parse_mode='Markdown',
            )
            return

        elif data.startswith("lyrics_"):
            parts = data.split("_", 2)
            if len(parts) != 3:
                return

            preferred_source = parts[1]
            token = parts[2]
            request_data = pop_lyrics_request(token)
            if not request_data:
                safe_edit_message_text(
                    "вќЊ Р—Р°РїСЂРѕСЃ РЅР° РїРѕРёСЃРє С‚РµРєСЃС‚Р° СѓСЃС‚Р°СЂРµР». РћС‚РїСЂР°РІСЊС‚Рµ РЅР°Р·РІР°РЅРёРµ РїРµСЃРЅРё РµС‰Рµ СЂР°Р·.",
                    chat_id=chat_id,
                    message_id=message_id,
                )
                return

            if request_data.get("user_id") != call.from_user.id:
                bot.answer_callback_query(call.id, "Р­С‚Р° РєРЅРѕРїРєР° РЅРµ РґР»СЏ РІР°СЃ.", show_alert=True)
                pending_lyrics_requests[token] = request_data
                return

            process_lyrics_lookup(
                chat_id=chat_id,
                message_id=message_id,
                query=request_data.get("query", ""),
                preferred_source=preferred_source,
            )
            return

        elif data == "back_to_menu":
            send_welcome(call.message)
            return

        elif data == "clear_cache":
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("вњ… Р”Р°, РѕС‡РёСЃС‚РёС‚СЊ РІСЃС‘", callback_data="clear_cache_confirm"),
                types.InlineKeyboardButton("вќЊ РќРµС‚, РѕС‚РјРµРЅРёС‚СЊ", callback_data="clear_cache_cancel")
            )
            try:
                safe_edit_message_text(
                    "вљ пёЏ *Р’РЅРёРјР°РЅРёРµ!*\n\n"
                    "Р’С‹ СѓРІРµСЂРµРЅС‹, С‡С‚Рѕ С…РѕС‚РёС‚Рµ СѓРґР°Р»РёС‚СЊ Р’РЎР• С„Р°Р№Р»С‹ РёР· РєСЌС€Р°?\n\n"
                    "рџ—‘пёЏ *Р‘СѓРґРµС‚ СѓРґР°Р»РµРЅРѕ:*\n"
                    "вЂў Р’СЃРµ СЃРєР°С‡Р°РЅРЅС‹Рµ С‚СЂРµРєРё\n"
                    "вЂў Р’СЃРµ РїРѕРґРєР°СЃС‚С‹\n\n"
                    "вљЎ *Р­С‚Рѕ РґРµР№СЃС‚РІРёРµ РЅРµР»СЊР·СЏ РѕС‚РјРµРЅРёС‚СЊ!*",
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='Markdown',
                    reply_markup=markup
                )
            except Exception as e:
                print(f"[ERROR] Failed to show clear_cache dialog: {e}")
            return

        elif data == "clear_cache_confirm":
            deleted_count = clear_cache_folders()
            try:
                safe_edit_message_text(
                    f"вњ… *РљСЌС€ РѕС‡РёС‰РµРЅ!*\n\n"
                    f"рџ—‘пёЏ РЈРґР°Р»РµРЅРѕ С„Р°Р№Р»РѕРІ: *{deleted_count}*\n\n"
                    f"рџ’ѕ РўРµРїРµСЂСЊ Сѓ РІР°СЃ {deleted_count} РњР‘ СЃРІРѕР±РѕРґРЅРѕРіРѕ РјРµСЃС‚Р°.",
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='Markdown'
                )
            except Exception as e:
                print(f"[ERROR] Failed to show clear_cache result: {e}")
            return

        elif data == "clear_cache_cancel":
            try:
                safe_edit_message_text(
                    "вќЊ *РћС‡РёСЃС‚РєР° РєСЌС€Р° РѕС‚РјРµРЅРµРЅР°.*\n\n"
                    "Р¤Р°Р№Р»С‹ РЅРµ Р±С‹Р»Рё СѓРґР°Р»РµРЅС‹.",
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='Markdown'
                )
            except Exception as e:
                print(f"[ERROR] Failed to show clear_cache cancel: {e}")
            return

        # РћР±СЂР°Р±РѕС‚РєР° РїРѕРґРїРёСЃРєРё
        elif data == "activate_promo":
            try:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("рџ”™ РќР°Р·Р°Рґ", callback_data="back_to_subscribe"))

                safe_edit_message_text(
                    "рџЋЃ *РђРєС‚РёРІР°С†РёСЏ РїСЂРѕРјРѕРєРѕРґР°*\n\n"
                    "РћС‚РїСЂР°РІСЊС‚Рµ РїСЂРѕРјРѕРєРѕРґ РІ С„РѕСЂРјР°С‚Рµ:\n"
                    "`/promo Р’РђРЁ_РљРћР”`\n\n"
                    "рџ’Ў *Р’Р°Р¶РЅРѕ:*\n"
                    "вЂў РљР°Р¶РґС‹Р№ РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ РјРѕР¶РµС‚ Р°РєС‚РёРІРёСЂРѕРІР°С‚СЊ С‚РѕР»СЊРєРѕ РѕРґРёРЅ РїСЂРѕРјРѕРєРѕРґ\n"
                    "вЂў РџРѕСЃР»Рµ Р°РєС‚РёРІР°С†РёРё РїСЂРѕРјРѕРєРѕРґ РЅРµР»СЊР·СЏ РёР·РјРµРЅРёС‚СЊ\n"
                    "вЂў РџСЂРѕРјРѕРєРѕРґС‹ РґР°СЋС‚ РґРѕСЃС‚СѓРї РЅР° 30 РґРЅРµР№\n"
                    "вЂў РСЃРєР»СЋС‡РµРЅРёРµ: V1_GAN13 - РІРµС‡РЅР°СЏ РїРѕРґРїРёСЃРєР°",
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='Markdown',
                    reply_markup=markup
                )
                print(f"[DEBUG] Successfully showed activate_promo menu for user {call.from_user.id}")
            except Exception as e:
                print(f"[ERROR] Failed to show activate_promo menu: {e}")
                try:
                    bot.send_message(
                        chat_id,
                        "рџЋЃ *РђРєС‚РёРІР°С†РёСЏ РїСЂРѕРјРѕРєРѕРґР°*\n\n"
                        "РћС‚РїСЂР°РІСЊС‚Рµ РїСЂРѕРјРѕРєРѕРґ РєРѕРјР°РЅРґРѕР№:\n"
                        "`/promo Р’РђРЁ_РљРћР”`",
                        parse_mode='Markdown'
                    )
                except Exception as e2:
                    print(f"[ERROR] Failed to send message as fallback: {e2}")
            return

        elif data == "buy_subscription":
            try:
                markup = types.InlineKeyboardMarkup(row_width=2)
                markup.add(types.InlineKeyboardButton("Use Promo Code", callback_data="activate_promo"))
                contact_button = build_admin_contact_button("Contact Admin")
                if contact_button:
                    markup.add(contact_button)
                safe_edit_message_text(
                    "рџ’і *РћС„РѕСЂРјР»РµРЅРёРµ РїРѕРґРїРёСЃРєРё*\n\n"
                    "рџ“‹ *Р’С‹Р±РµСЂРёС‚Рµ СЃРїРѕСЃРѕР±:*\n\n"
                    "1. рџЋЃ *РџСЂРѕРјРѕРєРѕРґ* - Р±РµСЃРїР»Р°С‚РЅРѕ Рё РЅР°РІСЃРµРіРґР°\n"
                    "2. рџ’° *РџР»Р°С‚РЅР°СЏ РїРѕРґРїРёСЃРєР°* - 49в‚Ѕ/РјРµСЃСЏС† С‡РµСЂРµР· Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°\n"
                    "3. рџ“ћ *РЎРІСЏР·СЊ* - РґР»СЏ РєРѕРЅСЃСѓР»СЊС‚Р°С†РёРё\n\n"
                    "рџ’Ў *Р РµРєРѕРјРµРЅРґСѓРµРј СЃРЅР°С‡Р°Р»Р° РїРѕРїСЂРѕР±РѕРІР°С‚СЊ РїСЂРѕРјРѕРєРѕРґС‹!*",
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='Markdown',
                    reply_markup=markup
                )
            except Exception as e:
                print(f"[ERROR] Failed to show buy_subscription: {e}")
            return

        elif data == "pricing":
            try:
                markup = types.InlineKeyboardMarkup(row_width=2)
                markup.add(types.InlineKeyboardButton("Use Promo Code", callback_data="activate_promo"))
                contact_button = build_admin_contact_button("Contact Admin")
                if contact_button:
                    markup.add(contact_button)
                safe_edit_message_text(
                    "рџ’° *РўР°СЂРёС„С‹ РїРѕРґРїРёСЃРєРё*\n\n"
                    "рџ”№ *PREMIUM РїРѕРґРїРёСЃРєР°* (49в‚Ѕ/РјРµСЃСЏС†):\n"
                    "вЂў РќРµРѕРіСЂР°РЅРёС‡РµРЅРЅРѕРµ СЃРєР°С‡РёРІР°РЅРёРµ РјСѓР·С‹РєРё\n"
                    "вЂў Р”РѕСЃС‚СѓРї РєРѕ РІСЃРµРј РёСЃС‚РѕС‡РЅРёРєР°Рј (YouTube, РЇРЅРґРµРєСЃ.РњСѓР·С‹РєР°)\n"
                    "вЂў РџРѕРґРґРµСЂР¶РєР° 24/7\n"
                    "вЂў Р‘С‹СЃС‚СЂР°СЏ Р·Р°РіСЂСѓР·РєР°\n\n"
                    "рџ’¬ *Р”Р»СЏ РѕС„РѕСЂРјР»РµРЅРёСЏ РїРѕРґРїРёСЃРєРё:*\n"
                    "1. РЎРІСЏР¶РёС‚РµСЃСЊ СЃ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂРѕРј\n"
                    "2. РЈРєР°Р¶РёС‚Рµ Р¶РµР»Р°РµРјС‹Р№ СЃСЂРѕРє РїРѕРґРїРёСЃРєРё\n"
                    "3. РџРѕСЃР»Рµ РѕРїР»Р°С‚Р° РІС‹ РїРѕР»СѓС‡РёС‚Рµ РґРѕСЃС‚СѓРї\n\n"
                    "рџЋЃ *РР»Рё Р°РєС‚РёРІРёСЂСѓР№С‚Рµ РїСЂРѕРјРѕРєРѕРґ РґР»СЏ Р±РµСЃРїР»Р°С‚РЅРѕРіРѕ РґРѕСЃС‚СѓРїР°!*",
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='Markdown',
                    reply_markup=markup
                )
            except Exception as e:
                print(f"[ERROR] Failed to show pricing: {e}")
            return

        elif data == "stats":
            try:
                user_id = call.from_user.id

                # РџСЂРѕРІРµСЂСЏРµРј, СЏРІР»СЏРµС‚СЃСЏ Р»Рё РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂРѕРј
                if user_id in ADMIN_IDS:
                    stats_text = (
                        "вљЎ *РђР”РњРРќРРЎРўР РђРўРћР РЎРљРђРЇ РЎРўРђРўРРЎРўРРљРђ*\n\n"
                        "рџ“Љ *Р’Р°С€Рё РїСЂРёРІРёР»РµРіРёРё:*\n"
                        "вЂў в™ѕпёЏ Р’РµС‡РЅР°СЏ РїРѕР»РЅР°СЏ РїРѕРґРїРёСЃРєР°\n"
                        "вЂў вљ™пёЏ РђРґРјРёРЅРёСЃС‚СЂР°С‚РёРІРЅС‹Рµ РїСЂР°РІР°\n"
                        "вЂў рџ“€ Р”РѕСЃС‚СѓРї РєРѕ РІСЃРµР№ СЃС‚Р°С‚РёСЃС‚РёРєРµ\n"
                        "вЂў рџ”§ РЈРїСЂР°РІР»РµРЅРёРµ РїСЂРѕРјРѕРєРѕРґР°РјРё\n\n"
                        "рџ’Ћ *РЎС‚Р°С‚СѓСЃ:* РђР”РњРРќРРЎРўР РђРўРћР  (Р’Р•Р§РќРђРЇ РїРѕРґРїРёСЃРєР°)"
                    )

                    markup = types.InlineKeyboardMarkup()
                    markup.add(
                        types.InlineKeyboardButton("рџ“€ РЎС‚Р°С‚РёСЃС‚РёРєР° Р±РѕС‚Р°", callback_data="admin_stats"),
                        types.InlineKeyboardButton("рџЋ« РЈРїСЂР°РІР»РµРЅРёРµ РїСЂРѕРјРѕРєРѕРґР°РјРё", callback_data="manage_promos"),
                        types.InlineKeyboardButton("рџ”™ РќР°Р·Р°Рґ", callback_data="back_to_subscribe")
                    )
                else:
                    stats = database.get_user_stats(user_id)

                    stats_text = "рџ“Љ *Р’Р°С€Р° СЃС‚Р°С‚РёСЃС‚РёРєР°*\n\n"

                    if stats:
                        stats_text += (
                            f"рџ“Ґ *РЎРєР°С‡РёРІР°РЅРёСЏ:*\n"
                            f"вЂў Р’СЃРµРіРѕ: {stats.get('total_downloads', 0)}\n"
                            f"вЂў РЎРµРіРѕРґРЅСЏ: {stats.get('today_downloads', 0)}\n"
                            f"вЂў РњР°РєСЃРёРјСѓРј Р·Р° РґРµРЅСЊ: {stats.get('max_daily_downloads', 0)}\n"
                            f"вЂў РђРєС‚РёРІРЅС‹С… РґРЅРµР№: {stats.get('active_days', 0)}\n\n"
                        )

                        if 'current_subscription' in stats:
                            sub = stats['current_subscription']
                            sub_type = sub.get('type', 'premium').upper()

                            if sub.get('promo_code') == 'V1_GAN13':
                                source = "рџЋЃ Р’Р•Р§РќР«Р™ РїСЂРѕРјРѕРєРѕРґ: V1_GAN13"
                            elif sub.get('is_promo'):
                                source = f"рџЋЃ РџСЂРѕРјРѕРєРѕРґ: {sub.get('promo_code', '')}"
                            else:
                                source = "рџ’і РћРїР»Р°С‚Р°"

                            stats_text += f"рџ’Ћ *РџРѕРґРїРёСЃРєР°:* {sub_type} ({source})\n\n"
                    else:
                        stats_text += "рџ“­ *РЎС‚Р°С‚РёСЃС‚РёРєР° РѕС‚СЃСѓС‚СЃС‚РІСѓРµС‚*\n\n"

                    # Р”РѕР±Р°РІР»СЏРµРј РёРЅС„РѕСЂРјР°С†РёСЋ Рѕ РїРѕРґРїРёСЃРєРµ
                    has_access, msg = database.check_subscription(user_id)
                    stats_text += f"рџ”ђ *РЎС‚Р°С‚СѓСЃ РґРѕСЃС‚СѓРїР°:*\n{msg}"

                    markup = types.InlineKeyboardMarkup()
                    markup.add(types.InlineKeyboardButton("рџ”™ РќР°Р·Р°Рґ", callback_data="back_to_subscribe"))

                safe_edit_message_text(
                    stats_text,
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='Markdown',
                    reply_markup=markup
                )
            except Exception as e:
                print(f"[ERROR] РћС€РёР±РєР° РїСЂРё РїРѕРєР°Р·Рµ СЃС‚Р°С‚РёСЃС‚РёРєРё: {e}")
                bot.answer_callback_query(call.id, "вќЊ РћС€РёР±РєР° Р·Р°РіСЂСѓР·РєРё СЃС‚Р°С‚РёСЃС‚РёРєРё")
            return

        elif data == "admin_stats":
            try:
                user_id = call.from_user.id
                if user_id not in ADMIN_IDS:
                    bot.answer_callback_query(call.id, "вќЊ РЈ РІР°СЃ РЅРµС‚ РїСЂР°РІ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР°")
                    return

                # РџРѕР»СѓС‡Р°РµРј СЃС‚Р°С‚РёСЃС‚РёРєСѓ
                active_users = database.get_active_users_count()
                total_downloads = database.get_total_downloads()
                all_promos = database.get_all_promo_codes()

                # РЎС‚Р°С‚РёСЃС‚РёРєР° РїРѕ РїСЂРѕРјРѕРєРѕРґР°Рј
                active_promos = [p for p in all_promos if p['is_active']]
                used_promos = sum(p['uses_count'] for p in all_promos)
                total_promos_created = len(all_promos)

                stats_text = (
                    "рџ“Љ *РЎС‚Р°С‚РёСЃС‚РёРєР° Р±РѕС‚Р° (РђРґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂ)*\n\n"
                    f"рџ‘Ґ *РџРѕР»СЊР·РѕРІР°С‚РµР»Рё:*\n"
                    f"вЂў РђРєС‚РёРІРЅС‹Рµ (30 РґРЅРµР№): {active_users}\n\n"
                    f"рџ“Ґ *РЎРєР°С‡РёРІР°РЅРёСЏ:*\n"
                    f"вЂў Р’СЃРµРіРѕ: {total_downloads}\n\n"
                    f"рџЋЃ *РџСЂРѕРјРѕРєРѕРґС‹:*\n"
                    f"вЂў Р’СЃРµРіРѕ СЃРѕР·РґР°РЅРѕ: {total_promos_created}\n"
                    f"вЂў РђРєС‚РёРІРЅС‹С…: {len(active_promos)}\n"
                    f"вЂў РСЃРїРѕР»СЊР·РѕРІР°РЅРѕ СЂР°Р·: {used_promos}\n\n"
                    f"рџ’ѕ *РљСЌС€:*\n"
                    f"вЂў РњСѓР·С‹РєР°: {len(get_folder_files(MUSIC_DIR))} С„Р°Р№Р»РѕРІ\n"
                    f"вЂў РџРѕРґРєР°СЃС‚С‹: {len(get_folder_files(PODCASTS_DIR))} С„Р°Р№Р»РѕРІ\n\n"
                    f"вЏ° *Р’СЂРµРјСЏ СЂР°Р±РѕС‚С‹:* {datetime.now().strftime('%d.%m.%Y %H:%M')}"
                )

                # Р”РѕР±Р°РІР»СЏРµРј СЃРїРёСЃРѕРє Р°РєС‚РёРІРЅС‹С… РїСЂРѕРјРѕРєРѕРґРѕРІ
                if active_promos:
                    stats_text += "\n\nрџЋ« *РђРєС‚РёРІРЅС‹Рµ РїСЂРѕРјРѕРєРѕРґС‹:*\n"
                    for promo in active_promos[:10]:
                        expiry = promo['expiry_date'].split()[0] if promo['expiry_date'] else "Р±РµСЃСЃСЂРѕС‡РЅРѕ (V1_GAN13)"
                        stats_text += f"вЂў `{promo['code']}` - {promo['subscription_type']} ({promo['uses_count']}/{promo['max_uses']}) РґРѕ {expiry}\n"
                    if len(active_promos) > 10:
                        stats_text += f"вЂў ... Рё РµС‰Рµ {len(active_promos) - 10}"

                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("рџ”™ РќР°Р·Р°Рґ", callback_data="stats"))

                safe_edit_message_text(
                    stats_text,
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='Markdown',
                    reply_markup=markup
                )
            except Exception as e:
                print(f"[ERROR] РћС€РёР±РєР° Р°РґРјРёРЅРёСЃС‚СЂР°С‚РёРІРЅРѕР№ СЃС‚Р°С‚РёСЃС‚РёРєРё: {e}")
                bot.answer_callback_query(call.id, "вќЊ РћС€РёР±РєР° Р·Р°РіСЂСѓР·РєРё СЃС‚Р°С‚РёСЃС‚РёРєРё")
            return

        elif data == "contact_admin":
            try:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("Back", callback_data="back_to_subscribe"))
                contact_button = build_admin_contact_button("Write to Admin")
                if contact_button:
                    markup.add(contact_button)

                safe_edit_message_text(
                    "*Contact admin*\n\n"
                    "Use the button below to open a dialog with the admin.\n\n"
                    "*When contacting us, include:*\n"
                    "1. Your Telegram ID\n"
                    "2. Reason for contact\n"
                    "3. Short description of the issue or request\n\n"
                    "*Response time:* usually within 24 hours",
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='Markdown',
                    reply_markup=markup
                )
            except Exception as e:
                print(f"[ERROR] Failed to show contact_admin: {e}")
            return

        elif data == "back_to_subscribe":
            # Р’РѕР·РІСЂР°С‰Р°РµРјСЃСЏ Рє РјРµРЅСЋ РїРѕРґРїРёСЃРєРё
            try:
                user_id = call.from_user.id
                has_access, msg = database.check_subscription(user_id)

                markup = types.InlineKeyboardMarkup(row_width=1)

                if not has_access:
                    markup.add(
                        types.InlineKeyboardButton("рџ’° РљСѓРїРёС‚СЊ РїРѕРґРїРёСЃРєСѓ (49в‚Ѕ/РјРµСЃСЏС†)", callback_data="buy_subscription"),
                        types.InlineKeyboardButton("рџ“ћ РЎРІСЏР·Р°С‚СЊСЃСЏ СЃ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂРѕРј", callback_data="contact_admin")
                    )
                    reply_text = (f"рџљ« *РЈ РІР°СЃ РЅРµС‚ Р°РєС‚РёРІРЅРѕР№ РїРѕРґРїРёСЃРєРё*\n\n"
                                  f"{msg}\n\n"
                                  f"рџ’Ў *РљР°Рє РїРѕР»СѓС‡РёС‚СЊ РґРѕСЃС‚СѓРї:*\n"
                                  f"1. рџ’° РљСѓРїРёС‚Рµ РїРѕРґРїРёСЃРєСѓ (РІСЃРµРіРѕ 49в‚Ѕ/РјРµСЃСЏС†)\n"
                                  f"2. рџ“ћ РЎРІСЏР¶РёС‚РµСЃСЊ СЃ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂРѕРј\n"
                                  f"3. рџЋЃ Р•СЃР»Рё РµСЃС‚СЊ РїСЂРѕРјРѕРєРѕРґ - РёСЃРїРѕР»СЊР·СѓР№С‚Рµ /promo РљРћР”\n\n"
                                  f"вњЁ *РћС„РѕСЂРјРёС‚Рµ РїРѕРґРїРёСЃРєСѓ Рё РїРѕР»СѓС‡РёС‚Рµ РґРѕСЃС‚СѓРї РєРѕ РІСЃРµРј С„СѓРЅРєС†РёСЏРј!*")
                else:
                    markup.add(
                        types.InlineKeyboardButton("рџ“Љ РЎС‚Р°С‚РёСЃС‚РёРєР°", callback_data="stats"),
                    )
                    reply_text = f"вњ… *РРЅС„РѕСЂРјР°С†РёСЏ Рѕ РїРѕРґРїРёСЃРєРµ*\n\n{msg}\n\n"
                    reply_text += (
                        "вњЁ *Р’Р°С€Рё РІРѕР·РјРѕР¶РЅРѕСЃС‚Рё:*\n"
                        "вЂў вњ… РЎРєР°С‡РёРІР°РЅРёРµ РјСѓР·С‹РєРё РёР· YouTube\n"
                        "вЂў вњ… РЎРєР°С‡РёРІР°РЅРёРµ РёР· РЇРЅРґРµРєСЃ.РњСѓР·С‹РєРё\n"
                        "вЂў вњ… Р‘С‹СЃС‚СЂР°СЏ Р·Р°РіСЂСѓР·РєР°\n"
                        "вЂў вњ… РђРІС‚РѕРјР°С‚РёС‡РµСЃРєР°СЏ СЃРѕСЂС‚РёСЂРѕРІРєР°\n\n"
                        "Р§С‚Рѕ РІС‹ С…РѕС‚РёС‚Рµ СЃРґРµР»Р°С‚СЊ?"
                    )

                safe_edit_message_text(
                    reply_text,
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='Markdown',
                    reply_markup=markup
                )
            except Exception as e:
                print(f"[ERROR] Failed to go back to subscribe: {e}")
            return

        # Р Р°Р·Р±РёСЂР°РµРј СЃР»РѕР¶РЅС‹Рµ callback_data СЃ РїРѕРґС‡РµСЂРєРёРІР°РЅРёРµРј
        if '_' in data:
            parts = data.split('_')

            # РџР°РіРёРЅР°С†РёСЏ РїРѕРёСЃРєР°
            if parts[0] == "page":
                try:
                    page = int(parts[1])
                    if chat_id in user_search_history:
                        history = user_search_history[chat_id]
                        query = history['query']
                        results = history['results']

                        message_text = show_search_results(chat_id, query, results, page=page)
                        keyboard = create_search_keyboard(results, page=page, show_all_button=True)

                        safe_edit_message_text(
                            message_text,
                            chat_id=chat_id,
                            message_id=message_id,
                            parse_mode='Markdown',
                            reply_markup=keyboard
                        )
                except Exception as e:
                    print(f"[!] РћС€РёР±РєР° РїР°РіРёРЅР°С†РёРё: {e}")

            # Р¤РёР»СЊС‚СЂР°С†РёСЏ
            elif parts[0] == "filter":
                if chat_id not in user_search_history:
                    return

                filter_type = parts[1]
                history = user_search_history[chat_id]
                query = history['query']
                all_results = history['results']
                original_results = history.get('original_results', all_results)

                if filter_type == "all":
                    filtered_results = original_results
                    show_all_button = True
                elif filter_type == "yandex":
                    filtered_results = [r for r in original_results if r.get('source') == 'yandex']
                    show_all_button = False
                elif filter_type == "youtube":
                    filtered_results = [r for r in original_results if r.get('source') == 'youtube']
                    show_all_button = False
                elif filter_type == "vk":
                    filtered_results = [r for r in original_results if r.get('source') == 'vk']
                    show_all_button = False
                else:
                    filtered_results = original_results
                    show_all_button = True

                if not filtered_results:
                    bot.answer_callback_query(call.id, "РќРµС‚ СЂРµР·СѓР»СЊС‚Р°С‚РѕРІ СЃ СЌС‚РёРј С„РёР»СЊС‚СЂРѕРј")
                    return

                for i, result in enumerate(filtered_results):
                    result['global_index'] = i + 1
                user_search_history[chat_id]['results'] = list(filtered_results)

                message_text = show_search_results(chat_id, query, filtered_results, page=0)
                keyboard = create_search_keyboard(filtered_results, page=0, show_all_button=show_all_button)

                try:
                    safe_edit_message_text(
                        message_text,
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode='Markdown',
                        reply_markup=keyboard
                    )
                except Exception as e:
                    print(f"[ERROR] Failed to filter: {e}")

            # РЎРєР°С‡РёРІР°РЅРёРµ РЇРЅРґРµРєСЃ С‚СЂРµРєР°
            elif parts[0] == "ya" and len(parts) >= 4:
                try:
                    # РџСЂРѕРІРµСЂРєР° РґРѕСЃС‚СѓРїР° РїРµСЂРµРґ СЃРєР°С‡РёРІР°РЅРёРµРј
                    user_id = call.from_user.id
                    has_access, msg = database.check_subscription(user_id)
                    if not has_access:
                        bot.answer_callback_query(call.id, f"рџљ« Р”РѕСЃС‚СѓРї Р·Р°РєСЂС‹С‚")
                        bot.send_message(
                            chat_id,
                            f"рџ”’ *Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰РµРЅ!*\n\n"
                            f"{msg}\n\n"
                            f"рџ’Ў РСЃРїРѕР»СЊР·СѓР№С‚Рµ /subscribe РґР»СЏ РїРѕР»СѓС‡РµРЅРёСЏ РґРѕСЃС‚СѓРїР°",
                            parse_mode='Markdown'
                        )
                        return

                    track_id = int(parts[1])
                    album_id = int(parts[2])
                    page = int(parts[3])

                    safe_edit_message_text(
                        "вљЎ *РЎРєР°С‡РёРІР°СЋ С‚СЂРµРє РёР· РЇРЅРґРµРєСЃ.РњСѓР·С‹РєРё...*",
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode='Markdown'
                    )

                    audio_path, title, performer, status = download_yandex_track_fast(track_id, album_id)

                    if status == "success" and audio_path and os.path.exists(audio_path):
                        # РЈРІРµР»РёС‡РёРІР°РµРј СЃС‡РµС‚С‡РёРє СЃРєР°С‡РёРІР°РЅРёР№
                        database.increment_download(user_id)

                        file_type = "РїРѕРґРєР°СЃС‚" if audio_path.startswith(PODCASTS_DIR) else "РјСѓР·С‹РєР°"
                        caption = f"рџЋµ {title} (РЇРЅРґРµРєСЃ.РњСѓР·С‹РєР°) | рџ“Ѓ {file_type}"

                        success = send_audio_fast(
                            chat_id=chat_id,
                            audio_path=audio_path,
                            title=title[:64],
                            performer=performer[:64],
                            caption=caption
                        )

                        if success:
                            # Р’РѕСЃСЃС‚Р°РЅР°РІР»РёРІР°РµРј СЂРµР·СѓР»СЊС‚Р°С‚С‹ РїРѕРёСЃРєР°
                            if chat_id in user_search_history:
                                history = user_search_history[chat_id]
                                results = history['results']
                                query = history['query']

                                message_text = show_search_results(chat_id, query, results, page=page)
                                keyboard = create_search_keyboard(results, page=page, show_all_button=True)

                                safe_edit_message_text(
                                    f"вњ… *РўСЂРµРє СЃРєР°С‡Р°РЅ!*\n\n"
                                    f"рџЋµ *{title}*\n"
                                    f"рџ‘¤ *{performer}*\n\n"
                                    f"вњЁ *РџСЂРѕРґРѕР»Р¶Р°Р№С‚Рµ РїРѕРёСЃРє:*",
                                    chat_id=chat_id,
                                    message_id=message_id,
                                    parse_mode='Markdown',
                                    reply_markup=keyboard
                                )
                            else:
                                markup = types.InlineKeyboardMarkup()
                                markup.add(types.InlineKeyboardButton("рџ”Ќ РќРѕРІС‹Р№ РїРѕРёСЃРє", callback_data="new_search"))

                                safe_edit_message_text(
                                    f"вњ… *РўСЂРµРє СѓСЃРїРµС€РЅРѕ СЃРєР°С‡Р°РЅ!*\n\n"
                                    f"рџЋµ *{title}*\n"
                                    f"рџ‘¤ *{performer}*\n\n"
                                    f"вњЁ РЎРєР°С‡Р°РЅРѕ РІ РїР°РїРєСѓ: {file_type}",
                                    chat_id=chat_id,
                                    message_id=message_id,
                                    parse_mode='Markdown',
                                    reply_markup=markup
                                )
                        else:
                            safe_edit_message_text(
                                f"вќЊ *РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РїСЂР°РІРёС‚СЊ С‚СЂРµРє*\n\n"
                                f"РџРѕРїСЂРѕР±СѓР№С‚Рµ РµС‰Рµ СЂР°Р· РёР»Рё РІС‹Р±РµСЂРёС‚Рµ РґСЂСѓРіРѕР№ С‚СЂРµРє.",
                                chat_id=chat_id,
                                message_id=message_id,
                                parse_mode='Markdown'
                            )
                    else:
                        safe_edit_message_text(
                            f"вќЊ *РћС€РёР±РєР° СЃРєР°С‡РёРІР°РЅРёСЏ*\n\n"
                            f"РџСЂРёС‡РёРЅР°: {status}",
                            chat_id=chat_id,
                            message_id=message_id,
                            parse_mode='Markdown'
                        )
                except Exception as e:
                    print(f"[!] РћС€РёР±РєР° СЃРєР°С‡РёРІР°РЅРёСЏ РЇРЅРґРµРєСЃ С‚СЂРµРєР°: {e}")
                    traceback.print_exc()
                    safe_edit_message_text(
                        f"вќЊ *РћС€РёР±РєР° РїСЂРё СЃРєР°С‡РёРІР°РЅРёРё*\n\n"
                        f"РџРѕРїСЂРѕР±СѓР№С‚Рµ РµС‰Рµ СЂР°Р·.",
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode='Markdown'
                    )

            # РЎРєР°С‡РёРІР°РЅРёРµ YouTube С‚СЂРµРєР°
            elif parts[0] == "vk" and len(parts) >= 4:
                try:
                    user_id = call.from_user.id
                    has_access, msg = database.check_subscription(user_id)
                    if not has_access:
                        bot.answer_callback_query(call.id, f"СЂСџС™В« Р вЂќР С•РЎРѓРЎвЂљРЎС“Р С— Р В·Р В°Р С”РЎР‚РЎвЂ№РЎвЂљ")
                        bot.send_message(
                            chat_id,
                            f"СЂСџвЂќвЂ™ *Р вЂќР С•РЎРѓРЎвЂљРЎС“Р С— Р В·Р В°Р С—РЎР‚Р ВµРЎвЂ°Р ВµР Р…!*\n\n"
                            f"{msg}\n\n"
                            f"СЂСџвЂ™РЋ Р ВРЎРѓР С—Р С•Р В»РЎРЉР В·РЎС“Р в„–РЎвЂљР Вµ /subscribe Р Т‘Р В»РЎРЏ Р С—Р С•Р В»РЎС“РЎвЂЎР ВµР Р…Р С‘РЎРЏ Р Т‘Р С•РЎРѓРЎвЂљРЎС“Р С—Р В°",
                            parse_mode='Markdown'
                        )
                        return

                    owner_id = int(parts[1])
                    track_id = int(parts[2])
                    page = int(parts[3])

                    selected_track = None
                    if chat_id in user_search_history:
                        history = user_search_history[chat_id]
                        for track in history.get('results', []):
                            if track.get('source') == 'vk' and int(track.get('owner_id', 0)) == owner_id and int(track.get('track_id', 0)) == track_id:
                                selected_track = track
                                break
                        if not selected_track:
                            for track in history.get('original_results', []):
                                if track.get('source') == 'vk' and int(track.get('owner_id', 0)) == owner_id and int(track.get('track_id', 0)) == track_id:
                                    selected_track = track
                                    break

                    safe_edit_message_text(
                        "РІС™РЋ *Р РЋР С”Р В°РЎвЂЎР С‘Р Р†Р В°РЎР‹ РЎвЂљРЎР‚Р ВµР С” Р С‘Р В· VK Music...*",
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode='Markdown'
                    )

                    audio_path, title, performer, status = download_vk_track_fast(
                        owner_id,
                        track_id,
                        track_url=selected_track.get('url') if selected_track else None,
                        title_hint=selected_track.get('title') if selected_track else None,
                        artist_hint=selected_track.get('artist') if selected_track else None,
                        duration_seconds=selected_track.get('duration_seconds', 0) if selected_track else 0,
                    )

                    if status == "success" and audio_path and os.path.exists(audio_path):
                        database.increment_download(user_id)
                        file_type = "Р С—Р С•Р Т‘Р С”Р В°РЎРѓРЎвЂљ" if audio_path.startswith(PODCASTS_DIR) else "Р СРЎС“Р В·РЎвЂ№Р С”Р В°"
                        caption = f"СЂСџР‹В§ {title} (VK Music) | СЂСџвЂњРѓ {file_type}"

                        success = send_audio_fast(
                            chat_id=chat_id,
                            audio_path=audio_path,
                            title=(title or "VK Track")[:64],
                            performer=(performer or "VK Artist")[:64],
                            caption=caption
                        )

                        if success:
                            if chat_id in user_search_history:
                                history = user_search_history[chat_id]
                                results = history['results']
                                query = history['query']
                                message_text = show_search_results(chat_id, query, results, page=page)
                                keyboard = create_search_keyboard(results, page=page, show_all_button=True)
                                safe_edit_message_text(
                                    f"РІСљвЂ¦ *Р СћРЎР‚Р ВµР С” РЎРѓР С”Р В°РЎвЂЎР В°Р Р…!*\n\n"
                                    f"СЂСџР‹В§ *{title}*\n"
                                    f"СЂСџвЂВ¤ *{performer}*\n\n"
                                    f"РІСљРЃ *Р СџРЎР‚Р С•Р Т‘Р С•Р В»Р В¶Р В°Р в„–РЎвЂљР Вµ Р С—Р С•Р С‘РЎРѓР С”:*",
                                    chat_id=chat_id,
                                    message_id=message_id,
                                    parse_mode='Markdown',
                                    reply_markup=keyboard
                                )
                            else:
                                markup = types.InlineKeyboardMarkup()
                                markup.add(types.InlineKeyboardButton("СЂСџвЂќРЊ Р СњР С•Р Р†РЎвЂ№Р в„– Р С—Р С•Р С‘РЎРѓР С”", callback_data="new_search"))
                                safe_edit_message_text(
                                    f"РІСљвЂ¦ *Р СћРЎР‚Р ВµР С” РЎС“РЎРѓР С—Р ВµРЎв‚¬Р Р…Р С• РЎРѓР С”Р В°РЎвЂЎР В°Р Р…!*\n\n"
                                    f"СЂСџР‹В§ *{title}*\n"
                                    f"СЂСџвЂВ¤ *{performer}*\n\n"
                                    f"РІСљРЃ Р РЋР С”Р В°РЎвЂЎР В°Р Р…Р С• Р Р† Р С—Р В°Р С—Р С”РЎС“: {file_type}",
                                    chat_id=chat_id,
                                    message_id=message_id,
                                    parse_mode='Markdown',
                                    reply_markup=markup
                                )
                        else:
                            safe_edit_message_text(
                                "РІСњРЉ *Р СњР Вµ РЎС“Р Т‘Р В°Р В»Р С•РЎРѓРЎРЉ Р С•РЎвЂљР С—РЎР‚Р В°Р Р†Р С‘РЎвЂљРЎРЉ РЎвЂљРЎР‚Р ВµР С”*",
                                chat_id=chat_id,
                                message_id=message_id,
                                parse_mode='Markdown'
                            )
                    else:
                        safe_edit_message_text(
                            f"РІСњРЉ *Р С›РЎв‚¬Р С‘Р В±Р С”Р В° РЎРѓР С”Р В°РЎвЂЎР С‘Р Р†Р В°Р Р…Р С‘РЎРЏ VK*\n\n"
                            f"Р СџРЎР‚Р С‘РЎвЂЎР С‘Р Р…Р В°: {status}",
                            chat_id=chat_id,
                            message_id=message_id,
                            parse_mode='Markdown'
                        )
                except Exception as e:
                    print(f"[!] Р С›РЎв‚¬Р С‘Р В±Р С”Р В° РЎРѓР С”Р В°РЎвЂЎР С‘Р Р†Р В°Р Р…Р С‘РЎРЏ VK РЎвЂљРЎР‚Р ВµР С”Р В°: {e}")
                    traceback.print_exc()
                    safe_edit_message_text(
                        f"РІСњРЉ *Р С›РЎв‚¬Р С‘Р В±Р С”Р В° Р С—РЎР‚Р С‘ РЎРѓР С”Р В°РЎвЂЎР С‘Р Р†Р В°Р Р…Р С‘Р С‘ VK*\n\n"
                        f"Р СџР С•Р С—РЎР‚Р С•Р В±РЎС“Р в„–РЎвЂљР Вµ Р ВµРЎвЂ°Р Вµ РЎР‚Р В°Р В·.",
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode='Markdown'
                    )

            elif parts[0] == "yt" and len(parts) >= 3:
                try:
                    # РџСЂРѕРІРµСЂРєР° РґРѕСЃС‚СѓРїР° РїРµСЂРµРґ СЃРєР°С‡РёРІР°РЅРёРµРј
                    user_id = call.from_user.id
                    has_access, msg = database.check_subscription(user_id)
                    if not has_access:
                        bot.answer_callback_query(call.id, f"рџљ« Р”РѕСЃС‚СѓРї Р·Р°РєСЂС‹С‚")
                        bot.send_message(
                            chat_id,
                            f"рџ”’ *Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰РµРЅ!*\n\n"
                            f"{msg}\n\n"
                            f"рџ’Ў РСЃРїРѕР»СЊР·СѓР№С‚Рµ /subscribe РґР»СЏ РїРѕР»СѓС‡РµРЅРёСЏ РґРѕСЃС‚СѓРїР°",
                            parse_mode='Markdown'
                        )
                        return

                    video_id = parts[1]
                    page = int(parts[2])
                    url = f"https://youtube.com/watch?v={video_id}"

                    safe_edit_message_text(
                        "вљЎ *РЎРєР°С‡РёРІР°СЋ С‚СЂРµРє СЃ YouTube...*",
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode='Markdown'
                    )

                    audio_path, title, performer, status = download_from_youtube_fast(url, is_url=True)

                    if status == "success" and audio_path and os.path.exists(audio_path):
                        # РЈРІРµР»РёС‡РёРІР°РµРј СЃС‡РµС‚С‡РёРє СЃРєР°С‡РёРІР°РЅРёР№
                        database.increment_download(user_id)

                        file_type = "РїРѕРґРєР°СЃС‚" if audio_path.startswith(PODCASTS_DIR) else "РјСѓР·С‹РєР°"
                        caption = f"рџЋµ {title} (YouTube) | рџ“Ѓ {file_type}"

                        success = send_audio_fast(
                            chat_id=chat_id,
                            audio_path=audio_path,
                            title=title[:64],
                            performer=performer[:64],
                            caption=caption
                        )

                        if success:
                            # Р’РѕСЃСЃС‚Р°РЅР°РІР»РёРІР°РµРј СЂРµР·СѓР»СЊС‚Р°С‚С‹ РїРѕРёСЃРєР°
                            if chat_id in user_search_history:
                                history = user_search_history[chat_id]
                                results = history['results']
                                query = history['query']

                                message_text = show_search_results(chat_id, query, results, page=page)
                                keyboard = create_search_keyboard(results, page=page, show_all_button=True)

                                safe_edit_message_text(
                                    f"вњ… *РўСЂРµРє СЃРєР°С‡Р°РЅ!*\n\n"
                                    f"рџЋµ *{title}*\n"
                                    f"рџ‘¤ *{performer}*\n\n"
                                    f"вњЁ *РџСЂРѕРґРѕР»Р¶Р°Р№С‚Рµ РїРѕРёСЃРє:*",
                                    chat_id=chat_id,
                                    message_id=message_id,
                                    parse_mode='Markdown',
                                    reply_markup=keyboard
                                )
                            else:
                                markup = types.InlineKeyboardMarkup()
                                markup.add(types.InlineKeyboardButton("рџ”Ќ РќРѕРІС‹Р№ РїРѕРёСЃРє", callback_data="new_search"))

                                safe_edit_message_text(
                                    f"вњ… *РўСЂРµРє СѓСЃРїРµС€РЅРѕ СЃРєР°С‡Р°РЅ!*\n\n"
                                    f"рџЋµ *{title}*\n"
                                    f"рџ‘¤ *{performer}*\n\n"
                                    f"вњЁ РЎРєР°С‡Р°РЅРѕ РІ РїР°РїРєСѓ: {file_type}",
                                    chat_id=chat_id,
                                    message_id=message_id,
                                    parse_mode='Markdown',
                                    reply_markup=markup
                                )
                        else:
                            safe_edit_message_text(
                                f"вќЊ *РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РїСЂР°РІРёС‚СЊ С‚СЂРµРє*\n\n"
                                f"РџРѕРїСЂРѕР±СѓР№С‚Рµ РµС‰Рµ СЂР°Р· РёР»Рё РІС‹Р±РµСЂРёС‚Рµ РґСЂСѓРіРѕР№ С‚СЂРµРє.",
                                chat_id=chat_id,
                                message_id=message_id,
                                parse_mode='Markdown'
                            )
                    else:
                        safe_edit_message_text(
                            f"вќЊ *РћС€РёР±РєР° СЃРєР°С‡РёРІР°РЅРёСЏ*\n\n"
                            f"РџСЂРёС‡РёРЅР°: {status}",
                            chat_id=chat_id,
                            message_id=message_id,
                            parse_mode='Markdown'
                        )
                except Exception as e:
                    print(f"[!] РћС€РёР±РєР° СЃРєР°С‡РёРІР°РЅРёСЏ YouTube С‚СЂРµРєР°: {e}")
                    traceback.print_exc()
                    safe_edit_message_text(
                        f"вќЊ *РћС€РёР±РєР° РїСЂРё СЃРєР°С‡РёРІР°РЅРёРё*\n\n"
                        f"РџРѕРїСЂРѕР±СѓР№С‚Рµ РµС‰Рµ СЂР°Р·.",
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode='Markdown'
                    )

            # РџСЂРѕСЃРјРѕС‚СЂ С„Р°Р№Р»РѕРІ РІ РїР°РїРєРµ
            elif parts[0] == "files" and len(parts) >= 3:
                folder_type = parts[1]
                try:
                    page = int(parts[2])
                except:
                    page = 0

                if folder_type == "music":
                    folder_path = MUSIC_DIR
                    folder_name = "РњСѓР·С‹РєР°"
                    emoji = "рџЋµ"
                else:
                    folder_path = PODCASTS_DIR
                    folder_name = "РџРѕРґРєР°СЃС‚С‹"
                    emoji = "рџЋ™пёЏ"

                files = get_folder_files(folder_path)

                if not files:
                    safe_edit_message_text(
                        f"{emoji} *РџР°РїРєР° СЃ {folder_name.lower()}*\n\n"
                        f"рџ“­ РџР°РїРєР° РїСѓСЃС‚Р°\n\n"
                        f"рџ’Ў *РЎРѕРІРµС‚:*\n"
                        f"вЂў РћС‚РїСЂР°РІСЊС‚Рµ РЅР°Р·РІР°РЅРёРµ РїРµСЃРЅРё РІ С‡Р°С‚\n"
                        f"вЂў РЎРєР°С‡Р°Р№С‚Рµ С‚СЂРµРєРё РёР· РїРѕРёСЃРєР°\n"
                        f"вЂў Р¤Р°Р№Р»С‹ РїРѕСЏРІСЏС‚СЃСЏ Р·РґРµСЃСЊ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё",
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode='Markdown'
                    )
                    return

                total_size = sum(f['size'] for f in files)
                message_text = (
                    f"{emoji} *РџР°РїРєР° СЃ {folder_name.lower()}*\n\n"
                    f"рџ“Љ *РЎС‚Р°С‚РёСЃС‚РёРєР°:*\n"
                    f"вЂў Р¤Р°Р№Р»РѕРІ: {len(files)}\n"
                    f"вЂў РћР±С‰РёР№ СЂР°Р·РјРµСЂ: {total_size:.2f} MB\n\n"
                    f"рџ“Ѓ Р’С‹Р±РµСЂРёС‚Рµ С„Р°Р№Р» РґР»СЏ РѕС‚РїСЂР°РІРєРё:"
                )

                keyboard = create_files_keyboard(files, page=page, folder_type=folder_type)
                safe_edit_message_text(
                    message_text,
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='Markdown',
                    reply_markup=keyboard
                )

            # РћС‚РїСЂР°РІРєР° С„Р°Р№Р»Р°
            elif parts[0] == "file" and len(parts) >= 4:
                folder_type = parts[1]
                try:
                    file_index = int(parts[2])
                    page = int(parts[3])
                except:
                    return

                if folder_type == "music":
                    folder_path = MUSIC_DIR
                else:
                    folder_path = PODCASTS_DIR

                files = get_folder_files(folder_path)

                if 0 <= file_index < len(files):
                    file_info = files[file_index]
                    file_path = file_info['path']

                    # РћС‚РїСЂР°РІР»СЏРµРј С„Р°Р№Р»
                    success = send_file_from_folder(chat_id, file_path)

                    if not success:
                        bot.answer_callback_query(call.id, "вќЊ РћС€РёР±РєР° РѕС‚РїСЂР°РІРєРё С„Р°Р№Р»Р°")

    except Exception as e:
        print(f"[!] РљСЂРёС‚РёС‡РµСЃРєР°СЏ РѕС€РёР±РєР° РІ РѕР±СЂР°Р±РѕС‚С‡РёРєРµ callback: {e}")
        traceback.print_exc()
        try:
            bot.answer_callback_query(call.id, f"вќЊ РћС€РёР±РєР°: {str(e)[:50]}")
        except:
            pass


# ============================================
# Р—РђРџРЈРЎРљ Р‘РћРўРђ
# ============================================

if __name__ == '__main__':
    print("=" * 60)
    print("🤖 ТЕЛЕГРАМ-МУЗЫКАЛЬНЫЙ БОТ ЗАПУЩЕН!")
    print("=" * 60)
    print(f"📁 Основная папка: {os.path.abspath(AUDIO_CACHE_DIR)}")
    print(f"🎵 Папка с музыкой: {os.path.abspath(MUSIC_DIR)}")
    print(f"🎙️ Папка с подкастами: {os.path.abspath(PODCASTS_DIR)}")

    if ym_client:
        try:
            account_info = ym_client.me.account_status()
            print(f"✅ Яндекс.Музыка: авторизован как {account_info.account.login}")
        except Exception:
            print("✅ Яндекс.Музыка: модуль активен")
    else:
        print("⚠️ Яндекс.Музыка: модуль отключен")

    print("📺 YouTube: модуль активен")
    print("⚡ Оптимизация: быстрая отправка файлов включена")
    print("🔍 Автоматический поиск: включен")
    print("💎 Система подписок: активна")
    print("🎁 Промокоды: доступны")
    print(f"⚡ FFMPEG потоков: {FFMPEG_THREADS}")
    print("=" * 60)
    print("ℹ️ Основные возможности:")
    print("   • Просто отправьте название песни в чат")
    print("   • Или исполнителя и название")
    print("   • /subscribe - информация о подписке")
    print("   • /promo КОД - активировать промокод")
    print("   • /status - проверка подключений")
    print("   • /clear_cache - очистить все файлы")
    print("=" * 60)
    print("🚀 Бот готов к работе!")
    print("=" * 60)

    try:
        bot.infinity_polling(timeout=120, long_polling_timeout=60)
    except Exception as e:
        print(f"❌ Критическая ошибка бота: {e}")
        traceback.print_exc()
        print("Проверьте токены и перезапустите бота.")

