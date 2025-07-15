#!/usr/bin/env python3
import json
import os
import hashlib
import requests
from datetime import datetime
from bs4 import BeautifulSoup
import logging

# Константы путей
SCHEDULES_DIR = 'schedules'
LAST_SCHEDULE_FILE = os.path.join(SCHEDULES_DIR, 'last_schedule.json')

# Настройка логирования
from log_handler import setup_logging
logger = setup_logging()

def ensure_schedules_dir():
    """Создает папку для расписаний, если её нет"""
    os.makedirs(SCHEDULES_DIR, exist_ok=True)

def parse_schedule(html_content):
    """
    Парсит HTML-контент расписания и возвращает структурированные данные.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    schedule_table = soup.find('table', class_='inf')

    if not schedule_table:
        return {"error": "Таблица расписания не найдена."}

    parsed_schedule = {}
    current_date = None

    # Пропускаем первые две строки заголовков таблицы
    rows = schedule_table.find_all('tr')[2:]

    for row in rows:
        cols = row.find_all(['td'])
        
        # Проверяем, является ли строка разделителем между днями
        if len(cols) == 1 and 'hd0' in cols[0].get('class', []):
            continue  # Пропускаем пустые строки-разделители

        lesson_number_td = None
        lesson_details_td = None
        
        # Если это строка с датой (первая колонка имеет rowspan)
        if 'rowspan' in cols[0].attrs and 'hd' in cols[0].get('class', []):
            date_day_text = cols[0].get_text(separator=' ', strip=True)
            parts = date_day_text.split()
            if len(parts) >= 2:
                current_date = parts[0]
                day_of_week = parts[1]
                parsed_schedule[current_date] = {
                    "day_of_week": day_of_week,
                    "lessons": []
                }
            
            if len(cols) > 1:
                lesson_number_td = cols[1]
                lesson_details_td = cols[2] if len(cols) > 2 else None
        # Если это последующая строка с парой для текущего дня
        elif current_date and len(cols) >= 2:
            lesson_number_td = cols[0]
            lesson_details_td = cols[1]
        
        if lesson_number_td and lesson_details_td:
            lesson_number = lesson_number_td.get_text(strip=True)
            
            if 'nul' in lesson_details_td.get('class', []):
                # Если пара свободна
                parsed_schedule[current_date]["lessons"].append({
                    "lesson_number": lesson_number,
                    "name": "Свободно",
                    "auditorium": "",
                    "teacher": "",
                    "subgroup": None
                })
            else:
                # Собираем все ссылки внутри ячейки деталей пары
                all_links_in_cell = lesson_details_td.find_all('a')
                
                current_lesson_info = {}
                subgroup_count = 0
                
                for link in all_links_in_cell:
                    if 'z1' in link.get('class', []):
                        # Нашли начало нового занятия (название предмета)
                        if current_lesson_info:  # Если уже есть незавершенное занятие, сохраняем его
                            subgroup_count += 1
                            current_lesson_info["subgroup"] = f"Подгруппа {subgroup_count}" if subgroup_count > 1 else None
                            parsed_schedule[current_date]["lessons"].append(current_lesson_info)
                            current_lesson_info = {}  # Сбрасываем для нового занятия
                        
                        current_lesson_info["name"] = link.get_text(strip=True)
                        current_lesson_info["lesson_number"] = lesson_number
                        
                    elif 'z2' in link.get('class', []):
                        # Нашли аудиторию
                        current_lesson_info["auditorium"] = link.get_text(strip=True)
                        
                    elif 'z3' in link.get('class', []):
                        # Нашли преподавателя
                        current_lesson_info["teacher"] = link.get_text(strip=True)
                
                # После цикла, если есть незавершенное занятие, добавляем его
                if current_lesson_info:
                    subgroup_count += 1
                    current_lesson_info["subgroup"] = f"Подгруппа {subgroup_count}" if subgroup_count > 1 else None
                    parsed_schedule[current_date]["lessons"].append(current_lesson_info)
                
                # Если после всех ссылок не было найдено ни одного занятия
                if not any(l.get("lesson_number") == lesson_number for l in parsed_schedule[current_date]["lessons"]):
                    parsed_schedule[current_date]["lessons"].append({
                        "lesson_number": lesson_number,
                        "name": "Неизвестно",
                        "auditorium": "",
                        "teacher": "",
                        "subgroup": None
                    })
    
    return parsed_schedule

def get_schedule():
    """
    Основная функция для получения расписания с сайта.
    Возвращает кортеж (update_time, schedule_data).
    """
    try:
        # Загрузка конфигурации
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        site_url = config.get("site_url")
        if not site_url:
            raise Exception("В config.json не найден ключ 'site_url'")

        # Загрузка HTML
        response = requests.get(site_url, timeout=10)
        response.raise_for_status()
        response.encoding = 'windows-1251'
        html_content = response.text

        # Парсинг
        schedule_data = parse_schedule(html_content)
        if "error" in schedule_data:
            raise Exception(schedule_data["error"])

        return datetime.now().strftime("%d.%m.%Y %H:%M"), schedule_data

    except Exception as e:
        logger.error(f"Ошибка в get_schedule: {e}")
        return None, None

def save_to_json(data, filename):
    """Сохраняет данные в JSON-файл."""
    ensure_schedules_dir()  # Убедимся, что папка существует
    filepath = os.path.join(SCHEDULES_DIR, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    logger.info(f"Расписание сохранено в {filepath}")

def get_schedule_hash(schedule_data):
    """Генерирует хеш расписания для сравнения."""
    schedule_str = json.dumps(schedule_data, sort_keys=True)
    return hashlib.md5(schedule_str.encode()).hexdigest()

def load_last_schedule():
    """Загружает последнее сохраненное расписание."""
    if os.path.exists(LAST_SCHEDULE_FILE):
        with open(LAST_SCHEDULE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

if __name__ == "__main__":
    # Запуск парсера в standalone режиме
    ensure_schedules_dir()  # Создаем папку если её нет
    update_time, schedule_data = get_schedule()
    
    if schedule_data:
        # Сохраняем в файл с временной меткой
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"schedule_{timestamp}.json"
        save_to_json(schedule_data, filename)
        
        # Сохраняем как последнее расписание
        save_to_json(schedule_data, "last_schedule.json")
        print(f"Расписание успешно сохранено (обновлено: {update_time})")
    else:
        print("Не удалось получить расписание")