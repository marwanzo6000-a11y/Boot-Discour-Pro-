# -*- coding: utf-8 -*-
import os
import re
import time
import sqlite3
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict, deque
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    raise RuntimeError('DISCORD_TOKEN is missing from .env')
DATABASE_PATH = 'data/bot.db'
DEFAULT_WELCOME_MESSAGE = '👋 أهلاً بك {member} في **{server}**!\nنتمنى لك وقتًا ممتعًا معنا ❤️'
DEFAULT_LEAVE_MESSAGE = '👋 غادر **{username}** السيرفر.'
MAX_MENTIONS = 5
SPAM_MESSAGE_LIMIT = 6
SPAM_WINDOW_SECONDS = 8
TICKET_CATEGORY_NAME = 'Tickets'
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(name)s | %(message)s')
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True

def can_manage_member(interaction: discord.Interaction, member: discord.Member):
    guild = interaction.guild
    moderator = interaction.user
    if member.id == moderator.id:
        return (False, '❌ لا يمكنك تنفيذ الإجراء على نفسك.')
    if member.id == guild.owner_id:
        return (False, '❌ لا يمكنك تنفيذ الإجراء على مالك السيرفر.')
    if moderator.id != guild.owner_id and member.top_role >= moderator.top_role:
        return (False, '❌ لا يمكنك التعامل مع عضو رتبته مساوية أو أعلى من رتبتك.')
    bot_member = guild.me
    if bot_member and member.top_role >= bot_member.top_role:
        return (False, '❌ رتبة البوت يجب أن تكون أعلى من رتبة العضو.')
    return (True, None)
DATABASE_PATH = 'data/bot.db'

