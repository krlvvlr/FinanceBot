import asyncio
import logging
import sqlite3
import aiohttp
from datetime import datetime, date, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

TOKEN = "8432934655:AAE6Ori3g_VpzoFo4w5OCRNpw5LHfTJbRIg"
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
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    cur.execute("PRAGMA table_info(users)")
    user_columns = [col[1] for col in cur.fetchall()]

    if "main_currency" not in user_columns:
        cur.execute("ALTER TABLE users ADD COLUMN main_currency TEXT DEFAULT NULL")

    cur.execute("PRAGMA table_info(transactions)")
    tx_columns = [col[1] for col in cur.fetchall()]

    tx_migrations = {
        "wallet": "ALTER TABLE transactions ADD COLUMN wallet TEXT DEFAULT 'card'",
        "currency": "ALTER TABLE transactions ADD COLUMN currency TEXT DEFAULT 'UAH'",
        "amount_uah": "ALTER TABLE transactions ADD COLUMN amount_uah REAL",
        "comment": "ALTER TABLE transactions ADD COLUMN comment TEXT",
    }

    for col, sql in tx_migrations.items():
        if col not in tx_columns:
            cur.execute(sql)

    cur.execute("""
    UPDATE transactions
    SET amount_uah = amount
    WHERE amount_uah IS NULL
    """)

    cur.execute("PRAGMA table_info(reminders)")
    reminder_columns = [col[1] for col in cur.fetchall()]

    if "remind_date" not in reminder_columns:
        cur.execute("ALTER TABLE reminders ADD COLUMN remind_date TEXT")

    if "repeat_type" not in reminder_columns:
        cur.execute("ALTER TABLE reminders ADD COLUMN repeat_type TEXT DEFAULT 'monthly'")

    conn.commit()
    conn.close()


def save_setting(key, value):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    INSERT OR REPLACE INTO settings (key, value)
    VALUES (?, ?)
    """, (key, value))

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
        if saved:
            return float(saved)
        return 40.0


def add_user(user_id, username):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    INSERT OR IGNORE INTO users (user_id, username, created_at)
    VALUES (?, ?, ?)
    """, (user_id, username, now().isoformat()))

    conn.commit()
    conn.close()


def set_main_currency(user_id, currency):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    UPDATE users
    SET main_currency = ?
    WHERE user_id = ?
    """, (currency, user_id))

    conn.commit()
    conn.close()


def get_main_currency(user_id):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    SELECT main_currency
    FROM users
    WHERE user_id = ?
    """, (user_id,))

    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return row[0]


def currency_symbol(currency):
    if currency == "USD":
        return "$"
    return "грн"


async def convert_from_uah(amount_uah, user_id):
    main_currency = get_main_currency(user_id) or "UAH"

    if main_currency == "USD":
        rate = await get_usd_rate()
        return amount_uah / rate, "USD", rate

    return amount_uah, "UAH", None


def normalize_wallet(wallet):
    wallet = wallet.lower()

    if wallet in ["карта", "card", "картка"]:
        return "card"

    if wallet in ["наличка", "нал", "cash", "кэш"]:
        return "cash"

    return None


def wallet_name(wallet):
    if wallet == "card":
        return "💳 Карта"
    if wallet == "cash":
        return "💵 Наличка"
    return wallet


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


async def add_transaction(user_id, tx_type, amount, currency, category, wallet, comment=""):
    rate = await get_usd_rate()
    amount_uah = amount * rate if currency == "USD" else amount

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


async def add_saving(user_id, amount, currency):
    rate = await get_usd_rate()
    amount_uah = amount * rate if currency == "USD" else amount

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO savings 
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

    result = {
        "UAH": 0,
        "USD": 0,
        "total_uah": 0
    }

    for currency, amount, amount_uah in rows:
        result[currency] = amount or 0
        result["total_uah"] += amount_uah or 0

    return result


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


