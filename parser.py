import requests
from bs4 import BeautifulSoup
import json
import hashlib

def get_schedule_hash(schedule):
    return hashlib.md5(json.dumps(schedule, sort_keys=True).encode('utf-8')).hexdigest()

# Модифицировать функцию save_schedule_to_json
def save_schedule_to_json(update_time, schedule):
    data = {
        'update_time': update_time,
        'schedule': schedule,
        'hash': get_schedule_hash(schedule)
    }
    with open('schedule.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        
# Функция для загрузки конфигурации из JSON файла
def load_config():
    with open('config.json', 'r', encoding='utf-8') as f:
        return json.load(f)

def get_schedule():
    # Загружаем конфигурацию
    config = load_config()
    url = config['site_url']  # Получаем ссылку на сайт из конфигурации
    response = requests.get(url)
    
    # Проверка успешности запроса
    if response.status_code != 200:
        print("Ошибка при получении страницы:", response.status_code)
        return None, None

    # Установка правильной кодировки
    response.encoding = 'windows-1251'  # Устанавливаем кодировку, используемую на сайте
    soup = BeautifulSoup(response.text, 'html.parser')

    # Извлечение времени обновления
    update_time = soup.find('div', class_='ref').text.strip().replace('Обновлено: ', '')

    # Извлечение расписания
    schedule = {}
    rows = soup.find_all('tr')  # Находим все строки таблицы
    
    current_day = None
    for row in rows:
        # Проверяем, является ли строка заголовком дня
        day_header = row.find('td', class_='hd')
        if day_header and day_header.get('rowspan'):
            current_day = day_header.text.strip()
            schedule[current_day] = []
        
        # Проверяем, есть ли пары в строке
        cells = row.find_all('td')
        if len(cells) > 2 and current_day:
            pair_number = cells[1].text.strip()
            subject_info = cells[2]  # Извлекаем информацию о предмете и подгруппах
            
            # Проверяем, есть ли подгруппы
            subgroup_cells = subject_info.find_all('a', class_='z1')  # Предметы
            audience_cells = subject_info.find_all('a', class_='z2')  # Аудитории
            teacher_cells = subject_info.find_all('a', class_='z3')  # Преподаватели
            
            if len(subgroup_cells) > 1:  # Если есть две подгруппы
                for i in range(2):  # Обрабатываем обе подгруппы
                    subject = subgroup_cells[i].text.strip() if i < len(subgroup_cells) else "Нет пары"
                    audience = audience_cells[i].text.strip() if i < len(audience_cells) else "Нет аудитории"
                    teacher = teacher_cells[i].text.strip() if i < len(teacher_cells) else "Нет"
                    
                    schedule[current_day].append({
                        "N_par": pair_number,
                        "Name": subject,
                        "Auditory": audience,
                        "Teacher": teacher,
                        "Subgroup": "Да" if i == 1 else "Нет",
                        "SN_par": pair_number,
                        "SName": subject,
                        "SAuditory": audience,
                        "STeacher": teacher
                    })
            else:  # Если подгрупп нет
                subject = subgroup_cells[0].text.strip() if subgroup_cells else "Нет пары"
                audience = audience_cells[0].text.strip() if audience_cells else "Нет аудитории"
                teacher = teacher_cells[0].text.strip() if teacher_cells else "Нет"
                
                schedule[current_day].append({
                    "N_par": pair_number,
                    "Name": subject,
                    "Auditory": audience,
                    "Teacher": teacher,
                    "Subgroup": "Нет"
                })
        elif current_day:
            # Если пара отсутствует, добавляем запись о свободной паре
            schedule[current_day].append({
                "N_par": "Нет пары",
                "Name": "Свободная пара",
                "Auditory": "Нет аудитории",
                "Teacher": "Нет",
                "Subgroup": "Нет"
            })

    return update_time, schedule

def save_schedule_to_json(update_time, schedule):
    data = {
        'update_time': update_time,
        'schedule': schedule
    }
    with open('schedule.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

if __name__ == "__main__":
    update_time, schedule = get_schedule()
    if schedule is not None:
        save_schedule_to_json(update_time, schedule)
        print("Расписание успешно сохранено в schedule.json")
    else:
        print("Не удалось получить расписание.")
