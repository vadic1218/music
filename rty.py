# ============================================
# МУЗЫКАЛЬНЫЙ БОТ ДЛЯ TELEGRAM
# Полная интеграция: YouTube + Яндекс.Музыка
# Система подписок и промокодов
# ============================================

# Импорт библиотек
import database
from pathlib import Path
from config import BOT_TOKEN, ADMIN_IDS, MAX_FILE_SIZE_MB, FFMPEG_THREADS, YANDEX_MUSIC_TOKEN
import telebot
import os
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
from yandex_music.exceptions import UnauthorizedError, NetworkError
import subprocess
import math
from telebot import types
import traceback
from datetime import datetime, timedelta

# --- НАСТРОЙКА БОТА ---
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set. Put it into the project .env file before starting the bot.")

bot = telebot.TeleBot(BOT_TOKEN)

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
        print(
            f"   - {promo['code']}: {promo['subscription_type']} (использовано: {promo['uses_count']}/{promo['max_uses']})")

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
        print("⚠️  Ошибка сети при подключении к Яндекс.Музыке.")
    except Exception as e:
        print(f"⚠️  Неизвестная ошибка инициализации Яндекс.Музыки: {e}")

# --- ОБЩИЕ ПЕРЕМЕННЫЕ ---
user_search_history = {}
user_files_state = {}
ym_client_lock = threading.Lock()

# --- НАСТРОЙКИ ПАПОК ---
AUDIO_CACHE_DIR = str(BASE_DIR / "audio_cache")
MUSIC_DIR = os.path.join(AUDIO_CACHE_DIR, "music")
PODCASTS_DIR = os.path.join(AUDIO_CACHE_DIR, "podcasts")

os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)
os.makedirs(MUSIC_DIR, exist_ok=True)
os.makedirs(PODCASTS_DIR, exist_ok=True)


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def check_access(user_id):
    """Проверяет, есть ли у пользователя доступ"""
    has_access, message = database.check_subscription(user_id)
    return has_access, message


def is_youtube_playlist(url):
    """Проверяет, является ли ссылка плейлистом YouTube"""
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
    """Извлекает ссылку на конкретное видео из плейлиста YouTube"""
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
        print(f"[YouTube] Ошибка извлечения видео из плейлиста: {e}")

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
    """Определяет папку для сохранения файла на основе длительности"""
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
        print(f"[!] Ошибка определения папки: {e}")
        return MUSIC_DIR


def compress_audio_if_needed_fast(audio_path, max_size_mb=MAX_FILE_SIZE_MB):
    """Быстрое сжатие аудиофайла с использованием потоков"""
    try:
        file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)

        if file_size_mb <= max_size_mb:
            return audio_path, False

        print(f"[!] Файл слишком большой: {file_size_mb:.2f} МБ. Быстро сжимаю...")

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
            print(f"[✓] Файл быстро сжат: {file_size_mb:.2f} МБ -> {new_size_mb:.2f} МБ")

            try:
                os.remove(audio_path)
            except:
                pass
            os.rename(compressed_path, audio_path)
            return audio_path, True
        else:
            print(f"[!] Не удалось быстро сжать файл: {result.stderr}")
            return audio_path, False

    except subprocess.TimeoutExpired:
        print(f"[!] Таймаут при быстром сжатии файла")
        return audio_path, False
    except Exception as e:
        print(f"[!] Ошибка при быстром сжатии файла: {e}")
        return audio_path, False


def split_large_audio_fast(audio_path, max_part_size_mb=MAX_FILE_SIZE_MB):
    """Быстрое разделение большого аудиофайла на части"""
    try:
        cmd = ['ffprobe', '-v', 'error', '-show_entries',
               'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', audio_path]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[!] Не удалось получить длительность аудио: {result.stderr}")
            return [audio_path]

        duration = float(result.stdout.strip())
        file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)

        num_parts = max(2, math.ceil(file_size_mb / max_part_size_mb))

        print(f"[!] Быстро разделяю файл на {num_parts} частей...")

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
                    print(f"[✓] Часть {part_num} создана: {part_size_mb:.2f} МБ")
                    parts.append(part_path)
                else:
                    print(f"[!] Ошибка создания части {part_num}: {result.stderr}")

        if parts:
            return parts
        else:
            return [audio_path]

    except Exception as e:
        print(f"[!] Ошибка быстрого разделения файла: {e}")
        return [audio_path]


def is_podcast_file(audio_path):
    """Проверяет, является ли файл подкастом"""
    return audio_path.startswith(PODCASTS_DIR)


def send_audio_fast(chat_id, audio_path, title=None, performer=None, caption=None, max_retries=2):
    """Быстрая отправка аудиофайла"""
    for attempt in range(max_retries):
        try:
            file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)

            if file_size_mb > MAX_FILE_SIZE_MB:
                audio_path, compressed = compress_audio_if_needed_fast(audio_path)
                if compressed:
                    file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
                    if caption:
                        caption = f"{caption} (быстро сжато)"

            if file_size_mb > MAX_FILE_SIZE_MB:
                print(f"[!] Файл все еще большой {file_size_mb:.1f}МБ. Быстро разделяю...")
                parts = split_large_audio_fast(audio_path)

                if len(parts) > 1:
                    print(f"[✓] Быстро разделен на {len(parts)} частей")

                    bot.send_message(chat_id,
                                     f"⚡ Файл быстро разделен на {len(parts)} частей...")

                    for i, part_path in enumerate(parts):
                        part_caption = f"{caption or ''} (часть {i + 1}/{len(parts)})".strip()

                        with open(part_path, 'rb') as audio_file:
                            bot.send_audio(
                                chat_id=chat_id,
                                audio=audio_file,
                                title=f"{title or ''} (часть {i + 1})"[:64] if title else None,
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
            print(f"[!] Ошибка Telegram API: {e}")
            if "file is too big" in str(e) or "400" in str(e):
                return send_document_fast(chat_id, audio_path, caption)
            elif attempt < max_retries - 1:
                time.sleep(2)
            else:
                return False
        except Exception as e:
            print(f"[!] Ошибка при отправке аудио: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                return False

    return False


def send_document_fast(chat_id, file_path, caption=None, max_retries=2):
    """Быстрая отправка файла как документ"""
    for attempt in range(max_retries):
        try:
            with open(file_path, 'rb') as doc_file:
                bot.send_chat_action(chat_id, 'upload_document')
                bot.send_document(
                    chat_id=chat_id,
                    document=doc_file,
                    caption=f"📁 {caption or os.path.basename(file_path)}",
                    timeout=300,
                    visible_file_name=os.path.basename(file_path)
                )
            return True
        except Exception as e:
            print(f"[!] Ошибка отправки документа: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                return False


def send_file_from_folder(chat_id, file_path):
    """Отправка файла из папки"""
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
                caption=f"📁 {filename}"
            )
        else:
            return send_document_fast(chat_id, file_path, os.path.basename(file_path))

    except Exception as e:
        print(f"[!] Ошибка отправки файла из папки: {e}")
        return False


def clear_cache_folders():
    """Очищает все файлы в папках кэша"""
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
                    print(f'Не удалось удалить {file_path}. Причина: {e}')

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
                    print(f'Не удалось удалить {file_path}. Причина: {e}')

        return total_deleted
    except Exception as e:
        print(f"[!] Ошибка при очистке кэша: {e}")
        return 0


# --- ПОИСК В ЯНДЕКС.МУЗЫКЕ ---
def search_yandex_music(query, search_type="all", limit=15):
    """Ищет треки в Яндекс.Музыке."""
    if not ym_client:
        print("[Yandex] Клиент не настроен для поиска")
        return []

    try:
        print(f"[Yandex] Поиск: '{query}' (тип: {search_type})")
        search_result = ym_client.search(query, type_='track', page=0)

        if not search_result or not search_result.tracks:
            print(f"[Yandex] По запросу '{query}' ничего не найдено")
            return []

        tracks = search_result.tracks.results[:limit]
        print(f"[Yandex] Найдено {len(tracks)} треков по запросу '{query}'")

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
                    [artist.name for artist in track.artists]) if track.artists else 'Неизвестный исполнитель'
                album_name = track.albums[0].title if track.albums else 'Неизвестный альбом'
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
                print(f"[Yandex] Ошибка форматирования трека: {e}")
                continue

        return formatted_results

    except Exception as e:
        print(f"[Yandex] Ошибка поиска: {e}")
        return []


# --- ПОИСК В YOUTUBE ---
def search_youtube_music(query, limit=10):
    """Ищет треки на YouTube по названию."""
    try:
        print(f"[YouTube Search] Поиск: '{query}'")

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
            'default_search': 'ytsearch',
            'noplaylist': True,
            'ignoreerrors': True,
            'geo_bypass': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_string = f"ytsearch{limit}:{query}"
            info = ydl.extract_info(search_string, download=False)

            if not info or 'entries' not in info:
                print(f"[YouTube Search] По запросу '{query}' ничего не найдено")
                return []

            videos = info['entries']
            formatted_results = []

            for i, video in enumerate(videos):
                try:
                    if not video:
                        continue

                    title = video.get('title', 'Без названия')
                    uploader = video.get('uploader', 'Неизвестный автор')
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
                        duration_str = "Неизвестно"

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
                    print(f"[YouTube Search] Ошибка форматирования видео {i}: {e}")
                    continue

            print(f"[YouTube Search] Найдено {len(formatted_results)} видео по запросу '{query}'")
            return formatted_results[:limit]

    except Exception as e:
        print(f"[YouTube Search] Ошибка поиска: {e}")
        return []


# --- СКАЧИВАНИЕ ИЗ YANDEX И YOUTUBЕ ---
def download_yandex_track_fast(track_id, album_id):
    """Скачивает трек из Яндекс.Музыки"""
    if not ym_client:
        return None, None, None, "Клиент Яндекс.Музыки не настроен."

    try:
        with ym_client_lock:
            tracks = ym_client.tracks([f"{track_id}:{album_id}"])

        if not tracks:
            return None, None, None, "Трек не найден."

        track = tracks[0]

        with ym_client_lock:
            download_info = track.get_download_info()

        if not download_info:
            return None, None, None, "Информация для скачивания недоступна."

        best_info = min(
            [info for info in download_info if info.codec == 'mp3'],
            key=lambda x: x.bitrate_in_kbps,
            default=None
        )

        if not best_info:
            best_info = download_info[0] if download_info else None
            if not best_info:
                return None, None, None, "Нет подходящего формата."

        safe_title = "".join([c for c in track.title if c.isalnum() or c in (' ', '-', '_')]).strip()
        safe_artists = "_".join([a.name for a in track.artists[:1]]) if track.artists else "Unknown"

        duration_seconds = track.duration_ms / 1000 if hasattr(track, 'duration_ms') else 0
        target_dir = get_target_folder(duration_seconds)
        os.makedirs(target_dir, exist_ok=True)

        filename = f"{safe_artists} - {safe_title}.mp3"
        filepath = os.path.join(target_dir, filename)

        if os.path.exists(filepath):
            os.remove(filepath)

        track.download(filepath, codec='mp3', bitrate_in_kbps=best_info.bitrate_in_kbps)

        return filepath, track.title, ", ".join(
            [a.name for a in track.artists]) if track.artists else "Unknown Artist", "success"

    except Exception as e:
        print(f"[Yandex] Ошибка скачивания: {e}")
        return None, None, None, f"Ошибка скачивания: {str(e)}"


def download_from_youtube_fast(query, is_url=False):
    """Скачивает аудио с YouTube"""
    try:
        if is_url and is_youtube_playlist(query):
            print("[YouTube] Получена ссылка на плейлист, извлекаю первое видео...")
            query = extract_video_from_playlist(query)

        ydl_info_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }

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

            title = video.get('title', 'Без названия')
            uploader = video.get('uploader', 'Неизвестный автор')
            duration = video.get('duration', 0)
            video_id = video.get('id', '')

            try:
                duration_int = int(duration) if duration else 0
            except (ValueError, TypeError):
                duration_int = 0

            target_dir = get_target_folder(duration_int)
            os.makedirs(target_dir, exist_ok=True)

            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': os.path.join(target_dir, f'%(id)s.%(ext)s'),
                'quiet': True,
                'no_warnings': True,
                'socket_timeout': 60,
                'retries': 10,
                'fragment_retries': 10,
                'extractor_retries': 3,
                'ignoreerrors': True,
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
                'nocheckcertificate': True,
                'geo_bypass': True,
                'sleep_interval': 1,
                'max_sleep_interval': 5,
            }

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
                        print(f"[YouTube] Ошибка переименования: {rename_error}")
                        return audio_path, title, uploader, "success"

            return None, title, uploader, "no_file"

    except Exception as e:
        print(f"[!] Ошибка YouTube: {e}")
        return None, None, None, f"Ошибка: {str(e)}"


