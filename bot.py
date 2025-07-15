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
USERS_DIR = 'users'
LOG_DIR = 'logs'
SCHEDULES_DIR = 'schedules'
CONFIG_FILE = 'config.json'

# Создаем папки если их нет
os.makedirs(USERS_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(SCHEDULES_DIR, exist_ok=True)

# Настройка логирования
from log_handler import setup_logging
logger = setup_logging()

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
    """Сохраняет расписание с хешем в папку schedules и в last_schedule.json"""
    try:
        schedule_hash = hashlib.md5(
            json.dumps(schedule, sort_keys=True).encode('utf-8')
        ).hexdigest()
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_schedule.json"
        filepath = os.path.join(SCHEDULES_DIR, filename)
        
        # Сохраняем полную версию с метаданными
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump({
                'update_time': update_time,
                'schedule': schedule,
                'hash': schedule_hash
            }, f, ensure_ascii=False, indent=4)
        
        # Сохраняем упрощенную версию в last_schedule.json
        last_schedule_file = os.path.join(SCHEDULES_DIR, 'last_schedule.json')
        with open(last_schedule_file, 'w', encoding='utf-8') as f:
            json.dump(schedule, f, ensure_ascii=False, indent=4)
        
        # Удаляем старые файлы (>500)
        schedule_files = sorted(glob.glob(os.path.join(SCHEDULES_DIR, '*.json')))
        if len(schedule_files) > 500:
            for file in schedule_files[:-500]:
                try:
                    if not file.endswith('last_schedule.json'):  # Не удаляем last_schedule.json
                        os.remove(file)
                except Exception as e:
                    logger.error(f"Ошибка удаления старого файла расписания: {e}")
        
        return schedule_hash
    except Exception as e:
        logger.error(f"Ошибка сохранения расписания: {e}")
        return None

def get_latest_schedule():
    """Получает последнее сохраненное расписание из last_schedule.json или парсит новое"""
    try:
        # Сначала пробуем загрузить last_schedule.json
        last_schedule_file = os.path.join(SCHEDULES_DIR, 'last_schedule.json')
        if os.path.exists(last_schedule_file):
            with open(last_schedule_file, 'r', encoding='utf-8') as f:
                schedule_data = json.load(f)
                return {
                    'update_time': datetime.now().strftime("%d.%m.%Y %H:%M"),
                    'schedule': schedule_data,
                    'hash': hashlib.md5(json.dumps(schedule_data, sort_keys=True).encode('utf-8')).hexdigest()
                }
        
        # Если файла нет, запускаем парсер
        from parser import get_schedule
        update_time, new_schedule = get_schedule()
        
        if new_schedule:
            # Сохраняем новое расписание
            schedule_hash = save_schedule(update_time, new_schedule)
            return {
                'update_time': update_time,
                'schedule': new_schedule,
                'hash': schedule_hash
            }
            
    except Exception as e:
        logger.error(f"Ошибка получения расписания: {e}")
    
    return None

def load_user_data(user_id):
    """Загружает данные пользователя из его файла"""
    user_file = os.path.join(USERS_DIR, f"{user_id}.json")
    if os.path.exists(user_file):
        with open(user_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def save_user_data(user_id, data):
    """Сохраняет данные пользователя в его файл"""
    user_file = os.path.join(USERS_DIR, f"{user_id}.json")
    with open(user_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def update_user_data(user_id, new_data):
    """Обновляет данные пользователя с гарантированным сохранением идентификаторов"""
    try:
        # Загрузка существующих данных или создание новых
        user_file = os.path.join(USERS_DIR, f"{user_id}.json")
        if os.path.exists(user_file):
            with open(user_file, 'r', encoding='utf-8') as f:
                current_data = json.load(f)
        else:
            current_data = {
                'user_id': user_id,
                'username': str(user_id),
                'first_seen': datetime.now().isoformat(),
                'last_seen': datetime.now().isoformat(),
                'notifications': False,
                'actions': [],
                'chat_id': None,
                'banned': False,
                'ban_reason': None
            }

        # Обновление данных с сохранением критичных полей
        for key, value in new_data.items():
            # Особые правила для ключевых полей
            if key == 'chat_id' and value is not None:
                current_data[key] = value
            elif key == 'user_id':
                continue  # Никогда не перезаписываем user_id из new_data
            else:
                current_data[key] = value

        # Гарантируем что user_id всегда правильный
        current_data['user_id'] = user_id

        # Сохранение обновленных данных
        with open(user_file, 'w', encoding='utf-8') as f:
            json.dump(current_data, f, ensure_ascii=False, indent=4)

        return current_data
    except Exception as e:
        logger.error(f"Ошибка обновления данных пользователя {user_id}: {str(e)}")
        return None

def log_user_activity(user_id, username, action, chat_id=None):
    """Логирует активность пользователя с сохранением идентификаторов"""
    try:
        # Подготовка данных для обновления
        update_data = {
            'username': username,
            'last_seen': datetime.now().isoformat()
        }

        # Добавляем chat_id если он предоставлен
        if chat_id is not None:
            update_data['chat_id'] = chat_id

        # Получаем текущие данные пользователя
        user_data = update_user_data(user_id, update_data)
        if not user_data:
            raise Exception("Не удалось обновить данные пользователя")

        # Добавляем запись в историю действий
        if 'actions' not in user_data:
            user_data['actions'] = []

        user_data['actions'].append({
            'time': datetime.now().isoformat(),
            'action': action,
            'chat_id': chat_id,  # Сохраняем chat_id для каждого действия
            'user_id': user_id   # И user_id для дублирования
        })

        # Сохраняем обновленные данные
        user_file = os.path.join(USERS_DIR, f"{user_id}.json")
        with open(user_file, 'w', encoding='utf-8') as f:
            json.dump(user_data, f, ensure_ascii=False, indent=4)

    except Exception as e:
        logger.error(f"Ошибка логирования активности для {user_id}: {str(e)}")

def is_user_banned(user_id):
    """Проверяет, заблокирован ли пользователь"""
    user_data = load_user_data(user_id)
    return user_data.get('banned', False) if user_data else False

async def send_ban_notification(user_id, reason, is_banned=True):
    """Отправляет уведомление о блокировке/разблокировке"""
    try:
        user_data = load_user_data(user_id)
        if not user_data or not user_data.get('chat_id'):
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
                chat_id=user_data['chat_id'],
                text=message
            )
            logger.info(f"Уведомление о {'блокировке' if is_banned else 'разблокировке'} отправлено пользователю {user_id}")
        except Exception as e:
            logger.error(f"Ошибка при отправке уведомления пользователю {user_id}: {e}")
            if "bot was blocked" in str(e).lower():
                update_user_data(user_id, {'notifications': False})
                logger.info(f"Уведомления для пользователя {user_id} отключены, так как бот был заблокирован.")
    except Exception as e:
        logger.error(f"Критическая ошибка в send_ban_notification: {e}")

def anti_spam(func):
    """Декоратор для защиты от спама"""
    async def wrapper(update, context):
        user_id = update.effective_user.id
        
        if is_user_banned(user_id):
            await update.message.reply_text("🚫 Вы заблокированы в этом боте.")
            return
            
        now = datetime.now()
        
        if user_id not in user_requests:
            user_requests[user_id] = []
        
        user_requests[user_id] = [
            t for t in user_requests[user_id] 
            if now - t < timedelta(seconds=10)
        ]
        
        if len(user_requests[user_id]) >= 15:
            await update.message.reply_text("⚠️ Слишком много запросов. Подождите 10 секунд.")
            return
        
        user_requests[user_id].append(now)
        return await func(update, context)
    return wrapper


@anti_spam
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /start с инициализацией состояния"""
    user = update.effective_user
    chat = update.effective_chat
    
    # Инициализация состояния пользователя
    if user.id not in user_states:
        user_states[user.id] = {"notifications_active": False}
    
    # Загрузка состояния уведомлений из базы
    user_data = load_user_data(user.id)
    if user_data and 'notifications' in user_data:
        user_states[user.id]["notifications_active"] = user_data['notifications']
    
    log_user_activity(user.id, user.username or str(user.id), "start", chat.id)
    
    await update.message.reply_text(
        "👋 Привет! Я бот для отслеживания расписания. Выберите действие:",
        reply_markup=create_keyboard(user_states[user.id]["notifications_active"])
    )

def create_keyboard(notifications_active=False):
    """Создает клавиатуру с рабочими кнопками"""
    buttons = [
        ["📅 Проверить расписание"],
        ["🔔 Уведомления: Вкл" if notifications_active else "🔕 Уведомления: Выкл"],
        ["ℹ️ Информация о боте"],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

@anti_spam
async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отображение расписания с кнопкой обновления"""
    user = update.effective_user
    chat = update.effective_chat
    
    log_user_activity(user.id, user.username or str(user.id), "schedule_request", chat.id)
    
    data = get_latest_schedule()
    if not data or 'schedule' not in data:
        await update.message.reply_text(
            "⚠️ Не удалось загрузить расписание. Попробуйте позже.",
            reply_markup=create_keyboard(user_states.get(user.id, {}).get("notifications_active", False))
        )  # Закрывающая скобка была пропущена
        return
    
    response = f"🕒 Расписание обновлено: {data.get('update_time', 'неизвестно')}\n\n"
    
    for date, day_data in data["schedule"].items():
        day_of_week = day_data.get("day_of_week", "")
        response += f"📅 {date} ({day_of_week}):\n"
        
        for lesson in day_data["lessons"]:
            if lesson.get("name") == "Свободно":
                response += f"🔢 {lesson.get('lesson_number', '?')}: Нет пары\n"
            else:
                subgroup = f" (Подгруппа {lesson['subgroup']})" if lesson.get("subgroup") else ""
                response += (
                    f"🔢 {lesson.get('lesson_number', '?')}: "
                    f"{lesson.get('name', 'Неизвестно')} - "
                    f"{lesson.get('auditorium', '')} "
                    f"(👨‍🏫 {lesson.get('teacher', '')}){subgroup}\n"
                )
        response += "\n"
    
    try:
        # Отправляем расписание с текущей клавиатурой
        current_state = user_states.get(user.id, {}).get("notifications_active", False)
        await update.message.reply_text(
            response,
            reply_markup=create_keyboard(current_state)
        )
    except Exception as e:
        logger.error(f"Ошибка отправки расписания: {e}")
        await update.message.reply_text(
            "⚠️ Произошла ошибка при формировании расписания",
            reply_markup=create_keyboard(user_states.get(user.id, {}).get("notifications_active", False))
        )

@anti_spam
async def toggle_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Переключение уведомлений через команду /notif"""
    user = update.effective_user
    chat = update.effective_chat
    
    if user.id not in user_states:
        user_states[user.id] = {"notifications_active": False}
    
    # Переключаем состояние
    user_states[user.id]["notifications_active"] = not user_states[user.id]["notifications_active"]
    new_state = user_states[user.id]["notifications_active"]
    
    # Сохраняем в базу
    update_user_data(user.id, {'notifications': new_state})
    log_user_activity(user.id, user.username or str(user.id), 
                     f"notifications_{'on' if new_state else 'off'}", chat.id)
    
    await update.message.reply_text(
        f"Уведомления {'включены' if new_state else 'отключены'}",
        reply_markup=create_keyboard(new_state)
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
async def handle_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает все входящие сообщения"""
    try:
        user = update.effective_user
        chat = update.effective_chat
        message = update.message

        # Логируем активность
        log_user_activity(
            user_id=user.id,
            username=user.username or str(user.id),
            action="message_received",
            chat_id=chat.id
        )

        # Обработка некомандных сообщений
        if not message.text.startswith('/'):
            # Инициализация состояния пользователя если нужно
            if user.id not in user_states:
                user_states[user.id] = {"notifications_active": False}

            # Отправка подсказки
            reply_text = (
                "ℹ️ Используйте команды для взаимодействия с ботом:\n"
                "/start - главное меню\n"
                "/check - показать расписание\n"
                "/notif - переключить уведомления\n"
                "/info - информация о боте"
            )
            
            await message.reply_text(
                reply_text,
                reply_markup=create_keyboard(user_states[user.id]["notifications_active"])
            )

    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {str(e)}")
        try:
            await update.message.reply_text("⚠️ Произошла ошибка при обработке сообщения")
        except:
            pass

@anti_spam
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений и кнопок"""
    user = update.effective_user
    chat = update.effective_chat
    message_text = update.message.text
    
    # Логируем активность
    log_user_activity(user.id, user.username or str(user.id), "button_press", chat.id)
    
    if user.id not in user_states:
        user_states[user.id] = {"notifications_active": False}
    
    # Обработка нажатий кнопок
    if message_text == "📅 Проверить расписание":
        await show_schedule(update, context)
    
    elif message_text.startswith("🔔 Уведомления: Вкл"):
        user_states[user.id]["notifications_active"] = False
        update_user_data(user.id, {'notifications': False})
        await update.message.reply_text(
            "Уведомления отключены",
            reply_markup=create_keyboard(False)
        )
    
    elif message_text.startswith("🔕 Уведомления: Выкл"):
        user_states[user.id]["notifications_active"] = True
        update_user_data(user.id, {'notifications': True})
        await update.message.reply_text(
            "Уведомления включены",
            reply_markup=create_keyboard(True)
        )
    
    elif message_text == "ℹ️ Информация о боте":
        await bot_info(update, context)
        
    else:
        await update.message.reply_text(
            "Используйте кнопки меню для взаимодействия с ботом",
            reply_markup=create_keyboard(user_states[user.id]["notifications_active"])
        )

async def send_notifications(context: CallbackContext, message):
    """Отправка уведомлений пользователям с включенными уведомлениями"""
    try:
        success = 0
        failed = 0
        
        for filename in os.listdir(USERS_DIR):
            if filename.endswith('.json'):
                user_id = filename[:-5]
                with open(os.path.join(USERS_DIR, filename), 'r', encoding='utf-8') as f:
                    user_data = json.load(f)
                
                # Пропускаем заблокированных пользователей
                if user_data.get('banned', False):
                    continue
                    
                # Проверяем, что уведомления включены и есть chat_id
                if user_data.get('notifications', False) and user_data.get('chat_id'):
                    try:
                        await context.bot.send_message(
                            chat_id=user_data['chat_id'],
                            text=message
                        )
                        success += 1
                    except Exception as e:
                        failed += 1
                        logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")
                        # Если бот заблокирован, отключаем уведомления
                        if "bot was blocked" in str(e).lower():
                            update_user_data(user_id, {'notifications': False})
                            logger.info(f"Уведомления для пользователя {user_id} отключены (бот заблокирован)")
        
        logger.info(f"Уведомления отправлены: успешно {success}, ошибок {failed}")
        
    except Exception as e:
        logger.error(f"Критическая ошибка при отправке уведомлений: {e}")

async def check_schedule_changes(context: CallbackContext):
    """Проверка изменений в расписании и отправка уведомлений"""
    try:
        config = load_config()
        interval = config.get('check_interval', 300)  # По умолчанию 5 минут
        
        # Получаем текущее расписание с сайта
        from parser import get_schedule
        update_time, new_schedule = get_schedule()
        
        if not new_schedule:
            logger.error("Не удалось получить новое расписание с сайта")
            return
            
        # Вычисляем хеш нового расписания
        new_hash = hashlib.md5(json.dumps(new_schedule, sort_keys=True).encode('utf-8')).hexdigest()
        
        # Получаем последнее сохраненное расписание
        old_data = get_latest_schedule()
        old_hash = old_data.get('hash') if old_data else None
        
        if old_hash != new_hash:
            logger.info(f"Обнаружены изменения в расписании (интервал проверки: {interval} сек)")
            
            # Сохраняем новое расписание
            save_schedule(update_time, new_schedule)
            
            # Формируем сообщение об изменениях
            message = (
                "🔄 Расписание обновлено!\n"
                f"Дата обновления: {update_time}\n"
                "Используйте команду /check чтобы посмотреть изменения"
            )
            
            # Отправляем уведомления пользователям с включенными уведомлениями
            await send_notifications(context, message)
        else:
            logger.info(f"Изменений в расписании не обнаружено (интервал проверки: {interval} сек)")
        
        # Планируем следующую проверку
        context.job_queue.run_once(
            check_schedule_changes, 
            interval,
            name="schedule_check"
        )
        
    except Exception as e:
        logger.error(f"Ошибка при проверке расписания: {e}")
        # Планируем повторную проверку через 5 минут в случае ошибки
        context.job_queue.run_once(
            check_schedule_changes, 
            300,
            name="schedule_check"
        )

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
    
    # Сохраняем PID файл при запуске
    pid_file = os.path.join(os.path.dirname(__file__), 'bot.pid')
    try:
        with open(pid_file, 'w') as f:
            f.write(str(os.getpid()))
    except Exception as e:
        logger.error(f"Ошибка сохранения PID файла: {e}")

    try:
        config = load_config()
        if not config or not config.get('token'):
            logger.error("❌ Токен бота не настроен!")
            return
            
        application = ApplicationBuilder().token(config["token"]).build()

        # Регистрируем обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("check", show_schedule))
        application.add_handler(CommandHandler("info", bot_info))
        application.add_handler(CommandHandler("notif", toggle_notifications))
        
        # Основной обработчик текстовых сообщений
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        # Планировщик проверки расписания
        check_interval = config.get('check_interval', 1800)
        if check_interval < 300:
            check_interval = 300
            logger.warning("Установлен минимальный интервал проверки (300 сек)")
        
        application.job_queue.run_once(check_schedule_changes, check_interval, name="schedule_check")
        
        logger.info(f"🤖 Бот запускается с интервалом проверки {check_interval} сек...")
        
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        
        await shutdown_event.wait()
        
    except InvalidToken:
        logger.error("❌ Неверный токен Telegram бота")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {str(e)}")
    finally:
        # Удаляем PID файл при завершении
        try:
            os.remove(pid_file)
        except Exception as e:
            logger.error(f"Ошибка удаления PID файла: {e}")
        
        await shutdown_application()

def signal_handler(sig, frame):
    """Обработчик сигналов для завершения работы"""
    logger.info("Получен сигнал завершения")
    shutdown_event.set()

if __name__ == "__main__":
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

def signal_handler(sig, frame):
    """Обработчик сигналов для завершения работы"""
    logger.info("Получен сигнал завершения")
    shutdown_event.set()

if __name__ == "__main__":
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