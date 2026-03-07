"""Entry point — starts the Telegram bot."""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Ensure the project root is on sys.path so `agent.*` imports work
# regardless of how the script is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ConversationHandler,
    MessageHandler, filters, ContextTypes,
)

from agent.orchestrator import Orchestrator
from agent.scheduler import Scheduler
from agent.onboarding import (
    start_onboarding, handle_name, handle_role, handle_style,
    handle_projects, handle_confirm, cancel,
    ASK_NAME, ASK_ROLE, ASK_STYLE, ASK_PROJECTS, CONFIRM,
    PERSONAL_DIR,
)

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
SECRETS_PATH = BASE_DIR / ".personal" / "secrets.env"


def load_secrets():
    """Load secrets from .personal/secrets.env or environment variables."""
    secrets = {}
    if SECRETS_PATH.exists():
        for line in SECRETS_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                secrets[key.strip()] = value.strip()
    # Environment variables override file values
    for key in ("ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_USER_ID"):
        env_val = os.environ.get(key)
        if env_val:
            secrets[key] = env_val
    return secrets


async def _send_response(update: Update, response: str):
    """Send a response, splitting into chunks if needed for Telegram's limit."""
    if len(response) > 4096:
        for i in range(0, len(response), 4096):
            await update.message.reply_text(response[i:i + 4096])
    else:
        await update.message.reply_text(response)


class _NeedsOnboardingFilter(filters.MessageFilter):
    """Returns True if .personal/identity.md does not exist."""

    def filter(self, message) -> bool:
        return not (PERSONAL_DIR / "identity.md").exists()


_needs_onboarding = _NeedsOnboardingFilter()


async def setup_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /setup — start onboarding (with auth check)."""
    orchestrator: Orchestrator = ctx.bot_data["orchestrator"]
    if str(update.effective_user.id) != str(orchestrator.authorized_user_id):
        return ConversationHandler.END
    return await start_onboarding(update, ctx)


async def auto_onboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Auto-trigger onboarding on first message if no profile exists."""
    orchestrator: Orchestrator = ctx.bot_data["orchestrator"]
    if str(update.effective_user.id) != str(orchestrator.authorized_user_id):
        return ConversationHandler.END
    await update.message.reply_text(
        "Welcome! I don't have a profile for you yet. Let's set one up."
    )
    return await start_onboarding(update, ctx)


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle incoming Telegram text messages."""
    try:
        orchestrator: Orchestrator = ctx.bot_data["orchestrator"]
        user_id = str(update.effective_user.id)
        message_text = update.message.text

        # Classify first (fast, ~1s)
        classification = await orchestrator.classify(message_text, user_id)

        if classification["type"] == "rejected":
            return

        if classification["type"] == "error":
            await _send_response(update, classification["response"])
            return

        if classification["type"] == "haiku":
            await _send_response(update, classification["response"])
            return

        # Sub-agent route: ack immediately, then do the work
        await update.message.reply_text("On it.")
        result = await orchestrator.execute_sub_agent(classification)
        await _send_response(update, result["response"])
    except Exception:
        logger.error("Unhandled error in handle_message", exc_info=True)
        try:
            await update.message.reply_text("Something went wrong. Please try again.")
        except Exception:
            logger.error("Failed to send error message to user", exc_info=True)


async def post_init(application: Application):
    """Called after the bot is initialized — start the scheduler."""
    orchestrator: Orchestrator = application.bot_data["orchestrator"]
    bot = application.bot
    authorized_user_id = orchestrator.authorized_user_id

    async def send_to_user(message: str):
        await bot.send_message(chat_id=authorized_user_id, text=message)

    orchestrator.send_to_user = send_to_user

    scheduler = Scheduler(orchestrator)
    asyncio.create_task(scheduler.start())
    logger.info("Scheduler background task created")


def main():
    secrets = load_secrets()

    # Validate required secrets
    missing = []
    for key in ("ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_USER_ID"):
        if not secrets.get(key):
            missing.append(key)
    if missing:
        logger.error(f"Missing required secrets: {', '.join(missing)}")
        logger.error("Run 'python setup.py' first, or set environment variables.")
        return

    # Set Anthropic API key for the client
    os.environ["ANTHROPIC_API_KEY"] = secrets["ANTHROPIC_API_KEY"]

    orchestrator = Orchestrator(authorized_user_id=secrets["TELEGRAM_USER_ID"])

    app = Application.builder().token(secrets["TELEGRAM_BOT_TOKEN"]).post_init(post_init).build()
    app.bot_data["orchestrator"] = orchestrator

    # Onboarding conversation handler — must be registered before the catch-all
    onboarding_handler = ConversationHandler(
        entry_points=[
            CommandHandler("setup", setup_command),
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & _needs_onboarding,
                auto_onboard,
            ),
        ],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name)],
            ASK_ROLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_role)],
            ASK_STYLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_style)],
            ASK_PROJECTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_projects)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(onboarding_handler)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot starting... Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