class Database:

    def __init__(self, path=DATABASE_PATH):
        self.path = path
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self):
        with self._connect() as conn:
            conn.execute('\n                CREATE TABLE IF NOT EXISTS guild_settings (\n                    guild_id INTEGER PRIMARY KEY,\n                    welcome_channel_id INTEGER,\n                    leave_channel_id INTEGER,\n                    welcome_message TEXT,\n                    leave_message TEXT,\n                    log_channel_id INTEGER,\n                    autorole_id INTEGER,\n                    antispam_enabled INTEGER DEFAULT 1,\n                    antilink_enabled INTEGER DEFAULT 0,\n                    antimention_enabled INTEGER DEFAULT 1,\n                    tickets_enabled INTEGER DEFAULT 1,\n                    ticket_category_id INTEGER,\n                    ticket_support_role_id INTEGER,\n                    created_at TEXT NOT NULL\n                )\n            ')
            conn.execute('\n                CREATE TABLE IF NOT EXISTS warnings (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    guild_id INTEGER NOT NULL,\n                    user_id INTEGER NOT NULL,\n                    moderator_id INTEGER NOT NULL,\n                    reason TEXT NOT NULL,\n                    created_at TEXT NOT NULL\n                )\n            ')
            conn.execute('\n                CREATE TABLE IF NOT EXISTS tickets (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    guild_id INTEGER NOT NULL,\n                    channel_id INTEGER UNIQUE NOT NULL,\n                    user_id INTEGER NOT NULL,\n                    created_at TEXT NOT NULL,\n                    closed_at TEXT\n                )\n            ')
            conn.execute('\n                CREATE INDEX IF NOT EXISTS idx_warnings_guild_user\n                ON warnings(guild_id, user_id)\n            ')
            conn.execute('\n                CREATE INDEX IF NOT EXISTS idx_tickets_guild_user\n                ON tickets(guild_id, user_id)\n            ')

    def ensure_guild(self, guild_id):
        with self._connect() as conn:
            conn.execute('INSERT OR IGNORE INTO guild_settings (guild_id, created_at) VALUES (?, ?)', (guild_id, datetime.now(timezone.utc).isoformat()))

    def get_guild_settings(self, guild_id):
        self.ensure_guild(guild_id)
        with self._connect() as conn:
            row = conn.execute('SELECT * FROM guild_settings WHERE guild_id = ?', (guild_id,)).fetchone()
        return dict(row) if row else None

    def update_guild_setting(self, guild_id, setting, value):
        allowed = {'welcome_channel_id', 'leave_channel_id', 'welcome_message', 'leave_message', 'log_channel_id', 'autorole_id', 'antispam_enabled', 'antilink_enabled', 'antimention_enabled', 'tickets_enabled', 'ticket_category_id', 'ticket_support_role_id'}
        if setting not in allowed:
            raise ValueError('Invalid setting')
        self.ensure_guild(guild_id)
        with self._connect() as conn:
            conn.execute(f'UPDATE guild_settings SET {setting} = ? WHERE guild_id = ?', (value, guild_id))

    def add_warning(self, guild_id, user_id, moderator_id, reason):
        with self._connect() as conn:
            cur = conn.execute('INSERT INTO warnings\n                   (guild_id,user_id,moderator_id,reason,created_at)\n                   VALUES (?,?,?,?,?)', (guild_id, user_id, moderator_id, reason, datetime.now(timezone.utc).isoformat()))
            return cur.lastrowid

    def get_warnings(self, guild_id, user_id):
        with self._connect() as conn:
            rows = conn.execute('SELECT * FROM warnings\n                   WHERE guild_id=? AND user_id=?\n                   ORDER BY id DESC', (guild_id, user_id)).fetchall()
        return [dict(r) for r in rows]

    def remove_warning(self, warning_id, guild_id):
        with self._connect() as conn:
            cur = conn.execute('DELETE FROM warnings WHERE id=? AND guild_id=?', (warning_id, guild_id))
            return cur.rowcount > 0

    def clear_warnings(self, guild_id, user_id):
        with self._connect() as conn:
            cur = conn.execute('DELETE FROM warnings WHERE guild_id=? AND user_id=?', (guild_id, user_id))
            return cur.rowcount

    def add_ticket(self, guild_id, channel_id, user_id):
        with self._connect() as conn:
            conn.execute('INSERT OR REPLACE INTO tickets\n                   (guild_id,channel_id,user_id,created_at,closed_at)\n                   VALUES (?,?,?,?,NULL)', (guild_id, channel_id, user_id, datetime.now(timezone.utc).isoformat()))

    def get_open_ticket_for_user(self, guild_id, user_id):
        with self._connect() as conn:
            row = conn.execute('SELECT * FROM tickets\n                   WHERE guild_id=? AND user_id=? AND closed_at IS NULL\n                   ORDER BY id DESC LIMIT 1', (guild_id, user_id)).fetchone()
        return dict(row) if row else None

    def get_ticket(self, channel_id):
        with self._connect() as conn:
            row = conn.execute('SELECT * FROM tickets WHERE channel_id=?', (channel_id,)).fetchone()
        return dict(row) if row else None

    def close_ticket(self, channel_id):
        with self._connect() as conn:
            conn.execute('UPDATE tickets SET closed_at=?\n                   WHERE channel_id=? AND closed_at IS NULL', (datetime.now(timezone.utc).isoformat(), channel_id))
db = Database()

