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
import asyncio # Добавить этот импорт
from bot import send_ban_notification as send_bot_notification # Импортируем функцию из bot.py

app = Flask(__name__, template_folder='templates')
app.secret_key = 'your-secret-key-here'

# Константы путей
LOG_DIR = 'logs'
SCHEDULES_DIR = 'schedules'
USER_DATA_FILE = 'users.json'
CONFIG_FILE = 'config.json'

def init_data_structure():
    """Инициализация файловой структуры"""
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(SCHEDULES_DIR, exist_ok=True)
    
    if not os.path.exists(USER_DATA_FILE):
        with open(USER_DATA_FILE, 'w') as f:
            json.dump({}, f)
    
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            'token': '',
            'site_url': 'http://94.72.18.202:8083/cg94.htm',
            'admin_login': 'admin',
            'admin_password': 'admin'
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
            'site_url': 'http://94.72.18.202:8083/cg94.htm',
            'admin_login': 'admin',
            'admin_password': 'admin'
        }

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
    """Статус бота"""
    return "running" if get_bot_process() else "stopped"

def send_ban_notification(user_id, reason, is_banned=True):
    """Отправляет уведомление о блокировке или разблокировке"""
    try:
        with open(USER_DATA_FILE, 'r', encoding='utf-8') as f:
            users = json.load(f)
            user = users.get(str(user_id))
            if not user:
                print(f"Пользователь {user_id} не найден.")
                return
            if not user.get('chat_id'):
                print(f"У пользователя {user_id} отсутствует chat_id.")
                return

        config = load_config()
        if not config or not config.get('token'):
            print("Токен бота не настроен.")
            return

        token = config['token']
        chat_id = user['chat_id']
        
        if is_banned:
            message = f"🚫 Вы были заблокированы в боте.\nПричина: {reason}"
        else:
            message = "✅ Ваша блокировка в боте снята!"

        # Отправка сообщения через Telegram API
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
    """Обновление конфигурации"""
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    config = load_config()
    new_config = {
        'token': request.form.get('token'),
        'site_url': request.form.get('site_url'),
        'admin_login': request.form.get('admin_login'),
        'admin_password': request.form.get('admin_password')
    }
    
    if config.get('token') != new_config['token'] and bot_status() == "running":
        restart_bot()
    
    if save_config(new_config):
        flash('Настройки успешно сохранены', 'success')
    else:
        flash('Ошибка при сохранении настроек', 'danger')
    
    return redirect(url_for('admin'))

@app.route('/restart_bot')
def restart_bot_route():
    """Перезапуск бота"""
    if not session.get('logged_in'):
        return jsonify({'message': 'Unauthorized'}), 401
    
    if restart_bot():
        return jsonify({'message': 'Бот успешно перезапущен'})
    return jsonify({'message': 'Ошибка при перезапуске бота'}), 500

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
        with open(USER_DATA_FILE, 'r', encoding='utf-8') as f:
            users = json.load(f)
            # Добавляем chat_id если его нет
            for user_id in users:
                if 'chat_id' not in users[user_id]:
                    users[user_id]['chat_id'] = None
            return jsonify(users)
    except:
        return jsonify({})

@app.route('/ban_user', methods=['POST'])
def ban_user():
    """Блокировка пользователя"""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401

    user_id = request.form.get('user_id')
    reason = request.form.get('reason', 'Нарушение правил')

    try:
        with open(USER_DATA_FILE, 'r+', encoding='utf-8') as f:
            users = json.load(f)
            if user_id not in users:
                return jsonify({'error': 'Пользователь не найден'}), 404
            
            users[user_id]['banned'] = True
            users[user_id]['ban_reason'] = reason
            f.seek(0)
            json.dump(users, f, ensure_ascii=False, indent=4)
            f.truncate()

        # Отправляем уведомление о блокировке
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
        with open(USER_DATA_FILE, 'r+', encoding='utf-8') as f:
            users = json.load(f)
            if user_id not in users:
                return jsonify({'error': 'Пользователь не найден'}), 404
            
            users[user_id]['banned'] = False
            if 'ban_reason' in users[user_id]:
                del users[user_id]['ban_reason']
            f.seek(0)
            json.dump(users, f, ensure_ascii=False, indent=4)
            f.truncate()

        # Отправляем уведомление о разблокировке
        send_ban_notification(user_id, "", is_banned=False)
        return jsonify({'message': f'Пользователь {user_id} разблокирован'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/send_message', methods=['POST'])
def send_message():
    """Отправка сообщения пользователям"""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401

    user_id = request.form.get('user_id', '').strip()
    message = request.form.get('message', '').strip()

    if not message:
        return jsonify({'error': 'Сообщение не может быть пустым'}), 400

    try:
        with open(USER_DATA_FILE, 'r', encoding='utf-8') as f:
            users = json.load(f)

        recipients = []
        if user_id:  # Отправка конкретному пользователю
            if user_id in users and users[user_id].get('chat_id'):
                # Отправляем даже если пользователь заблокирован
                recipients.append(users[user_id]['chat_id'])
            else:
                return jsonify({'error': 'У пользователя нет chat_id'}), 400
        else:  # Отправка всем
            # Пропускаем заблокированных при массовой рассылке
            recipients = [u.get('chat_id') for u in users.values() 
                         if u.get('chat_id') and not u.get('banned', False)]

        if not recipients:
            return jsonify({'error': 'Нет получателей'}), 400

        token = load_config()['token']
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        success, failed = 0, 0

        for chat_id in recipients:
            try:
                response = requests.post(
                    url,
                    json={
                        'chat_id': chat_id,
                        'text': message
                    },
                    headers={'Content-Type': 'application/json; charset=utf-8'}
                )
                if response.json().get('ok'):
                    success += 1
                else:
                    failed += 1
                    print(f"Ошибка Telegram API: {response.json()}")
            except Exception as e:
                failed += 1
                print(f"Ошибка отправки: {str(e)}")

        return jsonify({
            'message': f'Успешно отправлено: {success}, Ошибок: {failed}'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
    """Получение списка расписаний"""
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        files = sorted(glob.glob(os.path.join(SCHEDULES_DIR, '*.json')), reverse=True)[:500]
        schedules = []
        for file in files:
            with open(file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                schedules.append({
                    'filename': os.path.basename(file),
                    'update_time': data.get('update_time', ''),
                    'hash': data.get('hash', '')
                })
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
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        html = f"""
        <div class="card mb-4">
            <div class="card-header bg-primary text-white">
                <h5>Расписание от {data.get('update_time', 'неизвестно')}</h5>
            </div>
            <div class="card-body">
        """
        
        for day, pairs in data['schedule'].items():
            html += f"""
            <div class="day-schedule mb-4">
                <h6 class="day-title text-primary">{day}</h6>
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
            
            for pair in pairs:
                html += f"""
                <tr>
                    <td>{pair.get('N_par', '')}</td>
                    <td>{pair.get('Name', '')}</td>
                    <td>{pair.get('Auditory', '')}</td>
                    <td>{pair.get('Teacher', '')}</td>
                    <td>{'Да' if pair.get('Subgroup') == 'Да' else 'Нет'}</td>
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
    config = load_config()
    if config and config.get('token'):
        start_bot()
    app.run(host='0.0.0.0', port=5000, debug=False)