async def get_week_money(user_id):
    balance = await get_balance(user_id)

    today = now().date()
    days_left = 7 - today.weekday()

    card_per_day = balance["card"] / days_left
    cash_per_day = balance["cash"] / days_left

    return balance, days_left, card_per_day, cash_per_day


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
        raise ValueError("Неверный день")

    try:
        parsed_date = datetime.strptime(value, "%d.%m.%Y").date()
        return parsed_date.isoformat(), "once"
    except ValueError:
        pass

    try:
        parsed_date = datetime.strptime(value, "%d.%m").date()
        final_date = date(today.year, parsed_date.month, parsed_date.day)

        if final_date < today:
            final_date = date(today.year + 1, parsed_date.month, parsed_date.day)

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
        [KeyboardButton(text="⚙️ Валюта"), KeyboardButton(text="ℹ️ Помощь")]
    ],
    resize_keyboard=True
)


@dp.message(Command("start"))
async def start(message: Message):
    add_user(message.from_user.id, message.from_user.username)

    main_currency = get_main_currency(message.from_user.id)

    if not main_currency:
        await message.answer("""
💰 Финансовый бот запущен

Выбери основную валюту учета:
""", reply_markup=currency_keyboard)
        return

    await message.answer("""
💰 Финансовый бот запущен

Выбери действие через кнопки ниже.

ℹ️ Все примеры и команды:
/help
""", reply_markup=main_keyboard)


@dp.message(F.text == "🇺🇦 Вести в грн")
async def set_currency_uah(message: Message):
    add_user(message.from_user.id, message.from_user.username)
    set_main_currency(message.from_user.id, "UAH")

    await message.answer("""
✅ Основная валюта: гривна

Теперь баланс, статистика и неделя будут считаться в грн.
""", reply_markup=main_keyboard)


@dp.message(F.text == "🇺🇸 Вести в долларах")
async def set_currency_usd(message: Message):
    add_user(message.from_user.id, message.from_user.username)
    set_main_currency(message.from_user.id, "USD")

    await message.answer("""
✅ Основная валюта: доллар

Теперь баланс, статистика и неделя будут считаться в $ по актуальному курсу НБУ.
""", reply_markup=main_keyboard)


@dp.message(F.text == "⚙️ Валюта")
async def currency_settings(message: Message):
    current = get_main_currency(message.from_user.id) or "не выбрана"

    await message.answer(f"""
Текущая основная валюта: {current}

Выбери новую:
""", reply_markup=currency_keyboard)


@dp.message(Command("help"))
async def help_cmd(message: Message):
    await help_btn(message)


@dp.message(F.text == "➕ Доход")
async def income_mode(message: Message):
    user_modes[message.from_user.id] = "income"

    await message.answer("""
Введи доход:

25000 зарплата карта
500 usd зарплата карта
""")


@dp.message(F.text == "➖ Расход")
async def expense_mode(message: Message):
    user_modes[message.from_user.id] = "expense"

    await message.answer("""
Введи расход:

450 еда карта
20 usd еда наличка
""")


@dp.message(Command("course"))
async def course_cmd(message: Message):
    rate = await get_usd_rate()
    await message.answer(f"💵 Курс USD НБУ: {rate:.2f} грн")


@dp.message(F.text == "💵 Курс USD")
async def course_btn(message: Message):
    rate = await get_usd_rate()
    await message.answer(f"💵 Курс USD НБУ: {rate:.2f} грн")


@dp.message(Command("savings"))
async def savings_cmd(message: Message):
    await send_savings(message)


@dp.message(F.text == "🏦 Отложено")
async def savings_btn(message: Message):
    await send_savings(message)


