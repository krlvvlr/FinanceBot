import asyncio
import logging
import sqlite3
import aiohttp
from datetime import datetime, date, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

import os

# Теперь бот будет брать токен прямо из настроек Heroku автоматически
TOKEN = os.getenv("TELEGRAM_TOKEN")

DB_NAME = "finance.db"

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN)
dp = Dispatcher()

user_modes = {}


def now():
    return datetime.now()


def db():
    return sqlite3.connect(DB_NAME)


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        main_currency TEXT DEFAULT NULL,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        type TEXT,
        amount REAL,
        currency TEXT DEFAULT 'UAH',
        amount_uah REAL,
        category TEXT,
        wallet TEXT DEFAULT 'card',
        comment TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS savings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount REAL,
        currency TEXT,
        amount_uah REAL,
        rate REAL,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title TEXT,
        amount REAL,
        remind_date TEXT,
        repeat_type TEXT DEFAULT 'once',
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS weekly_limits (
        user_id INTEGER PRIMARY KEY,
        amount REAL,
        currency TEXT,
        amount_uah REAL,
        rate REAL,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    # Миграции
    cur.execute("PRAGMA table_info(users)")
    user_cols = [c[1] for c in cur.fetchall()]
    if "main_currency" not in user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN main_currency TEXT DEFAULT NULL")

    cur.execute("PRAGMA table_info(transactions)")
    tx_cols = [c[1] for c in cur.fetchall()]

    tx_migrations = {
        "wallet": "ALTER TABLE transactions ADD COLUMN wallet TEXT DEFAULT 'card'",
        "currency": "ALTER TABLE transactions ADD COLUMN currency TEXT DEFAULT 'UAH'",
        "amount_uah": "ALTER TABLE transactions ADD COLUMN amount_uah REAL",
        "comment": "ALTER TABLE transactions ADD COLUMN comment TEXT",
    }

    for col, sql in tx_migrations.items():
        if col not in tx_cols:
            cur.execute(sql)

    cur.execute("""
    UPDATE transactions
    SET amount_uah = amount
    WHERE amount_uah IS NULL
    """)

    cur.execute("PRAGMA table_info(reminders)")
    rem_cols = [c[1] for c in cur.fetchall()]

    if "remind_date" not in rem_cols:
        cur.execute("ALTER TABLE reminders ADD COLUMN remind_date TEXT")

    if "repeat_type" not in rem_cols:
        cur.execute("ALTER TABLE reminders ADD COLUMN repeat_type TEXT DEFAULT 'monthly'")

    conn.commit()
    conn.close()


def save_setting(key, value):
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def get_setting(key):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


async def get_usd_rate():
    url = "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange?valcode=USD&json"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                data = await response.json()
                rate = float(data[0]["rate"])
                save_setting("usd_rate", str(rate))
                return rate
    except Exception:
        saved = get_setting("usd_rate")
        return float(saved) if saved else 40.0


def add_user(user_id, username):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    INSERT OR IGNORE INTO users (user_id, username, created_at)
    VALUES (?, ?, ?)
    """, (user_id, username, now().isoformat()))

    conn.commit()
    conn.close()


def get_main_currency(user_id):
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT main_currency FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()

    conn.close()
    return row[0] if row else None


def set_main_currency(user_id, currency):
    conn = db()
    cur = conn.cursor()

    cur.execute("UPDATE users SET main_currency = ? WHERE user_id = ?", (currency, user_id))

    conn.commit()
    conn.close()


def currency_symbol(currency):
    return "$" if currency == "USD" else "грн"


def normalize_currency(value):
    value = value.lower()

    if value in ["usd", "$", "доллар", "доллары", "бакс", "баксы"]:
        return "USD"

    if value in ["uah", "грн", "гривна", "гривны"]:
        return "UAH"

    return "UAH"


def is_currency_word(value):
    return value.lower() in [
        "usd", "$", "доллар", "доллары", "бакс", "баксы",
        "uah", "грн", "гривна", "гривны"
    ]


def normalize_wallet(value):
    value = value.lower()

    if value in ["карта", "card", "картка"]:
        return "card"

    if value in ["наличка", "нал", "cash", "кэш"]:
        return "cash"

    return None


def wallet_name(wallet):
    if wallet == "card":
        return "💳 Карта"
    if wallet == "cash":
        return "💵 Наличка"
    return wallet


async def to_uah(amount, currency):
    rate = await get_usd_rate()
    amount_uah = amount * rate if currency == "USD" else amount
    return amount_uah, rate


async def from_uah(amount_uah, user_id):
    main_currency = get_main_currency(user_id) or "UAH"

    if main_currency == "USD":
        rate = await get_usd_rate()
        return amount_uah / rate, "USD", rate

    return amount_uah, "UAH", None


async def add_transaction(user_id, tx_type, amount, currency, category, wallet, comment=""):
    amount_uah, rate = await to_uah(amount, currency)

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO transactions 
    (user_id, type, amount, currency, amount_uah, category, wallet, comment, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        tx_type,
        amount,
        currency,
        amount_uah,
        category,
        wallet,
        comment,
        now().isoformat()
    ))

    conn.commit()
    conn.close()

    return amount_uah, rate


def get_balance_uah(user_id):
    conn = db()
    cur = conn.cursor()

    result = {"card": 0, "cash": 0}

    cur.execute("""
    SELECT wallet, type, SUM(amount_uah)
    FROM transactions
    WHERE user_id = ?
    GROUP BY wallet, type
    """, (user_id,))

    rows = cur.fetchall()
    conn.close()

    for wallet, tx_type, total in rows:
        if wallet not in result:
            continue

        if tx_type == "income":
            result[wallet] += total or 0

        elif tx_type == "expense":
            result[wallet] -= total or 0

    return result


async def get_balance(user_id):
    balance_uah = get_balance_uah(user_id)
    main_currency = get_main_currency(user_id) or "UAH"

    if main_currency == "USD":
        rate = await get_usd_rate()
        return {
            "card": balance_uah["card"] / rate,
            "cash": balance_uah["cash"] / rate,
            "currency": "USD",
            "rate": rate
        }

    return {
        "card": balance_uah["card"],
        "cash": balance_uah["cash"],
        "currency": "UAH",
        "rate": None
    }


def get_month_stats_uah(user_id):
    conn = db()
    cur = conn.cursor()

    month = now().strftime("%Y-%m")

    cur.execute("""
    SELECT type, SUM(amount_uah)
    FROM transactions
    WHERE user_id = ? AND substr(created_at, 1, 7) = ?
    GROUP BY type
    """, (user_id, month))

    rows = cur.fetchall()

    income = 0
    expense = 0

    for tx_type, total in rows:
        if tx_type == "income":
            income = total or 0
        elif tx_type == "expense":
            expense = total or 0

    cur.execute("""
    SELECT category, SUM(amount_uah)
    FROM transactions
    WHERE user_id = ?
    AND type = 'expense'
    AND category != 'перевод'
    AND substr(created_at, 1, 7) = ?
    GROUP BY category
    ORDER BY SUM(amount_uah) DESC
    """, (user_id, month))

    categories = cur.fetchall()

    conn.close()
    return income, expense, categories


async def transfer_money(user_id, amount, from_wallet, to_wallet):
    await add_transaction(user_id, "expense", amount, "UAH", "перевод", from_wallet)
    await add_transaction(user_id, "income", amount, "UAH", "перевод", to_wallet)


async def add_saving(user_id, amount, currency):
    amount_uah, rate = await to_uah(amount, currency)

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO savings (user_id, amount, currency, amount_uah, rate, created_at)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        amount,
        currency,
        amount_uah,
        rate,
        now().isoformat()
    ))

    conn.commit()
    conn.close()

    return amount_uah, rate


def get_savings(user_id):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    SELECT currency, SUM(amount), SUM(amount_uah)
    FROM savings
    WHERE user_id = ?
    GROUP BY currency
    """, (user_id,))

    rows = cur.fetchall()
    conn.close()

    result = {"UAH": 0, "USD": 0, "total_uah": 0}

    for currency, amount, amount_uah in rows:
        result[currency] = amount or 0
        result["total_uah"] += amount_uah or 0

    return result


