import os
import json
import logging
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters,
)

logging.basicConfig(level=logging.INFO)

# ---------- НАСТРОЙКИ ДОСТУПА ----------
BOT_TOKEN = os.environ["BOT_TOKEN"]
SHEET_ID = "1Dzce99k7q3yD9oUSGw0bziPjpMo8WBDDLPpwHB6nECg"

# Твой Telegram ID — сюда будут приходить уведомления
OWNER_ID = 8659182905

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
gc = gspread.authorize(creds)
spreadsheet = gc.open_by_key(SHEET_ID)

# ---------- СПРАВОЧНИКИ ----------
КОМАНДА = ["Старший менеджер", "Менеджер дист.", "Консультант"]
ПОСТАВЩИКИ = ["Урал Прайс", "ApplePrice", "Appleman", "Хохряков 72", "Параллельный импорт", "Другое"]

# Категория -> (тип, создаёт долг?)
КАТЕГОРИИ = {
    "Приём наличных":        ("Приход", None),
    "Оплата на юр.":         ("Приход", None),
    "Рассрочка":             ("Приход", "создаёт_дебиторку"),
    "Trade-in":              ("Приход", None),
    "Поступил товар в долг": ("Расход", "создаёт_кредиторку"),
    "Выдача денег поставщику": ("Расход", "гасит_кредиторку"),
    "Выдача налички":        ("Расход", None),
    "Выдано в долг":         ("Расход", "создаёт_дебиторку_выдача"),
    "Возврат":               ("Расход", None),
}

# ---------- ЭТАПЫ ДИАЛОГА ----------
(
    ТИП_ОПЕРАЦИИ,       # 0 — Заявка или Сделка
    КАТЕГОРИЯ,          # 1
    СУММА,              # 2
    СЧЁТ,               # 3
    БАНК_ПОСТ,          # 4
    КЛИЕНТ,             # 5
    ОСТАТОК,            # 6
    КТО,                # 7
    КОММЕНТ,            # 8
    IMEI,               # 9
    ВЫДАНО_ТИП,         # 10 — наличка или устройство (для "Выдано в долг")
    УСТРОЙСТВО,         # 11 — модель устройства
    TRADEIN_МОДЕЛЬ,     # 12
    TRADEIN_СУММА,      # 13
    TRADEIN_IMEI,       # 14
) = range(15)


# ---------- АВТОСОЗДАНИЕ ЛИСТОВ ----------
def ensure_sheets():
    titles = [ws.title for ws in spreadsheet.worksheets()]

    if "Операции" not in titles:
        ws = spreadsheet.add_worksheet("Операции", rows=1000, cols=9)
        ws.append_row(["Дата", "Тип", "Сумма", "Счёт", "Категория",
                       "Клиент / Поставщик", "Устройство / IMEI", "Кто провёл", "Комментарий"])
    if "Долги" not in titles:
        ws = spreadsheet.add_worksheet("Долги", rows=1000, cols=6)
        ws.append_row(["Дата", "Кто/Кому", "Тип долга", "Сумма", "Статус", "Комментарий"])
    if "Заявки" not in titles:
        ws = spreadsheet.add_worksheet("Заявки", rows=1000, cols=6)
        ws.append_row(["Дата", "Имя клиента", "Запрос", "Откуда пришёл", "Контакт", "Кто принял"])
    if "Настройки" not in titles:
        ws = spreadsheet.add_worksheet("Настройки", rows=50, cols=4)
        ws.append_row(["Команда", "Категория", "Тип", ""])
        rows = []
        cats = list(КАТЕГОРИИ.items())
        maxlen = max(len(КОМАНДА), len(cats))
        for i in range(maxlen):
            team = КОМАНДА[i] if i < len(КОМАНДА) else ""
            cat = cats[i][0] if i < len(cats) else ""
            typ = cats[i][1][0] if i < len(cats) else ""
            rows.append([team, cat, typ, ""])
        ws.append_rows(rows)
    if "Итого" not in titles:
        ws = spreadsheet.add_worksheet("Итого", rows=30, cols=3)
        data = [
            ["ИТОГО — состояние компании", "", ""],
            ["Нам должны (дебиторка)", '=SUMIFS(Долги!D:D;Долги!C:C;"Нам должны (дебиторка)";Долги!E:E;"Висит")', ""],
            ["Мы должны", '=SUMIFS(Долги!D:D;Долги!C:C;"Мы должны (поставщику)";Долги!E:E;"Висит")', ""],
            ["Товар на складе (ввожу сам)", 0, "меняю вручную"],
            ["КАПИТАЛ КОМПАНИИ", "=B2-B3+B4", "Дебиторка−Долги+Товар"],
        ]
        ws.append_rows(data, value_input_option="USER_ENTERED")


