#!/usr/bin/env python3
import os
import sys
import time
import psutil
import subprocess
import signal

def find_processes(names):
    """Находит все процессы с указанными именами"""
    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = ' '.join(proc.info['cmdline'] or [])
            if any(name in cmdline for name in names):
                processes.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return processes

def kill_processes(processes):
    """Принудительно завершает процессы"""
    for proc in processes:
        try:
            proc.kill()
            print(f"Процесс {proc.info['pid']} завершен")
        except Exception as e:
            print(f"Ошибка завершения процесса {proc.info['pid']}: {e}")

def start_bot():
    """Запускает main.py"""
    try:
        if sys.platform == 'win32':
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            creation_flags = 0
            
        subprocess.Popen(
            [sys.executable, 'main.py'],
            creationflags=creation_flags,
            start_new_session=True
        )
        print("Бот и админ-панель запущены")
        return True
    except Exception as e:
        print(f"Ошибка запуска: {e}")
        return False

def main(action):
    """Основная функция управления"""
    # Находим все связанные процессы
    target_processes = ['main.py', 'app.py', 'bot.py']
    processes = find_processes(target_processes)
    
    if not processes:
        print("Не найдено запущенных процессов бота")
    else:
        print(f"Найдено {len(processes)} процессов для завершения")
        kill_processes(processes)
        time.sleep(2)  # Даем время на завершение
    
    if action == 'restart':
        print("Перезапуск системы...")
        if start_bot():
            print("Система успешно перезапущена")
        else:
            print("Ошибка перезапуска системы")
    elif action == 'shutdown':
        print("Система выключена")

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ['restart', 'shutdown']:
        print("Использование: python bot_manager.py [restart|shutdown]")
        sys.exit(1)
    
    main(sys.argv[1])