async def set_weekly_limit(user_id, amount, currency):
    amount_uah, rate = await to_uah(amount, currency)

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    INSERT OR REPLACE INTO weekly_limits
    (user_id, amount, currency, amount_uah, rate, created_at)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        amount,
        currency,
        amount_uah,
        rate,
        now().isoformat()
    ))

    conn.commit()
    conn.close()

    return amount_uah, rate


def get_weekly_limit_uah(user_id):
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT amount_uah FROM weekly_limits WHERE user_id = ?", (user_id,))
    row = cur.fetchone()

    conn.close()
    return row[0] if row else None


def get_week_expenses_uah(user_id):
    current = now().date()
    monday = current - timedelta(days=current.weekday())
    monday_str = monday.isoformat()

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    SELECT SUM(amount_uah)
    FROM transactions
    WHERE user_id = ?
    AND type = 'expense'
    AND category != 'перевод'
    AND substr(created_at, 1, 10) >= ?
    """, (user_id, monday_str))

    spent = cur.fetchone()[0] or 0

    conn.close()
    return spent


async def get_week_report(user_id):
    limit_uah = get_weekly_limit_uah(user_id)
    spent_uah = get_week_expenses_uah(user_id)

    today = now().date()
    days_left = 7 - today.weekday()

    if limit_uah is None:
        balance = await get_balance(user_id)
        card_per_day = balance["card"] / days_left
        cash_per_day = balance["cash"] / days_left

        return {
            "has_limit": False,
            "balance": balance,
            "days_left": days_left,
            "card_per_day": card_per_day,
            "cash_per_day": cash_per_day
        }

    left_uah = limit_uah - spent_uah

    limit, currency, rate = await from_uah(limit_uah, user_id)
    spent, _, _ = await from_uah(spent_uah, user_id)
    left, _, _ = await from_uah(left_uah, user_id)

    per_day = left / days_left if days_left else left

    return {
        "has_limit": True,
        "limit": limit,
        "spent": spent,
        "left": left,
        "per_day": per_day,
        "days_left": days_left,
        "currency": currency,
        "rate": rate
    }


def parse_reminder_date(value):
    value = value.lower().strip()
    today = now().date()

    if value in ["сегодня", "today"]:
        return today.isoformat(), "once"

    if value in ["завтра", "tomorrow"]:
        return (today + timedelta(days=1)).isoformat(), "once"

    if value.startswith("каждый"):
        day = int(value.split()[-1])
        if 1 <= day <= 31:
            return str(day), "monthly"

    try:
        parsed = datetime.strptime(value, "%d.%m.%Y").date()
        return parsed.isoformat(), "once"
    except ValueError:
        pass

    try:
        parsed = datetime.strptime(value, "%d.%m").date()
        final_date = date(today.year, parsed.month, parsed.day)

        if final_date < today:
            final_date = date(today.year + 1, parsed.month, parsed.day)

        return final_date.isoformat(), "once"
    except ValueError:
        pass

    if value.isdigit():
        day = int(value)
        if 1 <= day <= 31:
            return str(day), "monthly"

    raise ValueError("Неверная дата")


def add_reminder(user_id, title, amount, remind_date, repeat_type):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO reminders
    (user_id, title, amount, remind_date, repeat_type, created_at)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        title,
        amount,
        remind_date,
        repeat_type,
        now().isoformat()
    ))

    conn.commit()
    conn.close()


def get_reminders(user_id):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    SELECT title, amount, remind_date, repeat_type
    FROM reminders
    WHERE user_id = ?
    ORDER BY id DESC
    """, (user_id,))

    rows = cur.fetchall()
    conn.close()

    return rows