# --- УНИВЕРСАЛЬНЫЙ ПОИСК ---
def universal_search_all(query, limit_per_service=5):
    """Ищет музыку в Яндекс.Музыке и YouTube."""
    all_results = []

    if ym_client:
        yandex_results = search_yandex_music(query, limit=limit_per_service)
        all_results.extend(yandex_results)

    youtube_results = search_youtube_music(query, limit=limit_per_service)
    all_results.extend(youtube_results)

    for i, result in enumerate(all_results):
        result['global_index'] = i + 1

    return all_results


def show_search_results(chat_id, query, results, page=0):
    """Показывает результаты поиска"""
    if not results:
        return "❌ По вашему запросу ничего не найдено."

    user_search_history[chat_id] = {
        'query': query,
        'results': results,
        'timestamp': time.time()
    }

    start_idx = page * 5
    end_idx = start_idx + 5
    page_results = results[start_idx:end_idx]

    message_text = f"🔍 *Результаты поиска: '{query}'*\n\n"

    yandex_count = len([r for r in results if r.get('source') == 'yandex'])
    youtube_count = len([r for r in results if r.get('source') == 'youtube'])

    message_text += f"*Найдено:* {len(results)} треков "
    message_text += f"(🎵 Яндекс: {yandex_count}, 📺 YouTube: {youtube_count})\n"
    message_text += f"*Страница:* {page + 1}/{(len(results) + 4) // 5}\n\n"

    for track in page_results:
        idx = track.get('global_index', 0)
        title = track.get('title', 'Без названия')
        source = track.get('source', 'unknown')

        if source == 'yandex':
            source_icon = "🎵"
            artist_info = track.get('artists', 'Неизвестный исполнитель')
        elif source == 'youtube':
            source_icon = "📺"
            artist_info = track.get('artist', 'Неизвестный автор')
        else:
            source_icon = "🔍"
            artist_info = 'Неизвестно'

        message_text += f"{idx}. {source_icon} *{title}*\n"
        message_text += f"   👤 {artist_info}\n"

        duration = track.get('duration', '0:00')
        message_text += f"   ⏱ {duration}\n\n"

    message_text += "Выберите трек для скачивания:"

    return message_text


def create_search_keyboard(results, page=0, results_per_page=5, show_all_button=True):
    """Создает инлайн-клавиатуру для результатов поиска"""
    markup = types.InlineKeyboardMarkup(row_width=2)

    start_idx = page * results_per_page
    end_idx = start_idx + results_per_page
    page_results = results[start_idx:end_idx]

    for track in page_results:
        idx = track.get('global_index', 0)
        title = track.get('title', 'Трек')
        source = track.get('source', 'unknown')

        if source == 'yandex':
            source_icon = "🎵"
        elif source == 'youtube':
            source_icon = "📺"
        else:
            source_icon = "🔍"

        btn_text = f"{source_icon} {idx}. {title[:15]}..."

        if source == 'yandex':
            btn_data = f"ya_{track.get('track_id', 0)}_{track.get('album_id', 0)}_{page}"
        elif source == 'youtube':
            btn_data = f"yt_{track.get('video_id', '')}_{page}"
        else:
            btn_data = f"info_{idx}_{page}"

        markup.add(types.InlineKeyboardButton(btn_text, callback_data=btn_data))

    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton("◀️ Назад", callback_data=f"page_{page - 1}"))

    if end_idx < len(results):
        nav_buttons.append(types.InlineKeyboardButton("Вперед ▶️", callback_data=f"page_{page + 1}"))

    if nav_buttons:
        markup.add(*nav_buttons)

    filter_buttons = []

    if show_all_button:
        filter_buttons.append(types.InlineKeyboardButton("🌐 Везде", callback_data="filter_all"))

    filter_buttons.extend([
        types.InlineKeyboardButton("🎵 Яндекс", callback_data="filter_yandex"),
        types.InlineKeyboardButton("📺 YouTube", callback_data="filter_youtube"),
        types.InlineKeyboardButton("🔄 Новый поиск", callback_data="new_search"),
    ])

    markup.add(*filter_buttons)

    return markup


# --- ФУНКЦИИ ДЛЯ РАБОТЫ С ПАПКАМИ ---
def get_folder_files(folder_path):
    """Получает список файлов в папке"""
    try:
        files = []
        if not os.path.exists(folder_path):
            print(f"[!] Папка не существует: {folder_path}")
            return files

        for file in os.listdir(folder_path):
            if file.endswith('.mp3'):
                file_path = os.path.join(folder_path, file)
                file_size = os.path.getsize(file_path) / (1024 * 1024)
                files.append({
                    'name': file,
                    'path': file_path,
                    'size': round(file_size, 2)
                })
        files.sort(key=lambda x: x['name'])
        return files
    except Exception as e:
        print(f"[!] Ошибка получения файлов из папки {folder_path}: {e}")
        return []


