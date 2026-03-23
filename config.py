import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _parse_admin_ids(raw_value: str) -> list[int]:
    admin_ids = []
    for chunk in raw_value.split(','):
        chunk = chunk.strip()
        if not chunk:
            continue
        admin_ids.append(int(chunk))
    return admin_ids

# Токен бота из BotFather
BOT_TOKEN = os.environ.get('BOT_TOKEN', '').strip()

# ID администраторов (ваш ID и других админов)
ADMIN_IDS = _parse_admin_ids(os.environ.get('ADMIN_IDS', ''))

# Токен Яндекс.Музыки (опционально)
YANDEX_MUSIC_TOKEN = os.environ.get('YANDEX_MUSIC_TOKEN', '').strip()
VK_LOGIN = os.environ.get('VK_LOGIN', '').strip()
VK_PASSWORD = os.environ.get('VK_PASSWORD', '').strip()
VK_ACCESS_TOKEN = os.environ.get('VK_ACCESS_TOKEN', '').strip()
ENABLE_VK = os.environ.get('ENABLE_VK', '').strip().lower() in {'1', 'true', 'yes', 'on'}

DATA_DIR = Path(os.environ.get('DATA_DIR', BASE_DIR / 'data')).expanduser()
CACHE_DIR = Path(os.environ.get('CACHE_DIR', BASE_DIR / 'audio_cache')).expanduser()
DATABASE_PATH = Path(os.environ.get('DATABASE_PATH', DATA_DIR / 'music_bot.db')).expanduser()

# Настройки кэша
MAX_FILE_SIZE_MB = 48
FFMPEG_THREADS = 4
SEARCH_RESULTS_PER_SOURCE = max(5, int(os.environ.get('SEARCH_RESULTS_PER_SOURCE', '50')))

# Настройки подписки
SUBSCRIPTION_PRICES = {
    'premium': 49  # рублей/месяц
}

# Лимиты для подписок
SUBSCRIPTION_LIMITS = {
    'premium': 999999  # практически безлимит
}

# Промокоды по умолчанию
DEFAULT_PROMO_CODES = [
    {
        'code': 'WELCOME',
        'type': 'premium',
        'uses': 1,    # 1 использование
        'days': 30    # 30 дней
    },
    {
        'code': 'TEST123',
        'type': 'premium',
        'uses': 1,    # 1 использование
        'days': 30    # 30 дней
    },
    {
        'code': 'V1_GAN13',
        'type': 'premium',
        'uses': 1,    # 1 использование, но вечная подписка
        'days': None  # бессрочная подписка
    },
    {
        'code': 'FREEMUSIC',
        'type': 'premium',
        'uses': 1,    # 1 использование
        'days': 30    # 30 дней
    }
]