def reset_user_data(user_id):
    conn = db()
    cur = conn.cursor()

    cur.execute("DELETE FROM transactions WHERE user_id = ?", (user_id,))
    cur.execute("DELETE FROM savings WHERE user_id = ?", (user_id,))
    cur.execute("DELETE FROM reminders WHERE user_id = ?", (user_id,))
    cur.execute("DELETE FROM weekly_limits WHERE user_id = ?", (user_id,))

    conn.commit()
    conn.close()


async def parse_money_input(text):
    parts = text.split()

    if len(parts) < 3:
        raise ValueError("Мало данных")

    amount = float(parts[0].replace(",", "."))
    currency = "UAH"
    index = 1

    if index < len(parts) and is_currency_word(parts[index]):
        currency = normalize_currency(parts[index])
        index += 1

    wallet = None
    wallet_index = None

    for i in range(index, len(parts)):
        normalized = normalize_wallet(parts[i])

        if normalized:
            wallet = normalized
            wallet_index = i
            break

    if not wallet:
        raise ValueError("Не указан кошелек")

    category_words = parts[index:wallet_index]

    if not category_words:
        raise ValueError("Не указана категория")

    category = " ".join(category_words)
    comment = " ".join(parts[wallet_index + 1:]) if len(parts) > wallet_index + 1 else ""

    return amount, currency, category, wallet, comment


