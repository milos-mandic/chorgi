"""Bot-based onboarding — collects profile info via Telegram conversation."""

import logging
from pathlib import Path

from telegram import Update
from telegram.ext import ConversationHandler, ContextTypes

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
PERSONAL_DIR = BASE_DIR / ".personal"
TEMPLATES_DIR = BASE_DIR / "templates"

# Conversation states
ASK_NAME, ASK_ROLE, ASK_STYLE, ASK_PROJECTS, CONFIRM = range(5)


async def start_onboarding(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Begin the onboarding conversation. Returns ASK_NAME state."""
    ctx.user_data["onboarding"] = {}
    await update.message.reply_text(
        "Let's set up your profile. You can /cancel at any time.\n\n"
        "What should I call you?"
    )
    return ASK_NAME


async def handle_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["onboarding"]["name"] = update.message.text.strip()
    await update.message.reply_text("What do you do? (your role or profession)")
    return ASK_ROLE


async def handle_role(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["onboarding"]["role"] = update.message.text.strip()
    await update.message.reply_text(
        "How should I communicate with you?\n"
        "(e.g., concise and direct, casual, formal, etc.)"
    )
    return ASK_STYLE


async def handle_style(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["onboarding"]["style"] = update.message.text.strip()
    await update.message.reply_text(
        "What are you currently working on?\n"
        "(active projects, priorities — a few lines is fine)"
    )
    return ASK_PROJECTS


async def handle_projects(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["onboarding"]["projects"] = update.message.text.strip()
    data = ctx.user_data["onboarding"]
    summary = (
        f"Name: {data['name']}\n"
        f"Role: {data['role']}\n"
        f"Style: {data['style']}\n"
        f"Projects: {data['projects']}\n\n"
        "Does this look right? (yes/no)"
    )
    await update.message.reply_text(summary)
    return CONFIRM


async def handle_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    answer = update.message.text.strip().lower()
    if answer in ("yes", "y"):
        data = ctx.user_data["onboarding"]
        ok, detail = write_profile(data)
        if ok:
            await update.message.reply_text("Profile saved! I'm ready to help.")
        else:
            await update.message.reply_text(f"Failed to save profile: {detail}")
        ctx.user_data.pop("onboarding", None)
        return ConversationHandler.END

    # Anything else restarts
    ctx.user_data["onboarding"] = {}
    await update.message.reply_text("Let's start over. What should I call you?")
    return ASK_NAME


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.pop("onboarding", None)
    await update.message.reply_text("Onboarding cancelled.")
    return ConversationHandler.END


def write_profile(data: dict) -> tuple:
    """Write identity.md and context.md from templates. Returns (ok, detail)."""
    try:
        PERSONAL_DIR.mkdir(parents=True, exist_ok=True)
        (PERSONAL_DIR / "memory").mkdir(parents=True, exist_ok=True)

        # Identity
        identity_tpl = TEMPLATES_DIR / "identity.md.template"
        if identity_tpl.exists():
            content = identity_tpl.read_text()
        else:
            content = "# Identity\n\n**Name:** {name}\n**Role:** {role}\n\n## Communication Style\n{style}\n"
        content = content.replace("{name}", data.get("name", ""))
        content = content.replace("{role}", data.get("role", ""))
        content = content.replace("{style}", data.get("style", ""))
        (PERSONAL_DIR / "identity.md").write_text(content)

        # Context
        context_tpl = TEMPLATES_DIR / "context.md.template"
        if context_tpl.exists():
            content = context_tpl.read_text()
        else:
            content = "# Current Context\n\n## Active Projects\n{projects}\n"
        content = content.replace("{projects}", data.get("projects", ""))
        (PERSONAL_DIR / "context.md").write_text(content)

        return True, "Profile written."
    except OSError as e:
        return False, str(e)
