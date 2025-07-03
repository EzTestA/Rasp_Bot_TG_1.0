#!/usr/bin/env python3
import os
import signal
import sys
import time
import psutil
import socket
import subprocess
from app import app as flask_app
from app import init_data_structure, load_config, start_bot

def get_local_ip():
    """Получаем локальный IP-адрес"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except:
        return "127.0.0.1"

def get_external_ip():
    """Пытаемся получить внешний IP"""
    try:
        import requests
        return requests.get('https://api.ipify.org').text
    except:
        return None

def signal_handler(sig, frame):
    """Обработчик для graceful shutdown"""
    print("\nПолучен сигнал завершения. Остановка сервера...")
    # Сначала пытаемся остановить бота
    from app import stop_bot
    stop_bot()
    sys.exit(0)

if __name__ == "__main__":
    try:
        # Принудительное завершение предыдущих экземпляров
        print("🔍 Проверка запущенных экземпляров бота...")
        killed = 0
        current_pid = os.getpid()
        
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = ' '.join(proc.info['cmdline'] or [])
                if ('python' in proc.info['name'].lower() and 
                    'bot.py' in cmdline and 
                    str(current_pid) not in cmdline):
                    
                    proc.kill()
                    killed += 1
                    print(f"⚠️ Завершен предыдущий процесс (PID: {proc.info['pid']})")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        if killed > 0:
            print(f"🛑 Завершено {killed} предыдущих экземпляров")
            time.sleep(1)

        # Настройка обработчиков сигналов
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Инициализация структуры
        init_data_structure()
        
        # Загрузка конфига и запуск бота
        config = load_config()
        bot_status = "❌ Не запущен (отсутствует токен)"
        
        if config and config.get('token'):
            print("🚀 Попытка запуска Telegram бота...")
            if start_bot():
                bot_status = "✅ Запущен"
            else:
                bot_status = "❌ Ошибка запуска"
        
        # Получение IP-адресов
        local_ip = get_local_ip()
        external_ip = get_external_ip()
        port = 5000
        
        print("\n" + "="*50)
        print(f"Статус бота: {bot_status}")
        print(f"🌐 Локальный адрес: http://{local_ip}:{port}")
        
        if external_ip:
            print(f"🌍 Внешний адрес: http://{external_ip}:{port}")
            print("   (Доступно, если проброшен порт в роутере и нет фаервола)")
        else:
            print("⚠️ Не удалось определить внешний IP-адрес")
        
        print("\nℹ️ Используйте админку для управления ботом")
        print("🛑 Нажмите Ctrl+C для остановки сервера")
        print("="*50 + "\n")
        
        flask_app.run(host='0.0.0.0', port=port)
        
    except Exception as e:
        print(f"⛔ Критическая ошибка: {e}")
        sys.exit(1)