currency_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🇺🇦 Вести в грн")],
        [KeyboardButton(text="🇺🇸 Вести в долларах")]
    ],
    resize_keyboard=True
)


main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Доход"), KeyboardButton(text="➖ Расход")],
        [KeyboardButton(text="💰 Баланс"), KeyboardButton(text="📅 На неделю")],
        [KeyboardButton(text="💵 Курс USD"), KeyboardButton(text="🏦 Отложено")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="⏰ Напоминания")],
        [KeyboardButton(text="⚙️ Валюта"), KeyboardButton(text="🗑 Сброс")],
        [KeyboardButton(text="ℹ️ Помощь")]
    ],
    resize_keyboard=True
)


@dp.message(Command("start"))
async def start(message: Message):
    add_user(message.from_user.id, message.from_user.username)

    if not get_main_currency(message.from_user.id):
        await message.answer(
            "💰 Финансовый бот запущен\n\nВыбери основную валюту учета:",
            reply_markup=currency_keyboard
        )
        return

    await message.answer(
        "💰 Финансовый бот запущен\n\nВыбери действие через кнопки ниже.\n\nℹ️ Помощь: /help",
        reply_markup=main_keyboard
    )


@dp.message(F.text == "🇺🇦 Вести в грн")
async def set_currency_uah(message: Message):
    add_user(message.from_user.id, message.from_user.username)
    set_main_currency(message.from_user.id, "UAH")
    await message.answer("✅ Основная валюта: гривна", reply_markup=main_keyboard)


@dp.message(F.text == "🇺🇸 Вести в долларах")
async def set_currency_usd(message: Message):
    add_user(message.from_user.id, message.from_user.username)
    set_main_currency(message.from_user.id, "USD")
    await message.answer("✅ Основная валюта: доллар", reply_markup=main_keyboard)


@dp.message(F.text == "⚙️ Валюта")
async def currency_settings(message: Message):
    current = get_main_currency(message.from_user.id) or "не выбрана"
    await message.answer(f"Текущая валюта: {current}\n\nВыбери новую:", reply_markup=currency_keyboard)


@dp.message(Command("help"))
async def help_cmd(message: Message):
    await help_btn(message)


@dp.message(F.text == "ℹ️ Помощь")
async def help_btn(message: Message):
    await message.answer("""
ℹ️ Помощь

Доход:
1. Нажми ➕ Доход
2. Введи:
25000 зарплата карта
500 usd зарплата карта
3000 продажа аккаунта наличка

Расход:
1. Нажми ➖ Расход
2. Введи:
450 еда карта
20 usd лекарства наличка
1200 бытовая химия карта

Категории можно писать свои:
еда, сигареты, коммуналка, бизнес, машина, долги, бытовая химия

Перевод между кошельками:
перевод 3000 карта наличка
перевод 1500 наличка карта

Недельный лимит:
лимит 7000
лимит 200 usd

Отложить:
отложить 100 usd
отложить 5000 грн

Напоминания:
напомни коммуналка 2500 25.05.2026
напомни интернет 300 завтра
напомни аренда 12000 1
напомни кредит 5000 каждый 15

Команды:
/balance
/week
/stats
/course
/savings
/help
""")


