import os
from dotenv import load_dotenv

load_dotenv()

# Токен бота из BotFather
BOT_TOKEN = os.environ.get('BOT_TOKEN')

# ID администраторов (ваш ID и других админов)
ADMIN_IDS = list(map(int, os.environ.get('ADMIN_IDS', '123456789').split(',')))

# Токен Яндекс.Музыки (опционально)
YANDEX_MUSIC_TOKEN = os.environ.get('YANDEX_MUSIC_TOKEN', '')

# Настройки кэша
MAX_FILE_SIZE_MB = 48
FFMPEG_THREADS = 4

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