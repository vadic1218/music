import sqlite3
import os
import json
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, Any, List
from config import DEFAULT_PROMO_CODES, SUBSCRIPTION_LIMITS, ADMIN_IDS

BASE_DIR = Path(__file__).resolve().parent


class Database:
    def __init__(self, db_path='music_bot.db'):
        self.db_path = str(Path(db_path)) if os.path.isabs(db_path) else str(BASE_DIR / db_path)
        self.lock = threading.RLock()
        self.init_db()

    def init_db(self):
        """Инициализация базы данных"""
        try:
            with self.lock:
                conn = sqlite3.connect(self.db_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # Таблица пользователей
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    language_code TEXT,
                    is_premium BOOLEAN DEFAULT FALSE,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    total_downloads INTEGER DEFAULT 0
                )
                ''')

                # Таблица подписок
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    subscription_type TEXT NOT NULL,
                    status TEXT DEFAULT 'active',
                    start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expiry_date TIMESTAMP,
                    is_promo BOOLEAN DEFAULT FALSE,
                    promo_code TEXT,
                    payment_method TEXT,
                    transaction_id TEXT UNIQUE,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
                ''')

                # Таблица промокодов
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS promo_codes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT UNIQUE NOT NULL,
                    subscription_type TEXT NOT NULL,
                    max_uses INTEGER DEFAULT 1,
                    uses_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expiry_date TIMESTAMP,
                    is_active BOOLEAN DEFAULT TRUE,
                    description TEXT
                )
                ''')

                # Таблица использования промокодов
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS promo_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    promo_code TEXT NOT NULL,
                    used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    subscription_type TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (user_id),
                    FOREIGN KEY (promo_code) REFERENCES promo_codes (code)
                )
                ''')

                # Таблица статистики использования
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS usage_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    date DATE DEFAULT CURRENT_DATE,
                    downloads INTEGER DEFAULT 0,
                    UNIQUE(user_id, date)
                )
                ''')

                conn.commit()

                # Создаем дефолтные промокоды
                self._create_default_promo_codes(cursor)

                conn.commit()
                conn.close()
                print(f"✅ База данных инициализирована: {self.db_path}")

        except Exception as e:
            print(f"[DATABASE] Ошибка инициализации: {e}")

    def _create_default_promo_codes(self, cursor):
        """Создает промокоды по умолчанию"""
        for promo in DEFAULT_PROMO_CODES:
            try:
                # Для V1_GAN13 - бессрочная подписка, для остальных 30 дней
                if promo['code'] == 'V1_GAN13':
                    expiry_date = None  # Бессрочная подписка
                else:
                    expiry_date = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')

                cursor.execute('''
                INSERT OR IGNORE INTO promo_codes 
                (code, subscription_type, max_uses, expiry_date, is_active, description)
                VALUES (?, ?, ?, ?, TRUE, ?)
                ''', (
                    promo['code'].upper(),
                    promo['type'],
                    promo['uses'],
                    expiry_date,
                    f"Промокод {promo['code']} - {promo['uses']} использование"
                ))

                print(f"✅ Создан промокод: {promo['code']} (использований: {promo['uses']})")

            except Exception as e:
                print(f"[DATABASE] Ошибка создания промокода {promo['code']}: {e}")

    def add_user(self, user_id: int, username: str = None,
                 first_name: str = None, last_name: str = None,
                 language_code: str = None, is_premium: bool = False) -> bool:
        """Добавляет пользователя в базу"""
        try:
            with self.lock:
                conn = sqlite3.connect(self.db_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute('''
                INSERT OR REPLACE INTO users 
                (user_id, username, first_name, last_name, language_code, is_premium, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (user_id, username, first_name, last_name, language_code, is_premium))

                conn.commit()
                conn.close()
                return True
        except Exception as e:
            print(f"[DATABASE] Ошибка добавления пользователя: {e}")
            return False

    def check_subscription(self, user_id: int) -> Tuple[bool, str]:
        """Проверяет активность подписки пользователя"""
        try:
            with self.lock:
                # Проверяем, является ли пользователь администратором
                if user_id in ADMIN_IDS:
                    return True, self._get_admin_subscription_message()

                conn = sqlite3.connect(self.db_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # Обновляем время последней активности
                cursor.execute('''
                UPDATE users SET last_seen = CURRENT_TIMESTAMP WHERE user_id = ?
                ''', (user_id,))

                # Проверяем активную подписку
                cursor.execute('''
                SELECT s.subscription_type, s.expiry_date, s.status, s.is_promo, s.promo_code,
                       (SELECT downloads FROM usage_stats 
                        WHERE user_id = ? AND date = DATE('now')) as today_downloads
                FROM subscriptions s
                WHERE s.user_id = ? 
                  AND s.status = 'active' 
                  AND (s.expiry_date IS NULL OR s.expiry_date > CURRENT_TIMESTAMP)
                ORDER BY s.start_date DESC
                LIMIT 1
                ''', (user_id, user_id))

                result = cursor.fetchone()

                # Если нет подписки
                if not result:
                    conn.close()
                    return False, "🚫 *У вас нет активной подписки*\n\nИспользуйте /subscribe для оформления доступа"

                sub_type = result['subscription_type']
                expiry_date = result['expiry_date']
                status = result['status']
                is_promo = result['is_promo']
                promo_code = result['promo_code']
                today_downloads = result['today_downloads'] or 0

                # Проверяем дневной лимит
                limit = SUBSCRIPTION_LIMITS.get(sub_type, 999999)

                if limit and today_downloads >= limit:
                    conn.close()
                    return False, f"❌ Достигнут дневной лимит ({limit} скачиваний)"

                # Форматируем сообщение
                if is_promo:
                    if promo_code == 'V1_GAN13':
                        source = "🎁 *ВЕЧНЫЙ промокод: V1_GAN13*"
                        expiry_text = "⏱ *Срок:* ВЕЧНАЯ подписка\n"
                    else:
                        source = f"🎁 Промокод: `{promo_code}`"

                        if expiry_date:
                            try:
                                expiry_date_obj = datetime.strptime(expiry_date, '%Y-%m-%d %H:%M:%S')
                                days_left = (expiry_date_obj - datetime.now()).days
                                expiry_text = f"⏱ *Срок:* до {expiry_date.split()[0]} ({days_left} дней)\n"
                            except:
                                expiry_text = f"⏱ *Срок:* 30 дней\n"
                        else:
                            expiry_text = f"⏱ *Срок:* 30 дней\n"
                else:
                    source = "💳 Оплата"
                    if expiry_date:
                        try:
                            expiry_date_obj = datetime.strptime(expiry_date, '%Y-%m-%d %H:%M:%S')
                            days_left = (expiry_date_obj - datetime.now()).days
                            expiry_text = f"⏱ *Срок:* до {expiry_date.split()[0]} ({days_left} дней)\n"
                        except:
                            expiry_text = f"⏱ *Срок:* НЕОГРАНИЧЕНО\n"
                    else:
                        expiry_text = f"⏱ *Срок:* НЕОГРАНИЧЕНО\n"

                message = f"✅ *PREMIUM подписка активна!*\n\n"
                message += f"📅 *Источник:* {source}\n"
                message += expiry_text
                message += f"📊 *Использовано сегодня:* {today_downloads}\n\n"

                # Добавляем информацию о возможностях
                message += "✨ *Ваши возможности:*\n"
                message += "• ✅ Неограниченное скачивание\n"
                message += "• ✅ Все источники (YouTube, Яндекс.Музыка)\n"
                message += "• ✅ Приоритетная поддержка\n"
                message += "• ✅ Быстрая загрузка\n"

                conn.close()
                return True, message

        except Exception as e:
            print(f"[DATABASE] Ошибка проверки подписки: {e}")
            return False, "⚠️ Ошибка проверки подписки"

    def _get_admin_subscription_message(self):
        """Сообщение о подписке для администраторов"""
        return (
            "✅ *АДМИНИСТРАТОРСКАЯ ПОДПИСКА*\n\n"
            "⚡ *Вам доступны все функции бота!*\n\n"
            "✨ *Ваши возможности:*\n"
            "• ✅ Неограниченное скачивание\n"
            "• ✅ Все источники (YouTube, Яндекс.Музыка)\n"
            "• ✅ Приоритетная поддержка\n"
            "• ✅ Быстрая загрузка\n"
            "• ⚙️ Административные права\n\n"
            "🚀 *Статус:* ВЕЧНАЯ АДМИНИСТРАТОРСКАЯ подписка"
        )

    def get_user_stats(self, user_id: int) -> Dict[str, Any]:
        """Получает статистику пользователя"""
        try:
            with self.lock:
                # Проверяем, является ли пользователь администратором
                if user_id in ADMIN_IDS:
                    return {
                        'total_downloads': 999,
                        'active_days': 999,
                        'max_daily_downloads': 999,
                        'today_downloads': 0,
                        'current_subscription': {
                            'type': 'premium',
                            'start_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            'expiry_date': None,
                            'is_promo': False,
                            'promo_code': 'ADMIN',
                            'is_admin': True
                        }
                    }

                conn = sqlite3.connect(self.db_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute('''
                SELECT 
                    COALESCE((SELECT SUM(downloads) FROM usage_stats WHERE user_id = ?), 0) as total_downloads,
                    COALESCE((SELECT COUNT(DISTINCT date) FROM usage_stats WHERE user_id = ?), 0) as active_days,
                    COALESCE((SELECT MAX(downloads) FROM usage_stats WHERE user_id = ?), 0) as max_daily_downloads,
                    COALESCE((SELECT downloads FROM usage_stats WHERE user_id = ? AND date = DATE('now')), 0) as today_downloads
                ''', (user_id, user_id, user_id, user_id))

                result = cursor.fetchone()

                cursor.execute('''
                SELECT subscription_type, start_date, expiry_date, is_promo, promo_code
                FROM subscriptions 
                WHERE user_id = ? AND status = 'active'
                ORDER BY start_date DESC
                LIMIT 1
                ''', (user_id,))

                sub_result = cursor.fetchone()

                conn.close()

                stats = {
                    'total_downloads': result['total_downloads'] if result else 0,
                    'active_days': result['active_days'] if result else 0,
                    'max_daily_downloads': result['max_daily_downloads'] if result else 0,
                    'today_downloads': result['today_downloads'] if result else 0
                }

                if sub_result:
                    stats['current_subscription'] = {
                        'type': sub_result['subscription_type'],
                        'start_date': sub_result['start_date'],
                        'expiry_date': sub_result['expiry_date'],
                        'is_promo': bool(sub_result['is_promo']),
                        'promo_code': sub_result['promo_code'],
                        'is_admin': False
                    }

                return stats

        except Exception as e:
            print(f"[DATABASE] Ошибка получения статистики: {e}")
            return {}

    def create_subscription(self, user_id: int, sub_type: str = 'premium',
                            duration_days: int = None,
                            is_promo: bool = False,
                            promo_code: str = None,
                            payment_method: str = None,
                            transaction_id: str = None) -> bool:
        """Создает новую подписку"""
        try:
            with self.lock:
                # Если пользователь администратор - ничего не делаем
                if user_id in ADMIN_IDS:
                    return True

                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()

                # Деактивируем старые активные подписки
                cursor.execute('''
                UPDATE subscriptions 
                SET status = 'expired' 
                WHERE user_id = ? AND status = 'active'
                ''', (user_id,))

                # Рассчитываем дату окончания
                start_date = datetime.now()
                expiry_date = None

                if duration_days:
                    expiry_date = start_date + timedelta(days=duration_days)
                    expiry_date = expiry_date.strftime('%Y-%m-%d %H:%M:%S')

                start_date_str = start_date.strftime('%Y-%m-%d %H:%M:%S')

                cursor.execute('''
                INSERT INTO subscriptions 
                (user_id, subscription_type, status, start_date, expiry_date, 
                 is_promo, promo_code, payment_method, transaction_id)
                VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?)
                ''', (user_id, sub_type, start_date_str, expiry_date,
                      is_promo, promo_code, payment_method, transaction_id))

                conn.commit()
                conn.close()
                return True

        except Exception as e:
            print(f"[DATABASE] Ошибка создания подписки: {e}")
            return False

    def increment_download(self, user_id: int) -> bool:
        """Увеличивает счетчик скачиваний"""
        try:
            with self.lock:
                # Администраторы не нуждаются в счетчике
                if user_id in ADMIN_IDS:
                    return True

                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()

                # Увеличиваем общий счетчик
                cursor.execute('''
                UPDATE users SET total_downloads = total_downloads + 1 
                WHERE user_id = ?
                ''', (user_id,))

                # Увеличиваем дневной счетчик
                cursor.execute('''
                INSERT INTO usage_stats (user_id, date, downloads)
                VALUES (?, DATE('now'), 1)
                ON CONFLICT(user_id, date) 
                DO UPDATE SET downloads = downloads + 1
                ''', (user_id,))

                conn.commit()
                conn.close()
                return True

        except Exception as e:
            print(f"[DATABASE] Ошибка увеличения счетчика: {e}")
            return False

    # ===== МЕТОДЫ ДЛЯ ПРОМОКОДОВ =====

    def check_promo_code(self, code: str) -> Dict[str, Any]:
        """Проверяет промокод"""
        try:
            with self.lock:
                conn = sqlite3.connect(self.db_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute('''
                SELECT code, subscription_type, max_uses, uses_count, expiry_date, is_active, description
                FROM promo_codes 
                WHERE code = ? COLLATE NOCASE
                ''', (code.upper(),))

                result = cursor.fetchone()

                if not result:
                    return {
                        'valid': False,
                        'message': '❌ Промокод не найден'
                    }

                # Проверка активности
                if not result['is_active']:
                    return {
                        'valid': False,
                        'message': '❌ Промокод неактивен'
                    }

                # Проверка срока действия
                if result['expiry_date']:
                    try:
                        expiry = datetime.strptime(result['expiry_date'], '%Y-%m-%d %H:%M:%S')
                        if expiry < datetime.now():
                            return {
                                'valid': False,
                                'message': '❌ Срок действия промокода истек'
                            }
                    except:
                        pass

                # Проверка лимита использования
                if result['max_uses'] > 0 and result['uses_count'] >= result['max_uses']:
                    return {
                        'valid': False,
                        'message': f'❌ Лимит использования исчерпан ({result["uses_count"]}/{result["max_uses"]})'
                    }

                conn.close()
                return {
                    'valid': True,
                    'message': f'✅ Промокод действителен!\nТип подписки: {result["subscription_type"].upper()}',
                    'data': dict(result)
                }

        except Exception as e:
            print(f"[DATABASE] Ошибка проверки промокода: {e}")
            return {
                'valid': False,
                'message': '❌ Ошибка проверки промокода'
            }

    def use_promo_code(self, user_id: int, code: str) -> Dict[str, Any]:
        """Активирует промокод для пользователя"""
        try:
            print(f"[DATABASE] use_promo_code: user_id={user_id}, code={code}")

            # Проверяем промокод (без вложенного lock)
            check_result = self._check_promo_code_no_lock(code)
            print(f"[DATABASE] Результат проверки: {check_result}")

            if not check_result['valid']:
                return {
                    'success': False,
                    'message': check_result['message']
                }

            promo_data = check_result['data']

            with self.lock:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()

                # Проверяем, не использовал ли пользователь уже этот промокод
                cursor.execute('''
                SELECT COUNT(*) FROM promo_usage 
                WHERE user_id = ? AND promo_code = ?
                ''', (user_id, promo_data['code']))

                if cursor.fetchone()[0] > 0:
                    conn.close()
                    return {
                        'success': False,
                        'message': '❌ Вы уже использовали этот промокод'
                    }

                # Проверяем, не использовал ли пользователь уже ЛЮБОЙ промокод
                cursor.execute('''
                SELECT COUNT(*) FROM promo_usage 
                WHERE user_id = ?
                ''', (user_id,))

                if cursor.fetchone()[0] > 0:
                    conn.close()
                    return {
                        'success': False,
                        'message': '❌ Вы уже использовали промокод ранее. Один пользователь может активировать только один промокод.'
                    }

                # Определяем длительность подписки
                duration_days = None
                expiry_date = None

                if promo_data['code'] == 'V1_GAN13':
                    # Для V1_GAN13 - бессрочная подписка
                    expiry_date = None
                    duration_text = "ВЕЧНАЯ"
                else:
                    # Для остальных - 30 дней
                    duration_days = 30
                    start_date = datetime.now()
                    expiry_date = start_date + timedelta(days=duration_days)
                    expiry_date = expiry_date.strftime('%Y-%m-%d %H:%M:%S')
                    duration_text = "30 дней"

                # Деактивируем старые активные подписки
                cursor.execute('''
                UPDATE subscriptions 
                SET status = 'expired' 
                WHERE user_id = ? AND status = 'active'
                ''', (user_id,))

                start_date_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                # Вставляем новую подписку
                cursor.execute('''
                INSERT INTO subscriptions 
                (user_id, subscription_type, status, start_date, expiry_date, 
                 is_promo, promo_code, payment_method, transaction_id)
                VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?)
                ''', (user_id, promo_data['subscription_type'], start_date_str, expiry_date,
                      True, promo_data['code'], "promo_code",
                      f"PROMO_{promo_data['code']}_{user_id}_{int(datetime.now().timestamp())}"))

                # Увеличиваем счетчик использования промокода
                cursor.execute('''
                UPDATE promo_codes 
                SET uses_count = uses_count + 1 
                WHERE code = ?
                ''', (promo_data['code'],))

                # Записываем использование
                cursor.execute('''
                INSERT INTO promo_usage (user_id, promo_code, subscription_type)
                VALUES (?, ?, ?)
                ''', (user_id, promo_data['code'], promo_data['subscription_type']))

                conn.commit()
                conn.close()

                if promo_data['code'] == 'V1_GAN13':
                    message = f'🎉 *ВЕЧНЫЙ промокод активирован!*\n\n' \
                              f'Вам предоставлена *ВЕЧНАЯ PREMIUM* подписка!\n\n' \
                              f'✨ Теперь вы можете скачивать музыку без ограничений навсегда!'
                else:
                    expiry_date_formatted = expiry_date.split()[0] if expiry_date else "30 дней"
                    message = f'🎉 *Промокод активирован!*\n\n' \
                              f'Вам предоставлена *{promo_data["subscription_type"].upper()}* подписка!\n' \
                              f'📅 *Срок действия:* {duration_text}\n\n' \
                              f'✨ Теперь вы можете скачивать музыку без ограничений!'

                return {
                    'success': True,
                    'message': message,
                    'subscription_type': promo_data['subscription_type'],
                    'promo_code': promo_data['code']
                }

        except Exception as e:
            print(f"[DATABASE] Ошибка использования промокода: {e}")
            return {
                'success': False,
                'message': '❌ Ошибка активации промокода'
            }

    def _check_promo_code_no_lock(self, code: str) -> Dict[str, Any]:
        """Проверяет промокод БЕЗ блокировки (для внутреннего использования)"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute('''
            SELECT code, subscription_type, max_uses, uses_count, expiry_date, is_active, description
            FROM promo_codes 
            WHERE code = ? COLLATE NOCASE
            ''', (code.upper(),))

            result = cursor.fetchone()

            if not result:
                conn.close()
                return {
                    'valid': False,
                    'message': '❌ Промокод не найден'
                }

            # Проверка активности
            if not result['is_active']:
                conn.close()
                return {
                    'valid': False,
                    'message': '❌ Промокод неактивен'
                }

            # Проверка срока действия
            if result['expiry_date']:
                try:
                    expiry = datetime.strptime(result['expiry_date'], '%Y-%m-%d %H:%M:%S')
                    if expiry < datetime.now():
                        conn.close()
                        return {
                            'valid': False,
                            'message': '❌ Срок действия промокода истек'
                        }
                except:
                    pass

            # Проверка лимита использования
            if result['max_uses'] > 0 and result['uses_count'] >= result['max_uses']:
                conn.close()
                return {
                    'valid': False,
                    'message': f'❌ Лимит использования исчерпан ({result["uses_count"]}/{result["max_uses"]})'
                }

            conn.close()
            return {
                'valid': True,
                'message': f'✅ Промокод действителен!\nТип подписки: {result["subscription_type"].upper()}',
                'data': dict(result)
            }

        except Exception as e:
            print(f"[DATABASE] Ошибка проверки промокода (no_lock): {e}")
            return {
                'valid': False,
                'message': '❌ Ошибка проверки промокода'
            }

    def get_user_promo_history(self, user_id: int) -> List[Dict[str, Any]]:
        """Получает историю промокодов пользователя"""
        try:
            with self.lock:
                conn = sqlite3.connect(self.db_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute('''
                SELECT pu.promo_code, pu.used_at, pu.subscription_type
                FROM promo_usage pu
                WHERE pu.user_id = ?
                ORDER BY pu.used_at DESC
                LIMIT 20
                ''', (user_id,))

                history = []
                for row in cursor.fetchall():
                    history.append({
                        'promo_code': row['promo_code'],
                        'used_at': row['used_at'],
                        'subscription_type': row['subscription_type']
                    })

                conn.close()
                return history

        except Exception as e:
            print(f"[DATABASE] Ошибка получения истории промокодов: {e}")
            return []

    def create_promo_code(self, code: str, subscription_type: str = "premium",
                          max_uses: int = 1, days_valid: int = 30,
                          description: str = None) -> Dict[str, Any]:
        """Создает новый промокод"""
        try:
            with self.lock:
                code = code.upper().strip()

                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()

                cursor.execute('SELECT COUNT(*) FROM promo_codes WHERE code = ?', (code,))
                if cursor.fetchone()[0] > 0:
                    conn.close()
                    return {
                        'success': False,
                        'message': f'❌ Промокод {code} уже существует'
                    }

                # Все промокоды по умолчанию на 30 дней (кроме специальных)
                days_valid = 30
                expiry_date = (datetime.now() + timedelta(days=days_valid)).strftime('%Y-%m-%d %H:%M:%S')

                cursor.execute('''
                INSERT INTO promo_codes 
                (code, subscription_type, max_uses, expiry_date, description, is_active)
                VALUES (?, ?, ?, ?, ?, TRUE)
                ''', (code, subscription_type, max_uses, expiry_date, description))

                conn.commit()
                conn.close()

                return {
                    'success': True,
                    'message': f'✅ Промокод создан!\n\n' \
                               f'📋 *Детали:*\n' \
                               f'• Код: `{code}`\n' \
                               f'• Тип: {subscription_type.upper()}\n' \
                               f'• Использований: {max_uses}\n' \
                               f'• Срок: 30 дней'
                }

        except Exception as e:
            print(f"[DATABASE] Ошибка создания промокода: {e}")
            return {
                'success': False,
                'message': f'❌ Ошибка создания промокода: {str(e)}'
            }

    def get_all_promo_codes(self) -> List[Dict[str, Any]]:
        """Получает все промокоды"""
        try:
            with self.lock:
                conn = sqlite3.connect(self.db_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute('''
                SELECT code, subscription_type, max_uses, uses_count, 
                       expiry_date, is_active, description, created_at
                FROM promo_codes
                ORDER BY created_at DESC
                ''')

                promos = []
                for row in cursor.fetchall():
                    promos.append({
                        'code': row['code'],
                        'subscription_type': row['subscription_type'],
                        'max_uses': row['max_uses'],
                        'uses_count': row['uses_count'],
                        'expiry_date': row['expiry_date'],
                        'is_active': bool(row['is_active']),
                        'description': row['description'],
                        'created_at': row['created_at']
                    })

                conn.close()
                return promos

        except Exception as e:
            print(f"[DATABASE] Ошибка получения промокодов: {e}")
            return []

    def get_active_users_count(self, days: int = 30) -> int:
        """Получает количество активных пользователей"""
        try:
            with self.lock:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()

                cursor.execute('''
                SELECT COUNT(DISTINCT user_id) 
                FROM usage_stats 
                WHERE date > DATE('now', ?)
                ''', (f'-{days} days',))

                result = cursor.fetchone()[0]
                conn.close()
                return result or 0

        except Exception as e:
            print(f"[DATABASE] Ошибка получения активных пользователей: {e}")
            return 0

    def get_total_downloads(self) -> int:
        """Получает общее количество скачиваний"""
        try:
            with self.lock:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()

                cursor.execute('SELECT SUM(total_downloads) FROM users')
                result = cursor.fetchone()[0]
                conn.close()
                return result or 0

        except Exception as e:
            print(f"[DATABASE] Ошибка получения общего количества скачиваний: {e}")
            return 0


# Создаем глобальный экземпляр базы данных
db = Database()

# Создаем функции для прямого вызова
add_user = db.add_user
check_subscription = db.check_subscription
check_promo_code = db.check_promo_code
use_promo_code = db.use_promo_code
increment_download = db.increment_download
get_user_stats = db.get_user_stats
get_user_promo_history = db.get_user_promo_history
create_promo_code = db.create_promo_code
get_all_promo_codes = db.get_all_promo_codes
get_active_users_count = db.get_active_users_count
get_total_downloads = db.get_total_downloads