class Welcome(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def render(template, member):
        return template.format(member=member.mention, username=member.display_name, server=member.guild.name, count=member.guild.member_count)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        settings = db.get_guild_settings(member.guild.id)
        channel_id = settings.get('welcome_channel_id')
        if not channel_id:
            return
        channel = member.guild.get_channel(channel_id)
        if not channel:
            return
        message = self.render(settings.get('welcome_message') or DEFAULT_WELCOME_MESSAGE, member)
        embed = discord.Embed(title='👋 عضو جديد!', description=message, color=discord.Color.green())
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass
        role_id = settings.get('autorole_id')
        role = member.guild.get_role(role_id) if role_id else None
        if role:
            try:
                await member.add_roles(role, reason='Autorole')
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        settings = db.get_guild_settings(member.guild.id)
        channel_id = settings.get('leave_channel_id')
        if not channel_id:
            return
        channel = member.guild.get_channel(channel_id)
        if not channel:
            return
        message = self.render(settings.get('leave_message') or DEFAULT_LEAVE_MESSAGE, member)
        embed = discord.Embed(title='👋 عضو غادر', description=message, color=discord.Color.orange())
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @app_commands.command(name='welcome', description='تحديد روم الترحيب')
    @app_commands.describe(channel='الروم')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome(self, interaction: discord.Interaction, channel: discord.TextChannel):
        db.update_guild_setting(interaction.guild.id, 'welcome_channel_id', channel.id)
        await interaction.response.send_message(f'✅ روم الترحيب: {channel.mention}', ephemeral=True)

    @app_commands.command(name='welcome-message', description='تغيير رسالة الترحيب')
    @app_commands.describe(message='الرسالة')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_message(self, interaction: discord.Interaction, message: str):
        db.update_guild_setting(interaction.guild.id, 'welcome_message', message)
        await interaction.response.send_message('✅ تم حفظ رسالة الترحيب.', ephemeral=True)

    @app_commands.command(name='leave-channel', description='تحديد روم المغادرة')
    @app_commands.describe(channel='الروم')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def leave_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        db.update_guild_setting(interaction.guild.id, 'leave_channel_id', channel.id)
        await interaction.response.send_message(f'✅ روم المغادرة: {channel.mention}', ephemeral=True)

    @app_commands.command(name='autorole', description='تحديد الرتبة التلقائية')
    @app_commands.describe(role='الرتبة')
    @app_commands.checks.has_permissions(manage_roles=True)
    async def autorole(self, interaction: discord.Interaction, role: discord.Role):
        if role >= interaction.guild.me.top_role:
            return await interaction.response.send_message('❌ رتبة البوت يجب أن تكون أعلى من الرتبة المحددة.', ephemeral=True)
        db.update_guild_setting(interaction.guild.id, 'autorole_id', role.id)
        await interaction.response.send_message(f'✅ الرتبة التلقائية: {role.mention}', ephemeral=True)

class Moderation(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='ban', description='حظر عضو')
    @app_commands.describe(member='العضو', reason='السبب')
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: str='لم يتم تحديد سبب'):
        ok, msg = can_manage_member(interaction, member)
        if not ok:
            return await interaction.response.send_message(msg, ephemeral=True)
        await member.ban(reason=reason)
        await interaction.response.send_message(f'🔨 تم حظر {member.mention}.\n**السبب:** {reason}')

    @app_commands.command(name='kick', description='طرد عضو')
    @app_commands.describe(member='العضو', reason='السبب')
    @app_commands.checks.has_permissions(kick_members=True)
    @app_commands.checks.bot_has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str='لم يتم تحديد سبب'):
        ok, msg = can_manage_member(interaction, member)
        if not ok:
            return await interaction.response.send_message(msg, ephemeral=True)
        await member.kick(reason=reason)
        await interaction.response.send_message(f'👢 تم طرد {member.mention}.\n**السبب:** {reason}')

    @app_commands.command(name='timeout', description='تايم أوت لعضو')
    @app_commands.describe(member='العضو', minutes='المدة بالدقائق', reason='السبب')
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    async def timeout(self, interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str='لم يتم تحديد سبب'):
        if not 1 <= minutes <= 40320:
            return await interaction.response.send_message('❌ المدة يجب أن تكون بين دقيقة و28 يومًا.', ephemeral=True)
        ok, msg = can_manage_member(interaction, member)
        if not ok:
            return await interaction.response.send_message(msg, ephemeral=True)
        await member.timeout(timedelta(minutes=minutes), reason=reason)
        await interaction.response.send_message(f'⏱️ تم إعطاء {member.mention} تايم أوت لمدة **{minutes} دقيقة**.\n**السبب:** {reason}')

    @app_commands.command(name='untimeout', description='إزالة تايم أوت')
    @app_commands.describe(member='العضو')
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member):
        ok, msg = can_manage_member(interaction, member)
        if not ok:
            return await interaction.response.send_message(msg, ephemeral=True)
        await member.timeout(None, reason=f'Removed by {interaction.user}')
        await interaction.response.send_message(f'🔓 تم إزالة التايم أوت عن {member.mention}.')

    @app_commands.command(name='unban', description='إلغاء حظر باستخدام ID')
    @app_commands.describe(user_id='ID المستخدم')
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, user_id: str):
        try:
            user = await self.bot.fetch_user(int(user_id))
            await interaction.guild.unban(user, reason=f'Unban by {interaction.user}')
        except (ValueError, discord.NotFound, discord.HTTPException):
            return await interaction.response.send_message('❌ لم أستطع إلغاء الحظر لهذا ID.', ephemeral=True)
        await interaction.response.send_message(f'🔓 تم إلغاء حظر **{user}**.')

    @app_commands.command(name='clear', description='حذف رسائل')
    @app_commands.describe(amount='1 إلى 100')
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.bot_has_permissions(manage_messages=True)
    async def clear(self, interaction: discord.Interaction, amount: int):
        if not 1 <= amount <= 100:
            return await interaction.response.send_message('❌ العدد يجب أن يكون بين 1 و100.', ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(f'🧹 تم حذف **{len(deleted)}** رسالة.', ephemeral=True)

    @app_commands.command(name='lock', description='قفل الروم')
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.checks.bot_has_permissions(manage_channels=True)
    async def lock(self, interaction: discord.Interaction):
        overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = False
        await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.response.send_message('🔒 تم قفل الروم.')

    @app_commands.command(name='unlock', description='فتح الروم')
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.checks.bot_has_permissions(manage_channels=True)
    async def unlock(self, interaction: discord.Interaction):
        overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = True
        await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.response.send_message('🔓 تم فتح الروم.')

    @app_commands.command(name='slowmode', description='تحديد Slowmode')
    @app_commands.describe(seconds='0 إلى 21600')
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.checks.bot_has_permissions(manage_channels=True)
    async def slowmode(self, interaction: discord.Interaction, seconds: int):
        if not 0 <= seconds <= 21600:
            return await interaction.response.send_message('❌ القيمة يجب أن تكون بين 0 و21600.', ephemeral=True)
        await interaction.channel.edit(slowmode_delay=seconds)
        await interaction.response.send_message(f'🐌 تم ضبط Slowmode على **{seconds} ثانية**.')

    async def cog_app_command_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            msg = '❌ ليس لديك الصلاحية اللازمة.'
        elif isinstance(error, app_commands.BotMissingPermissions):
            msg = '❌ البوت لا يملك الصلاحية اللازمة.'
        else:
            print(f'[Moderation] {error!r}')
            msg = '❌ حدث خطأ أثناء تنفيذ الأمر.'
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

class Warnings(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='warn', description='تحذير عضو')
    @app_commands.describe(member='العضو', reason='سبب التحذير')
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str):
        ok, msg = can_manage_member(interaction, member)
        if not ok:
            return await interaction.response.send_message(msg, ephemeral=True)
        warning_id = db.add_warning(interaction.guild.id, member.id, interaction.user.id, reason)
        await interaction.response.send_message(f'⚠️ تم تحذير {member.mention}.\n**رقم التحذير:** `{warning_id}`\n**السبب:** {reason}')

    @app_commands.command(name='warnings', description='عرض تحذيرات عضو')
    @app_commands.describe(member='العضو')
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warnings(self, interaction: discord.Interaction, member: discord.Member):
        rows = db.get_warnings(interaction.guild.id, member.id)
        if not rows:
            return await interaction.response.send_message(f'✅ لا توجد تحذيرات لـ {member.mention}.', ephemeral=True)
        embed = discord.Embed(title=f'⚠️ تحذيرات {member}', color=discord.Color.orange())
        for row in rows[:10]:
            embed.add_field(name=f"#{row['id']} — {row['reason']}", value=f"المشرف: <@{row['moderator_id']}>", inline=False)
        if len(rows) > 10:
            embed.set_footer(text=f'إجمالي التحذيرات: {len(rows)}')
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name='remove-warn', description='حذف تحذير')
    @app_commands.describe(warning_id='رقم التحذير')
    @app_commands.checks.has_permissions(moderate_members=True)
    async def remove_warn(self, interaction: discord.Interaction, warning_id: int):
        if not db.remove_warning(warning_id, interaction.guild.id):
            return await interaction.response.send_message('❌ التحذير غير موجود.', ephemeral=True)
        await interaction.response.send_message(f'✅ تم حذف التحذير **#{warning_id}**.')

    @app_commands.command(name='clear-warnings', description='حذف كل تحذيرات عضو')
    @app_commands.describe(member='العضو')
    @app_commands.checks.has_permissions(moderate_members=True)
    async def clear_warnings(self, interaction: discord.Interaction, member: discord.Member):
        count = db.clear_warnings(interaction.guild.id, member.id)
        await interaction.response.send_message(f'🧹 تم حذف **{count}** تحذيرًا عن {member.mention}.')