async def send_savings(message: Message):
    savings = get_savings(message.from_user.id)
    main_currency = get_main_currency(message.from_user.id) or "UAH"

    total, currency, rate = await convert_from_uah(
        savings["total_uah"],
        message.from_user.id
    )

    symbol = currency_symbol(currency)

    text = f"""
🏦 Отложено

Гривна: {savings["UAH"]:.2f} грн
Доллары: {savings["USD"]:.2f} $

Всего: {total:.2f} {symbol}
"""

    if main_currency == "USD":
        text += f"\nКурс USD: {rate:.2f} грн"

    await message.answer(text)


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
    balance, days_left, card_per_day, cash_per_day = await get_week_money(message.from_user.id)
    symbol = currency_symbol(balance["currency"])

    text = f"""
📅 Деньги до конца недели

Дней осталось: {days_left}

💳 Карта:
{balance["card"]:.2f} {symbol}
В день: {card_per_day:.2f} {symbol}

💵 Наличка:
{balance["cash"]:.2f} {symbol}
В день: {cash_per_day:.2f} {symbol}
"""

    if balance["currency"] == "USD":
        text += f"\nКурс USD: {balance['rate']:.2f} грн"

    await message.answer(text)


@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    await send_stats(message)


@dp.message(F.text == "📊 Статистика")
async def stats_btn(message: Message):
    await send_stats(message)


async def send_stats(message: Message):
    income_uah, expense_uah, categories_uah = get_month_stats_uah(message.from_user.id)

    income, currency, rate = await convert_from_uah(income_uah, message.from_user.id)
    expense, _, _ = await convert_from_uah(expense_uah, message.from_user.id)

    symbol = currency_symbol(currency)
    balance = income - expense

    text = f"""
📊 Статистика за месяц

Доход: {income:.2f} {symbol}
Расходы: {expense:.2f} {symbol}
Остаток: {balance:.2f} {symbol}

Траты по категориям:
"""

    if not categories_uah:
        text += "\nПока трат нет."
    else:
        for category, total_uah in categories_uah:
            total, _, _ = await convert_from_uah(total_uah, message.from_user.id)
            text += f"\n— {category}: {total:.2f} {symbol}"

    if currency == "USD":
        text += f"\n\nКурс USD: {rate:.2f} грн"

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


@dp.message(F.text == "ℹ️ Помощь")
async def help_btn(message: Message):
    await message.answer("""
ℹ️ Помощь

1. Сначала нажми:
➕ Доход
или
➖ Расход

2. Потом введи сумму:

Доход:
25000 зарплата карта
500 usd зарплата карта

Расход:
450 еда карта
20 usd еда наличка

Отложить:
отложить 100 usd
отложить 5000 грн

Перевод:
перевод 3000 карта наличка

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

Сменить валюту:
⚙️ Валюта
""")


async def parse_money_input(text):
    parts = text.split()

    amount = float(parts[0])
    currency = "UAH"

    if len(parts) >= 2 and is_currency_word(parts[1]):
        currency = normalize_currency(parts[1])
        category_index = 2
    else:
        category_index = 1

    category = parts[category_index]
    wallet = normalize_wallet(parts[category_index + 1])
    comment = " ".join(parts[category_index + 2:]) if len(parts) > category_index + 2 else ""

    return amount, currency, category, wallet, comment


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

    if text.lower().startswith("отложить"):
        try:
            parts = text.split()

            amount = float(parts[1])
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

            amount = float(parts[1])
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
            amount = float(parts[2])
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

            if not wallet:
                await message.answer("Укажи кошелек: карта или наличка")
                return

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
                    f"✅ {operation} добавлен:\n{amount:.2f} $ = {amount_uah:.2f} грн\n{category} — {wallet_name(wallet)}\nКурс: {rate:.2f}"
                )
            else:
                await message.answer(
                    f"✅ {operation} добавлен:\n{amount:.2f} грн — {category} — {wallet_name(wallet)}"
                )

            user_modes.pop(user_id, None)

        except Exception:
            await message.answer("""
Ошибка формата.

Примеры:
25000 зарплата карта
500 usd зарплата карта
450 еда наличка
20 usd еда карта
""")

        return

    await message.answer("""
Не понял.

Нажми ➕ Доход или ➖ Расход.

Потом введи:
25000 зарплата карта
450 еда наличка
100 usd зарплата карта
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

                for user_id, title, amount, repeat_type, remind_date in rows:
                    try:
                        await bot.send_message(
                            user_id,
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