@dp.message(F.text == "➕ Доход")
async def income_mode(message: Message):
    user_modes[message.from_user.id] = "income"
    await message.answer("Введи доход:\n\n25000 зарплата карта\n500 usd зарплата карта")


@dp.message(F.text == "➖ Расход")
async def expense_mode(message: Message):
    user_modes[message.from_user.id] = "expense"
    await message.answer("Введи расход:\n\n450 еда карта\n20 usd лекарства наличка")


@dp.message(Command("course"))
async def course_cmd(message: Message):
    rate = await get_usd_rate()
    await message.answer(f"💵 Курс USD НБУ: {rate:.2f} грн")


@dp.message(F.text == "💵 Курс USD")
async def course_btn(message: Message):
    rate = await get_usd_rate()
    await message.answer(f"💵 Курс USD НБУ: {rate:.2f} грн")


@dp.message(Command("balance"))
async def balance_cmd(message: Message):
    await send_balance(message)


@dp.message(F.text == "💰 Баланс")
async def balance_btn(message: Message):
    await send_balance(message)


async def send_balance(message: Message):
    balance = await get_balance(message.from_user.id)
    symbol = currency_symbol(balance["currency"])
    total = balance["card"] + balance["cash"]

    text = f"""
💰 Баланс

💳 Карта: {balance["card"]:.2f} {symbol}
💵 Наличка: {balance["cash"]:.2f} {symbol}

Всего: {total:.2f} {symbol}
"""

    if balance["currency"] == "USD":
        text += f"\nКурс USD: {balance['rate']:.2f} грн"

    await message.answer(text)


@dp.message(Command("week"))
async def week_cmd(message: Message):
    await send_week(message)


@dp.message(F.text == "📅 На неделю")
async def week_btn(message: Message):
    await send_week(message)


async def send_week(message: Message):
    report = await get_week_report(message.from_user.id)

    if not report["has_limit"]:
        balance = report["balance"]
        symbol = currency_symbol(balance["currency"])

        text = f"""
📅 На неделю

Недельный лимит не установлен.

Сейчас бот делит твой баланс на дни до конца недели.

Дней осталось: {report["days_left"]}

💳 Карта в день: {report["card_per_day"]:.2f} {symbol}
💵 Наличка в день: {report["cash_per_day"]:.2f} {symbol}

Чтобы установить лимит:
лимит 7000
лимит 200 usd
"""
        await message.answer(text)
        return

    symbol = currency_symbol(report["currency"])

    text = f"""
📅 Недельный лимит

Лимит: {report["limit"]:.2f} {symbol}
Потрачено: {report["spent"]:.2f} {symbol}
Осталось: {report["left"]:.2f} {symbol}

Дней осталось: {report["days_left"]}
Можно тратить в день: {report["per_day"]:.2f} {symbol}
"""

    if report["currency"] == "USD":
        text += f"\nКурс USD: {report['rate']:.2f} грн"

    await message.answer(text)


@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    await send_stats(message)


@dp.message(F.text == "📊 Статистика")
async def stats_btn(message: Message):
    await send_stats(message)


async def send_stats(message: Message):
    income_uah, expense_uah, categories_uah = get_month_stats_uah(message.from_user.id)

    income, currency, rate = await from_uah(income_uah, message.from_user.id)
    expense, _, _ = await from_uah(expense_uah, message.from_user.id)

    symbol = currency_symbol(currency)

    text = f"""
📊 Статистика за месяц

Доход: {income:.2f} {symbol}
Расходы: {expense:.2f} {symbol}
Остаток: {(income - expense):.2f} {symbol}

Траты по категориям:
"""

    if not categories_uah:
        text += "\nПока трат нет."
    else:
        for category, total_uah in categories_uah:
            total, _, _ = await from_uah(total_uah, message.from_user.id)
            text += f"\n— {category}: {total:.2f} {symbol}"

    if currency == "USD":
        text += f"\n\nКурс USD: {rate:.2f} грн"

    await message.answer(text)


