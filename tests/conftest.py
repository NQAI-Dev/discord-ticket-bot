import sys
import os
import types

# Ensure the project root is importable when pytest runs from anywhere.
ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(ROOT)
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

# Stub the heavy `discord` module so unit tests can exercise the
# pure-Python helpers without a live Discord client. Peewee is in the
# venv already and we use the real one.
try:
    import discord  # noqa: F401
except ImportError:
    discord = types.ModuleType("discord")
    discord.Intents = types.SimpleNamespace(default=lambda: types.SimpleNamespace())
    class _Color:
        @staticmethod
        def from_rgb(*a, **kw):
            return None
    discord.Color = _Color
    discord.TextChannel = type("TextChannel", (), {})
    discord.CategoryChannel = type("CategoryChannel", (), {})
    discord.Member = type("Member", (), {})
    discord.PermissionOverwrite = type("PermissionOverwrite", (), {})
    discord.ui = types.ModuleType("discord.ui")
    discord.ui.ButtonStyle = types.SimpleNamespace(secondary="secondary", danger="danger", success="success")
    discord.ui.TextInput = type("TextInput", (), {})
    discord.ui.Modal = type("Modal", (), {})
    discord.ui.View = type("View", (), {})
    discord.ui.Select = type("Select", (), {})
    discord.ui.SelectOption = type("SelectOption", (), {})
    discord.ui.button = lambda **kw: (lambda fn: fn)
    discord.TextStyle = types.SimpleNamespace(short="short", paragraph="paragraph")
    discord.Interaction = type("Interaction", (), {})
    discord.Embed = type("Embed", (), {})
    discord.File = type("File", (), {})
    discord.ext = types.ModuleType("discord.ext")
    discord.ext.commands = types.ModuleType("discord.ext.commands")
    discord.ext.commands.Bot = type("Bot", (), {"__init__": lambda self, **kw: None})
    sys.modules["discord"] = discord
    sys.modules["discord.ui"] = discord.ui
    sys.modules["discord.ext"] = discord.ext
    sys.modules["discord.ext.commands"] = discord.ext.commands

import pytest


@pytest.fixture
def main_module(tmp_path):
    """Reload `main` and re-point the SqliteDatabase at a tmp file.

    `main.db = SqliteDatabase(DB_PATH)` is bound at import time, and the
    `Metadata`/`Ticket` model classes capture that database via their
    `class Meta: database = db`. Replacing `main.db` after import does
    NOT propagate to the models — so we use Peewee's `db.init(path)`
    which re-opens the same Database object on a different file. The
    model classes still hold the right reference; the on-disk location
    is what changes.
    """
    db_path = tmp_path / "tickets.db"
    if "main" in sys.modules:
        del sys.modules["main"]
    import main
    # Re-point the existing DB at a tmp file. Don't touch CONFIG — the
    # production token is harmless in tests because we never call
    # bot.run(). This also avoids opening the real config on Windows
    # where ConfigPath differs.
    main.db.init(str(db_path))
    return main


@pytest.fixture
def sql_db(main_module):
    """Create the Metadata + Ticket tables and yield the module."""
    main_module.init_db()
    # Replace CONFIG so tests don't read the production token / channel IDs.
    main_module.CONFIG = {
        "token": "test-token",
        "guild_id": 12345,
        "panel_channel_id": 67890,
        "transcript_channel_id": 11111,
        "staff_role_ids": [99999],
        "ticket_types": [
            {"id": "tech", "name": "Техническая поддержка", "category_id": 44444, "fields": []},
            {"id": "billing", "name": "Биллинг", "category_id": 55555, "fields": [
                {"id": "amount", "label": "Сумма", "required": True}
            ]},
        ],
    }
    yield main_module
    # tmp_path cleanup is automatic.
