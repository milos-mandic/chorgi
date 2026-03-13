# General Skill

The default catch-all skill. Handles research, writing, analysis, code, file operations, and any task that doesn't match a more specialized skill.

## How it works

When a message doesn't match a specialized skill (email, calendar, fathom), Haiku routes it here. The sub-agent has broad tool access and up to 15 turns to complete the task.

---

## Tools

- **Bash** — run shell commands
- **Read** / **Write** / **Edit** — file operations
- **WebSearch** / **WebFetch** — internet access

---

## Setup

No special setup needed. This skill ships with the repo and works out of the box.

---

## Usage

Any task that doesn't fit a specialized skill routes here automatically. Examples:

- "Summarize this article"
- "Write a Python script that..."
- "What's the weather in SF?"
- "Research the best options for..."