@dp.message(Command("savings"))
async def savings_cmd(message: Message):
    await send_savings(message)


@dp.message(F.text == "🏦 Отложено")
async def savings_btn(message: Message):
    await send_savings(message)


async def send_savings(message: Message):
    savings = get_savings(message.from_user.id)
    total, currency, rate = await from_uah(savings["total_uah"], message.from_user.id)
    symbol = currency_symbol(currency)

    text = f"""
🏦 Отложено

Гривна: {savings["UAH"]:.2f} грн
Доллары: {savings["USD"]:.2f} $

Всего: {total:.2f} {symbol}
"""

    if currency == "USD":
        text += f"\nКурс USD: {rate:.2f} грн"

    await message.answer(text)


@dp.message(F.text == "⏰ Напоминания")
async def reminders_btn(message: Message):
    reminders = get_reminders(message.from_user.id)

    if not reminders:
        await message.answer("""
Напоминаний пока нет.

Примеры:
напомни коммуналка 2500 25.05.2026
напомни интернет 300 завтра
напомни аренда 12000 1
напомни кредит 5000 каждый 15
""")
        return

    text = "⏰ Твои напоминания:\n"

    for title, amount, remind_date, repeat_type in reminders:
        if repeat_type == "monthly":
            text += f"\n— {title}: {amount:.2f}, каждый месяц {remind_date} числа"
        else:
            text += f"\n— {title}: {amount:.2f}, дата {remind_date}"

    await message.answer(text)


@dp.message(F.text == "🗑 Сброс")
async def reset_btn(message: Message):
    reset_user_data(message.from_user.id)
    user_modes.pop(message.from_user.id, None)

    await message.answer("""
🗑 Данные сброшены.

Удалено:
— доходы
— расходы
— отложено
— напоминания
— недельный лимит

Валюта учета сохранена.
""")


