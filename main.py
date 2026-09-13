import os
import json
import asyncio
import datetime
from io import BytesIO
from typing import Optional, Dict, Any

import discord
from discord.ext import commands
from peewee import (
    SqliteDatabase,
    Model,
    CharField,
    TextField,
    BigIntegerField,
    DateTimeField,
    AutoField,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DB_PATH = os.path.join(BASE_DIR, "tickets.db")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

db = SqliteDatabase(DB_PATH)


class BaseModel(Model):
    class Meta:
        database = db


class Metadata(BaseModel):
    key = CharField(primary_key=True)
    value = TextField()


class Ticket(BaseModel):
    ticket_id = AutoField()
    channel_id = BigIntegerField(unique=True)
    guild_id = BigIntegerField()
    user_id = BigIntegerField()
    ticket_type = CharField()
    fields_json = TextField(default="{}")
    status = CharField(default="open")
    created_at = DateTimeField(default=lambda: datetime.datetime.now(datetime.timezone.utc))
    closed_at = DateTimeField(null=True)
    closed_by = BigIntegerField(null=True)


def init_db():
    db.connect(reuse_if_open=True)
    db.create_tables([Metadata, Ticket])


def get_meta(key: str) -> Optional[str]:
    try:
        item = Metadata.get_or_none(Metadata.key == key)
        return item.value if item else None
    except Exception:
        return None


def set_meta(key: str, value: str):
    Metadata.insert(key=key, value=value).on_conflict(
        conflict_target=[Metadata.key],
        update={Metadata.value: value}
    ).execute()


def create_ticket_record(channel_id: int, guild_id: int, user_id: int, ticket_type: str, fields: dict) -> int:
    record = Ticket.create(
        channel_id=channel_id,
        guild_id=guild_id,
        user_id=user_id,
        ticket_type=ticket_type,
        fields_json=json.dumps(fields, ensure_ascii=False),
        status="open",
        created_at=datetime.datetime.now(datetime.timezone.utc)
    )
    return record.ticket_id


def get_ticket_by_channel(channel_id: int) -> Optional[Dict[str, Any]]:
    try:
        rec = Ticket.get_or_none(Ticket.channel_id == channel_id)
        if not rec:
            return None
        return {
            "ticket_id": rec.ticket_id,
            "channel_id": rec.channel_id,
            "guild_id": rec.guild_id,
            "user_id": rec.user_id,
            "ticket_type": rec.ticket_type,
            "fields": json.loads(rec.fields_json or "{}"),
            "status": rec.status,
            "created_at": rec.created_at,
            "closed_at": rec.closed_at,
            "closed_by": rec.closed_by,
        }
    except Exception:
        return None


def close_ticket_record(channel_id: int, closed_by: int):
    try:
        Ticket.update(
            status="closed",
            closed_at=datetime.datetime.now(datetime.timezone.utc),
            closed_by=closed_by
        ).where(Ticket.channel_id == channel_id).execute()
    except Exception as e:
        print(f"Error updating ticket record: {e}")


def find_ticket_type_cfg(type_id: str) -> Optional[dict]:
    for tt in CONFIG.get("ticket_types", []):
        if tt["id"] == type_id:
            return tt
    return None


async def build_ticket_channel(interaction: discord.Interaction, type_cfg: dict, answers: dict):
    guild = interaction.guild
    user = interaction.user
    if not guild or not isinstance(user, discord.Member):
        return

    category = guild.get_channel(type_cfg["category_id"])
    if not category or not isinstance(category, discord.CategoryChannel):
        category = None

    # Determine ticket ID
    last_ticket = Ticket.select().order_by(Ticket.ticket_id.desc()).first()
    next_id = (last_ticket.ticket_id + 1) if last_ticket else 1
    channel_name = f"тикет-{next_id:04d}"

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
            manage_channels=True,
            manage_messages=True
        )
    }

    for role_id in CONFIG.get("staff_role_ids", []):
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True
            )

    try:
        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"Тикет #{next_id:04d} | Автор: {user.name} ({user.id}) | Тип: {type_cfg['name']}"
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Ошибка создания канала: {e}", ephemeral=True)
        return

    ticket_num = create_ticket_record(channel.id, guild.id, user.id, type_cfg["id"], answers)

    embed = discord.Embed(
        title=f"Тикет #{ticket_num:04d} — {type_cfg['name']}",
        description=f"Здравствуйте, {user.mention}!\nСлужба поддержки скоро ответит вам.\nОпишите вашу проблему подробно.",
        color=discord.Color.from_rgb(43, 100, 215),
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="Создатель тикета", value=f"{user.mention} (`{user.name}`)", inline=True)
    embed.add_field(name="Категория", value=type_cfg["name"], inline=True)

    if answers:
        for label, val in answers.items():
            val_str = str(val).strip() or "—"
            if len(val_str) > 1024:
                val_str = val_str[:1020] + "..."
            embed.add_field(name=label, value=val_str, inline=False)

    embed.set_footer(text="Mars Host • Служба поддержки", icon_url=guild.icon.url if guild.icon else None)

    await channel.send(content=user.mention, embed=embed, view=TicketControlView())
    await interaction.followup.send(f"✅ Ваш тикет создан: {channel.mention}", ephemeral=True)


class TicketModal(discord.ui.Modal):
    def __init__(self, type_cfg: dict):
        super().__init__(title=type_cfg["name"][:45])
        self.type_cfg = type_cfg
        self.inputs = []

        for f in type_cfg.get("fields", []):
            item = discord.ui.TextInput(
                label=f["label"][:45],
                placeholder=f.get("placeholder", "")[:100],
                required=f.get("required", True),
                style=discord.TextStyle.short if len(f.get("placeholder", "")) < 40 else discord.TextStyle.paragraph,
                max_length=500
            )
            self.inputs.append((f["label"], item))
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        answers = {label: item.value for label, item in self.inputs}
        await build_ticket_channel(interaction, self.type_cfg, answers)


class ConfirmCreationView(discord.ui.View):
    def __init__(self, type_cfg: dict):
        super().__init__(timeout=120)
        self.type_cfg = type_cfg

    @discord.ui.button(label="Создать тикет", style=discord.ButtonStyle.success, emoji="📩")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        self.stop()
        await build_ticket_channel(interaction, self.type_cfg, {})


class TicketSelect(discord.ui.Select):
    def __init__(self):
        options = []
        for tt in CONFIG.get("ticket_types", []):
            options.append(discord.SelectOption(
                label=tt["name"][:100],
                value=tt["id"],
                description=tt.get("description", "")[:100],
                emoji="🎫"
            ))
        super().__init__(
            placeholder="Выберите категорию тикета...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="persistent:ticket_type_select"
        )

    async def callback(self, interaction: discord.Interaction):
        selected_id = self.values[0]
        type_cfg = find_ticket_type_cfg(selected_id)
        if not type_cfg:
            await interaction.response.send_message("❌ Неизвестная категория тикета.", ephemeral=True)
            return

        fields = type_cfg.get("fields", [])
        if fields:
            modal = TicketModal(type_cfg)
            await interaction.response.send_modal(modal)
        else:
            view = ConfirmCreationView(type_cfg)
            await interaction.response.send_message(
                f"Вы выбрали **{type_cfg['name']}**.\nНажмите кнопку ниже для подтверждения создания тикета.",
                view=view,
                ephemeral=True
            )


class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelect())

    @discord.ui.button(
        label="Регламент и правила поддержки",
        style=discord.ButtonStyle.secondary,
        emoji="📜",
        custom_id="persistent:rules_button",
        row=1
    )
    async def show_rules(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🪐 Mars Host — Регламент технической поддержки",
            description=(
                "**Пожалуйста, ознакомьтесь с правилами перед открытием тикета:**\n\n"
                "**1. Сфера компетенции поддержки:**\n"
                "• Устранение сбоев запуска и базовых ошибок серверов\n"
                "• Локализация конфликтов ядер и плагинов\n"
                "• Диагностика сетевой доступности нод и панели\n"
                "*Примечание: глубокая настройка чужих сборок и разработка плагинов поддержкой не выполняются.*\n\n"
                "**2. Культура общения:**\n"
                "• Общайтесь уважительно: без токсичности, мата и оскорблений.\n"
                "• Запрещены спам-пинги команды («срочно», «ответьте»). При нарушении — закрытие тикета или мут.\n\n"
                "**3. Корректность категории:**\n"
                "• Выбирайте категорию строго по теме вашей проблемы.\n\n"
                "**4. Сроки рассмотрения:**\n"
                "• Базовые вопросы — в течение **6 часов** в рабочее время.\n"
                "• Сложные инциденты и диагностика нод — до **24 часов**.\n\n"
                "**5. Требования к информации:**\n"
                "• Указывайте ядро, версию Java, ссылку на сервер в панели, crash-log или ошибку.\n"
                "• Сообщения формата «ничего не работает» без логов закрываются без ответа.\n\n"
                "**6. Авторство обращения:**\n"
                "• Тикет открывает исключительно владелец сервера.\n"
                "• Если настройкой занимается доверенный технический администратор, владелец должен лично запросить в тикете его добавление.\n\n"
                "**7. Один вопрос — один тикет:**\n"
                "• Для каждой новой несвязанной проблемы создавайте отдельный тикет."
            ),
            color=discord.Color.from_rgb(230, 74, 25)
        )
        embed.set_footer(text="Mars Host • Соблюдение регламента ускоряет помощь")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class CloseConfirmModal(discord.ui.Modal, title="Закрытие тикета"):
    reason = discord.ui.TextInput(
        label="Причина закрытия (необязательно)",
        placeholder="Укажите причину закрытия тикета...",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return

        ticket_data = get_ticket_by_channel(channel.id)
        reason_text = self.reason.value.strip() or "Не указана"

        await channel.send(f"🔒 Тикет закрывается пользователем {interaction.user.mention}...\nПричина: *{reason_text}*")

        messages = []
        async for m in channel.history(limit=5000, oldest_first=True):
            ts = m.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            author = f"{m.author.name} ({m.author.id})"
            content = m.clean_content or "[Вложения/Embed]"
            if m.attachments:
                att = " ".join(a.url for a in m.attachments)
                content += f" [Вложения: {att}]"
            messages.append(f"[{ts}] {author}: {content}")

        transcript_text = "\n".join(messages)
        transcript_bytes = transcript_text.encode("utf-8")

        close_ticket_record(channel.id, interaction.user.id)

        transcript_ch_id = CONFIG.get("transcript_channel_id")
        transcript_ch = interaction.guild.get_channel(transcript_ch_id) if transcript_ch_id else None

        ticket_id_str = f"#{ticket_data['ticket_id']:04d}" if ticket_data else channel.name
        user_mention = f"<@{ticket_data['user_id']}>" if ticket_data else "Неизвестен"
        ticket_type = ticket_data.get("ticket_type", "—") if ticket_data else "—"

        type_cfg = find_ticket_type_cfg(ticket_type)
        type_name = type_cfg["name"] if type_cfg else ticket_type

        trans_embed = discord.Embed(
            title=f"Лог тикета {ticket_id_str}",
            color=discord.Color.dark_gray(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        trans_embed.add_field(name="Канал", value=f"`{channel.name}`", inline=True)
        trans_embed.add_field(name="Создатель", value=user_mention, inline=True)
        trans_embed.add_field(name="Категория", value=type_name, inline=True)
        trans_embed.add_field(name="Закрыл", value=f"{interaction.user.mention} (`{interaction.user.name}`)", inline=True)
        trans_embed.add_field(name="Причина", value=reason_text, inline=True)
        trans_embed.add_field(name="Сообщений", value=str(len(messages)), inline=True)

        if ticket_data and ticket_data.get("fields"):
            fields_summary = []
            for k, v in ticket_data["fields"].items():
                fields_summary.append(f"• **{k}:** {v}")
            trans_embed.add_field(name="Анкета тикета", value="\n".join(fields_summary)[:1024], inline=False)

        file = discord.File(fp=BytesIO(transcript_bytes), filename=f"transcript-{channel.name}.txt")

        if transcript_ch and isinstance(transcript_ch, discord.TextChannel):
            try:
                await transcript_ch.send(embed=trans_embed, file=file)
            except Exception as e:
                print(f"Error sending transcript to channel: {e}")

        await asyncio.sleep(4)
        try:
            await channel.delete(reason=f"Тикет закрыт: {reason_text}")
        except Exception as e:
            print(f"Error deleting channel: {e}")


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Закрыть тикет", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="persistent:close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CloseConfirmModal())


class TicketBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            proxy=CONFIG.get("proxy")
        )

    async def setup_hook(self):
        init_db()
        self.add_view(PanelView())
        self.add_view(TicketControlView())

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        guild = self.get_guild(CONFIG["guild_id"])
        if not guild:
            print(f"Guild {CONFIG['guild_id']} not found!")
            return

        panel_ch = guild.get_channel(CONFIG["panel_channel_id"])
        if not panel_ch or not isinstance(panel_ch, discord.TextChannel):
            print(f"Panel channel {CONFIG['panel_channel_id']} not found!")
            return

        saved_msg_id = get_meta("panel_message_id")
        panel_message = None
        if saved_msg_id:
            try:
                panel_message = await panel_ch.fetch_message(int(saved_msg_id))
            except Exception:
                panel_message = None

        embed = discord.Embed(
            title="🪐 Поддержка Mars Host",
            description=(
                "Добро пожаловать в центр поддержки **Mars Host**!\n\n"
                "Чтобы создать тикет, выберите подходящую категорию в выпадающем меню ниже.\n"
                "После выбора заполните открывшуюся анкету — "
                "это поможет специалистам быстрее решить ваш вопрос.\n\n"
                "**Категории обращений:**\n"
                "🛠️ **Техническая поддержка** — вопросы работы серверов и хостинга\n"
                "🔧 **Решение проблем** — устранение неполадок с серверами\n"
                "💼 **Продажи** — спонсорство, партнёрство и предложения\n"
                "💳 **Пополнение кредитов** — зачисление баланса за оплату\n"
                "❓ **Иное** — любые другие вопросы поддержки"
            ),
            color=discord.Color.from_rgb(52, 120, 246)
        )
        embed.set_footer(text="Mars Host • Выберите категорию ниже для создания тикета")
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        if panel_message:
            try:
                await panel_message.edit(embed=embed, view=PanelView())
                print(f"Updated existing panel message {panel_message.id}")
            except Exception as e:
                print(f"Failed to edit existing panel message: {e}")
                panel_message = None

        if not panel_message:
            new_msg = await panel_ch.send(embed=embed, view=PanelView())
            set_meta("panel_message_id", str(new_msg.id))
            print(f"Posted new panel message {new_msg.id}")


if __name__ == "__main__":
    bot = TicketBot()
    bot.run(CONFIG["token"])
