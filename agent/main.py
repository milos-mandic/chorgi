"""Entry point — starts the Telegram bot."""

import asyncio
import logging
import os
import re
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
from agent.webhook import WebhookServer
from agent import voice
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
    for key in ("ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_USER_ID",
                 "OPENAI_API_KEY", "WEBHOOK_SECRET", "WEBHOOK_PORT",
                 "FATHOM_WEBHOOK_SECRET",
                 "GOOGLE_OAUTH_CREDENTIALS", "CALENDAR_OWNER_ID",
                 "CALENDAR_BOT_ID"):
        env_val = os.environ.get(key)
        if env_val:
            secrets[key] = env_val
    return secrets


def md_to_telegram_html(text: str) -> str:
    """Convert common markdown to Telegram-supported HTML."""
    # Escape HTML entities first
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")

    # Code blocks: ```...``` → <pre>...</pre>
    text = re.sub(r"```(?:\w*)\n?(.*?)```", r"<pre>\1</pre>", text, flags=re.DOTALL)

    # Inline code: `...` → <code>...</code>
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)

    # Bold: **...** → <b>...</b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)

    # List items: - item → • item
    text = re.sub(r"^- ", "• ", text, flags=re.MULTILINE)

    return text


def _strip_html(html: str) -> str:
    """Strip Telegram HTML tags back to plain text."""
    text = html
    for tag in ("b", "pre", "code"):
        text = text.replace(f"<{tag}>", "").replace(f"</{tag}>", "")
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return text


def _smart_chunk(text: str, limit: int = 4096) -> list[str]:
    """Split text into chunks at newline boundaries to avoid breaking HTML tags."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


async def _send_response(update: Update, response: str):
    """Send a response with HTML formatting, falling back to plain text."""
    html = md_to_telegram_html(response)
    for chunk in _smart_chunk(html):
        try:
            await update.message.reply_text(chunk, parse_mode="HTML")
        except Exception:
            await update.message.reply_text(_strip_html(chunk))


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
        ack = classification.get("ack", "On it.")
        await update.message.reply_text(ack)
        result = await orchestrator.execute_sub_agents(classification)
        await _send_response(update, result["response"])
    except Exception:
        logger.error("Unhandled error in handle_message", exc_info=True)
        try:
            await update.message.reply_text("Something went wrong. Please try again.")
        except Exception:
            logger.error("Failed to send error message to user", exc_info=True)


async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle incoming Telegram voice messages."""
    ogg_in = None
    ogg_out = None
    try:
        orchestrator: Orchestrator = ctx.bot_data["orchestrator"]
        user_id = str(update.effective_user.id)
        msg_id = update.message.message_id

        # Download voice file
        voice_file = await update.message.voice.get_file()
        ogg_in = Path(f"/tmp/chorgi_v1_voice_{msg_id}.ogg")
        await voice_file.download_to_drive(str(ogg_in))

        # Transcribe
        try:
            transcription = await asyncio.to_thread(voice.transcribe_audio, ogg_in)
        except RuntimeError as e:
            logger.error("Transcription failed: %s", e)
            await update.message.reply_text(
                "Couldn't transcribe your voice message. "
                "Make sure OPENAI_API_KEY is configured."
            )
            return

        # Show the user what was heard
        await update.message.reply_text(f"\U0001f3a4 {transcription}")

        # Route through normal message flow
        classification = await orchestrator.classify(transcription, user_id)

        if classification["type"] == "rejected":
            return

        if classification["type"] == "error":
            response_text = classification["response"]
        elif classification["type"] == "haiku":
            response_text = classification["response"]
        else:
            ack = classification.get("ack", "On it.")
            await update.message.reply_text(ack)
            result = await orchestrator.execute_sub_agents(classification)
            response_text = result["response"]

        # Try to generate and send voice response
        try:
            ogg_out = await asyncio.to_thread(voice.tts_generate, response_text[:4096])
            with open(ogg_out, "rb") as f:
                await update.message.reply_voice(voice=f)
        except RuntimeError as e:
            logger.warning("TTS failed, sending text only: %s", e)

        # Always send text as well
        await _send_response(update, response_text)

    except Exception:
        logger.error("Unhandled error in handle_voice", exc_info=True)
        try:
            await update.message.reply_text("Something went wrong. Please try again.")
        except Exception:
            logger.error("Failed to send error message to user", exc_info=True)
    finally:
        # Clean up temp files
        for path in (ogg_in, ogg_out):
            if path:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass


async def post_init(application: Application):
    """Called after the bot is initialized — start the scheduler and webhook server."""
    orchestrator: Orchestrator = application.bot_data["orchestrator"]
    bot = application.bot
    authorized_user_id = orchestrator.authorized_user_id

    async def send_to_user(message: str):
        html = md_to_telegram_html(message)
        for chunk in _smart_chunk(html):
            try:
                await bot.send_message(
                    chat_id=authorized_user_id, text=chunk, parse_mode="HTML"
                )
            except Exception:
                await bot.send_message(
                    chat_id=authorized_user_id, text=_strip_html(chunk)
                )

    orchestrator.send_to_user = send_to_user

    scheduler = Scheduler(orchestrator)
    asyncio.create_task(scheduler.start())
    logger.info("Scheduler background task created")

    # Start webhook server
    webhook_server = WebhookServer()
    webhook_server.start(asyncio.get_running_loop(), orchestrator)
    application.bot_data["webhook_server"] = webhook_server


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

    # Set API keys and config for clients
    os.environ["ANTHROPIC_API_KEY"] = secrets["ANTHROPIC_API_KEY"]
    if secrets.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = secrets["OPENAI_API_KEY"]
    for key in ("WEBHOOK_SECRET", "WEBHOOK_PORT", "FATHOM_WEBHOOK_SECRET",
                 "GOOGLE_OAUTH_CREDENTIALS", "CALENDAR_OWNER_ID",
                 "CALENDAR_BOT_ID"):
        if secrets.get(key):
            os.environ[key] = secrets[key]

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
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    logger.info("Bot starting... Press Ctrl+C to stop.")
    # Python 3.12+ requires an explicit event loop before run_polling()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling()


if __name__ == "__main__":
    main()