class Logs(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    async def send_log(self, guild, title, description, color=discord.Color.blurple()):
        settings = db.get_guild_settings(guild.id)
        channel_id = settings.get('log_channel_id')
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if not channel:
            return
        embed = discord.Embed(title=title, description=description, color=color)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @app_commands.command(name='set-logs', description='تحديد روم اللوق')
    @app_commands.describe(channel='روم اللوق')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_logs(self, interaction: discord.Interaction, channel: discord.TextChannel):
        db.update_guild_setting(interaction.guild.id, 'log_channel_id', channel.id)
        await interaction.response.send_message(f'✅ تم تحديد {channel.mention} كروم Logs.', ephemeral=True)

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        await self.send_log(guild, '🔨 Ban', f'تم حظر **{user}** (`{user.id}`).', discord.Color.red())

    @commands.Cog.listener()
    async def on_member_unban(self, guild, user):
        await self.send_log(guild, '🔓 Unban', f'تم إلغاء حظر **{user}** (`{user.id}`).', discord.Color.green())

    @commands.Cog.listener()
    async def on_member_join(self, member):
        await self.send_log(member.guild, '📥 Member Join', f'دخل **{member}** (`{member.id}`).', discord.Color.green())

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        await self.send_log(member.guild, '📤 Member Leave', f'غادر **{member}** (`{member.id}`).', discord.Color.orange())

class CloseTicketView(discord.ui.View):

    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label='إغلاق التذكرة', style=discord.ButtonStyle.danger, emoji='🔒', custom_id='ticket_close_button')
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = db.get_ticket(interaction.channel.id)
        if not ticket:
            return await interaction.response.send_message('❌ هذه القناة ليست تذكرة.', ephemeral=True)
        is_owner = interaction.user.id == ticket['user_id']
        can_manage = interaction.user.guild_permissions.manage_channels
        if not is_owner and (not can_manage):
            return await interaction.response.send_message('❌ صاحب التذكرة أو الإدارة فقط يمكنهم إغلاقها.', ephemeral=True)
        db.close_ticket(interaction.channel.id)
        await interaction.response.send_message('🔒 سيتم إغلاق التذكرة...')
        await interaction.channel.delete(reason=f'Ticket closed by {interaction.user}')

