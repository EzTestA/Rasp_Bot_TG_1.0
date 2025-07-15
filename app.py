#!/usr/bin/env python3
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from telegram import Bot
import json
import os
import signal
import subprocess
import sys
import psutil
import time
import glob
import threading
import requests
from datetime import datetime
from urllib.parse import quote_plus
import asyncio
from bot import send_ban_notification as send_bot_notification

app = Flask(__name__, template_folder='templates')
app.secret_key = 'your-secret-key-here'
from log_handler import setup_logging
logger = setup_logging()

# Константы путей
USERS_DIR = 'users'
LOG_DIR = 'logs'
SCHEDULES_DIR = 'schedules'
CONFIG_FILE = 'config.json'
MESSAGES_DIR = 'messages'
INDIVIDUAL_MSGS_FILE = os.path.join(MESSAGES_DIR, 'individual_messages.json')
GROUP_MSGS_FILE = os.path.join(MESSAGES_DIR, 'group_messages.json')
BROADCAST_MSGS_FILE = os.path.join(MESSAGES_DIR, 'broadcast_messages.json')

def init_message_files():
    """Инициализация файлов сообщений при запуске"""
    os.makedirs(MESSAGES_DIR, exist_ok=True)
    
    # Создаем файлы если их нет
    for file_path in [INDIVIDUAL_MSGS_FILE, GROUP_MSGS_FILE, BROADCAST_MSGS_FILE]:
        if not os.path.exists(file_path):
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump([], f, ensure_ascii=False, indent=4)