def read_settings():
    global КОМАНДА
    try:
        ws = spreadsheet.worksheet("Настройки")
        records = ws.get_all_values()[1:]
        team = [r[0] for r in records if len(r) > 0 and r[0].strip()]
        if team:
            КОМАНДА = team
    except Exception as e:
        logging.warning(f"settings read failed: {e}")


def kb(options, cols=2):
    rows, row = [], []
    for o in options:
        row.append(o)
        if len(row) == cols:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)


# ---------- УВЕДОМЛЕНИЕ ВЛАДЕЛЬЦУ ----------
async def notify_owner(context: ContextTypes.DEFAULT_TYPE, d: dict):
    try:
        тип_emoji = "🟢" if d.get("Тип") == "Приход" else "🔴"
        долг = d.get("Долг")

        устройство = d.get("Устройство", "")
        imei = d.get("IMEI", "")
        device_line = ""
        if устройство:
            device_line = f"*Устройство:* {устройство}\n"
        if imei:
            device_line += f"*IMEI:* {imei}\n"

        текст = (
            f"{тип_emoji} *Новая операция — Урал Стор*\n\n"
            f"*{d.get('Тип', '')}:* {d.get('Сумма', 0):.0f} ₽\n"
            f"*Счёт:* {d.get('Счёт', '-')}\n"
            f"*Категория:* {d.get('Категория', '-')}\n"
            f"*Клиент/Поставщик:* {d.get('Клиент', '-')}\n"
            f"{device_line}"
            f"*Провёл:* {d.get('Кто', '-')}\n"
            f"*Комментарий:* {d.get('Комментарий', '-')}\n"
            f"*Время:* {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )

        if долг == "создаёт_кредиторку":
            текст += f"\n\n📌 Долг поставщику: {d.get('Сумма', 0):.0f} ₽"
        elif долг == "создаёт_дебиторку" and d.get("Остаток_долга", 0) > 0:
            текст += f"\n\n📌 Клиент должен: {d.get('Остаток_долга', 0):.0f} ₽"
        elif долг == "создаёт_дебиторку_выдача":
            текст += f"\n\n📌 Выдано в долг: {d.get('Сумма', 0):.0f} ₽"

        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=текст,
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.warning(f"Не удалось отправить уведомление: {e}")


# ---------- ДИАЛОГ ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    read_settings()
    context.user_data.clear()
    await update.message.reply_text(
        "👋 Привет! Что фиксируем?",
        reply_markup=kb(["💰 Сделка", "📋 Заявка"], cols=2),
    )
    return ТИП_ОПЕРАЦИИ


async def тип_выбран(update: Update, context: ContextTypes.DEFAULT_TYPE):
    выбор = update.message.text.strip()

    if "Заявка" in выбор:
        await update.message.reply_text(
            "📋 Раздел Заявки скоро будет готов. Пока фиксируй через Сделку.\n\n/start — начать заново.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    if "Сделка" in выбор:
        await update.message.reply_text(
            "💰 Новая сделка. Выбери категорию:",
            reply_markup=kb(list(КАТЕГОРИИ.keys()), cols=2),
        )
        return КАТЕГОРИЯ

    await update.message.reply_text("Выбери кнопкой.")
    return ТИП_ОПЕРАЦИИ


async def cat_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cat = update.message.text.strip()
    if cat not in КАТЕГОРИИ:
        await update.message.reply_text("Выбери категорию кнопкой.")
        return КАТЕГОРИЯ
    context.user_data["Категория"] = cat
    context.user_data["Тип"], context.user_data["Долг"] = КАТЕГОРИИ[cat]

    # Trade-in — особый сценарий
    if cat == "Trade-in":
        await update.message.reply_text(
            "Модель устройства? (например: iPhone 13 Pro 256GB)",
            reply_markup=ReplyKeyboardRemove()
        )
        return TRADEIN_МОДЕЛЬ

    # Выдано в долг — уточняем тип
    if cat == "Выдано в долг":
        await update.message.reply_text(
            "Что выдано?",
            reply_markup=kb(["Наличка", "Устройство"], cols=2)
        )
        return ВЫДАНО_ТИП

    # Возврат — сначала устройство и IMEI
    if cat == "Возврат":
        await update.message.reply_text(
            "Модель устройства? (или «-» если не техника)",
            reply_markup=ReplyKeyboardRemove()
        )
        return УСТРОЙСТВО

    await update.message.reply_text(
        "Сумма? (числом, например 95000)",
        reply_markup=ReplyKeyboardRemove()
    )
    return СУММА


# ---------- TRADE-IN ----------
async def tradein_модель(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["Устройство"] = update.message.text.strip()
    await update.message.reply_text("Сумма выкупа? (числом)")
    return TRADEIN_СУММА


async def tradein_сумма(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.replace(" ", "").replace(",", ".")
    try:
        context.user_data["Сумма"] = float(raw)
    except ValueError:
        await update.message.reply_text("Введи число.")
        return TRADEIN_СУММА
    await update.message.reply_text("IMEI устройства? (или «-»)")
    return TRADEIN_IMEI


async def tradein_imei(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["IMEI"] = update.message.text.strip()
    await update.message.reply_text(
        "Счёт? (например: Касса, Альфа, Т-Банк)",
        reply_markup=ReplyKeyboardRemove()
    )
    return СЧЁТ


# ---------- ВЫДАНО В ДОЛГ ----------
async def выдано_тип(update: Update, context: ContextTypes.DEFAULT_TYPE):
    тип = update.message.text.strip()
    context.user_data["Выдано_тип"] = тип

    if тип == "Устройство":
        await update.message.reply_text(
            "Модель устройства?",
            reply_markup=ReplyKeyboardRemove()
        )
        return УСТРОЙСТВО

    await update.message.reply_text(
        "Сумма? (числом)",
        reply_markup=ReplyKeyboardRemove()
    )
    return СУММА


async def устройство_введено(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["Устройство"] = update.message.text.strip()
    await update.message.reply_text("IMEI? (или «-»)")
    return IMEI


async def imei_введён(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["IMEI"] = update.message.text.strip()
    await update.message.reply_text("Сумма? (или 0 если не фиксируем деньги)")
    return СУММА


# ---------- ОСНОВНОЙ ФЛОУ ----------
async def sum_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.replace(" ", "").replace(",", ".")
    try:
        context.user_data["Сумма"] = float(raw)
    except ValueError:
        await update.message.reply_text("Не похоже на число. Введи ещё раз.")
        return СУММА

    долг = context.user_data["Долг"]
    if долг in ("создаёт_кредиторку", "гасит_кредиторку"):
        await update.message.reply_text("Поставщик?", reply_markup=kb(ПОСТАВЩИКИ, cols=2))
        return БАНК_ПОСТ

    await update.message.reply_text(
        "Счёт? (например: Касса, Альфа, Т-Банк)",
        reply_markup=ReplyKeyboardRemove()
    )
    return СЧЁТ


async def supplier_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["Клиент"] = update.message.text.strip()
    await update.message.reply_text(
        "Счёт? (например: Касса, Альфа, Т-Банк)",
        reply_markup=ReplyKeyboardRemove()
    )
    return СЧЁТ


async def account_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["Счёт"] = update.message.text.strip()
    if "Клиент" in context.user_data:
        await update.message.reply_text("Кто провёл?", reply_markup=kb(КОМАНДА, cols=1))
        return КТО
    await update.message.reply_text(
        "Клиент / ФИО? (или «-» если нет)",
        reply_markup=ReplyKeyboardRemove()
    )
    return КЛИЕНТ


async def client_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["Клиент"] = update.message.text.strip()
    долг = context.user_data["Долг"]
    if долг == "создаёт_дебиторку":
        await update.message.reply_text(
            "Полная сумма сделки? (остаток уйдёт в долг клиента). Если долга нет — введи ту же сумму."
        )
        return ОСТАТОК
    await update.message.reply_text("Кто провёл?", reply_markup=kb(КОМАНДА, cols=1))
    return КТО


async def full_amount_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.replace(" ", "").replace(",", ".")
    try:
        full = float(raw)
    except ValueError:
        await update.message.reply_text("Введи число.")
        return ОСТАТОК
    context.user_data["Остаток_долга"] = max(0.0, full - context.user_data["Сумма"])
    await update.message.reply_text("Кто провёл?", reply_markup=kb(КОМАНДА, cols=1))
    return КТО


async def who_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    who = update.message.text.strip()
    if who not in КОМАНДА:
        await update.message.reply_text("Выбери кнопкой.")
        return КТО
    context.user_data["Кто"] = who
    await update.message.reply_text("Комментарий? (или «-»)", reply_markup=ReplyKeyboardRemove())
    return КОММЕНТ


async def comment_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = context.user_data
    d["Комментарий"] = update.message.text.strip()
    date = datetime.now().strftime("%Y-%m-%d")

    устройство_info = ""
    if d.get("Устройство") or d.get("IMEI"):
        устройство_info = f"{d.get('Устройство', '')} {d.get('IMEI', '')}".strip()

    # 1) Пишем операцию
    spreadsheet.worksheet("Операции").append_row([
        date, d.get("Тип"), d.get("Сумма", 0), d.get("Счёт", "-"), d.get("Категория"),
        d.get("Клиент", "-"), устройство_info, d.get("Кто"), d.get("Комментарий"),
    ], value_input_option="USER_ENTERED")

    # 2) Долги
    долг = d.get("Долг")
    долги_ws = spreadsheet.worksheet("Долги")

    if долг == "создаёт_кредиторку":
        долги_ws.append_row([
            date, d.get("Клиент", "-"), "Мы должны (поставщику)",
            d.get("Сумма", 0), "Висит", d.get("Комментарий")
        ], value_input_option="USER_ENTERED")

    elif долг == "создаёт_дебиторку" and d.get("Остаток_долга", 0) > 0:
        долги_ws.append_row([
            date, d.get("Клиент", "-"), "Нам должны (дебиторка)",
            d.get("Остаток_долга", 0), "Висит", "остаток по рассрочке"
        ], value_input_option="USER_ENTERED")

    elif долг == "создаёт_дебиторку_выдача":
        долги_ws.append_row([
            date, d.get("Клиент", "-"), "Нам должны (дебиторка)",
            d.get("Сумма", 0), "Висит", f"Выдано в долг: {устройство_info or 'наличка'}"
        ], value_input_option="USER_ENTERED")

    # 3) Уведомление владельцу
    await notify_owner(context, d)

    # 4) Сводка пользователю
    txt = (
        f"✅ Записано!\n\n"
        f"{d.get('Тип')} {d.get('Сумма', 0):.0f} ₽\n"
        f"Счёт: {d.get('Счёт', '-')}\n"
        f"Категория: {d.get('Категория')}\n"
        f"Клиент: {d.get('Клиент', '-')}\n"
    )
    if устройство_info:
        txt += f"Устройство: {устройство_info}\n"
    txt += f"Провёл: {d.get('Кто')}"

    if долг == "создаёт_кредиторку":
        txt += f"\n\n📌 Долг поставщику {d.get('Сумма', 0):.0f} ₽ записан."
    elif долг == "создаёт_дебиторку" and d.get("Остаток_долга", 0) > 0:
        txt += f"\n\n📌 Клиент должен {d.get('Остаток_долга', 0):.0f} ₽ — записано."
    elif долг == "создаёт_дебиторку_выдача":
        txt += f"\n\n📌 Выдано в долг {d.get('Сумма', 0):.0f} ₽ — записано."

    txt += "\n\n/start — новая операция"
    await update.message.reply_text(txt, reply_markup=ReplyKeyboardRemove())
    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Отменено. /start — начать заново.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def main():
    ensure_sheets()
    read_settings()
    app = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ТИП_ОПЕРАЦИИ:  [MessageHandler(filters.TEXT & ~filters.COMMAND, тип_выбран)],
            КАТЕГОРИЯ:     [MessageHandler(filters.TEXT & ~filters.COMMAND, cat_chosen)],
            СУММА:         [MessageHandler(filters.TEXT & ~filters.COMMAND, sum_entered)],
            БАНК_ПОСТ:     [MessageHandler(filters.TEXT & ~filters.COMMAND, supplier_chosen)],
            СЧЁТ:          [MessageHandler(filters.TEXT & ~filters.COMMAND, account_chosen)],
            КЛИЕНТ:        [MessageHandler(filters.TEXT & ~filters.COMMAND, client_entered)],
            ОСТАТОК:       [MessageHandler(filters.TEXT & ~filters.COMMAND, full_amount_entered)],
            КТО:           [MessageHandler(filters.TEXT & ~filters.COMMAND, who_chosen)],
            КОММЕНТ:       [MessageHandler(filters.TEXT & ~filters.COMMAND, comment_and_save)],
            ВЫДАНО_ТИП:    [MessageHandler(filters.TEXT & ~filters.COMMAND, выдано_тип)],
            УСТРОЙСТВО:    [MessageHandler(filters.TEXT & ~filters.COMMAND, устройство_введено)],
            IMEI:          [MessageHandler(filters.TEXT & ~filters.COMMAND, imei_введён)],
            TRADEIN_МОДЕЛЬ:[MessageHandler(filters.TEXT & ~filters.COMMAND, tradein_модель)],
            TRADEIN_СУММА: [MessageHandler(filters.TEXT & ~filters.COMMAND, tradein_сумма)],
            TRADEIN_IMEI:  [MessageHandler(filters.TEXT & ~filters.COMMAND, tradein_imei)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv)
    logging.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