class Tickets(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='ticket-setup', description='إنشاء لوحة التذاكر')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ticket_setup(self, interaction: discord.Interaction):
        embed = discord.Embed(title='🎫 الدعم الفني', description='اضغط الزر بالأسفل لفتح تذكرة خاصة مع الإدارة.', color=discord.Color.blurple())
        view = TicketOpenView(self.bot)
        await interaction.channel.send(embed=embed, view=view)
        await interaction.response.send_message('✅ تم إنشاء لوحة التذاكر.', ephemeral=True)

    @app_commands.command(name='ticket-category', description='تحديد كاتيجوري التذاكر')
    @app_commands.describe(category='Category')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ticket_category(self, interaction: discord.Interaction, category: discord.CategoryChannel):
        db.update_guild_setting(interaction.guild.id, 'ticket_category_id', category.id)
        await interaction.response.send_message(f'✅ Category التذاكر: **{category.name}**', ephemeral=True)

    @app_commands.command(name='ticket-support-role', description='تحديد رتبة الدعم')
    @app_commands.describe(role='رتبة الدعم')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ticket_support_role(self, interaction: discord.Interaction, role: discord.Role):
        db.update_guild_setting(interaction.guild.id, 'ticket_support_role_id', role.id)
        await interaction.response.send_message(f'✅ رتبة الدعم: {role.mention}', ephemeral=True)

