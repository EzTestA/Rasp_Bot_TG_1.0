#!/usr/bin/env python3
import os
import gzip
import logging
import logging.handlers
from datetime import datetime

LOG_DIR = 'logs'
MAX_LOG_SIZE = 50 * 1024  # 1 MB для теста
LOG_FILENAME = os.path.join(LOG_DIR, 'bot.log')

class GZipRotator:
    @staticmethod
    def namer(name):
        # Получаем базовое имя файла без расширения
        base = os.path.basename(name).split('.')[0]
        # Получаем текущую дату и время для имени архива
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Сохраняем архив в той же папке logs
        return os.path.join(LOG_DIR, f"{base}_{timestamp}.log.gz")

    @staticmethod
    def rotator(source, dest):
        # Создаем папку logs если её нет (дополнительная проверка)
        os.makedirs(os.path.dirname(dest) or LOG_DIR, exist_ok=True)
        
        # Читаем исходный файл
        with open(source, 'rb') as f_in:
            # Сжимаем и записываем в архив
            with gzip.open(dest, 'wb') as f_out:
                f_out.writelines(f_in)
        # Удаляем исходный файл
        os.remove(source)

def setup_logging(test_mode=False):
    # Создаем папку для логов если её нет
    os.makedirs(LOG_DIR, exist_ok=True)
    
    # В тестовом режиме создаем новый файл при каждом запуске
    if test_mode and os.path.exists(LOG_FILENAME):
        test_filename = f"bot_test_{datetime.now().strftime('%H%M%S')}.log"
        os.rename(LOG_FILENAME, os.path.join(LOG_DIR, test_filename))
    
    handler = logging.handlers.RotatingFileHandler(
        LOG_FILENAME,
        maxBytes=MAX_LOG_SIZE,
        backupCount=3,  # Уменьшаем для теста
        encoding='utf-8'
    )
    
    handler.rotator = GZipRotator.rotator
    handler.namer = GZipRotator.namer
    
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger