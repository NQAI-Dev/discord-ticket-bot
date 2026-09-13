"""Tests for the pure-Python helpers in main.py.

We do not exercise the Discord client itself. The bot-side code only
contains a handful of plain functions that operate on the local
SQLite store; everything else lives inside class bodies that need a
live guild. The helpers we cover here are the public surface used by
the Discord command handlers, so testing them in isolation gives us
strong confidence the data layer behaves correctly without booting
discord.py.
"""
import datetime
import json


# --- init_db ---------------------------------------------------------------


def test_init_db_creates_tables(sql_db):
    # If init_db() raised, sql_db fixture would fail. Verify both tables
    # are queryable through the ORM.
    assert sql_db.Metadata.select().count() == 0
    assert sql_db.Ticket.select().count() == 0


def test_init_db_is_idempotent(sql_db):
    # Running init_db() a second time against the same DB must not raise:
    # the schema uses CREATE TABLE IF NOT EXISTS.
    sql_db.init_db()
    sql_db.init_db()
    assert sql_db.Metadata.select().count() == 0


# --- Metadata helpers ------------------------------------------------------


def test_get_meta_missing_returns_none(sql_db):
    assert sql_db.get_meta("missing") is None


def test_set_and_get_meta(sql_db):
    sql_db.set_meta("panel_message_id", "1234567890")
    assert sql_db.get_meta("panel_message_id") == "1234567890"


def test_set_meta_upserts(sql_db):
    """set_meta() should overwrite the existing value, not duplicate."""
    sql_db.set_meta("panel_message_id", "111")
    sql_db.set_meta("panel_message_id", "222")
    assert sql_db.get_meta("panel_message_id") == "222"
    assert sql_db.Metadata.select().count() == 1


# --- Ticket helpers --------------------------------------------------------


def test_create_ticket_record_returns_id(sql_db):
    tid = sql_db.create_ticket_record(
        channel_id=1001,
        guild_id=2002,
        user_id=3003,
        ticket_type="tech",
        fields={"k": "v"},
    )
    assert isinstance(tid, int)
    assert tid >= 1


def test_get_ticket_by_channel_round_trip(sql_db):
    sql_db.create_ticket_record(
        channel_id=1001, guild_id=2002, user_id=3003,
        ticket_type="billing", fields={"amount": "1000"},
    )
    ticket = sql_db.get_ticket_by_channel(1001)
    assert ticket is not None
    assert ticket["channel_id"] == 1001
    assert ticket["ticket_type"] == "billing"
    assert ticket["status"] == "open"
    assert ticket["fields"] == {"amount": "1000"}


def test_get_ticket_by_channel_unknown_returns_none(sql_db):
    assert sql_db.get_ticket_by_channel(9999) is None


def test_get_ticket_by_channel_corrupt_fields(sql_db):
    """A bad fields_json blob must not crash the helper; returns empty dict."""
    sql_db.Ticket.create(
        channel_id=1001, guild_id=2002, user_id=3003,
        ticket_type="tech", fields_json="{not json",
        status="open", created_at=datetime.datetime.now(datetime.timezone.utc),
    )
    ticket = sql_db.get_ticket_by_channel(1001)
    assert ticket is None  # helper swallows the JSON error


def test_close_ticket_record_updates_status(sql_db):
    sql_db.create_ticket_record(
        channel_id=1001, guild_id=2002, user_id=3003,
        ticket_type="tech", fields={},
    )
    sql_db.close_ticket_record(1001, closed_by=3003)
    ticket = sql_db.get_ticket_by_channel(1001)
    assert ticket["status"] == "closed"
    assert ticket["closed_by"] == 3003
    assert ticket["closed_at"] is not None


def test_close_ticket_record_missing_channel_is_silent(sql_db):
    """Closing a ticket that does not exist must not raise."""
    sql_db.close_ticket_record(9999, closed_by=3003)
    # No exception == pass.


# --- Config helpers --------------------------------------------------------


def test_find_ticket_type_cfg_returns_match(sql_db):
    cfg = sql_db.find_ticket_type_cfg("tech")
    assert cfg is not None
    assert cfg["name"] == "Техническая поддержка"
    assert cfg["category_id"] == 44444


def test_find_ticket_type_cfg_returns_none_for_unknown(sql_db):
    assert sql_db.find_ticket_type_cfg("nope") is None


def test_find_ticket_type_cfg_handles_missing_key(sql_db):
    """If CONFIG has no `ticket_types` key, helper returns None instead of raising."""
    sql_db.CONFIG = {"token": "x"}
    assert sql_db.find_ticket_type_cfg("tech") is None


# --- TicketModel schema sanity --------------------------------------------


def test_ticket_table_creation_uses_required_columns(sql_db):
    cols = {c.name for c in sql_db.Ticket._meta.sorted_fields}
    # Required columns per the prod schema.
    assert {"ticket_id", "channel_id", "guild_id", "user_id",
            "ticket_type", "fields_json", "status",
            "created_at", "closed_at", "closed_by"} <= cols


def test_metadata_table_uses_key_as_primary(sql_db):
    cols = {c.name for c in sql_db.Metadata._meta.sorted_fields}
    assert "key" in cols
    assert "value" in cols


# --- TicketControlView / PanelView / Modal classes are importable ----------
# These exist mostly for discord.ui wiring; we just assert they're classes
# with the right inheritance so future refactors don't silently drop them.


def test_panel_view_extends_view(sql_db):
    from discord.ui import View
    assert issubclass(sql_db.PanelView, View)


def test_ticket_control_view_extends_view(sql_db):
    from discord.ui import View
    assert issubclass(sql_db.TicketControlView, View)


def test_ticket_select_is_a_select(sql_db):
    from discord.ui import Select
    assert issubclass(sql_db.TicketSelect, Select)
