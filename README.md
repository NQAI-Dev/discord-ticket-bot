# discord-ticket-bot

Discord support-ticket bot for the **Mars Host** community Discord.
Members open a ticket via a persistent panel message; staff moderate
through Modals; transcripts are written to a designated channel
and the ticket channel is deleted on close.

## Stack

- **discord.py 2.7** — gateway + UI components (View/Modal/Select).
- **peewee 4.5** — SQLite store for tickets + bot metadata.
- Python 3.14, single-file `main.py`, runs as a systemd service.

## Layout

```
.
├── main.py              # bot + handlers + DB helpers (single file)
├── config.json          # token, channels, ticket types
├── tickets.db           # runtime SQLite store (gitignored)
├── venv/                # Python venv with discord.py + peewee (gitignored)
└── tests/
    ├── conftest.py      # discord/peewee stubs + tmp-DB fixture
    └── test_db_helpers.py
```

## Config

`config.json` fields the bot reads:

| Key                   | Purpose                                                              |
| --------------------- | -------------------------------------------------------------------- |
| `token`               | Discord bot token (Bot scope; needs `Manage Channels` for category ops) |
| `guild_id`            | Single-guild deployment; bot refuses to run on other guilds         |
| `panel_channel_id`    | Channel where the persistent "open a ticket" panel is posted        |
| `transcript_channel_id` | Channel that receives transcript files on close                   |
| `staff_role_ids`      | Roles granted read/write to every ticket channel                     |
| `ticket_types`        | List of categories with `id`, `name`, `category_id`, optional `fields` |

## DB schema

Two tables, created by `init_db()` (idempotent):

- **`Metadata(key, value)`** — keyed kv store; currently holds `panel_message_id`
  so the bot can edit its existing panel message after a restart.
- **`Ticket(ticket_id, channel_id, guild_id, user_id, ticket_type, fields_json, status, created_at, closed_at, closed_by)`** — one row per opened channel; `fields_json` carries the answers from the open modal.

## Run

```bash
# Production (already wired as systemd unit discord-ticket-bot.service):
sudo systemctl status discord-ticket-bot
journalctl -u discord-ticket-bot -f

# Manual:
cd /home/openclaw/Projects/discord-ticket-bot
./venv/bin/python3 main.py
```

## Test

```bash
./venv/bin/python3 -m pytest tests/ -v
```

Tests stub `discord.*` and exercise the DB helpers
(`init_db`, `get_meta`/`set_meta`, ticket CRUD, type lookup).
Discord UI classes are import-asserted only — they need a live
gateway to be exercised end-to-end.

## Deploy

`discord-ticket-bot.service` (systemd, `Restart=always`, runs as
`openclaw`). The unit is intentionally pinned to a single venv and
single working directory; config changes require a
`sudo systemctl restart discord-ticket-bot`.

## Tickets flow

1. User clicks the persistent **"Open a ticket"** select on the panel message.
2. If the type has `fields`, a Modal asks the questions in `ticket_types[].fields`.
3. `build_ticket_channel` creates a private channel under the type's
   `category_id`, computes the next ticket number from the DB, posts
   an intro embed, writes the row.
4. The **"Close ticket"** button under every ticket channel opens a
   Modal that asks for an optional reason; on submit it walks the
   channel history, posts a transcript file + log embed to
   `transcript_channel_id`, deletes the channel after 4s, and flips
   the row to `status=closed`.
