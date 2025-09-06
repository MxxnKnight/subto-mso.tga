import os
import json
import logging
import asyncio
from flask import Flask, request, jsonify
import telegram
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Environment Variables ---
TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

if not TOKEN:
    raise ValueError("No BOT_TOKEN found in environment variables")

# --- Telegram Bot Setup ---
# In this architecture, we create the Application object but don't run it with run_polling or run_webhook.
# Flask and Gunicorn will manage the server lifecycle.
application = Application.builder().token(TOKEN).build()

# --- Flask App Setup ---
app = Flask(__name__)

# --- Bot Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message when the /start command is issued."""
    logger.info(f"Received /start command from user {update.effective_user.name}")
    await update.message.reply_text('Welcome! Send me a movie or series name to search.')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles regular text messages."""
    query = update.message.text
    logger.info(f"Received message: '{query}' from user {update.effective_user.name}")
    await update.message.reply_text(f"You searched for: {query}. Bot logic to process this is not yet implemented.")

# --- Flask Routes ---
@app.route('/')
def index():
    return "API and Bot Server is running."

@app.route('/api/subtitles')
def api_test():
    """A simple API test endpoint."""
    return jsonify({"status": "ok", "message": "This is a test API endpoint."})

@app.route('/set_webhook', methods=['GET'])
def set_webhook():
    """A route to manually set the webhook."""
    if not WEBHOOK_URL:
        logger.error("WEBHOOK_URL environment variable not set!")
        return "Error: WEBHOOK_URL environment variable not set!", 500

    webhook_full_url = f'{WEBHOOK_URL}/telegram'

    # Use an event loop to run the async set_webhook function
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    success = loop.run_until_complete(application.bot.set_webhook(webhook_full_url))

    if success:
        logger.info(f"Webhook set successfully to {webhook_full_url}")

        # Send startup notification to owner
        owner_id = os.environ.get("OWNER_ID")
        if owner_id:
            loop.run_until_complete(bot.send_message(chat_id=owner_id, text="Bot is up and running!"))
            logger.info(f"Sent startup notification to OWNER_ID {owner_id}")

        return f"Webhook set successfully to {webhook_full_url}"
    else:
        logger.error("Webhook setup failed!")
        return "Webhook setup failed!", 500

@app.route('/telegram', methods=['POST'])
def telegram_webhook():
    """This is the main webhook endpoint for Telegram updates."""
    update_data = request.get_json(force=True)

    # Use an event loop to run the async process_update function
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(application.process_update(Update.de_json(update_data, application.bot)))

    return "ok"

def main():
    # --- Add Handlers to the Application ---
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # --- Run Flask App ---
    # Render provides the PORT environment variable.
    port = int(os.environ.get('PORT', 5000))
    # Gunicorn will be used in production, this is for local dev.
    app.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    main()