def create_files_keyboard(files, page=0, files_per_page=10, folder_type="music"):
    """Создает клавиатуру для выбора файлов из папки"""
    markup = types.InlineKeyboardMarkup(row_width=2)

    start_idx = page * files_per_page
    end_idx = start_idx + files_per_page
    page_files = files[start_idx:end_idx]

    for i, file in enumerate(page_files):
        btn_text = f"📄 {file['name'][:20]}..."
        btn_data = f"file_{folder_type}_{start_idx + i}_{page}"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=btn_data))

    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton("◀️ Назад", callback_data=f"files_{folder_type}_{page - 1}"))

    if end_idx < len(files):
        nav_buttons.append(types.InlineKeyboardButton("Вперед ▶️", callback_data=f"files_{folder_type}_{page + 1}"))

    if nav_buttons:
        markup.row(*nav_buttons)

    markup.add(
        types.InlineKeyboardButton("🗑️ Очистить кэш", callback_data="clear_cache"),
        types.InlineKeyboardButton("🔙 Назад к меню", callback_data="back_to_menu")
    )

    return markup


# --- ОСНОВНАЯ ФУНКЦИЯ ДЛЯ АВТОМАТИЧЕСКОГО ПОИСКА ---
def process_search_query(chat_id, query, is_command=False):
    """Обрабатывает поисковый запрос (автоматически или по команде)"""
    try:
        query = query.strip()

        if not query or len(query) < 2:
            if is_command:
                bot.send_message(chat_id, "❌ Запрос слишком короткий. Введите название песни или исполнителя.")
            return

        if is_command:
            wait_msg = bot.send_message(chat_id, f"🔍 Ищу '{query}' во всех источниках...")
        else:
            wait_msg = bot.send_message(chat_id, f"🔍 Автоматический поиск: '{query}'...")

        results = universal_search_all(query, limit_per_service=5)

        if not results:
            bot.edit_message_text(f"❌ По запросу '{query}' ничего не найдено.",
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
            print(f"[!] Ошибка отправки результатов: {e}")
            bot.edit_message_text(f"✅ Найдено {len(results)} результатов. Используйте кнопки ниже для выбора.",
                                  chat_id=chat_id,
                                  message_id=wait_msg.message_id,
                                  reply_markup=keyboard)

    except Exception as e:
        print(f"[!] Ошибка при обработке поискового запроса: {e}")


# ============================================
# ОБРАБОТЧИКИ КОМАНД TELEGRAM
# ============================================

# Измененный /promo handler
@bot.message_handler(commands=['promo'])
def handle_promo(message):
    """Активация промокода"""
    try:
        user_id = message.from_user.id
        print(f"[DEBUG] /promo command from user {user_id}")

        # Получаем промокод из команды
        parts = message.text.split()
        if len(parts) < 2:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🎁 Активировать промокод", callback_data="activate_promo"))

            bot.reply_to(message,
                         "🎁 *Активация промокода*\n\n"
                         "Для активации промокода используйте команду:\n"
                         "`/promo ВАШ_ПРОМОКОД`\n\n"
                         "Или нажмите кнопку ниже для ввода промокода:",
                         parse_mode='Markdown',
                         reply_markup=markup)
            return

        promo_code = parts[1].strip()
        print(f"[DEBUG] Trying to use promo code: {promo_code} for user {user_id}")

        # Проверяем длину промокода
        if len(promo_code) < 3:
            bot.reply_to(message, "❌ Промокод слишком короткий. Минимальная длина - 3 символа.")
            return

        # Проверяем, является ли пользователь администратором
        if user_id in ADMIN_IDS:
            bot.reply_to(message,
                         "⚡ *Вы администратор!*\n\n"
                         "Вам автоматически предоставлена бесконечная полная подписка.\n"
                         "Промокоды вам не нужны!",
                         parse_mode='Markdown')
            return

        # Показываем ожидание
        wait_msg = bot.reply_to(message, f"🔍 Проверяю промокод `{promo_code}`...", parse_mode='Markdown')

        # Активируем промокод
        print(f"[DEBUG] Calling database.use_promo_code...")
        result = database.use_promo_code(user_id, promo_code)
        print(f"[DEBUG] Promo code result: {result}")

        # Удаляем сообщение ожидания
        try:
            bot.delete_message(message.chat.id, wait_msg.message_id)
        except:
            pass

        # Отправляем результат
        if result.get('success'):
            # Получаем обновленную информацию о подписке
            has_access, msg = database.check_subscription(user_id)

            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton("📊 Моя подписка", callback_data="back_to_subscribe"),
                types.InlineKeyboardButton("🎵 Скачать музыку", callback_data="new_search")
            )

            bot.reply_to(message,
                         f"🎉 *Успешно!*\n\n"
                         f"{result['message']}\n\n"
                         f"🚀 *Начните прямо сейчас:*\n"
                         f"1. Отправьте название песни в чат\n"
                         f"2. Используйте поиск через кнопки\n"
                         f"3. Отправьте ссылку на трек",
                         parse_mode='Markdown',
                         reply_markup=markup)
        else:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🔄 Попробовать другой код", callback_data="activate_promo"))

            bot.reply_to(message,
                         f"❌ *Не удалось активировать промокод*\n\n"
                         f"*Код:* `{promo_code}`\n"
                         f"*Причина:* {result.get('message', 'Неизвестная ошибка')}\n\n"
                         f"💡 *Советы:*\n"
                         f"• Проверьте правильность написания\n"
                         f"• Убедитесь, что промокод еще действителен\n"
                         f"• Помните: один пользователь = один промокод",
                         parse_mode='Markdown',
                         reply_markup=markup)

    except Exception as e:
        print(f"[ERROR] Ошибка обработки промокода: {e}")
        traceback.print_exc()
        bot.reply_to(message,
                     "❌ Произошла ошибка при обработке промокода. Попробуйте позже.")


# Обновленная функция handle_subscribe (убрать кнопки промокодов)
@bot.message_handler(commands=['subscribe'])
def handle_subscribe(message):
    """Обработчик команды подписки"""
    try:
        user_id = message.from_user.id

        # Проверяем наличие подписки
        has_access, msg = database.check_subscription(user_id)

        # Создаем клавиатуру для ответа
        markup = types.InlineKeyboardMarkup(row_width=1)

        if not has_access:
            # У пользователя нет активной подписки
            markup.add(
                types.InlineKeyboardButton("💰 Купить подписку (49₽/месяц)", callback_data="buy_subscription"),
                types.InlineKeyboardButton("📞 Связаться с администратором", callback_data="contact_admin")
            )

            reply_text = (
                "🚫 *У вас нет активной подписки*\n\n"
                "🔓 *Доступ ограничен:*\n"
                "• ❌ Скачивание музыки недоступно\n"
                "• ❌ Поиск с ограничениями\n\n"
                "💡 *Как получить доступ:*\n"
                "1. 💰 Купите подписку (всего 49₽/месяц)\n"
                "2. 📞 Свяжитесь с администратором\n"
                "3. 🎁 Если есть промокод - используйте /promo КОД\n\n"
                "✨ *Оформите подписку и получите доступ ко всем функциям!*"
            )

            bot.reply_to(message, reply_text, parse_mode='Markdown', reply_markup=markup)
        else:
            # У пользователя уже есть подписка
            markup.add(
                types.InlineKeyboardButton("📊 Статистика", callback_data="stats"),
            )

            reply_text = f"✅ *Информация о подписке*\n\n{msg}\n\n"
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