class TicketOpenView(discord.ui.View):

    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label='فتح تذكرة', style=discord.ButtonStyle.success, emoji='🎫', custom_id='ticket_open_button')
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        settings = db.get_guild_settings(guild.id)
        existing = db.get_open_ticket_for_user(guild.id, interaction.user.id)
        if existing:
            channel = guild.get_channel(existing['channel_id'])
            if channel:
                return await interaction.response.send_message(f'❌ لديك تذكرة مفتوحة بالفعل: {channel.mention}', ephemeral=True)
        category_id = settings.get('ticket_category_id')
        category = guild.get_channel(category_id) if category_id else None
        if category is None:
            category = await guild.create_category('Tickets')
            db.update_guild_setting(guild.id, 'ticket_category_id', category.id)
        overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False), interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True), guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True)}
        support_role_id = settings.get('ticket_support_role_id')
        support_role = guild.get_role(support_role_id) if support_role_id else None
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        channel = await guild.create_text_channel(name=f'ticket-{interaction.user.name}', category=category, overwrites=overwrites, reason=f'Ticket created by {interaction.user}')
        db.add_ticket(guild.id, channel.id, interaction.user.id)
        embed = discord.Embed(title='🎫 تذكرتك', description='اكتب مشكلتك هنا، وسيقوم فريق الدعم بمساعدتك.\n\nعند الانتهاء اضغط **إغلاق التذكرة**.', color=discord.Color.blurple())
        await channel.send(content=interaction.user.mention, embed=embed, view=CloseTicketView(self.bot))
        await interaction.response.send_message(f'✅ تم إنشاء تذكرتك: {channel.mention}', ephemeral=True)
URL_RE = re.compile('https?://\\S+', re.IGNORECASE)

