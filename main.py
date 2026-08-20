import os
import sys
import json
import re
import asyncio
import sqlite3
from datetime import datetime
import logging

import aiohttp

import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.utils import get_random_id

# -------------------------------------------------------------------
# 1. КОНФИГУРАЦИЯ И НАСТРОЙКИ
# -------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)

VK_TOKEN = os.getenv("VK_TOKEN") or os.getenv("BOT_TOKEN")
GROUP_ID = os.getenv("GROUP_ID")

if not VK_TOKEN or not GROUP_ID:
    print("❌ Ошибка: Не заданы переменные окружения VK_TOKEN или GROUP_ID!")
    sys.exit(1)

ALLOWED_USERS = {854927157, 1087120613}

# Настройка БД в директории app/data
DATA_DIR = os.path.join("app", "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "bot_database.db")

SMELL_MAP = {
    "#dfdc8f": "Племя Вихря 🌾",
    "#ff861c": "Племя Солнца ☀️",
    "#00b4d8": "Племя Потока 🌊",
    "#71c68b": "Племя Мрака 🌲",
    "#576198": "Клан Горных Вершин 🏔️",
    "#befffb": "Звёздные Угодья ✨",
    "#f777a6": "Домашки 🏠",
    "#e3d1c8": "Одиночки 🐾",
    "#911922": "Племя Сумеречного Леса 🌑",
}

# -------------------------------------------------------------------
# 2. ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ
# -------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS characters (
        char_id INTEGER PRIMARY KEY,
        name TEXT,
        character_text TEXT,
        biography_text TEXT,
        last_parsed_data TEXT
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS stats_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        char_id INTEGER,
        timestamp TEXT,
        data TEXT,
        FOREIGN KEY(char_id) REFERENCES characters(char_id)
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS photos (
        photo_id INTEGER PRIMARY KEY AUTOINCREMENT,
        char_id INTEGER,
        vk_photo_attachment TEXT,
        FOREIGN KEY(char_id) REFERENCES characters(char_id)
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_states (
        user_id INTEGER PRIMARY KEY,
        state TEXT,
        char_id INTEGER
    )
    """)
    
    conn.commit()
    conn.close()

init_db()

# -------------------------------------------------------------------
# 3. ПАРСИНГ СТРАНИЦ (БЕЗ BS4)
# -------------------------------------------------------------------
def parse_num_val(val: str) -> float:
    if not val:
        return 0.0
    val_clean = str(val).replace(",", ".").replace(" ", "").strip()
    match = re.search(r"\d+(?:\.\d+)?", val_clean)
    return float(match.group(0)) if match else 0.0

def parse_game_hours(text: str) -> float:
    if not text:
        return 0.0
    days_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*(?:дн|день|дня|дней)", text, re.I)
    hours_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*(?:ч|час|часа|часов)", text, re.I)
    mins_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*(?:мин|минута|минуты|минут)", text, re.I)

    total_hours = 0.0
    if days_match: total_hours += parse_num_val(days_match.group(1)) * 24
    if hours_match: total_hours += parse_num_val(hours_match.group(1))
    if mins_match: total_hours += parse_num_val(mins_match.group(1)) / 60.0
    return round(total_hours, 2) if (days_match or hours_match or mins_match) else round(parse_num_val(text), 2)

async def fetch_character_data(char_id: int) -> dict | None:
    url = f"https://stats.worldcats.ru/p/{char_id}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()
        except Exception:
            return None

    # Очистка HTML от тегов
    text_content = re.sub(r'<[^>]+>', ' ', html)

    name = f"Персонаж {char_id}"
    account = "Неизвестно"
    
    smell = "Неизвестно"
    hex_matches = re.findall(r'#[0-9a-fA-F]{6}\b', html)
    for hex_code in hex_matches:
        if hex_code.lower() in SMELL_MAP:
            smell = SMELL_MAP[hex_code.lower()]
            break

    def extract_val(label_patterns):
        for pattern in label_patterns:
            match = re.search(rf"{pattern}[^<]*?:\s*([\d\.,\s]+)", text_content, re.I)
            if match:
                return parse_num_val(match.group(1))
        return 0.0

    name_match = re.search(r"Имя:\s*([^\n<]+)", text_content)
    if name_match: name = name_match.group(1).strip()
    
    role = "Неизвестно"
    role_match = re.search(r"Должность:\s*([^\n<]+)", text_content)
    if role_match: role = role_match.group(1).strip()

    acc_match = re.search(r"Аккаунт:\s*([^\n<]+)", text_content)
    if acc_match: account = acc_match.group(1).strip()

    hours_match = re.search(r"(?:проведено в игре|время в игре)[^<]*?:\s*([^\n<]+)", text_content, re.I)
    game_hours = parse_game_hours(hours_match.group(1)) if hours_match else 0.0

    return {
        "id": char_id,
        "name": name,
        "smell": smell,
        "role": role,
        "account": account,
        "total_rating": int(extract_val(["общий рейтинг"])),
        "age": int(extract_val(["возраст"])),
        "achievements": int(extract_val(["личные достижения"])),
        "combat_skills": extract_val(["боевые умения", "боевое умение"]),
        "item_search": extract_val(["поиск предметов"]),
        "enemy_search": extract_val(["поиск врагов"]),
        "digging_skill": extract_val(["умение копать"]),
        "swimming_skill": extract_val(["умение плавать"]),
        "tribe_loyalty": extract_val(["верность племени"]),
        "roleplay_skill": extract_val(["ролевая игра"]),
        "agility": extract_val(["ловкость"]),
        "endurance": extract_val(["выносливость"]),
        "players_killed": int(extract_val(["убито игроков"])),
        "creatures_killed": int(extract_val(["убито существ"])),
        "interactions_count": int(extract_val(["число взаимодействий"])),
        "locations_crossed": int(extract_val(["преодолено локаций"])),
        "items_picked": int(extract_val(["подобрано предметов"])),
        "game_hours": game_hours,
        "items_dropped": int(extract_val(["выброшено предметов"]))
    }

# -------------------------------------------------------------------
# 4. ВК БОТ И КЛАВИАТУРЫ
# -------------------------------------------------------------------
vk_session = vk_api.VkApi(token=VK_TOKEN)
vk = vk_session.get_api()

def create_keyboard(buttons_matrix):
    return json.dumps({
        "one_time": False,
        "buttons": buttons_matrix
    }, ensure_ascii=False)

def get_character_menu_text(data: dict) -> str:
    return (
        f"🐾 {data['name']} [{data['id']}]\n\n"
        f"📋 Основная информация:\n"
        f"• Общий рейтинг: {data['total_rating']}\n"
        f"• Запах племени: {data['smell']}\n"
        f"• Должность: {data['role']}\n"
        f"• Возраст: {data['age']} лун(ы)\n"
        f"• Личные достижения: {data['achievements']}\n"
        f"• Аккаунт: {data['account']}\n\n"
        f"⚔️ Навыки:\n"
        f"• Боевые умения: {data['combat_skills']}\n"
        f"• Поиск предметов: {data['item_search']}\n"
        f"• Поиск врагов: {data['enemy_search']}\n"
        f"• Умение копать: {data['digging_skill']}\n"
        f"• Умение плавать: {data['swimming_skill']}\n"
        f"• Верность племени: {data['tribe_loyalty']}\n"
        f"• Ролевая игра: {data['roleplay_skill']}\n"
        f"• Ловкость: {data['agility']}\n"
        f"• Выносливость: {data['endurance']}\n\n"
        f"📊 Статистика:\n"
        f"• Убито игроков: {data['players_killed']}\n"
        f"• Убито существ: {data['creatures_killed']}\n"
        f"• Число взаимодействий: {data['interactions_count']}\n"
        f"• Преодолено локаций: {data['locations_crossed']}\n"
        f"• Подобрано предметов: {data['items_picked']}\n"
        f"• Проведено в игре: {data['game_hours']} ч\n"
        f"• Выброшено предметов: {data['items_dropped']}"
    )

def send_char_card(peer_id, char_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT last_parsed_data FROM characters WHERE char_id = ?", (char_id,))
    row = cursor.fetchone()
    conn.close()

    if not row or not row[0]:
        vk.messages.send(peer_id=peer_id, message="⚠️ Данные персонажа ещё не загружены.", random_id=get_random_id())
        return

    data = json.loads(row[0])
    text = get_character_menu_text(data)

    kb = create_keyboard([
        [{"action": {"type": "text", "payload": json.dumps({"cmd": "arts", "id": char_id}), "label": "Арты"}, "color": "secondary"},
         {"action": {"type": "text", "payload": json.dumps({"cmd": "char_text", "id": char_id}), "label": "Характер"}, "color": "secondary"}],
        [{"action": {"type": "text", "payload": json.dumps({"cmd": "bio", "id": char_id}), "label": "Биография"}, "color": "secondary"},
         {"action": {"type": "text", "payload": json.dumps({"cmd": "stats_days", "id": char_id, "page": 1}), "label": "Статистика по дням"}, "color": "secondary"}],
        [{"action": {"type": "text", "payload": json.dumps({"cmd": "refresh", "id": char_id}), "label": "Обновить"}, "color": "primary"}],
        [{"action": {"type": "text", "payload": json.dumps({"cmd": "all_chars", "page": 1}), "label": "◀️ К списку"}, "color": "negative"}]
    ])

    vk.messages.send(peer_id=peer_id, message=text, keyboard=kb, random_id=get_random_id())

# -------------------------------------------------------------------
# 5. ФОНОВЫЙ ПАРСИНГ КАЖДЫЕ 5 МИНУТ
# -------------------------------------------------------------------
async def update_character_in_db(char_id: int):
    data = await fetch_character_data(char_id)
    if not data:
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("UPDATE characters SET name = ?, last_parsed_data = ? WHERE char_id = ?",
                   (data["name"], json.dumps(data, ensure_ascii=False), char_id))
    
    cursor.execute("INSERT INTO stats_history (char_id, timestamp, data) VALUES (?, ?, ?)",
                   (char_id, now_str, json.dumps(data, ensure_ascii=False)))
    
    conn.commit()
    conn.close()

async def background_scheduler():
    while True:
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT char_id FROM characters")
            char_ids = [row[0] for row in cursor.fetchall()]
            conn.close()

            for cid in char_ids:
                await update_character_in_db(cid)
                await asyncio.sleep(1)

        except Exception as e:
            logging.error(f"Ошибка в фоновом планировщике: {e}")

        await asyncio.sleep(300)

# -------------------------------------------------------------------
# 6. ОБРАБОТКА СООБЩЕНИЙ ЧАТА
# -------------------------------------------------------------------
def render_stats_diff(old_d: dict, new_d: dict) -> str:
    diffs = []
    
    main_diffs = []
    if old_d.get("age") != new_d.get("age"):
        main_diffs.append(f"Возраст: {old_d.get('age')} > {new_d.get('age')}")
    if old_d.get("role") != new_d.get("role"):
        main_diffs.append(f"Должность: {old_d.get('role')} -> {new_d.get('role')}")

    skills_map = {
        "combat_skills": "боевые умения",
        "item_search": "поиск предметов",
        "enemy_search": "поиск врагов",
        "digging_skill": "умение копать",
        "swimming_skill": "умение плавать",
        "tribe_loyalty": "верность племени",
        "roleplay_skill": "ролевая игра",
        "agility": "ловкость",
        "endurance": "выносливость"
    }

    skill_diffs = []
    for k, label in skills_map.items():
        v1, v2 = old_d.get(k, 0.0), new_d.get(k, 0.0)
        if v2 > v1:
            diff = round(v2 - v1, 2)
            skill_diffs.append(f"{label}: {v1} -> {v2} (+{diff})")

    if main_diffs:
        diffs.append("Основная информация:\n" + "\n".join(main_diffs))
    if skill_diffs:
        diffs.append("Навыки:\n" + "\n".join(skill_diffs))

    return "\n\n".join(diffs)

def handle_message(peer_id, user_id, text, payload, attachments):
    if user_id not in ALLOWED_USERS:
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT state, char_id FROM user_states WHERE user_id = ?", (user_id,))
    user_state = cursor.fetchone()

    if user_state:
        state, char_id = user_state
        if text.strip().lower() == "отменить":
            cursor.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
            vk.messages.send(peer_id=peer_id, message="❌ Действие отменено.", random_id=get_random_id())
            send_char_card(peer_id, char_id)
            return

        if state == "ADD_PHOTOS":
            photo_atts = []
            for att in attachments:
                if att.get("type") == "photo":
                    photo = att["photo"]
                    owner_id = photo["owner_id"]
                    pid = photo["id"]
                    access_key = photo.get("access_key", "")
                    att_str = f"photo{owner_id}_{pid}"
                    if access_key: att_str += f"_{access_key}"
                    photo_atts.append(att_str)

            if photo_atts:
                for p_att in photo_atts[:10]:
                    cursor.execute("INSERT INTO photos (char_id, vk_photo_attachment) VALUES (?, ?)", (char_id, p_att))
                cursor.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
                conn.commit()
                conn.close()
                vk.messages.send(peer_id=peer_id, message=f"✅ Успешно добавлено фото: {len(photo_atts)} шт.", random_id=get_random_id())
                send_char_card(peer_id, char_id)
                return

        elif state == "EDIT_CHAR":
            cursor.execute("UPDATE characters SET character_text = ? WHERE char_id = ?", (text, char_id))
            cursor.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
            vk.messages.send(peer_id=peer_id, message="✅ Характер обновлён!", random_id=get_random_id())
            send_char_card(peer_id, char_id)
            return

        elif state == "EDIT_BIO":
            cursor.execute("UPDATE characters SET biography_text = ? WHERE char_id = ?", (text, char_id))
            cursor.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
            vk.messages.send(peer_id=peer_id, message="✅ Биография обновлена!", random_id=get_random_id())
            send_char_card(peer_id, char_id)
            return

    if text.startswith("/add "):
        try:
            cid = int(text.split()[1])
            cursor.execute("INSERT OR IGNORE INTO characters (char_id) VALUES (?)", (cid,))
            conn.commit()
            conn.close()
            asyncio.run_coroutine_threadsafe(update_character_in_db(cid), asyncio.get_event_loop())
            vk.messages.send(peer_id=peer_id, message=f"✅ Персонаж ID {cid} добавлен!", random_id=get_random_id())
            return
        except Exception:
            vk.messages.send(peer_id=peer_id, message="❌ Ошибка. Формат: /add {id}", random_id=get_random_id())
            conn.close()
            return

    if text.startswith("/dell photo "):
        try:
            pid = int(text.split()[2])
            cursor.execute("DELETE FROM photos WHERE photo_id = ?", (pid,))
            conn.commit()
            conn.close()
            vk.messages.send(peer_id=peer_id, message=f"🗑 Фото ID {pid} удалено!", random_id=get_random_id())
            return
        except Exception:
            vk.messages.send(peer_id=peer_id, message="❌ Ошибка. Формат: /dell photo {id}", random_id=get_random_id())
            conn.close()
            return

    cmd = payload.get("cmd") if payload else None
    
    if text.lower() in ["/all", "все персонажи"] or cmd == "all_chars":
        page = payload.get("page", 1) if payload else 1
        cursor.execute("SELECT char_id, name FROM characters ORDER BY char_id DESC")
        chars = cursor.fetchall()
        conn.close()

        if not chars:
            vk.messages.send(peer_id=peer_id, message="Список персонажей пуст. Добавьте через `/add {id}`", random_id=get_random_id())
            return

        per_page = 5
        total_pages = (len(chars) + per_page - 1) // per_page
        start_idx = (page - 1) * per_page
        page_chars = chars[start_idx:start_idx + per_page]

        kb_matrix = []
        for cid, cname in page_chars:
            label = f"{cname or f'ID {cid}'}"[:40]
            kb_matrix.append([{"action": {"type": "text", "payload": json.dumps({"cmd": "open_char", "id": cid}), "label": label}, "color": "primary"}])

        nav_row = []
        if page > 1:
            nav_row.append({"action": {"type": "text", "payload": json.dumps({"cmd": "all_chars", "page": page - 1}), "label": "◀️ Стр 1"}, "color": "secondary"})
        if page < total_pages:
            nav_row.append({"action": {"type": "text", "payload": json.dumps({"cmd": "all_chars", "page": page + 1}), "label": f"Стр {page + 1} ▶️"}, "color": "secondary"})
        
        if nav_row:
            kb_matrix.append(nav_row)

        vk.messages.send(peer_id=peer_id, message=f"📜 Список персонажей (Стр. {page}/{total_pages}):", keyboard=create_keyboard(kb_matrix), random_id=get_random_id())
        return

    if cmd == "open_char":
        conn.close()
        send_char_card(peer_id, payload["id"])
        return

    if cmd == "refresh":
        conn.close()
        cid = payload["id"]
        vk.messages.send(peer_id=peer_id, message="⏳ Парсим данные с сайта...", random_id=get_random_id())
        
        loop = asyncio.get_event_loop()
        future = asyncio.run_coroutine_threadsafe(fetch_character_data(cid), loop)
        data = future.result()

        if data:
            c = sqlite3.connect(DB_PATH)
            cur = c.cursor()
            cur.execute("UPDATE characters SET name = ?, last_parsed_data = ? WHERE char_id = ?",
                        (data["name"], json.dumps(data, ensure_ascii=False), cid))
            c.commit()
            c.close()
            vk.messages.send(peer_id=peer_id, message="✅ Данные обновлены!", random_id=get_random_id())
            send_char_card(peer_id, cid)
        else:
            vk.messages.send(peer_id=peer_id, message="❌ Ошибка загрузки данных с сайта.", random_id=get_random_id())
        return

    if cmd == "arts":
        cid = payload["id"]
        cursor.execute("SELECT photo_id, vk_photo_attachment FROM photos WHERE char_id = ?", (cid,))
        photos = cursor.fetchall()

        if not photos:
            cursor.execute("REPLACE INTO user_states (user_id, state, char_id) VALUES (?, ?, ?)", (user_id, "ADD_PHOTOS", cid))
            conn.commit()
            conn.close()

            kb = create_keyboard([[{"action": {"type": "text", "label": "Отменить"}, "color": "negative"}]])
            vk.messages.send(peer_id=peer_id, message="🖼 Артов нет. Отправьте до 10 фото в чат или нажмите 'Отменить':", keyboard=kb, random_id=get_random_id())
            return

        conn.close()
        for i in range(0, len(photos), 10):
            chunk = photos[i:i+10]
            atts = [p[1] for p in chunk]
            ids_str = ", ".join([f"ID:{p[0]}" for p in chunk])
            vk.messages.send(peer_id=peer_id, message=f"🖼 Арты ({ids_str}):", attachment=",".join(atts), random_id=get_random_id())

        c = sqlite3.connect(DB_PATH)
        cur = c.cursor()
        cur.execute("REPLACE INTO user_states (user_id, state, char_id) VALUES (?, ?, ?)", (user_id, "ADD_PHOTOS", cid))
        c.commit()
        c.close()

        kb = create_keyboard([[{"action": {"type": "text", "label": "Отменить"}, "color": "negative"}]])
        vk.messages.send(peer_id=peer_id, message="➕ Отправьте фото (до 10 шт.) для добавления или нажмите 'Отменить':", keyboard=kb, random_id=get_random_id())
        return

    if cmd in ["char_text", "bio"]:
        cid = payload["id"]
        col = "character_text" if cmd == "char_text" else "biography_text"
        label_name = "Характер" if cmd == "char_text" else "Биография"
        state_name = "EDIT_CHAR" if cmd == "char_text" else "EDIT_BIO"

        cursor.execute(f"SELECT {col} FROM characters WHERE char_id = ?", (cid,))
        row = cursor.fetchone()
        curr_val = row[0] if row else None

        cursor.execute("REPLACE INTO user_states (user_id, state, char_id) VALUES (?, ?, ?)", (user_id, state_name, cid))
        conn.commit()
        conn.close()

        kb = create_keyboard([
            [{"action": {"type": "text", "label": "Отменить"}, "color": "negative"}]
        ])

        msg = f"📜 **{label_name}**:\n\n{curr_val or 'Не заполнено'}\n\nЧтобы изменить, отправьте новый текст в чат:"
        vk.messages.send(peer_id=peer_id, message=msg, keyboard=kb, random_id=get_random_id())
        return

    if cmd == "stats_days":
        cid = payload["id"]
        page = payload.get("page", 1)

        cursor.execute("SELECT timestamp, data FROM stats_history WHERE char_id = ? ORDER BY id DESC", (cid,))
        history = cursor.fetchall()
        conn.close()

        if len(history) < 2:
            vk.messages.send(peer_id=peer_id, message="📊 Недостаточно данных для формирования изменений.", random_id=get_random_id())
            return

        diffs_list = []
        for i in range(len(history) - 1):
            t_new, d_new_str = history[i]
            t_old, d_old_str = history[i+1]
            diff_text = render_stats_diff(json.loads(d_old_str), json.loads(d_new_str))
            if diff_text:
                diffs_list.append(f"📅 [{t_new}]\n{diff_text}")

        if not diffs_list:
            vk.messages.send(peer_id=peer_id, message="📊 Прокачек/изменений за последнее время не зафиксировано.", random_id=get_random_id())
            return

        per_page = 5
        total_pages = (len(diffs_list) + per_page - 1) // per_page
        start_idx = (page - 1) * per_page
        page_diffs = diffs_list[start_idx:start_idx + per_page]

        response_text = f"📊 Статистика прокачек (Стр. {page}/{total_pages}):\n\n" + "\n\n───────────────\n\n".join(page_diffs)

        nav_row = []
        if page > 1:
            nav_row.append({"action": {"type": "text", "payload": json.dumps({"cmd": "stats_days", "id": cid, "page": page - 1}), "label": "стр1"}, "color": "secondary"})
        if page < total_pages:
            nav_row.append({"action": {"type": "text", "payload": json.dumps({"cmd": "stats_days", "id": cid, "page": page + 1}), "label": f"стр{page + 1}"}, "color": "secondary"})

        kb = create_keyboard([nav_row, [{"action": {"type": "text", "payload": json.dumps({"cmd": "open_char", "id": cid}), "label": "◀️ Назад к карточке"}, "color": "negative"}]]) if nav_row else create_keyboard([[{"action": {"type": "text", "payload": json.dumps({"cmd": "open_char", "id": cid}), "label": "◀️ Назад к карточке"}, "color": "negative"}]])

        vk.messages.send(peer_id=peer_id, message=response_text, keyboard=kb, random_id=get_random_id())
        return

    conn.close()

# -------------------------------------------------------------------
# 7. ЗАПУСК БОТА
# -------------------------------------------------------------------
def start_vk_bot():
    longpoll = VkBotLongPoll(vk_session, GROUP_ID)
    print("🚀 ВК-бот запущен и ожидает сообщений из беседы...")

    for event in longpoll.listen():
        if event.type == VkBotEventType.MESSAGE_NEW:
            msg = event.object.message
            peer_id = msg.get("peer_id")
            user_id = msg.get("from_id")
            
            # Реакция только на беседы
            if peer_id <= 2000000000:
                continue

            text = msg.get("text", "")
            attachments = msg.get("attachments", [])
            
            payload = {}
            if msg.get("payload"):
                try:
                    payload = json.loads(msg["payload"])
                except Exception:
                    pass

            handle_message(peer_id, user_id, text, payload, attachments)

async def main():
    asyncio.create_task(background_scheduler())
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, start_vk_bot)

if __name__ == "__main__":
    asyncio.run(main())