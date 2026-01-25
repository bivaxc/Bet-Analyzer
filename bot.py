from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler

# ⚠️ Replace with your real token
BOT_TOKEN = "8496461157:AAFyRtuAIC9LvbRoxKlsp06bMQKgDdJJj-4"

# /start command
async def start(update: Update, context):
    await update.message.reply_text(
        "✅ BBR Sport Bot is live!\nUse /predict"
    )

# /predict command
async def predict(update: Update, context):
    await update.message.reply_text(
        "⚽ BBR SPORT TIP\n"
        "Market: Over 1.5 Goals\n"
        "Confidence: High\n"
        "Reason: Strong scoring trend"
    )

# Build bot
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("predict", predict))
app.run_polling()