def init_data_structure():
    """Инициализация файловой структуры"""
    os.makedirs(USERS_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(SCHEDULES_DIR, exist_ok=True)
    os.makedirs(MESSAGES_DIR, exist_ok=True)
    init_message_files()
    
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            'token': '',
            'site_url': 'SITE',
            'admin_login': 'admin',
            'admin_password': 'admin',
            'check_interval': 1800
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(default_config, f, indent=4)

def load_config():
    """Загрузка конфигурации"""
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {
            'token': '',
            'site_url': 'SITE',
            'admin_login': 'admin',
            'admin_password': 'admin',
            'check_interval': 1800
        }
    
def init_message_files():
    """Инициализация файлов сообщений при запуске"""
    for file_path in [INDIVIDUAL_MSGS_FILE, GROUP_MSGS_FILE, BROADCAST_MSGS_FILE]:
        if not os.path.exists(file_path):
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump([], f, ensure_ascii=False, indent=4)

def save_config(config):
    """Сохранение конфигурации"""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        print(f"Ошибка сохранения конфига: {e}")
        return False

def get_bot_process():
    """Поиск процесса бота"""
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info['cmdline'] or []
            if 'python' in proc.info['name'].lower() and 'bot.py' in ' '.join(cmdline):
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None

def stop_bot():
    """Остановка бота"""
    try:
        bot_process = get_bot_process()
        if not bot_process:
            return True
            
        if sys.platform == 'win32':
            subprocess.run(['taskkill', '/F', '/PID', str(bot_process.info['pid'])], 
                          check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.kill(bot_process.info['pid'], signal.SIGKILL)
        
        time.sleep(2)
        return get_bot_process() is None
    except Exception as e:
        print(f"Ошибка остановки бота: {e}")
        return False

def start_bot():
    """Запуск бота"""
    try:
        if sys.platform == 'win32':
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            creation_flags = 0
            
        subprocess.Popen(
            [sys.executable, 'bot.py'],
            creationflags=creation_flags,
            start_new_session=True
        )
        time.sleep(2)
        return True
    except Exception as e:
        print(f"Ошибка запуска бота: {e}")
        return False

def restart_bot():
    """Перезапуск бота"""
    stop_bot()
    time.sleep(2)
    return start_bot()

def bot_status():
    """Статус бота с учетом работы в Termux"""
    try:
        # Проверяем, работает ли в Termux
        is_termux = 'com.termux' in os.environ.get('PREFIX', '')
        
        if is_termux:
            # Альтернативный способ проверки для Termux
            try:
                # Проверяем наличие файла с PID бота
                pid_file = os.path.join(os.path.dirname(__file__), 'bot.pid')
                if os.path.exists(pid_file):
                    with open(pid_file, 'r') as f:
                        pid = int(f.read().strip())
                    # Проверяем существует ли процесс с таким PID
                    return "running" if os.path.exists(f"/proc/{pid}") else "stopped"
                return "stopped"
            except:
                return "stopped"
        else:
            # Стандартная проверка для других ОС
            return "running" if get_bot_process() else "stopped"
    except Exception as e:
        print(f"Ошибка проверки статуса бота: {e}")
        return "stopped"

def load_user_data(user_id):
    """Загружает данные пользователя"""
    user_file = os.path.join(USERS_DIR, f"{user_id}.json")
    if os.path.exists(user_file):
        with open(user_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def save_user_data(user_id, data):
    """Сохраняет данные пользователя"""
    user_file = os.path.join(USERS_DIR, f"{user_id}.json")
    with open(user_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def send_ban_notification(user_id, reason, is_banned=True):
    """Отправляет уведомление о блокировке или разблокировке"""
    try:
        user_data = load_user_data(user_id)
        if not user_data:
            print(f"Пользователь {user_id} не найден.")
            return
        if not user_data.get('chat_id'):
            print(f"У пользователя {user_id} отсутствует chat_id.")
            return

        config = load_config()
        if not config or not config.get('token'):
            print("Токен бота не настроен.")
            return

        token = config['token']
        chat_id = user_data['chat_id']
        
        if is_banned:
            message = f"🚫 Вы были заблокированы в боте.\nПричина: {reason}"
        else:
            message = "✅ Ваша блокировка в боте снята!"

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        response = requests.post(
            url,
            json={
                'chat_id': chat_id,
                'text': message
            }
        )
        
        if response.status_code == 200:
            print(f"Уведомление отправлено пользователю {user_id}.")
        else:
            print(f"Ошибка отправки уведомления: {response.json()}")
    except Exception as e:
        print(f"Ошибка отправки уведомления: {e}")

@app.route('/')
@app.route('/admin')
def admin():
    """Главная страница админки"""
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    config = load_config()
    return render_template('index.html', config=config, bot_status=bot_status())

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Страница входа"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        config = load_config()
        
        if username == config['admin_login'] and password == config['admin_password']:
            session['logged_in'] = True
            return redirect(url_for('admin'))
        else:
            flash('Неверный логин или пароль', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    """Выход из системы"""
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/shutdown', methods=['POST'])
def shutdown():
    """Выключение системы"""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        if not stop_bot():
            return jsonify({'error': 'Не удалось остановить бота'}), 500
        
        def delayed_shutdown():
            time.sleep(1)
            os.kill(os.getpid(), signal.SIGTERM)
        
        threading.Thread(target=delayed_shutdown, daemon=True).start()
        
        return jsonify({'message': 'Система выключается...'})
    except Exception as e:
        return jsonify({'error': f'Ошибка выключения: {str(e)}'}), 500

@app.route('/update_config', methods=['POST'])
def update_config():
    """Обновление конфигурации с проверкой интервала"""
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    try:
        try:
            check_interval = int(request.form.get('check_interval', 1800))
            if check_interval < 300:
                flash('Интервал проверки не может быть меньше 300 секунд (5 минут)', 'warning')
                check_interval = 300
            elif check_interval > 86400:
                flash('Интервал проверки не может быть больше 86400 секунд (24 часа)', 'warning')
                check_interval = 86400
        except ValueError:
            flash('Некорректное значение интервала проверки. Используется значение по умолчанию (1800 сек)', 'warning')
            check_interval = 1800

        new_config = {
            'token': request.form.get('token', '').strip(),
            'site_url': request.form.get('site_url', '').strip(),
            'admin_login': request.form.get('admin_login', '').strip(),
            'admin_password': request.form.get('admin_password', '').strip(),
            'check_interval': check_interval
        }

        if not all([new_config['site_url'], new_config['admin_login'], new_config['admin_password']]):
            flash('Все поля обязательны для заполнения', 'danger')
            return redirect(url_for('admin'))

        old_config = load_config()
        token_changed = old_config.get('token') != new_config['token']

        if save_config(new_config):
            flash('Настройки успешно сохранены', 'success')
            
            if token_changed or (old_config.get('check_interval') != check_interval):
                if restart_bot():
                    flash('Бот успешно перезапущен с новыми параметрами', 'success')
                else:
                    flash('Бот не запущен - проверьте токен', 'warning')
        else:
            flash('Ошибка при сохранении настроек', 'danger')

    except Exception as e:
        app.logger.error(f"Ошибка при обновлении конфига: {str(e)}")
        flash(f'Произошла ошибка: {str(e)}', 'danger')

    return redirect(url_for('admin'))

def restart_bot():
    """Перезапуск бота с учетом новых параметров конфигурации"""
    try:
        if not stop_bot():
            print("⚠️ Не удалось остановить бота перед перезапуском")
            return False

        time.sleep(2)

        config = load_config()
        check_interval = config.get('check_interval', 1800)

        if not config.get('token'):
            print("❌ Токен бота не настроен, перезапуск невозможен")
            return False

        if sys.platform == 'win32':
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            creation_flags = 0

        process = subprocess.Popen(
            [sys.executable, 'bot.py'],
            creationflags=creation_flags,
            start_new_session=True
        )

        time.sleep(3)
        if process.poll() is not None:
            print(f"❌ Бот завершился с кодом {process.returncode}")
            return False

        print(f"🔄 Бот успешно перезапущен (интервал проверки: {check_interval} сек)")
        return True

    except Exception as e:
        print(f"⛔ Критическая ошибка при перезапуске бота: {str(e)}")
        return False
    
@app.route('/execute_command', methods=['POST'])
def execute_command():
    """Выполняет команду перезапуска или выключения"""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        data = request.get_json()
        command = data.get('command')
        
        if command not in ['restart', 'shutdown']:
            return jsonify({'error': 'Invalid command'}), 400
        
        # Запускаем менеджер в отдельном процессе
        if sys.platform == 'win32':
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            creation_flags = 0
            
        subprocess.Popen(
            [sys.executable, 'bot_manager.py', command],
            creationflags=creation_flags,
            start_new_session=True
        )
        
        return jsonify({
            'message': f'Команда {command} принята. Система будет {"перезапущена" if command == "restart" else "выключена"} через несколько секунд.'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/start_bot')
def start_bot_route():
    """Запуск бота"""
    if not session.get('logged_in'):
        return jsonify({'message': 'Unauthorized'}), 401
    
    if start_bot():
        return jsonify({'message': 'Бот успешно запущен'})
    return jsonify({'message': 'Ошибка при запуске бота'}), 500

@app.route('/stop_bot')
def stop_bot_route():
    """Остановка бота"""
    if not session.get('logged_in'):
        return jsonify({'message': 'Unauthorized'}), 401
    
    if stop_bot():
        return jsonify({'message': 'Бот успешно остановлен'})
    return jsonify({'message': 'Ошибка при остановке бота'}), 500

@app.route('/get_users')
def get_users():
    """Получение списка пользователей"""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        users = {}
        for filename in os.listdir(USERS_DIR):
            if filename.endswith('.json'):
                user_id = filename[:-5]
                with open(os.path.join(USERS_DIR, filename), 'r', encoding='utf-8') as f:
                    user_data = json.load(f)
                    if 'chat_id' not in user_data:
                        user_data['chat_id'] = None
                    # Добавляем количество действий
                    user_data['total_actions'] = len(user_data.get('actions', []))
                    users[user_id] = user_data
        return jsonify(users)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/get_user_actions/<user_id>')
def get_user_actions(user_id):
    """Получение действий конкретного пользователя"""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        user_data = load_user_data(user_id)
        if not user_data:
            return jsonify({'error': 'Пользователь не найден'}), 404
        
        return jsonify({
            'username': user_data.get('username'),
            'total_actions': len(user_data.get('actions', [])),
            'actions': user_data.get('actions', [])
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/get_messages/<message_type>')
def get_messages(message_type):
    """Получение сообщений по типу с автоматическим созданием файлов если их нет"""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        # Инициализация файлов если их нет
        init_message_files()
        
        file_map = {
            'individual': INDIVIDUAL_MSGS_FILE,
            'group': GROUP_MSGS_FILE,
            'broadcast': BROADCAST_MSGS_FILE
        }

        if message_type not in file_map:
            return jsonify({'error': 'Invalid message type'}), 400

        # Чтение сообщений из файла
        with open(file_map[message_type], 'r', encoding='utf-8') as f:
            messages = json.load(f)
            return jsonify({
                'messages': messages[::-1],  # Свежие сообщения первыми
                'type': message_type,
                'count': len(messages)
            })
    except Exception as e:
        logger.error(f"Ошибка получения сообщений: {str(e)}")
        return jsonify({'error': str(e)}), 500
    
@app.route('/get_all_messages')
def get_all_messages():
    """Получение всех сообщений из всех типов"""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        all_messages = []
        
        # Читаем сообщения из всех файлов
        for msg_type, file_path in [
            ('individual', INDIVIDUAL_MSGS_FILE),
            ('group', GROUP_MSGS_FILE),
            ('broadcast', BROADCAST_MSGS_FILE)
        ]:
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    messages = json.load(f)
                    for msg in messages:
                        msg['message_type'] = msg_type
                        all_messages.append(msg)
        
        # Сортируем по времени (новые сначала)
        all_messages.sort(key=lambda x: x['timestamp'], reverse=True)
        
        return jsonify({'messages': all_messages})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/ban_user', methods=['POST'])
def ban_user():
    """Блокировка пользователя"""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401

    user_id = request.form.get('user_id')
    reason = request.form.get('reason', 'Нарушение правил')

    try:
        user_data = load_user_data(user_id)
        if not user_data:
            return jsonify({'error': 'Пользователь не найден'}), 404
        
        user_data['banned'] = True
        user_data['ban_reason'] = reason
        save_user_data(user_id, user_data)

        send_ban_notification(user_id, reason, is_banned=True)
        return jsonify({'message': f'Пользователь {user_id} заблокирован'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/unban_user', methods=['POST'])
def unban_user():
    """Разблокировка пользователя"""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401

    user_id = request.form.get('user_id')

    try:
        user_data = load_user_data(user_id)
        if not user_data:
            return jsonify({'error': 'Пользователь не найден'}), 404
        
        user_data['banned'] = False
        if 'ban_reason' in user_data:
            del user_data['ban_reason']
        save_user_data(user_id, user_data)

        send_ban_notification(user_id, "", is_banned=False)
        return jsonify({'message': f'Пользователь {user_id} разблокирован'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/send_message', methods=['POST'])
def send_message():
    """Универсальный обработчик отправки сообщений"""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        # Получаем данные
        user_ids = request.form.getlist('user_ids[]')
        message = request.form.get('message', '').strip()
        message_type = request.form.get('message_type', 'broadcast')

        if not message:
            return jsonify({'error': 'Message text cannot be empty'}), 400

        config = load_config()
        if not config or not config.get('token'):
            return jsonify({'error': 'Bot token not configured'}), 500

        # Подготовка данных для истории
        msg_data = {
            'timestamp': datetime.now().isoformat(),
            'message': message,
            'sender': 'admin',
            'recipients': user_ids if message_type != 'broadcast' else 'all',
            'type': message_type,
            'delivered': []
        }

        token = config['token']
        success_count = 0
        failed_count = 0
        recipients = []

        # Обработка разных типов сообщений
        if message_type == 'broadcast':
            # Всем пользователям
            for filename in os.listdir(USERS_DIR):
                if filename.endswith('.json'):
                    with open(os.path.join(USERS_DIR, filename), 'r', encoding='utf-8') as f:
                        user_data = json.load(f)
                        if not user_data.get('banned', False) and user_data.get('chat_id'):
                            recipients.append({
                                'user_id': user_data.get('user_id'),
                                'chat_id': user_data['chat_id']
                            })
        else:
            # Конкретным пользователям
            for user_id in user_ids:
                user_data = load_user_data(user_id)
                if user_data and user_data.get('chat_id') and not user_data.get('banned', False):
                    recipients.append({
                        'user_id': user_id,
                        'chat_id': user_data['chat_id']
                    })
                else:
                    failed_count += 1
                    msg_data['delivered'].append({
                        'user_id': user_id,
                        'status': 'failed',
                        'reason': 'User not found or banned'
                    })

        # Отправка сообщений
        for recipient in recipients:
            try:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                response = requests.post(url, json={
                    'chat_id': recipient['chat_id'],
                    'text': message
                }, timeout=10)

                if response.json().get('ok'):
                    success_count += 1
                    msg_data['delivered'].append({
                        'user_id': recipient['user_id'],
                        'chat_id': recipient['chat_id'],
                        'status': 'success'
                    })
                else:
                    failed_count += 1
                    error = response.json().get('description', 'Unknown error')
                    msg_data['delivered'].append({
                        'user_id': recipient['user_id'],
                        'chat_id': recipient['chat_id'],
                        'status': 'failed',
                        'reason': error
                    })
            except Exception as e:
                failed_count += 1
                msg_data['delivered'].append({
                    'user_id': recipient.get('user_id'),
                    'status': 'failed',
                    'reason': str(e)
                })

        # Сохраняем в историю
        _save_message_to_history(message_type, msg_data)

        return jsonify({
            'message': f'Успешно отправлено: {success_count}, Ошибок: {failed_count}',
            'details': msg_data
        })

    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения: {str(e)}")
        return jsonify({'error': str(e)}), 500

def _save_message_to_history(message_type, msg_data):
    """Сохраняет сообщение в соответствующую историю"""
    try:
        file_map = {
            'individual': INDIVIDUAL_MSGS_FILE,
            'group': GROUP_MSGS_FILE,
            'broadcast': BROADCAST_MSGS_FILE
        }
        
        file_path = file_map.get(message_type)
        if not file_path:
            raise ValueError("Invalid message type")
        
        # Чтение существующих сообщений
        messages = []
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                messages = json.load(f)
        
        # Добавление нового сообщения
        messages.append(msg_data)
        
        # Сохранение
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(messages, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Error saving message history: {str(e)}")

def _send_telegram_message(token, chat_id, text, user_id=None):
    """Вспомогательная функция для отправки сообщения"""
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        response = requests.post(url, json={
            'chat_id': chat_id,
            'text': text
        }, timeout=10)
        
        if response.json().get('ok'):
            return {'success': True}
        else:
            error = response.json().get('description', 'Unknown error')
            logger.error(f"Failed to send to {user_id or 'unknown'}/{chat_id}: {error}")
            return {'success': False, 'error': error}
    except Exception as e:
        logger.error(f"Error sending to {user_id or 'unknown'}/{chat_id}: {str(e)}")
        return {'success': False, 'error': str(e)}

def _save_message_to_history(message_type, msg_data):
    """Сохраняет сообщение в соответствующую историю"""
    try:
        file_map = {
            'individual': INDIVIDUAL_MSGS_FILE,
            'group': GROUP_MSGS_FILE,
            'broadcast': BROADCAST_MSGS_FILE
        }
        
        file_path = file_map.get(message_type)
        if not file_path:
            raise ValueError("Invalid message type")
        
        # Чтение существующих сообщений
        messages = []
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                messages = json.load(f)
        
        # Добавление нового сообщения
        messages.append(msg_data)
        
        # Сохранение
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(messages, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Error saving message history: {str(e)}")
    
@app.route('/get_logs')
def get_logs():
    """Получение логов"""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        log_file = os.path.join(LOG_DIR, 'bot.log')
        with open(log_file, 'r', encoding='utf-8') as f:
            return jsonify({'logs': f.read()})
    except:
        return jsonify({'error': 'Logs not found'}), 404

@app.route('/clear_logs')
def clear_logs():
    """Очистка логов"""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        log_file = os.path.join(LOG_DIR, 'bot.log')
        open(log_file, 'w').close()
        return jsonify({'message': 'Логи очищены'})
    except:
        return jsonify({'error': 'Ошибка очистки логов'}), 500

@app.route('/get_schedules')
def get_schedules():
    """Получение списка расписаний, включая last_schedule.json"""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        # Получаем все файлы расписаний (кроме last_schedule.json)
        files = sorted(glob.glob(os.path.join(SCHEDULES_DIR, '*.json')), reverse=True)
        files = [f for f in files if not f.endswith('last_schedule.json')][:500]
        
        schedules = []
        
        # Добавляем last_schedule.json первым в списке
        last_schedule_file = os.path.join(SCHEDULES_DIR, 'last_schedule.json')
        if os.path.exists(last_schedule_file):
            with open(last_schedule_file, 'r', encoding='utf-8') as f:
                schedule_data = json.load(f)
                schedules.append({
                    'filename': 'last_schedule.json',
                    'update_time': datetime.now().strftime("%d.%m.%Y %H:%M"),
                    'is_current': True  # Добавляем флаг текущего расписания
                })
        
        # Добавляем остальные расписания
        for file in files:
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    schedules.append({
                        'filename': os.path.basename(file),
                        'update_time': data.get('update_time', ''),
                        'is_current': False
                    })
            except:
                continue
                
        return jsonify(schedules)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/get_schedule/<filename>')
def get_schedule(filename):
    """Получение конкретного расписания"""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        filepath = os.path.join(SCHEDULES_DIR, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    except:
        return jsonify({'error': 'Schedule not found'}), 404

@app.route('/get_formatted_schedule/<filename>')
def get_formatted_schedule(filename):
    """Получение форматированного расписания"""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        filepath = os.path.join(SCHEDULES_DIR, filename)
        
        # Обработка last_schedule.json
        if filename == 'last_schedule.json':
            with open(filepath, 'r', encoding='utf-8') as f:
                schedule_data = json.load(f)
                update_time = datetime.now().strftime("%d.%m.%Y %H:%M")
        else:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                schedule_data = data.get('schedule', {})
                update_time = data.get('update_time', 'неизвестно')
        
        if not schedule_data:
            return jsonify({'error': 'Расписание пустое'}), 404
            
        html = f"""
        <div class="card mb-4">
            <div class="card-header bg-primary text-white">
                <h5>Расписание от {update_time} { '(Текущее)' if filename == 'last_schedule.json' else ''}</h5>
            </div>
            <div class="card-body">
        """
        
        for date, day_data in schedule_data.items():
            day_of_week = day_data.get("day_of_week", "")
            html += f"""
            <div class="day-schedule mb-4">
                <h6 class="day-title text-primary">{date} ({day_of_week})</h6>
                <table class="table table-bordered table-sm">
                    <thead class="thead-dark">
                        <tr>
                            <th>№</th>
                            <th>Предмет</th>
                            <th>Аудитория</th>
                            <th>Преподаватель</th>
                            <th>Подгруппа</th>
                        </tr>
                    </thead>
                    <tbody>
            """
            
            for lesson in day_data["lessons"]:
                html += f"""
                <tr>
                    <td>{lesson.get('lesson_number', '')}</td>
                    <td>{lesson.get('name', '')}</td>
                    <td>{lesson.get('auditorium', '')}</td>
                    <td>{lesson.get('teacher', '')}</td>
                    <td>{'Да' if lesson.get('subgroup') else 'Нет'}</td>
                </tr>
                """
            
            html += """
                    </tbody>
                </table>
            </div>
            """
        
        html += """
            </div>
        </div>
        """
        
        return jsonify({'html': html})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/logs')
def logs():
    """Страница логов"""
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('logs.html')

@app.route('/users')
def users():
    """Страница пользователей"""
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('users.html')

@app.route('/schedule')
def schedule():
    """Страница расписания"""
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('schedule.html')

if __name__ == '__main__':
    init_data_structure()
    init_message_files()
    config = load_config()
    if config and config.get('token'):
        start_bot()
    app.run(host='0.0.0.0', port=5000, debug=False)