# Обновленная функция send_welcome (убрать упоминание промокодов)
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    """Начальное приветствие"""
    try:
        user_id = message.from_user.id
        username = message.from_user.username
        first_name = message.from_user.first_name

        # Добавляем пользователя в базу
        database.add_user(
            user_id=user_id,
            username=username,
            first_name=first_name,
            last_name=message.from_user.last_name,
            language_code=message.from_user.language_code,
            is_premium=message.from_user.is_premium if hasattr(message.from_user, 'is_premium') else False
        )

        # Создаем клавиатуру
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        btn_liked = types.KeyboardButton('🎵 Мне понравилось')
        btn_search = types.KeyboardButton('🔍 Поиск музыки')
        btn_youtube = types.KeyboardButton('📺 YouTube')
        btn_music = types.KeyboardButton('📁 Музыка')
        btn_podcasts = types.KeyboardButton('🎙️ Подкасты')
        btn_clear = types.KeyboardButton('🗑️ Очистить кэш')
        btn_subscribe = types.KeyboardButton('💎 Подписка')
        btn_help = types.KeyboardButton('📋 Помощь')

        keyboard.row(btn_liked, btn_search)
        keyboard.row(btn_youtube)
        keyboard.row(btn_music, btn_podcasts, btn_clear)
        keyboard.row(btn_subscribe, btn_help)

        # Проверяем, является ли пользователь администратором
        if user_id in ADMIN_IDS:
            admin_text = "⚡ *Вы администратор!* Вам доступны все функции бота!"
            welcome_text = (
                f"🎵 *Привет, Администратор {first_name or 'друг'}!*\n\n"
                f"{admin_text}\n\n"
                "*Добро пожаловать в универсальный музыкальный бот!*\n\n"

                "⚡ *Что умеет бот:*\n"
                "• 🔍 *Автоматический поиск* - просто отправьте название песни\n"
                "• 🎵 *Яндекс.Музыка* - поиск и скачивание треков\n"
                "• 📺 *YouTube* - скачивание музыки с YouTube\n"
                "• 💎 *PREMIUM подписка* - 49₽/месяц для пользователей\n\n"

                "📋 *Основные команды:*\n"
                "• /subscribe - информация о подписке\n"
                "• /admin_create_promo - создать промокод\n"
                "• /admin_stats - статистика бота\n"
                "• /status - статус бота\n"
                "• /clear_cache - очистить кэш\n\n"

                "🚀 *Начните с поиска музыки!*"
            )
        else:
            welcome_text = (
                f"🎵 *Привет, {first_name or 'друг'}!*\n\n"
                "*Добро пожаловать в универсальный музыкальный бот!*\n\n"

                "⚡ *Что умеет бот:*\n"
                "• 🔍 *Автоматический поиск* - просто отправьте название песни\n"
                "• 🎵 *Яндекс.Музыка* - поиск и скачивание треков\n"
                "• 📺 *YouTube* - скачивание музыки с YouTube\n"
                "• 💎 *PREMIUM подписка* - 49₽/месяц\n\n"

                "📋 *Основные команды:*\n"
                "• /subscribe - информация о подписке\n"
                "• /status - статус бота\n"
                "• /clear_cache - очистить кэш\n\n"

                "🚀 *Начните с поиска музыки!*"
            )

        bot.reply_to(message, welcome_text, parse_mode='Markdown',
                     disable_web_page_preview=True, reply_markup=keyboard)

        # Проверяем наличие подписки и отправляем приветственное сообщение
        has_access, msg = database.check_subscription(user_id)
        if not has_access and user_id not in ADMIN_IDS:
            time.sleep(1)
            bot.send_message(
                message.chat.id,
                "💡 *Совет:* Для полного доступа ко всем функциям оформите подписку "
                "или активируйте промокод командой /promo КОД",
                parse_mode='Markdown'
            )

    except Exception as e:
        print(f"[ERROR] Ошибка в обработчике start: {e}")
        traceback.print_exc()
        bot.reply_to(message, "Добро пожаловать! Используйте кнопки меню для навигации.")


@bot.message_handler(commands=['status', 'check'])
def handle_status(message):
    """Показывает статус подключения к сервисам"""
    status_text = "📊 *Статус подключений бота*\n\n"

    if ym_client:
        try:
            account_info = ym_client.me.account_status()
            status_text += "✅ *Яндекс.Музыка*: Авторизован\n"
        except:
            status_text += "❌ *Яндекс.Музыка*: Ошибка авторизации\n"
    else:
        status_text += "⚠️  *Яндекс.Музыка*: Токен не указан\n"

    status_text += "✅ *YouTube*: Сервис доступен\n"

    music_files = len(get_folder_files(MUSIC_DIR))
    podcast_files = len(get_folder_files(PODCASTS_DIR))

    status_text += f"\n📊 *Статистика файлов:*\n"
    status_text += f"• 🎵 Музыка: {music_files} файлов\n"
    status_text += f"• 🎙️ Подкасты: {podcast_files} файлов\n"

    status_text += f"\n📁 *Пути к папкам:*\n"
    status_text += f"• Музыка: `{os.path.abspath(MUSIC_DIR)}`\n"
    status_text += f"• Подкасты: `{os.path.abspath(PODCASTS_DIR)}`\n"

    bot.reply_to(message, status_text, parse_mode='Markdown')


# ============================================
# АДМИНИСТРАТИВНЫЕ КОМАНДЫ
# ============================================

@bot.message_handler(commands=['admin_create_promo'])
def handle_create_promo(message):
    """Создание промокода (только для администраторов)"""
    try:
        # Проверка прав администратора
        user_id = message.from_user.id
        if user_id not in ADMIN_IDS:
            bot.reply_to(message, "❌ У вас нет прав для выполнения этой команды.")
            return

        # Парсинг команды
        parts = message.text.split()

        if len(parts) < 3:
            bot.reply_to(message,
                         "📝 *Использование:*\n"
                         "`/admin_create_promo КОД ТИП [МАКС_ИСПОЛЬЗОВАНИЙ] [ОПИСАНИЕ]`\n\n"
                         "⚠️ *Внимание:* Все промокоды действуют 30 дней!\n\n"
                         "*Примеры:*\n"
                         "• `/admin_create_promo WELCOME premium 100 Приветственный код`\n"
                         "• `/admin_create_promo SPECIAL premium 10 Специальная акция`\n"
                         "• `/admin_create_promo TEST premium 5 Тестовый промокод`\n\n"
                         "*Единственный тип подписки:* premium\n"
                         "*Срок действия:* 30 дней (фиксировано)",
                         parse_mode='Markdown')
            return

        promo_code = parts[1].upper()
        sub_type = parts[2].lower()

        # Проверяем тип подписки
        if sub_type != 'premium':
            bot.reply_to(message, "❌ Неверный тип подписки. Допустимый: premium")
            return

        max_uses = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1

        # Всегда 30 дней, не принимаем параметр дней
        # Собираем описание
        description = ' '.join(parts[4:]) if len(parts) > 4 else None

        # Создание промокода (всегда на 30 дней)
        result = database.create_promo_code(
            code=promo_code,
            subscription_type=sub_type,
            max_uses=max_uses,
            days_valid=30,  # Фиксированное значение
            description=description
        )

        if result['success']:
            bot.reply_to(message, result['message'], parse_mode='Markdown')
        else:
            bot.reply_to(message, result['message'], parse_mode='Markdown')

    except Exception as e:
        print(f"[ERROR] Ошибка создания промокода: {e}")
        traceback.print_exc()
        bot.reply_to(message, f"❌ Ошибка: {str(e)}")