@dp.message()
async def text_handler(message: Message):
    text = message.text.strip()
    user_id = message.from_user.id

    add_user(user_id, message.from_user.username)

    if not get_main_currency(user_id):
        await message.answer("Сначала выбери основную валюту учета:", reply_markup=currency_keyboard)
        return

    if text.startswith("+"):
        user_modes[user_id] = "income"
        text = text[1:].strip()

    elif text.startswith("-"):
        user_modes[user_id] = "expense"
        text = text[1:].strip()

    if text.lower().startswith("лимит"):
        try:
            parts = text.split()
            amount = float(parts[1].replace(",", "."))
            currency = normalize_currency(parts[2]) if len(parts) > 2 else "UAH"

            amount_uah, rate = await set_weekly_limit(user_id, amount, currency)

            if currency == "USD":
                await message.answer(
                    f"📅 Недельный лимит установлен:\n{amount:.2f} $ = {amount_uah:.2f} грн\nКурс: {rate:.2f}"
                )
            else:
                await message.answer(f"📅 Недельный лимит установлен:\n{amount:.2f} грн")

        except Exception:
            await message.answer("Ошибка. Пример:\nлимит 7000\nлимит 200 usd")

        return

    if text.lower().startswith("отложить"):
        try:
            parts = text.split()
            amount = float(parts[1].replace(",", "."))
            currency = normalize_currency(parts[2]) if len(parts) > 2 else "UAH"

            amount_uah, rate = await add_saving(user_id, amount, currency)

            if currency == "USD":
                await message.answer(
                    f"🏦 Отложено:\n{amount:.2f} $ = {amount_uah:.2f} грн\nКурс: {rate:.2f}"
                )
            else:
                await message.answer(f"🏦 Отложено:\n{amount:.2f} грн")

        except Exception:
            await message.answer("Ошибка. Пример:\nотложить 100 usd\nотложить 5000 грн")

        return

    if text.lower().startswith("перевод"):
        try:
            parts = text.split()
            amount = float(parts[1].replace(",", "."))
            from_wallet = normalize_wallet(parts[2])
            to_wallet = normalize_wallet(parts[3])

            if not from_wallet or not to_wallet:
                await message.answer("Пример:\nперевод 3000 карта наличка")
                return

            await transfer_money(user_id, amount, from_wallet, to_wallet)

            await message.answer(
                f"🔁 Перевод выполнен:\n{amount:.2f} грн\n{wallet_name(from_wallet)} → {wallet_name(to_wallet)}"
            )

        except Exception:
            await message.answer("Ошибка. Пример:\nперевод 3000 карта наличка")

        return

    if text.lower().startswith("напомни"):
        try:
            parts = text.split()
            title = parts[1]
            amount = float(parts[2].replace(",", "."))
            raw_date = " ".join(parts[3:])

            remind_date, repeat_type = parse_reminder_date(raw_date)
            add_reminder(user_id, title, amount, remind_date, repeat_type)

            if repeat_type == "monthly":
                await message.answer(
                    f"⏰ Напоминание добавлено:\n{title} — {amount:.2f}, каждый месяц {remind_date} числа"
                )
            else:
                await message.answer(
                    f"⏰ Напоминание добавлено:\n{title} — {amount:.2f}\nДата: {remind_date}"
                )

        except Exception:
            await message.answer("""
Ошибка. Примеры:

напомни коммуналка 2500 25.05.2026
напомни интернет 300 завтра
напомни аренда 12000 1
напомни кредит 5000 каждый 15
""")

        return

    mode = user_modes.get(user_id)

    if mode in ["income", "expense"]:
        try:
            amount, currency, category, wallet, comment = await parse_money_input(text)

            amount_uah, rate = await add_transaction(
                user_id=user_id,
                tx_type=mode,
                amount=amount,
                currency=currency,
                category=category,
                wallet=wallet,
                comment=comment
            )

            operation = "Доход" if mode == "income" else "Расход"

            if currency == "USD":
                await message.answer(
                    f"✅ {operation} добавлен:\n"
                    f"{amount:.2f} $ = {amount_uah:.2f} грн\n"
                    f"{category} — {wallet_name(wallet)}\n"
                    f"Курс: {rate:.2f}"
                )
            else:
                await message.answer(
                    f"✅ {operation} добавлен:\n"
                    f"{amount:.2f} грн — {category} — {wallet_name(wallet)}"
                )

            user_modes.pop(user_id, None)

        except Exception:
            await message.answer("""
Ошибка формата.

Примеры:
25000 зарплата карта
500 usd зарплата карта
450 еда наличка
1200 бытовая химия карта
""")

        return

    await message.answer("""
Не понял.

Нажми ➕ Доход или ➖ Расход.

Или используй команды:
лимит 7000
перевод 3000 карта наличка
отложить 100 usd
""")


async def reminder_worker():
    already_sent = set()

    while True:
        current = now()
        today = current.date().isoformat()
        current_day = str(current.day)

        if current.hour == 10:
            key = current.strftime("%Y-%m-%d-%H")

            if key not in already_sent:
                conn = db()
                cur = conn.cursor()

                cur.execute("""
                SELECT user_id, title, amount, repeat_type, remind_date
                FROM reminders
                WHERE 
                    (repeat_type = 'once' AND remind_date = ?)
                    OR
                    (repeat_type = 'monthly' AND remind_date = ?)
                """, (today, current_day))

                rows = cur.fetchall()

                for uid, title, amount, repeat_type, remind_date in rows:
                    try:
                        await bot.send_message(
                            uid,
                            f"⏰ Напоминание:\nСегодня нужно оплатить {title} — {amount:.2f}"
                        )
                    except Exception as e:
                        logging.error(e)

                cur.execute("""
                DELETE FROM reminders
                WHERE repeat_type = 'once' AND remind_date = ?
                """, (today,))

                conn.commit()
                conn.close()

                already_sent.add(key)

        await asyncio.sleep(60)


async def main():
    init_db()
    asyncio.create_task(reminder_worker())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())