class Protection(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.spam = defaultdict(deque)

    @app_commands.command(name='antilink', description='تشغيل أو إيقاف منع الروابط')
    @app_commands.describe(enabled='true للتشغيل، false للإيقاف')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def antilink(self, interaction: discord.Interaction, enabled: bool):
        db.update_guild_setting(interaction.guild.id, 'antilink_enabled', int(enabled))
        state = 'تشغيل' if enabled else 'إيقاف'
        await interaction.response.send_message(f'🔗 تم {state} Anti-Link.', ephemeral=True)

    @app_commands.command(name='antispam', description='تشغيل أو إيقاف Anti-Spam')
    @app_commands.describe(enabled='true للتشغيل، false للإيقاف')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def antispam(self, interaction: discord.Interaction, enabled: bool):
        db.update_guild_setting(interaction.guild.id, 'antispam_enabled', int(enabled))
        state = 'تشغيل' if enabled else 'إيقاف'
        await interaction.response.send_message(f'🚨 تم {state} Anti-Spam.', ephemeral=True)

    @app_commands.command(name='antimention', description='تشغيل أو إيقاف منع المنشنات الكثيرة')
    @app_commands.describe(enabled='true للتشغيل، false للإيقاف')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def antimention(self, interaction: discord.Interaction, enabled: bool):
        db.update_guild_setting(interaction.guild.id, 'antimention_enabled', int(enabled))
        state = 'تشغيل' if enabled else 'إيقاف'
        await interaction.response.send_message(f'📢 تم {state} Anti-Mention.', ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return
        settings = db.get_guild_settings(message.guild.id)
        if settings.get('antilink_enabled') and URL_RE.search(message.content):
            if not message.author.guild_permissions.manage_messages:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                try:
                    await message.channel.send(f'🚫 {message.author.mention} الروابط ممنوعة هنا.', delete_after=4)
                except discord.HTTPException:
                    pass
                return
        if settings.get('antimention_enabled'):
            mention_count = len(message.mentions) + len(message.role_mentions)
            if mention_count >= MAX_MENTIONS:
                if not message.author.guild_permissions.manage_messages:
                    try:
                        await message.delete()
                    except discord.HTTPException:
                        pass
                    return
        if settings.get('antispam_enabled'):
            key = (message.guild.id, message.author.id)
            now = time.monotonic()
            q = self.spam[key]
            q.append(now)
            while q and now - q[0] > SPAM_WINDOW_SECONDS:
                q.popleft()
            if len(q) >= SPAM_MESSAGE_LIMIT:
                if not message.author.guild_permissions.manage_messages:
                    try:
                        await message.delete()
                    except discord.HTTPException:
                        pass
                    try:
                        await message.author.timeout(timedelta(seconds=30), reason='Anti-Spam')
                    except Exception:
                        pass
                    q.clear()

class Info(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='server', description='معلومات السيرفر')
    async def server(self, interaction: discord.Interaction):
        guild = interaction.guild
        embed = discord.Embed(title=f'📊 {guild.name}', color=discord.Color.blurple())
        embed.add_field(name='👥 الأعضاء', value=str(guild.member_count), inline=True)
        embed.add_field(name='💬 الرومات', value=str(len(guild.channels)), inline=True)
        embed.add_field(name='🎭 الرتب', value=str(len(guild.roles)), inline=True)
        embed.add_field(name='🆔 ID', value=str(guild.id), inline=False)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name='user', description='معلومات عضو')
    @app_commands.describe(member='العضو')
    async def user(self, interaction: discord.Interaction, member: discord.Member):
        embed = discord.Embed(title=f'👤 {member}', color=member.color if member.color != discord.Color.default() else discord.Color.blurple())
        embed.add_field(name='ID', value=str(member.id), inline=False)
        embed.add_field(name='أعلى رتبة', value=member.top_role.mention, inline=True)
        embed.add_field(name='دخل السيرفر', value=discord.utils.format_dt(member.joined_at, 'R'), inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        await interaction.response.send_message(embed=embed)

class ManagementBot(commands.Bot):

    def __init__(self):
        super().__init__(command_prefix='!', intents=intents, help_command=None)

    async def setup_hook(self):
        cogs = [Welcome(self), Moderation(self), Warnings(self), Logs(self), Tickets(self), Protection(self), Info(self)]
        for cog in cogs:
            try:
                await self.add_cog(cog)
                logging.info('Loaded %s', cog.__class__.__name__)
            except Exception:
                logging.exception('Could not load %s', cog.__class__.__name__)
        try:
            self.add_view(TicketOpenView(self))
            self.add_view(CloseTicketView(self))
            logging.info('Loaded persistent ticket views')
        except Exception:
            logging.exception('Could not load persistent ticket views')
        try:
            synced = await self.tree.sync()
            logging.info('Synced %d slash commands', len(synced))
        except Exception:
            logging.exception('Slash command sync failed')

    async def on_ready(self):
        logging.info('Logged in as %s (%s)', self.user, self.user.id)
        await self.change_presence(status=discord.Status.online, activity=discord.Game(name='إدارة السيرفر'))

async def main():
    bot = ManagementBot()
    try:
        await bot.start(TOKEN)
    except discord.LoginFailure:
        logging.error('Discord token is invalid.')
    finally:
        if not bot.is_closed():
            await bot.close()
if __name__ == '__main__':
    asyncio.run(main())
