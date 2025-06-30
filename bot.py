#!/usr/bin/env python3
import json
import logging
import hashlib
import sys
import os
import asyncio
import glob
from datetime import datetime, timedelta
import psutil
from telegram import Update, ReplyKeyboardMarkup, Bot
from telegram.error import InvalidToken, RetryAfter
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackContext
)

# Конфигурация путей
LOG_DIR = 'logs'
SCHEDULES_DIR = 'schedules'
USER_DATA_FILE = 'users.json'
CONFIG_FILE = 'config.json'

# Создаем папки если их нет
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(SCHEDULES_DIR, exist_ok=True)

# Настройка логирования
log_file = os.path.join(LOG_DIR, 'bot.log')
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8', mode='a'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)
logger.propagate = False

# Глобальные переменные
user_states = {}
application = None
shutdown_event = asyncio.Event()
user_requests = {}

def load_config():
    """Загрузка конфигурации из файла"""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки конфигурации: {e}")
        return None

def save_schedule(update_time, schedule):
    """Сохраняет расписание с хешем в папку schedules"""
    try:
        schedule_hash = hashlib.md5(
            json.dumps(schedule, sort_keys=True).encode('utf-8')
        ).hexdigest()
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_schedule.json"
        filepath = os.path.join(SCHEDULES_DIR, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump({
                'update_time': update_time,
                'schedule': schedule,
                'hash': schedule_hash
            }, f, ensure_ascii=False, indent=4)
        
        # Удаляем старые файлы (>500)
        schedule_files = sorted(glob.glob(os.path.join(SCHEDULES_DIR, '*.json')))
        if len(schedule_files) > 500:
            for file in schedule_files[:-500]:
                try:
                    os.remove(file)
                except Exception as e:
                    logger.error(f"Ошибка удаления старого файла расписания: {e}")
        
        return schedule_hash
    except Exception as e:
        logger.error(f"Ошибка сохранения расписания: {e}")
        return None

def get_latest_schedule():
    """Получает последнее сохраненное расписание"""
    try:
        files = sorted(glob.glob(os.path.join(SCHEDULES_DIR, '*.json')), reverse=True)
        if files:
            with open(files[0], 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка получения расписания: {e}")
    return None

def validate_user_data():
    """Проверяет и исправляет данные пользователей"""
    try:
        if not os.path.exists(USER_DATA_FILE):
            return

        with open(USER_DATA_FILE, 'r+', encoding='utf-8') as f:
            users = json.load(f)
            modified = False
            
            for user_id, user_data in users.items():
                # Исправляем некорректные значения chat_id
                if user_data.get('chat_id') in ['null', 'None', None]:
                    users[user_id]['chat_id'] = None
                    modified = True
                elif isinstance(user_data.get('chat_id'), str):
                    try:
                        users[user_id]['chat_id'] = int(user_data['chat_id'])
                        modified = True
                    except ValueError:
                        users[user_id]['chat_id'] = None
                        modified = True
                
                # Проверяем другие обязательные поля
                required_fields = {
                    'username': '',
                    'first_seen': datetime.now().isoformat(),
                    'last_seen': datetime.now().isoformat(),
                    'notifications': False,
                    'actions': [],
                    'banned': False,
                    'ban_reason': None
                }
                
                for field, default in required_fields.items():
                    if field not in user_data:
                        users[user_id][field] = default
                        modified = True

            if modified:
                f.seek(0)
                json.dump(users, f, ensure_ascii=False, indent=4)
                f.truncate()
                logger.info("Данные пользователей успешно валидированы")
                
    except Exception as e:
        logger.error(f"Ошибка валидации user.json: {e}")

def log_user_activity(user_id, username, action):
    """Логирование активности пользователей"""
    try:
        users = {}
        if os.path.exists(USER_DATA_FILE):
            with open(USER_DATA_FILE, 'r', encoding='utf-8') as f:
                users = json.load(f)
        
        user_key = str(user_id)
        if user_key not in users:
            users[user_key] = {
                'username': username,
                'first_seen': datetime.now().isoformat(),
                'last_seen': datetime.now().isoformat(),
                'notifications': False,
                'actions': [],
                'chat_id': None,
                'banned': False,
                'ban_reason': None
            }
        
        users[user_key]['last_seen'] = datetime.now().isoformat()
        users[user_key]['actions'].append({
            'time': datetime.now().isoformat(),
            'action': action
        })
        
        with open(USER_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(users, f, ensure_ascii=False, indent=4)
            
    except Exception as e:
        logger.error(f"Ошибка логирования активности: {e}")

def is_user_banned(user_id):
    """Проверяет, заблокирован ли пользователь"""
    try:
        with open(USER_DATA_FILE, 'r', encoding='utf-8') as f:
            users = json.load(f)
            return users.get(str(user_id), {}).get('banned', False)
    except Exception as e:
        logger.error(f"Ошибка проверки блокировки: {e}")
        return False


def update_user_data(user_id, data):
    """Обновляет данные пользователя"""
    try:
        users = {}
        if os.path.exists(USER_DATA_FILE):
            with open(USER_DATA_FILE, 'r', encoding='utf-8') as f:
                users = json.load(f)
        
        user_key = str(user_id)
        if user_key not in users:
            users[user_key] = {
                'username': str(user_id),
                'first_seen': datetime.now().isoformat(),
                'last_seen': datetime.now().isoformat(),
                'notifications': False,
                'actions': [],
                'chat_id': None,
                'banned': False,
                'ban_reason': None
            }
        
        # Обновляем только переданные данные
        users[user_key].update(data)
        
        with open(USER_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(users, f, ensure_ascii=False, indent=4)
            
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления данных пользователя: {e}")
        return False

async def send_ban_notification(user_id, reason, is_banned=True):
    """Отправляет уведомление о блокировке/разблокировке"""
    try:
        with open(USER_DATA_FILE, 'r', encoding='utf-8') as f:
            users = json.load(f)
            user = users.get(str(user_id))
            if not user or not user.get('chat_id'):
                logger.warning(f"Не удалось отправить уведомление пользователю {user_id}: chat_id не найден или пользователь не существует.")
                return

        config = load_config()
        if not config or not config.get('token'):
            logger.error("Токен бота не настроен для отправки уведомления")
            return
            
        bot = Bot(token=config['token'])
        
        try:
            if is_banned:
                message = f"🚫 Вы были заблокированы в боте.\nПричина: {reason}"
            else:
                message = "✅ Ваша блокировка в боте снята!"

            await bot.send_message(
                chat_id=user['chat_id'],
                text=message
            )
            logger.info(f"Уведомление о {'блокировке' if is_banned else 'разблокировке'} отправлено пользователю {user_id}")
        except Exception as e:
            logger.error(f"Ошибка при отправке уведомления пользователю {user_id}: {e}")
            if "bot was blocked" in str(e).lower():
                # Если бот заблокирован пользователем, отключаем уведомления для него
                update_user_data(int(user_id), {'notifications': False})
                logger.info(f"Уведомления для пользователя {user_id} отключены, так как бот был заблокирован.")
    except Exception as e:
        logger.error(f"Критическая ошибка в send_ban_notification: {e}")

def anti_spam(func):
    """Декоратор для защиты от спама"""
    async def wrapper(update, context):
        user_id = update.effective_user.id
        
        # Проверка на блокировку
        if is_user_banned(user_id):
            await update.message.reply_text("🚫 Вы заблокированы в этом боте.")
            return
            
        now = datetime.now()
        
        if user_id not in user_requests:
            user_requests[user_id] = []
        
        # Удаляем старые запросы
        user_requests[user_id] = [
            t for t in user_requests[user_id] 
            if now - t < timedelta(seconds=10)
        ]
        
        if len(user_requests[user_id]) >= 15:  # Лимит 15 запросов в 10 секунд
            await update.message.reply_text("⚠️ Слишком много запросов. Подождите 10 секунд.")
            return
        
        user_requests[user_id].append(now)
        return await func(update, context)
    return wrapper

def is_user_banned(user_id):
    """Проверяет, заблокирован ли пользователь"""
    try:
        with open(USER_DATA_FILE, 'r', encoding='utf-8') as f:
            users = json.load(f)
            return users.get(str(user_id), {}).get('banned', False)
    except Exception as e:
        logger.error(f"Ошибка проверки блокировки: {e}")
        return False

@anti_spam
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /start"""
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    chat_id = update.effective_chat.id
    
    # Обновляем данные пользователя
    update_user_data(user_id, {
        'username': username,
        'chat_id': chat_id,
        'last_seen': datetime.now().isoformat()
    })
    
    if user_id not in user_states:
        user_states[user_id] = {"notifications_active": False}
    
    log_user_activity(user_id, username, "start")
    
    await update.message.reply_text(
        "👋 Привет! Я бот для отслеживания расписания. Выберите действие:",
        reply_markup=create_keyboard(user_states[user_id]["notifications_active"])
    )

def create_keyboard(notifications_active):
    """Создает клавиатуру с основными кнопками"""
    buttons = [
        ["📅 Проверить расписание"],
        ["✅ Уведомления" if notifications_active else "❌ Уведомления"],
        ["ℹ️ Информация о боте"]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

@anti_spam
async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отображение расписания"""
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    log_user_activity(user_id, username, "schedule_request")
    
    data = get_latest_schedule()
    if not data:
        await update.message.reply_text("⚠️ Расписание временно недоступно")
        return
    
    response = f"🕒 Расписание обновлено: {data['update_time']}\n\n"
    for day, pairs in data["schedule"].items():
        response += f"📅 {day}:\n"
        for pair in pairs:
            if pair.get("Name") and pair["Auditory"] != "Нет аудитории":
                subgroup = f" (Подгруппа {pair['Subgroup']})" if pair.get("Subgroup") == "Да" else ""
                response += f"🔢 {pair['N_par']}: {pair['Name']} - {pair['Auditory']} (👨‍🏫 {pair['Teacher']}){subgroup}\n"
            else:
                response += f"🔢 Нет пары: ❌\n"
        response += "\n"
    
    try:
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Ошибка отправки расписания: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка при формировании расписания")

@anti_spam
async def toggle_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Переключение уведомлений"""
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    
    if user_id not in user_states:
        user_states[user_id] = {"notifications_active": False}
    
    user_states[user_id]["notifications_active"] = not user_states[user_id]["notifications_active"]
    status = user_states[user_id]["notifications_active"]
    
    # Обновляем статус в базе
    update_user_data(user_id, {'notifications': status})
    log_user_activity(user_id, username, f"notifications_{'on' if status else 'off'}")
    
    await update.message.reply_text(
        f"Уведомления {'✅ активированы' if status else '❌ деактивированы'}",
        reply_markup=create_keyboard(status)
    )

@anti_spam
async def bot_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Информация о боте"""
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    log_user_activity(user_id, username, "bot_info")
    
    await update.message.reply_text(
        "🤖 Бот расписания\n\n"
        "📝 Команды:\n"
        "/start - Главное меню\n"
        "/check - Показать расписание\n"
        "/notif - Переключить уведомления\n\n"
        "🔔 При изменениях в расписании я пришлю уведомление!"
    )

@anti_spam
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    user_id = update.effective_user.id
    if user_id not in user_states:
        user_states[user_id] = {"notifications_active": False}

    text = update.message.text
    if text == "ℹ️ Информация о боте":
        await bot_info(update, context)
    elif text == "📅 Проверить расписание":
        await show_schedule(update, context)
    elif text in ["✅ Уведомления", "❌ Уведомления"]:
        await toggle_notifications(update, context)
    else:
        await update.message.reply_text(
            "❌ Неизвестная команда",
            reply_markup=create_keyboard(user_states[user_id]["notifications_active"])
        )

async def send_notifications(context: CallbackContext, message):
    """Отправка уведомлений пользователям"""
    try:
        if not os.path.exists(USER_DATA_FILE):
            return

        with open(USER_DATA_FILE, 'r') as f:
            users = json.load(f)
        
        for user_id_str, user_data in users.items():
            # Пропускаем заблокированных пользователей
            if user_data.get('banned', False):
                continue
                
            # Отправляем только если есть chat_id и включены уведомления
            if user_data.get('chat_id') and user_data.get('notifications', False):
                try:
                    await context.bot.send_message(
                        chat_id=user_data['chat_id'],
                        text=message
                    )
                    logger.info(f"Уведомление отправлено пользователю {user_id_str}")
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления: {e}")
                    if "bot was blocked" in str(e).lower():
                        update_user_data(int(user_id_str), {'notifications': False})
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомлений: {e}")

async def check_schedule_changes(context: CallbackContext):
    """Проверка изменений в расписании"""
    try:
        old_data = get_latest_schedule()
        old_hash = old_data.get('hash') if old_data else None
        
        from parser import get_schedule
        update_time, new_schedule = get_schedule()
        
        if new_schedule:
            new_hash = save_schedule(update_time, new_schedule)
            
            if old_hash != new_hash:
                logger.info("Обнаружены изменения в расписании")
                await send_notifications(
                    context,
                    f"🔄 Расписание обновлено: {update_time}\n"
                    "Используйте команду /check чтобы посмотреть изменения"
                )
    except ImportError:
        logger.error("Модуль parser не найден")
    except Exception as e:
        logger.error(f"Ошибка при проверке расписания: {e}")

async def shutdown_application():
    """Корректное завершение работы"""
    global application
    if application:
        try:
            logger.info("Завершение работы бота...")
            if application.updater:
                await application.updater.stop()
            await application.stop()
            await application.shutdown()
            logger.info("Бот корректно завершил работу")
        except Exception as e:
            logger.error(f"Ошибка при завершении работы: {e}")
        finally:
            application = None

async def main():
    """Основная функция запуска бота"""
    global application
    
    # Проверяем и исправляем данные пользователей перед запуском
    validate_user_data()
    
    try:
        config = load_config()
        if not config or not config.get('token'):
            logger.error("❌ Токен бота не настроен!")
            return
            
        application = ApplicationBuilder().token(config["token"]).build()

        # Регистрация обработчиков
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("check", show_schedule))
        application.add_handler(CommandHandler("info", bot_info))
        application.add_handler(CommandHandler("notif", toggle_notifications))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        # Планировщик проверки обновлений (каждые 30 минут)
        application.job_queue.run_repeating(
            check_schedule_changes, 
            interval=1800, 
            first=10
        )
        
        logger.info("🤖 Бот запускается...")
        
        # Запуск бота
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        
        # Ожидаем сигнала завершения
        await shutdown_event.wait()
        
    except InvalidToken:
        logger.error("❌ Неверный токен Telegram бота")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {str(e)}")
    finally:
        await shutdown_application()

def signal_handler(sig, frame):
    """Обработчик сигналов для завершения работы"""
    logger.info("Получен сигнал завершения")
    shutdown_event.set()

if __name__ == "__main__":
    # Настройка обработчиков сигналов
    import signal
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Принудительная остановка бота")
    except Exception as e:
        logger.error(f"Необработанная ошибка: {e}")
    finally:
        logger.info("Бот завершил работу")

def is_bot_running():
    """Проверяет, запущен ли бот"""
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info['cmdline'] or []
            if 'python' in proc.info['name'].lower() and 'bot.py' in ' '.join(cmdline):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False