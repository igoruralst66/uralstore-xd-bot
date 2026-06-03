import os
import json
import logging
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Этапы диалога
DATE, CLIENT_TYPE, NAME, TOPIC, WHAT_SAID, IMPRESSION, NEXT_STEP, WHO_LED = range(8)

def get_sheet():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    creds_dict = json.loads(creds_json)
    scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open_by_key("1Dzce99k7q3yD9oUSGw0bziPjpMo8WBDDLPpwHB6nECg")
    return sheet.sheet1

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Записываем опыт клиента 📋\n\nКакая дата звонка? (например: 04.06.2025)",
        reply_markup=ReplyKeyboardRemove()
    )
    return DATE

async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["date"] = update.message.text
    keyboard = [["B2B", "B2C"]]
    await update.message.reply_text(
        "Тип клиента?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    )
    return CLIENT_TYPE

async def get_client_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["client_type"] = update.message.text
    await update.message.reply_text("Имя или компания клиента?", reply_markup=ReplyKeyboardRemove())
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text
    await update.message.reply_text("Тема звонка?")
    return TOPIC

async def get_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["topic"] = update.message.text
    await update.message.reply_text("Что сказал клиент? (кратко, своими словами)")
    return WHAT_SAID

async def get_what_said(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["what_said"] = update.message.text
    keyboard = [["1", "2", "3", "4", "5"]]
    await update.message.reply_text(
        "Впечатление от звонка (1 — плохо, 5 — отлично)?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    )
    return IMPRESSION

async def get_impression(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["impression"] = update.message.text
    await update.message.reply_text("Следующий шаг?", reply_markup=ReplyKeyboardRemove())
    return NEXT_STEP

async def get_next_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["next_step"] = update.message.text
    await update.message.reply_text("Кто вёл звонок?")
    return WHO_LED

async def get_who_led(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["who_led"] = update.message.text
    data = context.user_data
    try:
        sheet = get_sheet()
        sheet.append_row([
            data.get("date"),
            data.get("client_type"),
            data.get("name"),
            data.get("topic"),
            data.get("what_said"),
            data.get("impression"),
            data.get("next_step"),
            data.get("who_led"),
            datetime.now().strftime("%d.%m.%Y %H:%M")
        ])
        await update.message.reply_text("✅ Записано в таблицу! Спасибо.\n\nНовый звонок? /start")
    except Exception as e:
        logger.error(f"Ошибка записи: {e}")
        await update.message.reply_text(f"❌ Ошибка записи: {e}")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено. Начать заново: /start", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def main():
    token = os.environ.get("BOT_TOKEN")
    app = Application.builder().token(token).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_date)],
            CLIENT_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_client_type)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_topic)],
            WHAT_SAID: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_what_said)],
            IMPRESSION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_impression)],
            NEXT_STEP: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_next_step)],
            WHO_LED: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_who_led)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv_handler)
    app.run_polling()

if __name__ == "__main__":
    main()
