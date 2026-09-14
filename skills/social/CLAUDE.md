# Social Skill

You manage the user's social posting board: LinkedIn posts and Substack Notes, shown in the dashboard's **Social** tab. The tab has Mon–Sun columns and one row per platform. Each platform gets **at most one post per day**.

The user publishes by hand. They copy the finished text from the board, or from the Telegram nudge the bot sends at the scheduled time.

## Rules
- Run commands via Bash from this directory. All reads and writes go through `social_cli.py`; never edit `workspace/posts.json` directly.
- You run non-interactively. Don't ask for clarification, and report what you did concisely.
- Post text is stored and copied **exactly** as written:
  - Plain text only. No Markdown (`**bold**` shows up literally) and no HTML.
  - Separate paragraphs with a blank line (`\n\n`).
  - Unicode bold/italic letters (𝗯𝗼𝗹𝗱, 𝘪𝘵𝘢𝘭𝘪𝘤) and emoji are fine.
- LinkedIn posts: at most 3000 characters. Substack Notes: at most 10000.
- Times are Europe/Berlin wall-clock time, written `YYYY-MM-DD HH:MM`. Resolve relative dates ("next Tuesday") with the date ladder at the top of your context.
- Only replace existing posts (`--overwrite`) when the user asked for it.

## The contract
The import contract is `schema/social_posts.v1.schema.json` (JSON Schema 2020-12). A valid example is `schema/example_week.json`.

```json
{
  "version": 1,
  "posts": [
    {
      "platform": "linkedin",
      "scheduled_at": "2026-09-21 08:30",
      "text": "Hook line.\n\nBody paragraph.\n\nClosing line.",
      "status": "ready",
      "notes": "optional private note",
      "image": {"data_uri": "data:image/png;base64,…", "alt": "optional"}
    }
  ]
}
```
- Required fields: `platform` (`linkedin` | `substack_note`), `scheduled_at` and `text`.
- `status` is `draft` | `ready` | `posted` and defaults to `ready`.
- `image` is optional. It must be a PNG, JPEG, WebP or GIF base64 data URI of at most 8 MB, and the declared type must match the bytes.
- Unknown fields are rejected.
- Two posts for the same platform on the same day are rejected.

## Commands
```bash
/Users/chorgi/projects/chorgi_bot/.venv/bin/python3 social_cli.py schema                      # print the contract
/Users/chorgi/projects/chorgi_bot/.venv/bin/python3 social_cli.py validate <file.json>        # check a file (or - for stdin)
/Users/chorgi/projects/chorgi_bot/.venv/bin/python3 social_cli.py import <file.json> --dry-run  # preview: new / conflict / error per post
/Users/chorgi/projects/chorgi_bot/.venv/bin/python3 social_cli.py import <file.json>          # save (all or nothing)
/Users/chorgi/projects/chorgi_bot/.venv/bin/python3 social_cli.py import <file.json> --overwrite  # replace posts already in those slots
/Users/chorgi/projects/chorgi_bot/.venv/bin/python3 social_cli.py list                        # this week
/Users/chorgi/projects/chorgi_bot/.venv/bin/python3 social_cli.py list --week 2026-09-21      # the week containing that date
/Users/chorgi/projects/chorgi_bot/.venv/bin/python3 social_cli.py get <post_id>               # one post as JSON, exact text
/Users/chorgi/projects/chorgi_bot/.venv/bin/python3 social_cli.py set-status <post_id> posted # draft | ready | posted
```

## Workflow: put a week of posts on the board
1. `list --week <any date in that week>` to see what is already scheduled.
2. Write the posts as a contract document to `workspace/imports/<monday>.json` with the Write tool.
3. `validate` the file and fix every error until it prints ✓.
4. `import --dry-run` to check for conflicts with existing posts. Add `--overwrite` only if the user asked to replace them.
5. `import`.
6. Report one line per post: day, date, platform, time and the first line of the text.

**"I posted today's LinkedIn post":** `list`, find the post, then `set-status <id> posted`.

**"What's scheduled this week?":** `list`, grouped by day.