@bot.message_handler(commands=['admin_stats'])
def handle_admin_stats(message):
    """Статистика бота (админы)"""
    try:
        user_id = message.from_user.id
        if user_id not in ADMIN_IDS:
            bot.reply_to(message, "❌ У вас нет прав для выполнения этой команды.")
            return

        # Получаем статистику
        active_users = database.get_active_users_count()
        total_downloads = database.get_total_downloads()
        all_promos = database.get_all_promo_codes()

        # Статистика по промокодам
        active_promos = [p for p in all_promos if p['is_active']]
        used_promos = sum(p['uses_count'] for p in all_promos)
        total_promos_created = len(all_promos)

        stats_text = (
            "📊 *Статистика бота*\n\n"
            f"👥 *Пользователи:*\n"
            f"• Активные (30 дней): {active_users}\n\n"
            f"📥 *Скачивания:*\n"
            f"• Всего: {total_downloads}\n\n"
            f"🎁 *Промокоды:*\n"
            f"• Всего создано: {total_promos_created}\n"
            f"• Активных: {len(active_promos)}\n"
            f"• Использовано раз: {used_promos}\n\n"
            f"💾 *Кэш:*\n"
            f"• Музыка: {len(get_folder_files(MUSIC_DIR))} файлов\n"
            f"• Подкасты: {len(get_folder_files(PODCASTS_DIR))} файлов\n\n"
            f"⏰ *Время работы:* {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )

        # Добавляем список активных промокодов
        if active_promos:
            stats_text += "\n\n🎫 *Активные промокоды:*\n"
            for promo in active_promos[:10]:  # Показываем первые 10
                expiry = promo['expiry_date'].split()[0] if promo['expiry_date'] else "бессрочно"
                stats_text += f"• `{promo['code']}` - {promo['subscription_type']} ({promo['uses_count']}/{promo['max_uses']}) до {expiry}\n"
            if len(active_promos) > 10:
                stats_text += f"• ... и еще {len(active_promos) - 10}"

        bot.reply_to(message, stats_text, parse_mode='Markdown')

    except Exception as e:
        print(f"[ERROR] Ошибка статистики: {e}")
        traceback.print_exc()
        bot.reply_to(message, f"❌ Ошибка: {str(e)}")


# ============================================
# ОСНОВНЫЕ КОМАНДЫ БОТА
# ============================================

@bot.message_handler(commands=['clear_cache', 'clear'])
def handle_clear_cache(message):
    """Очищает кэш файлов"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ Да, очистить всё", callback_data="clear_cache_confirm"),
        types.InlineKeyboardButton("❌ Нет, отменить", callback_data="clear_cache_cancel")
    )
    bot.reply_to(message,
                 "⚠️ *Внимание!*\n\n"
                 "Вы уверены, что хотите удалить ВСЕ файлы из кэша?\n\n"
                 "🗑️ *Будет удалено:*\n"
                 "• Все скачанные треки\n"
                 "• Все подкасты\n\n"
                 "⚡ *Это действие нельзя отменить!*",
                 parse_mode='Markdown',
                 reply_markup=markup)


@bot.message_handler(commands=['search_all', 'search'])
def handle_search_all(message):
    """Поиск во всех доступных сервисах"""
    query = message.text.replace('/search_all', '').replace('/search', '').strip()
    process_search_query(message.chat.id, query, is_command=True)


@bot.message_handler(commands=['search_yandex'])
def handle_search_yandex(message):
    """Поиск только в Яндекс.Музыке"""
    if not ym_client:
        bot.reply_to(message, "❌ Клиент Яндекс.Музыки не настроен.")
        return

    query = message.text.replace('/search_yandex', '').strip()

    if not query:
        bot.reply_to(message, "📝 Использование: `/search_yandex <запрос>`", parse_mode='Markdown')
        return

    wait_msg = bot.reply_to(message, f"🎵 Ищу '{query}' в Яндекс.Музыке...")

    results = search_yandex_music(query, limit=15)

    if not results:
        bot.edit_message_text(f"❌ По запросу '{query}' ничего не найдено.",
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
        print(f"[!] Ошибка отправки результатов: {e}")
        bot.edit_message_text(f"✅ Найдено {len(results)} результатов. Используйте кнопки ниже для выбора.",
                              chat_id=message.chat.id,
                              message_id=wait_msg.message_id,
                              reply_markup=keyboard)


@bot.message_handler(commands=['search_youtube', 'youtube'])
def handle_search_youtube(message):
    """Поиск только на YouTube"""
    query = message.text.replace('/search_youtube', '').replace('/youtube', '').strip()

    if not query:
        bot.reply_to(message, "📝 Использование: `/search_youtube <запрос>`", parse_mode='Markdown')
        return

    wait_msg = bot.reply_to(message, f"📺 Ищу '{query}' на YouTube...")

    results = search_youtube_music(query, limit=15)

    if not results:
        bot.edit_message_text(f"❌ По запросу '{query}' ничего не найдено на YouTube.",
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
        print(f"[!] Ошибка отправки результатов: {e}")
        bot.edit_message_text(f"✅ Найдено {len(results)} результатов. Используйте кнопки ниже для выбора.",
                              chat_id=message.chat.id,
                              message_id=wait_msg.message_id,
                              reply_markup=keyboard)


# ============================================
# ОБРАБОТЧИКИ КНОПОК МЕНЮ
# ============================================

@bot.message_handler(func=lambda m: m.text and any(x in m.text for x in ['music.yandex', 'youtube.com', 'youtu.be']))
def handle_music_link(message):
    """Обрабатывает прямые ссылки на музыку"""
    try:
        # Проверка доступа
        user_id = message.from_user.id
        has_access, msg = database.check_subscription(user_id)
        if not has_access:
            bot.reply_to(message,
                         f"🚫 *Доступ запрещен!*\n\n"
                         f"{msg}\n\n"
                         f"💡 Для доступа к скачиванию:\n"
                         f"• Используйте команду /subscribe\n"
                         f"• Активируйте промокод /promo КОД\n"
                         f"• Обратитесь к администратору",
                         parse_mode='Markdown')
            return

        wait_msg = bot.reply_to(message, "🔗 Анализирую ссылку...")
        url = message.text.strip()

        if 'music.yandex' in url:
            import re
            match = re.search(r'music\.yandex\.\w+/album/(\d+)/track/(\d+)', url)
            if match:
                album_id, track_id = match.groups()
                audio_path, title, performer, status = download_yandex_track_fast(int(track_id), int(album_id))
                if status == "success" and audio_path:
                    # Увеличиваем счетчик скачиваний
                    database.increment_download(user_id)

                    file_type = "подкаст" if audio_path.startswith(PODCASTS_DIR) else "музыка"
                    caption = f"🎵 {title} (Яндекс.Музыка) | 📁 {file_type}"

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
                        bot.edit_message_text("❌ Не удалось отправить аудио.",
                                              chat_id=message.chat.id,
                                              message_id=wait_msg.message_id)
                    return
            bot.edit_message_text(f"❌ Не удалось обработать Яндекс-ссылку",
                                  chat_id=message.chat.id,
                                  message_id=wait_msg.message_id)

        elif 'youtube.com' in url or 'youtu.be' in url:
            bot.edit_message_text("📥 Скачиваю с YouTube...",
                                  chat_id=message.chat.id,
                                  message_id=wait_msg.message_id)

            audio_path, title, performer, status = download_from_youtube_fast(url, is_url=True)

            if status == "success" and audio_path:
                # Увеличиваем счетчик скачиваний
                database.increment_download(user_id)

                file_type = "подкаст" if audio_path.startswith(PODCASTS_DIR) else "музыка"
                caption = f"🎵 {title} (YouTube) | 📁 {file_type}"

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
                    bot.edit_message_text("❌ Не удалось отправить аудио.",
                                          chat_id=message.chat.id,
                                          message_id=wait_msg.message_id)
                return
            else:
                error_msg = "❌ Ошибка загрузки"
                bot.edit_message_text(f"{error_msg}: {status}",
                                      chat_id=message.chat.id,
                                      message_id=wait_msg.message_id)
        else:
            bot.edit_message_text(f"❌ Формат ссылки не поддерживается",
                                  chat_id=message.chat.id,
                                  message_id=wait_msg.message_id)
    except Exception as e:
        print(f"[!] Ошибка в обработчике ссылки: {e}")
        traceback.print_exc()
        try:
            bot.reply_to(message, f"❌ Ошибка: {str(e)[:100]}")
        except:
            pass


@bot.message_handler(func=lambda message: message.text == '🎵 Мне понравилось')
def handle_liked_button(message):
    bot.reply_to(message,
                 "🎵 *Мне понравилось*\n\n"
                 "🎧 *Эта функция в разработке...*\n\n"
                 "Скоро здесь можно будет:\n"
                 "• Смотреть историю скачиваний\n"
                 "• Добавлять треки в избранное\n"
                 "• Создавать плейлисты\n\n"
                 "Следите за обновлениями!",
                 parse_mode='Markdown')


@bot.message_handler(func=lambda message: message.text == '🔍 Поиск музыки')
def handle_search_button(message):
    bot.reply_to(message,
                 "🔍 *Поиск музыки*\n\n"
                 "🎵 *Просто отправьте в чат:*\n"
                 "• Название песни\n"
                 "• Имя исполнителя\n"
                 "• Ссылку на трек\n\n"
                 "⚡ *Автоматический поиск во всех источниках!*\n\n"
                 "💡 *Примеры:*\n"
                 "• `Shape of You`\n"
                 "• `Imagine Dragons Believer`\n"
                 "• `https://youtube.com/...`\n\n"
                 "🎯 *Или используйте команды:*\n"
                 "• `/search_all <запрос>` - поиск везде\n"
                 "• `/search_yandex <запрос>` - только Яндекс\n"
                 "• `/search_youtube <запрос>` - только YouTube",
                 parse_mode='Markdown')


@bot.message_handler(func=lambda message: message.text == '📺 YouTube')
def handle_youtube_button(message):
    bot.reply_to(message,
                 "📺 *YouTube Музыка*\n\n"
                 "🎵 *Как скачивать:*\n"
                 "1. Отправьте название песни в чат\n"
                 "2. Или отправьте ссылку на видео\n"
                 "3. Выберите трек из результатов\n"
                 "4. Скачайте аудиофайл\n\n"
                 "🔗 *Поддерживаемые ссылки:*\n"
                 "• Видео: `youtube.com/watch?...`\n"
                 "• Короткие: `youtu.be/...`\n"
                 "• Плейлисты (первое видео)\n\n"
                 "⚡ *Пример:* Просто отправьте `Shape of You`",
                 parse_mode='Markdown')


@bot.message_handler(func=lambda message: message.text == '📁 Музыка')
def handle_music_folder(message):
    """Показывает список музыкальных файлов"""
    files = get_folder_files(MUSIC_DIR)

    if not files:
        bot.reply_to(message,
                     "🎵 *Папка с музыкой*\n\n"
                     "📭 Папка пуста\n\n"
                     "💡 *Совет:*\n"
                     "• Отправьте название песни в чат\n"
                     "• Скачайте треки из поиска\n"
                     "• Файлы появятся здесь автоматически")
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
    """Показывает список подкастов"""
    files = get_folder_files(PODCASTS_DIR)

    if not files:
        bot.reply_to(message,
                     "🎙️ *Папка с подкастами*\n\n"
                     "📭 Папка пуста\n\n"
                     "💡 *Совет:*\n"
                     "• Скачайте длинные видео с YouTube\n"
                     "• Подкасты сохраняются сюда автоматически\n"
                     "• Файлы >20 минут считаются подкастами")
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


@bot.message_handler(func=lambda message: message.text == '📋 Помощь')
def handle_help_button(message):
    send_welcome(message)


# ============================================
# АВТОМАТИЧЕСКИЙ ПОИСК ПО ТЕКСТОВОМУ СООБЩЕНИЮ
# ============================================

@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_auto_search(message):
    """Автоматически ищет музыку по любому текстовому сообщению"""
    try:
        if message.text.startswith('/'):
            return

        # Список кнопок меню, которые уже обработаны выше
        button_texts = [
            '🎵 Мне понравилось', '🔍 Поиск музыки', '📺 YouTube',
            '📁 Музыка', '🎙️ Подкасты', '🗑️ Очистить кэш',
            '💎 Подписка', '📋 Помощь'
        ]

        if message.text in button_texts:
            return

        if any(x in message.text for x in ['music.yandex', 'youtube.com', 'youtu.be']):
            return

        query = message.text.strip()
        if len(query) < 2:
            return

        if len(query) > 100:
            bot.reply_to(message, "❌ Запрос слишком длинный. Пожалуйста, укажите более короткое название.")
            return

        if query.lower() in ['поиск', 'search', 'искать', 'музыка', 'песня']:
            return

        # Проверяем доступ перед поиском
        user_id = message.from_user.id
        has_access, msg = database.check_subscription(user_id)

        if not has_access:
            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton("💎 Получить доступ", callback_data="buy_subscription"),
                types.InlineKeyboardButton("🎁 Активировать промокод", callback_data="activate_promo")
            )

            bot.reply_to(message,
                         f"🔒 *Доступ ограничен*\n\n"
                         f"Для поиска и скачивания музыки нужна подписка.\n\n"
                         f"{msg}\n\n"
                         f"💡 *Как получить доступ:*\n"
                         f"1. Активируйте промокод\n"
                         f"2. Оформите подписку\n"
                         f"3. Обратитесь к администратору",
                         parse_mode='Markdown',
                         reply_markup=markup)
            return

        # Если доступ есть - выполняем поиск
        process_search_query(message.chat.id, query, is_command=False)

    except Exception as e:
        print(f"[!] Ошибка в автоматическом поиске: {e}")
        traceback.print_exc()


# ============================================
# ОБРАБОТЧИК INLINE-КНОПОК
# ============================================

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    """Упрощенный обработчик callback-запросов"""
    try:
        chat_id = call.message.chat.id
        message_id = call.message.message_id
        data = call.data

        print(f"[DEBUG] Callback received from user {call.from_user.id}: {data}")

        # Отвечаем сразу, чтобы убрать "часики"
        try:
            bot.answer_callback_query(call.id)
        except Exception as e:
            print(f"[DEBUG] Error answering callback query: {e}")

        # Разбираем данные
        if data == "new_search":
            try:
                bot.edit_message_text(
                    "🔍 *Новый поиск*\n\n"
                    "Просто отправьте название песни или исполнителя в чат!\n\n"
                    "🎵 *Примеры:*\n"
                    "• Shape of You\n"
                    "• Imagine Dragons\n"
                    "• Queen Bohemian Rhapsody\n\n"
                    "⚡ Поиск работает автоматически!",
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='Markdown'
                )
            except Exception as e:
                print(f"[ERROR] Failed to edit message for new_search: {e}")
            return

        elif data == "back_to_menu":
            send_welcome(call.message)
            return

        elif data == "clear_cache":
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("✅ Да, очистить всё", callback_data="clear_cache_confirm"),
                types.InlineKeyboardButton("❌ Нет, отменить", callback_data="clear_cache_cancel")
            )
            try:
                bot.edit_message_text(
                    "⚠️ *Внимание!*\n\n"
                    "Вы уверены, что хотите удалить ВСЕ файлы из кэша?\n\n"
                    "🗑️ *Будет удалено:*\n"
                    "• Все скачанные треки\n"
                    "• Все подкасты\n\n"
                    "⚡ *Это действие нельзя отменить!*",
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
                bot.edit_message_text(
                    f"✅ *Кэш очищен!*\n\n"
                    f"🗑️ Удалено файлов: *{deleted_count}*\n\n"
                    f"💾 Теперь у вас {deleted_count} МБ свободного места.",
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='Markdown'
                )
            except Exception as e:
                print(f"[ERROR] Failed to show clear_cache result: {e}")
            return

        elif data == "clear_cache_cancel":
            try:
                bot.edit_message_text(
                    "❌ *Очистка кэша отменена.*\n\n"
                    "Файлы не были удалены.",
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='Markdown'
                )
            except Exception as e:
                print(f"[ERROR] Failed to show clear_cache cancel: {e}")
            return

        # Обработка подписки
        elif data == "activate_promo":
            try:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_subscribe"))

                bot.edit_message_text(
                    "🎁 *Активация промокода*\n\n"
                    "Отправьте промокод в формате:\n"
                    "`/promo ВАШ_КОД`\n\n"
                    "💡 *Важно:*\n"
                    "• Каждый пользователь может активировать только один промокод\n"
                    "• После активации промокод нельзя изменить\n"
                    "• Промокоды дают доступ на 30 дней\n"
                    "• Исключение: V1_GAN13 - вечная подписка",
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
                        "🎁 *Активация промокода*\n\n"
                        "Отправьте промокод командой:\n"
                        "`/promo ВАШ_КОД`",
                        parse_mode='Markdown'
                    )
                except Exception as e2:
                    print(f"[ERROR] Failed to send message as fallback: {e2}")
            return

        elif data == "buy_subscription":
            try:
                markup = types.InlineKeyboardMarkup(row_width=2)
                markup.add(
                    types.InlineKeyboardButton("🎁 Активировать промокод", callback_data="activate_promo"),
                    types.InlineKeyboardButton("📞 Связаться", url="https://t.me/your_username")
                )
                bot.edit_message_text(
                    "💳 *Оформление подписки*\n\n"
                    "📋 *Выберите способ:*\n\n"
                    "1. 🎁 *Промокод* - бесплатно и навсегда\n"
                    "2. 💰 *Платная подписка* - 49₽/месяц через администратора\n"
                    "3. 📞 *Связь* - для консультации\n\n"
                    "💡 *Рекомендуем сначала попробовать промокоды!*",
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
                markup.add(
                    types.InlineKeyboardButton("🎁 Активировать промокод", callback_data="activate_promo"),
                    types.InlineKeyboardButton("📞 Связаться", url="https://t.me/your_username")
                )
                bot.edit_message_text(
                    "💰 *Тарифы подписки*\n\n"
                    "🔹 *PREMIUM подписка* (49₽/месяц):\n"
                    "• Неограниченное скачивание музыки\n"
                    "• Доступ ко всем источникам (YouTube, Яндекс.Музыка)\n"
                    "• Поддержка 24/7\n"
                    "• Быстрая загрузка\n\n"
                    "💬 *Для оформления подписки:*\n"
                    "1. Свяжитесь с администратором\n"
                    "2. Укажите желаемый срок подписки\n"
                    "3. После оплата вы получите доступ\n\n"
                    "🎁 *Или активируйте промокод для бесплатного доступа!*",
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

                # Проверяем, является ли пользователь администратором
                if user_id in ADMIN_IDS:
                    stats_text = (
                        "⚡ *АДМИНИСТРАТОРСКАЯ СТАТИСТИКА*\n\n"
                        "📊 *Ваши привилегии:*\n"
                        "• ♾️ Вечная полная подписка\n"
                        "• ⚙️ Административные права\n"
                        "• 📈 Доступ ко всей статистике\n"
                        "• 🔧 Управление промокодами\n\n"
                        "💎 *Статус:* АДМИНИСТРАТОР (ВЕЧНАЯ подписка)"
                    )

                    markup = types.InlineKeyboardMarkup()
                    markup.add(
                        types.InlineKeyboardButton("📈 Статистика бота", callback_data="admin_stats"),
                        types.InlineKeyboardButton("🎫 Управление промокодами", callback_data="manage_promos"),
                        types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_subscribe")
                    )
                else:
                    stats = database.get_user_stats(user_id)

                    stats_text = "📊 *Ваша статистика*\n\n"

                    if stats:
                        stats_text += (
                            f"📥 *Скачивания:*\n"
                            f"• Всего: {stats.get('total_downloads', 0)}\n"
                            f"• Сегодня: {stats.get('today_downloads', 0)}\n"
                            f"• Максимум за день: {stats.get('max_daily_downloads', 0)}\n"
                            f"• Активных дней: {stats.get('active_days', 0)}\n\n"
                        )

                        if 'current_subscription' in stats:
                            sub = stats['current_subscription']
                            sub_type = sub.get('type', 'premium').upper()

                            if sub.get('promo_code') == 'V1_GAN13':
                                source = "🎁 ВЕЧНЫЙ промокод: V1_GAN13"
                            elif sub.get('is_promo'):
                                source = f"🎁 Промокод: {sub.get('promo_code', '')}"
                            else:
                                source = "💳 Оплата"

                            stats_text += f"💎 *Подписка:* {sub_type} ({source})\n\n"
                    else:
                        stats_text += "📭 *Статистика отсутствует*\n\n"

                    # Добавляем информацию о подписке
                    has_access, msg = database.check_subscription(user_id)
                    stats_text += f"🔐 *Статус доступа:*\n{msg}"

                    markup = types.InlineKeyboardMarkup()
                    markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_subscribe"))

                bot.edit_message_text(
                    stats_text,
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='Markdown',
                    reply_markup=markup
                )
            except Exception as e:
                print(f"[ERROR] Ошибка при показе статистики: {e}")
                bot.answer_callback_query(call.id, "❌ Ошибка загрузки статистики")
            return

        elif data == "admin_stats":
            try:
                user_id = call.from_user.id
                if user_id not in ADMIN_IDS:
                    bot.answer_callback_query(call.id, "❌ У вас нет прав администратора")
                    return

                # Получаем статистику
                active_users = database.get_active_users_count()
                total_downloads = database.get_total_downloads()
                all_promos = database.get_all_promo_codes()

                # Статистика по промокодам
                active_promos = [p for p in all_promos if p['is_active']]
                used_promos = sum(p['uses_count'] for p in all_promos)
                total_promos_created = len(all_promos)

                stats_text = (
                    "📊 *Статистика бота (Администратор)*\n\n"
                    f"👥 *Пользователи:*\n"
                    f"• Активные (30 дней): {active_users}\n\n"
                    f"📥 *Скачивания:*\n"
                    f"• Всего: {total_downloads}\n\n"
                    f"🎁 *Промокоды:*\n"
                    f"• Всего создано: {total_promos_created}\n"
                    f"• Активных: {len(active_promos)}\n"
                    f"• Использовано раз: {used_promos}\n\n"
                    f"💾 *Кэш:*\n"
                    f"• Музыка: {len(get_folder_files(MUSIC_DIR))} файлов\n"
                    f"• Подкасты: {len(get_folder_files(PODCASTS_DIR))} файлов\n\n"
                    f"⏰ *Время работы:* {datetime.now().strftime('%d.%m.%Y %H:%M')}"
                )

                # Добавляем список активных промокодов
                if active_promos:
                    stats_text += "\n\n🎫 *Активные промокоды:*\n"
                    for promo in active_promos[:10]:
                        expiry = promo['expiry_date'].split()[0] if promo['expiry_date'] else "бессрочно (V1_GAN13)"
                        stats_text += f"• `{promo['code']}` - {promo['subscription_type']} ({promo['uses_count']}/{promo['max_uses']}) до {expiry}\n"
                    if len(active_promos) > 10:
                        stats_text += f"• ... и еще {len(active_promos) - 10}"

                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="stats"))

                bot.edit_message_text(
                    stats_text,
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='Markdown',
                    reply_markup=markup
                )
            except Exception as e:
                print(f"[ERROR] Ошибка административной статистики: {e}")
                bot.answer_callback_query(call.id, "❌ Ошибка загрузки статистики")
            return

        elif data == "contact_admin":
            try:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_subscribe"))

                bot.edit_message_text(
                    "📞 *Связь с администратором*\n\n"
                    "Для связи с администратором:\n\n"
                    "👤 *Контакты:*\n"
                    "• Telegram: @your_username\n"
                    "• Email: admin@example.com\n\n"
                    "💬 *При обращении укажите:*\n"
                    "1. Ваш Telegram ID\n"
                    "2. Цель обращения (подписка, вопрос, проблема)\n"
                    "3. По возможности - скриншот проблемы\n\n"
                    "⏱ *Время ответа:*\n"
                    "• Обычно в течение 24 часов",
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='Markdown',
                    reply_markup=markup
                )
            except Exception as e:
                print(f"[ERROR] Failed to show contact_admin: {e}")
            return

        elif data == "back_to_subscribe":
            # Возвращаемся к меню подписки
            try:
                user_id = call.from_user.id
                has_access, msg = database.check_subscription(user_id)

                markup = types.InlineKeyboardMarkup(row_width=1)

                if not has_access:
                    markup.add(
                        types.InlineKeyboardButton("💰 Купить подписку (49₽/месяц)", callback_data="buy_subscription"),
                        types.InlineKeyboardButton("📞 Связаться с администратором", callback_data="contact_admin")
                    )
                    reply_text = (f"🚫 *У вас нет активной подписки*\n\n"
                                  f"{msg}\n\n"
                                  f"💡 *Как получить доступ:*\n"
                                  f"1. 💰 Купите подписку (всего 49₽/месяц)\n"
                                  f"2. 📞 Свяжитесь с администратором\n"
                                  f"3. 🎁 Если есть промокод - используйте /promo КОД\n\n"
                                  f"✨ *Оформите подписку и получите доступ ко всем функциям!*")
                else:
                    markup.add(
                        types.InlineKeyboardButton("📊 Статистика", callback_data="stats"),
                    )
                    reply_text = f"✅ *Информация о подписке*\n\n{msg}\n\n"
                    reply_text += (
                        "✨ *Ваши возможности:*\n"
                        "• ✅ Скачивание музыки из YouTube\n"
                        "• ✅ Скачивание из Яндекс.Музыки\n"
                        "• ✅ Быстрая загрузка\n"
                        "• ✅ Автоматическая сортировка\n\n"
                        "Что вы хотите сделать?"
                    )

                bot.edit_message_text(
                    reply_text,
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='Markdown',
                    reply_markup=markup
                )
            except Exception as e:
                print(f"[ERROR] Failed to go back to subscribe: {e}")
            return

        # Разбираем сложные callback_data с подчеркиванием
        if '_' in data:
            parts = data.split('_')

            # Пагинация поиска
            if parts[0] == "page":
                try:
                    page = int(parts[1])
                    if chat_id in user_search_history:
                        history = user_search_history[chat_id]
                        query = history['query']
                        results = history['results']

                        message_text = show_search_results(chat_id, query, results, page=page)
                        keyboard = create_search_keyboard(results, page=page, show_all_button=True)

                        bot.edit_message_text(
                            message_text,
                            chat_id=chat_id,
                            message_id=message_id,
                            parse_mode='Markdown',
                            reply_markup=keyboard
                        )
                except Exception as e:
                    print(f"[!] Ошибка пагинации: {e}")

            # Фильтрация
            elif parts[0] == "filter":
                if chat_id not in user_search_history:
                    return

                filter_type = parts[1]
                history = user_search_history[chat_id]
                query = history['query']
                all_results = history['results']

                if filter_type == "all":
                    filtered_results = all_results
                    show_all_button = True
                elif filter_type == "yandex":
                    filtered_results = [r for r in all_results if r.get('source') == 'yandex']
                    show_all_button = False
                elif filter_type == "youtube":
                    filtered_results = [r for r in all_results if r.get('source') == 'youtube']
                    show_all_button = False
                else:
                    filtered_results = all_results
                    show_all_button = True

                if not filtered_results:
                    bot.answer_callback_query(call.id, "Нет результатов с этим фильтром")
                    return

                for i, result in enumerate(filtered_results):
                    result['global_index'] = i + 1

                user_search_history[chat_id]['results'] = filtered_results

                message_text = show_search_results(chat_id, query, filtered_results, page=0)
                keyboard = create_search_keyboard(filtered_results, page=0, show_all_button=show_all_button)

                try:
                    bot.edit_message_text(
                        message_text,
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode='Markdown',
                        reply_markup=keyboard
                    )
                except Exception as e:
                    print(f"[ERROR] Failed to filter: {e}")

            # Скачивание Яндекс трека
            elif parts[0] == "ya" and len(parts) >= 4:
                try:
                    # Проверка доступа перед скачиванием
                    user_id = call.from_user.id
                    has_access, msg = database.check_subscription(user_id)
                    if not has_access:
                        bot.answer_callback_query(call.id, f"🚫 Доступ закрыт")
                        bot.send_message(
                            chat_id,
                            f"🔒 *Доступ запрещен!*\n\n"
                            f"{msg}\n\n"
                            f"💡 Используйте /subscribe для получения доступа",
                            parse_mode='Markdown'
                        )
                        return

                    track_id = int(parts[1])
                    album_id = int(parts[2])
                    page = int(parts[3])

                    bot.edit_message_text(
                        "⚡ *Скачиваю трек из Яндекс.Музыки...*",
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode='Markdown'
                    )

                    audio_path, title, performer, status = download_yandex_track_fast(track_id, album_id)

                    if status == "success" and audio_path and os.path.exists(audio_path):
                        # Увеличиваем счетчик скачиваний
                        database.increment_download(user_id)

                        file_type = "подкаст" if audio_path.startswith(PODCASTS_DIR) else "музыка"
                        caption = f"🎵 {title} (Яндекс.Музыка) | 📁 {file_type}"

                        success = send_audio_fast(
                            chat_id=chat_id,
                            audio_path=audio_path,
                            title=title[:64],
                            performer=performer[:64],
                            caption=caption
                        )

                        if success:
                            # Восстанавливаем результаты поиска
                            if chat_id in user_search_history:
                                history = user_search_history[chat_id]
                                results = history['results']
                                query = history['query']

                                message_text = show_search_results(chat_id, query, results, page=page)
                                keyboard = create_search_keyboard(results, page=page, show_all_button=True)

                                bot.edit_message_text(
                                    f"✅ *Трек скачан!*\n\n"
                                    f"🎵 *{title}*\n"
                                    f"👤 *{performer}*\n\n"
                                    f"✨ *Продолжайте поиск:*",
                                    chat_id=chat_id,
                                    message_id=message_id,
                                    parse_mode='Markdown',
                                    reply_markup=keyboard
                                )
                            else:
                                markup = types.InlineKeyboardMarkup()
                                markup.add(types.InlineKeyboardButton("🔍 Новый поиск", callback_data="new_search"))

                                bot.edit_message_text(
                                    f"✅ *Трек успешно скачан!*\n\n"
                                    f"🎵 *{title}*\n"
                                    f"👤 *{performer}*\n\n"
                                    f"✨ Скачано в папку: {file_type}",
                                    chat_id=chat_id,
                                    message_id=message_id,
                                    parse_mode='Markdown',
                                    reply_markup=markup
                                )
                        else:
                            bot.edit_message_text(
                                f"❌ *Не удалось отправить трек*\n\n"
                                f"Попробуйте еще раз или выберите другой трек.",
                                chat_id=chat_id,
                                message_id=message_id,
                                parse_mode='Markdown'
                            )
                    else:
                        bot.edit_message_text(
                            f"❌ *Ошибка скачивания*\n\n"
                            f"Причина: {status}",
                            chat_id=chat_id,
                            message_id=message_id,
                            parse_mode='Markdown'
                        )
                except Exception as e:
                    print(f"[!] Ошибка скачивания Яндекс трека: {e}")
                    traceback.print_exc()
                    bot.edit_message_text(
                        f"❌ *Ошибка при скачивании*\n\n"
                        f"Попробуйте еще раз.",
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode='Markdown'
                    )

            # Скачивание YouTube трека
            elif parts[0] == "yt" and len(parts) >= 3:
                try:
                    # Проверка доступа перед скачиванием
                    user_id = call.from_user.id
                    has_access, msg = database.check_subscription(user_id)
                    if not has_access:
                        bot.answer_callback_query(call.id, f"🚫 Доступ закрыт")
                        bot.send_message(
                            chat_id,
                            f"🔒 *Доступ запрещен!*\n\n"
                            f"{msg}\n\n"
                            f"💡 Используйте /subscribe для получения доступа",
                            parse_mode='Markdown'
                        )
                        return

                    video_id = parts[1]
                    page = int(parts[2])
                    url = f"https://youtube.com/watch?v={video_id}"

                    bot.edit_message_text(
                        "⚡ *Скачиваю трек с YouTube...*",
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode='Markdown'
                    )

                    audio_path, title, performer, status = download_from_youtube_fast(url, is_url=True)

                    if status == "success" and audio_path and os.path.exists(audio_path):
                        # Увеличиваем счетчик скачиваний
                        database.increment_download(user_id)

                        file_type = "подкаст" if audio_path.startswith(PODCASTS_DIR) else "музыка"
                        caption = f"🎵 {title} (YouTube) | 📁 {file_type}"

                        success = send_audio_fast(
                            chat_id=chat_id,
                            audio_path=audio_path,
                            title=title[:64],
                            performer=performer[:64],
                            caption=caption
                        )

                        if success:
                            # Восстанавливаем результаты поиска
                            if chat_id in user_search_history:
                                history = user_search_history[chat_id]
                                results = history['results']
                                query = history['query']

                                message_text = show_search_results(chat_id, query, results, page=page)
                                keyboard = create_search_keyboard(results, page=page, show_all_button=True)

                                bot.edit_message_text(
                                    f"✅ *Трек скачан!*\n\n"
                                    f"🎵 *{title}*\n"
                                    f"👤 *{performer}*\n\n"
                                    f"✨ *Продолжайте поиск:*",
                                    chat_id=chat_id,
                                    message_id=message_id,
                                    parse_mode='Markdown',
                                    reply_markup=keyboard
                                )
                            else:
                                markup = types.InlineKeyboardMarkup()
                                markup.add(types.InlineKeyboardButton("🔍 Новый поиск", callback_data="new_search"))

                                bot.edit_message_text(
                                    f"✅ *Трек успешно скачан!*\n\n"
                                    f"🎵 *{title}*\n"
                                    f"👤 *{performer}*\n\n"
                                    f"✨ Скачано в папку: {file_type}",
                                    chat_id=chat_id,
                                    message_id=message_id,
                                    parse_mode='Markdown',
                                    reply_markup=markup
                                )
                        else:
                            bot.edit_message_text(
                                f"❌ *Не удалось отправить трек*\n\n"
                                f"Попробуйте еще раз или выберите другой трек.",
                                chat_id=chat_id,
                                message_id=message_id,
                                parse_mode='Markdown'
                            )
                    else:
                        bot.edit_message_text(
                            f"❌ *Ошибка скачивания*\n\n"
                            f"Причина: {status}",
                            chat_id=chat_id,
                            message_id=message_id,
                            parse_mode='Markdown'
                        )
                except Exception as e:
                    print(f"[!] Ошибка скачивания YouTube трека: {e}")
                    traceback.print_exc()
                    bot.edit_message_text(
                        f"❌ *Ошибка при скачивании*\n\n"
                        f"Попробуйте еще раз.",
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode='Markdown'
                    )

            # Просмотр файлов в папке
            elif parts[0] == "files" and len(parts) >= 3:
                folder_type = parts[1]
                try:
                    page = int(parts[2])
                except:
                    page = 0

                if folder_type == "music":
                    folder_path = MUSIC_DIR
                    folder_name = "Музыка"
                    emoji = "🎵"
                else:
                    folder_path = PODCASTS_DIR
                    folder_name = "Подкасты"
                    emoji = "🎙️"

                files = get_folder_files(folder_path)

                if not files:
                    bot.edit_message_text(
                        f"{emoji} *Папка с {folder_name.lower()}*\n\n"
                        f"📭 Папка пуста\n\n"
                        f"💡 *Совет:*\n"
                        f"• Отправьте название песни в чат\n"
                        f"• Скачайте треки из поиска\n"
                        f"• Файлы появятся здесь автоматически",
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode='Markdown'
                    )
                    return

                total_size = sum(f['size'] for f in files)
                message_text = (
                    f"{emoji} *Папка с {folder_name.lower()}*\n\n"
                    f"📊 *Статистика:*\n"
                    f"• Файлов: {len(files)}\n"
                    f"• Общий размер: {total_size:.2f} MB\n\n"
                    f"📁 Выберите файл для отправки:"
                )

                keyboard = create_files_keyboard(files, page=page, folder_type=folder_type)
                bot.edit_message_text(
                    message_text,
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='Markdown',
                    reply_markup=keyboard
                )

            # Отправка файла
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

                    # Отправляем файл
                    success = send_file_from_folder(chat_id, file_path)

                    if not success:
                        bot.answer_callback_query(call.id, "❌ Ошибка отправки файла")

    except Exception as e:
        print(f"[!] Критическая ошибка в обработчике callback: {e}")
        traceback.print_exc()
        try:
            bot.answer_callback_query(call.id, f"❌ Ошибка: {str(e)[:50]}")
        except:
            pass


# ============================================
# ЗАПУСК БОТА
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
            print(f"✅ Яндекс.Музыка: Авторизован как {account_info.account.login}")
        except:
            print("✅ Яндекс.Музыка: Модуль активен")
    else:
        print("⚠️  Яндекс.Музыка: Модуль отключен")

    print("📺 YouTube: Модуль активен")
    print("⚡ Оптимизация: Быстрая отправка файлов включена")
    print("🔍 Автоматический поиск: Включен")
    print("💎 Система подписок: Активна")
    print("🎁 Промокоды: Доступны")
    print(f"⚡ FFMPEG потоков: {FFMPEG_THREADS}")
    print("=" * 60)
    print("ℹ️  Основные возможности:")
    print("   • Просто отправьте название песни в чат!")
    print("   • Или исполнителя и название!")
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
