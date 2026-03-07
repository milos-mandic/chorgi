#!/usr/bin/env python3
"""First-run onboarding script. Creates .personal/ from templates."""

import os
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
PERSONAL_DIR = BASE_DIR / ".personal"
TEMPLATES_DIR = BASE_DIR / "templates"


def prompt(question: str, secret: bool = False) -> str:
    if secret:
        import getpass
        return getpass.getpass(f"{question}: ")
    return input(f"{question}: ")


def main():
    # Python version check
    if sys.version_info < (3, 11):
        print(f"Error: Python 3.11+ required (you have {sys.version})")
        sys.exit(1)

    print("\nWelcome to the Agent Harness. Let's set you up.\n")

    if PERSONAL_DIR.exists():
        overwrite = input(".personal/ already exists. Overwrite? (y/N): ")
        if overwrite.lower() != "y":
            print("Aborted.")
            return

    print("Make sure you have installed dependencies: pip install -r requirements.txt\n")

    # Collect secrets
    print("--- API Keys ---")
    anthropic_key = prompt("Anthropic API key", secret=True)
    if anthropic_key and not anthropic_key.startswith("sk-ant-"):
        print("  Warning: Anthropic API keys typically start with 'sk-ant-'. Double-check your key.")

    telegram_token = prompt("Telegram bot token", secret=True)

    telegram_user_id = prompt("Your Telegram user ID (numeric)")
    if telegram_user_id and not telegram_user_id.strip().isdigit():
        print("  Error: Telegram user ID must be numeric.")
        return

    # Collect personal info
    print("\n--- About You ---")
    name = prompt("What should I call you?")
    role = prompt("What do you do? (brief)")
    style = prompt("Communication style preference? (e.g., concise, detailed, casual)")
    projects = prompt("What are you currently working on?")

    # Create .personal/
    PERSONAL_DIR.mkdir(exist_ok=True)
    memory_dir = PERSONAL_DIR / "memory"
    memory_dir.mkdir(exist_ok=True)

    # Write secrets.env
    secrets_path = PERSONAL_DIR / "secrets.env"
    secrets_path.write_text(
        f"ANTHROPIC_API_KEY={anthropic_key}\n"
        f"TELEGRAM_BOT_TOKEN={telegram_token}\n"
        f"TELEGRAM_USER_ID={telegram_user_id}\n"
    )
    print(f"  ✓ {secrets_path}")

    # Write identity.md from template
    identity_template = (TEMPLATES_DIR / "identity.md.template").read_text()
    identity_content = identity_template.replace("{name}", name).replace("{role}", role).replace("{style}", style)
    identity_path = PERSONAL_DIR / "identity.md"
    identity_path.write_text(identity_content)
    print(f"  ✓ {identity_path}")

    # Write context.md from template
    context_template = (TEMPLATES_DIR / "context.md.template").read_text()
    context_content = context_template.replace("{projects}", projects)
    context_path = PERSONAL_DIR / "context.md"
    context_path.write_text(context_content)
    print(f"  ✓ {context_path}")

    # Create empty memory files
    for f in ["long_term.md", "short_term.md", "scratch.md"]:
        p = memory_dir / f
        p.write_text("")
        print(f"  ✓ {p}")

    # Check for claude CLI
    claude_path = shutil.which("claude")
    if claude_path:
        print(f"\nClaude Code CLI... ✓ found ({claude_path})")
    else:
        print("\nWarning: Claude Code CLI not found in PATH.")
        print("  Install it: https://docs.anthropic.com/en/docs/claude-code")

    print(f"\nSetup complete! Run: python agent/main.py\n")


if __name__ == "__main__":
    main()
