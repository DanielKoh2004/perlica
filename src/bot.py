import io
import logging
import datetime
from typing import Optional, List, Dict, Any, Callable
from datetime import timedelta
import discord
from discord.ext import commands, tasks

from src.config import settings
from src.database import DatabaseManager
from src.extractor import ExtractionEngine, ExtractedPayload
from src.formatters import (
    format_action_preview,
    format_action_confirmation,
    format_daily_summary,
    format_morning_briefing,
    format_full_snapshot_summary,
    format_budget_overview,
    format_query_results,
    format_help_guide,
    format_weekly_executive_review,
    format_task_selector_embed,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("discord_agent")

intents = discord.Intents.default()
intents.message_content = True
intents.dm_messages = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

db = DatabaseManager(settings.DATABASE_PATH)
extractor = ExtractionEngine(settings.GROQ_API_KEY, settings.GROQ_MODEL)


# --- INTERACTIVE DISCORD UI VIEWS ---

class ActionIngestionView(discord.ui.View):
    """3-button interactive view: Confirm, Edit, or Reject new entries."""

    def __init__(
        self,
        on_confirm: Callable[[discord.Interaction], Any],
        on_edit: Callable[[discord.Interaction], Any],
        on_reject: Callable[[discord.Interaction], Any],
        timeout: float = 180.0,
    ):
        super().__init__(timeout=timeout)
        self.on_confirm = on_confirm
        self.on_edit = on_edit
        self.on_reject = on_reject

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green, emoji="✅", custom_id="btn_confirm_ingest")
    async def confirm_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        for child in self.children:
            child.disabled = True
        await self.on_confirm(interaction)

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.secondary, emoji="✏️", custom_id="btn_edit_ingest")
    async def edit_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        for child in self.children:
            child.disabled = True
        await self.on_edit(interaction)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.red, emoji="❌", custom_id="btn_reject_ingest")
    async def reject_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        for child in self.children:
            child.disabled = True
        await self.on_reject(interaction)


class ConfirmActionView(discord.ui.View):
    """2-button interactive confirmation view for undo, delete, and reopen."""

    def __init__(
        self,
        on_confirm: Callable[[discord.Interaction], Any],
        on_cancel: Optional[Callable[[discord.Interaction], Any]] = None,
        timeout: float = 120.0,
    ):
        super().__init__(timeout=timeout)
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green, emoji="✅", custom_id="btn_confirm_action")
    async def confirm_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        for child in self.children:
            child.disabled = True
        await self.on_confirm(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red, emoji="❌", custom_id="btn_cancel_action")
    async def cancel_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        for child in self.children:
            child.disabled = True
        if self.on_cancel:
            await self.on_cancel(interaction)
        else:
            await interaction.response.edit_message(
                content="❌ Action cancelled.", embed=None, view=None
            )


class TaskSelectMenu(discord.ui.Select):
    """Native Discord Select Menu for 1-tap batch task completion (strictly capped to top 25)."""

    def __init__(self, open_tasks: List[Dict[str, Any]]):
        # Strict safeguard: Discord API hard-limits select menus to 25 options
        capped_tasks = open_tasks[:25]
        options = []
        for t in capped_tasks:
            due_str = f" | Due: {t['due_date']}" if t.get("due_date") else ""
            desc_text = f"Priority: {t['priority']}{due_str}"
            options.append(
                discord.SelectOption(
                    label=f"#{t['id']}: {t['description']}"[:100],
                    value=str(t["id"]),
                    description=desc_text[:100],
                    emoji="🎯" if t.get("priority") == "HIGH" else "📌",
                )
            )

        super().__init__(
            placeholder="Select tasks to mark as DONE...",
            min_values=1,
            max_values=min(len(options), 25),
            options=options,
            custom_id="select_task_completion",
        )

    async def callback(self, interaction: discord.Interaction):
        selected_ids = [int(v) for v in self.values]
        now_local = datetime.datetime.now(settings.tz)
        now_str = now_local.strftime("%Y-%m-%d %H:%M:%S")

        completed_tasks = await db.complete_tasks_by_ids(selected_ids, completed_at=now_str)
        remaining_tasks = await db.get_open_tasks()

        done_names = ", ".join([f"`#{t['id']}` {t['description']}" for t in completed_tasks])
        msg_header = f"✅ **Completed {len(completed_tasks)} task(s):** {done_names}\n"

        if remaining_tasks:
            new_embed = format_task_selector_embed(remaining_tasks)
            new_view = TaskMultiSelectView(remaining_tasks)
            await interaction.response.edit_message(content=msg_header, embed=new_embed, view=new_view)
        else:
            done_embed = discord.Embed(
                title="🎉 All Tasks Completed!",
                description="You have completed all pending tasks. Outstanding work!",
                color=discord.Color.green(),
            )
            await interaction.response.edit_message(content=msg_header, embed=done_embed, view=None)


class TaskMultiSelectView(discord.ui.View):
    """View container for task completion dropdown."""

    def __init__(self, open_tasks: List[Dict[str, Any]], timeout: float = 300.0):
        super().__init__(timeout=timeout)
        if open_tasks:
            self.add_item(TaskSelectMenu(open_tasks))


class QuickActionView(discord.ui.View):
    """Persistent 4-button quick action bar attached to briefings, summaries, and help guides."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Budget Health", style=discord.ButtonStyle.primary, emoji="📊", custom_id="perlica:btn:budgets")
    async def budget_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        now_local = datetime.datetime.now(settings.tz)
        month_str = now_local.strftime("%Y-%m")
        status = await db.get_budget_status(month_str)
        embed = format_budget_overview(status)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Open Tasks", style=discord.ButtonStyle.primary, emoji="📋", custom_id="perlica:btn:tasks")
    async def tasks_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        open_tasks = await db.get_open_tasks()
        embed = format_task_selector_embed(open_tasks)
        view = TaskMultiSelectView(open_tasks) if open_tasks else None
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Export CSV", style=discord.ButtonStyle.secondary, emoji="📄", custom_id="perlica:btn:csv")
    async def csv_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        now_local = datetime.datetime.now(settings.tz)
        month_str = now_local.strftime("%Y-%m")
        start_month = now_local.strftime("%Y-%m-01")
        csv_text = await db.generate_csv_data(start_month)
        csv_file = discord.File(
            io.BytesIO(csv_text.encode("utf-8")),
            filename=f"Perlica_Expenses_{month_str}.csv",
        )
        await interaction.response.send_message(
            content=f"📄 **Expense export for {now_local.strftime('%B %Y')}:**",
            file=csv_file,
            ephemeral=True,
        )

    @discord.ui.button(label="AI Advice", style=discord.ButtonStyle.success, emoji="💡", custom_id="perlica:btn:advice")
    async def advice_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        now_local = datetime.datetime.now(settings.tz)
        snapshot = await db.get_full_snapshot()
        advice = await extractor.generate_ai_insight(
            prompt_topic="Provide a proactive financial and productivity insight based on my current data.",
            snapshot_data=snapshot,
            now_local=now_local,
        )
        await interaction.followup.send(content=f"💡 **AI Financial & Focus Insight:**\n{advice}", ephemeral=True)


# --- BOT LIFECYCLE & BACKGROUND SCHEDULED LOOPS ---

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await db.init_db()
    logger.info("Database schema initialized successfully.")

    # Register persistent view for restart resilience
    bot.add_view(QuickActionView())

    if not daily_summary_loop.is_running():
        daily_summary_loop.start()
        logger.info(
            f"Daily summary loop scheduled at {settings.DAILY_SUMMARY_TIME} ({settings.TIMEZONE})."
        )

    if not morning_briefing_loop.is_running():
        morning_briefing_loop.start()
        logger.info(
            f"Morning briefing loop scheduled at {settings.MORNING_BRIEFING_TIME} ({settings.TIMEZONE})."
        )

    if not weekly_review_loop.is_running():
        weekly_review_loop.start()
        logger.info(
            f"Weekly executive review loop scheduled on Sundays at {settings.WEEKLY_REVIEW_TIME} ({settings.TIMEZONE})."
        )


summary_h, summary_m = settings.summary_hour_minute
summary_time = datetime.time(hour=summary_h, minute=summary_m, tzinfo=settings.tz)

morning_h, morning_m = settings.morning_hour_minute
morning_time = datetime.time(hour=morning_h, minute=morning_m, tzinfo=settings.tz)

weekly_h, weekly_m = settings.weekly_review_hour_minute
weekly_time = datetime.time(hour=weekly_h, minute=weekly_m, tzinfo=settings.tz)


@tasks.loop(time=morning_time)
async def morning_briefing_loop():
    """Background scheduled job dispatching morning briefing at 08:30 via DM."""
    target_user = None
    if settings.ALLOWED_USER_ID:
        target_user = bot.get_user(settings.ALLOWED_USER_ID) or await bot.fetch_user(settings.ALLOWED_USER_ID)

    if not target_user:
        return

    now_local = datetime.datetime.now(settings.tz)
    today_str = now_local.strftime("%Y-%m-%d")
    month_str = now_local.strftime("%Y-%m")

    open_tasks = await db.get_open_tasks()
    due_bills = await db.get_due_recurring_bills(now_local.date())
    upcoming_bills = await db.get_upcoming_recurring_bills(now_local.date(), days_ahead=3)
    budget_status = await db.get_budget_status(month_str)

    embed = format_morning_briefing(
        open_tasks=open_tasks,
        due_bills=due_bills,
        budget_status=budget_status,
        date_str=today_str,
        upcoming_bills=upcoming_bills,
    )
    try:
        await target_user.send(embed=embed, view=QuickActionView())
        logger.info(f"Dispatched morning briefing DM for {today_str}.")
    except Exception as e:
        logger.error(f"Failed to send morning briefing DM: {e}")


@tasks.loop(time=summary_time)
async def daily_summary_loop():
    """Background scheduled job dispatching daily spending and task summaries via DM."""
    target_user = None
    if settings.ALLOWED_USER_ID:
        target_user = bot.get_user(settings.ALLOWED_USER_ID) or await bot.fetch_user(settings.ALLOWED_USER_ID)

    now_local = datetime.datetime.now(settings.tz)
    today_str = now_local.strftime("%Y-%m-%d")

    expenses, total_spent, open_tasks = await db.get_daily_summary(today_str)
    spending_pace = await db.get_spending_pace(today_str)
    streak_info = await db.get_productivity_streak(today_str)

    embed = format_daily_summary(
        expenses=expenses,
        total_spent=total_spent,
        open_tasks=open_tasks,
        date_str=today_str,
        spending_pace=spending_pace,
        streak_info=streak_info,
    )

    try:
        if target_user:
            await target_user.send(embed=embed, view=QuickActionView())
            logger.info(f"Dispatched daily summary DM to user {target_user.name} for {today_str}.")
        elif settings.DISCORD_CHANNEL_ID:
            channel = bot.get_channel(settings.DISCORD_CHANNEL_ID) or await bot.fetch_channel(settings.DISCORD_CHANNEL_ID)
            if channel:
                await channel.send(embed=embed, view=QuickActionView())
                logger.info(f"Dispatched daily summary to channel for {today_str}.")
    except Exception as e:
        logger.error(f"Failed to send daily summary embed: {e}")


@tasks.loop(time=weekly_time)
async def weekly_review_loop():
    """Background scheduled job dispatching Sunday 8:00 PM Weekly Executive Review."""
    now_local = datetime.datetime.now(settings.tz)
    # Check if today is Sunday (weekday == 6 in Python datetime)
    if now_local.weekday() != 6:
        return

    target_user = None
    if settings.ALLOWED_USER_ID:
        target_user = bot.get_user(settings.ALLOWED_USER_ID) or await bot.fetch_user(settings.ALLOWED_USER_ID)

    if not target_user:
        return

    today_date = now_local.date()
    start_of_week = (today_date - timedelta(days=6)).strftime("%Y-%m-%d")
    end_of_week = today_date.strftime("%Y-%m-%d")

    review_data = await db.get_weekly_review_data(start_of_week, end_of_week)
    ai_kickoff = await extractor.generate_ai_insight(
        prompt_topic="Sunday Weekly Executive Review. Analyze the week's accomplishments and provide a high-impact strategic kickoff for Monday.",
        snapshot_data=await db.get_full_snapshot(start_of_week, end_of_week),
        now_local=now_local,
    )

    embed = format_weekly_executive_review(review_data, ai_strategic_kickoff=ai_kickoff)
    try:
        await target_user.send(embed=embed, view=QuickActionView())
        logger.info(f"Dispatched Sunday Weekly Review DM for {start_of_week} to {end_of_week}.")
    except Exception as e:
        logger.error(f"Failed to send weekly review DM: {e}")


# --- MESSAGE DISPATCHER & INGESTION ---

@bot.event
async def on_message(message: discord.Message):
    # Ignore bot messages
    if message.author.bot or message.author.id == bot.user.id:
        return

    # Restrict to Direct Messages (DMs) only
    if message.guild is not None:
        return

    # Security: Restrict exclusively to the designated user ID
    if settings.ALLOWED_USER_ID and message.author.id != settings.ALLOWED_USER_ID:
        return

    content = message.content.strip()
    image_attachment = None

    # Check attachments for voice notes or receipt photos
    if message.attachments:
        for att in message.attachments:
            lower_name = att.filename.lower()
            if any(lower_name.endswith(ext) for ext in [".ogg", ".mp3", ".m4a", ".wav", ".webm"]):
                async with message.channel.typing():
                    audio_bytes = await att.read()
                    transcribed = await extractor.transcribe_audio((att.filename, audio_bytes))
                    if transcribed:
                        await message.reply(f"🎙️ *Voice Note Transcribed:* \"{transcribed}\"")
                        content = f"{content} {transcribed}".strip()
                    else:
                        await message.reply("⚠️ Could not transcribe the audio file.")
            elif any(lower_name.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp"]):
                image_attachment = att

    if not content and not image_attachment:
        return

    # Direct Help Command Check
    if content.lower() in ("!help", "help", "guide", "how to use", "/help", "commands"):
        await message.reply(embed=format_help_guide(), view=QuickActionView())
        return

    # Visual feedback: typing indicator in DM
    async with message.channel.typing():
        now_local = datetime.datetime.now(settings.tz)
        now_str = now_local.strftime("%Y-%m-%d %H:%M:%S")
        month_str = now_local.strftime("%Y-%m")
        open_tasks = await db.get_open_tasks()
        recurring_bills = await db.list_recurring_bills()

        # Receipt Image OCR extraction
        if image_attachment and not content:
            img_bytes = await image_attachment.read()
            payload: ExtractedPayload = await extractor.extract_from_image(
                image_bytes=img_bytes,
                filename=image_attachment.filename,
                now_local=now_local,
                open_tasks=open_tasks,
                recurring_bills=recurring_bills,
            )
        else:
            # Extract structured payload via LLM layer with live tasks and bills
            payload: ExtractedPayload = await extractor.extract_information(
                text=content,
                now_local=now_local,
                open_tasks=open_tasks,
                recurring_bills=recurring_bills,
            )

        # 0. Zero-Assumption Clarification Prompt
        if payload.needs_clarification and payload.clarification_prompt:
            await message.reply(payload.clarification_prompt)
            return

        # 1. CSV Data Export
        if payload.export_csv:
            start_month = now_local.strftime("%Y-%m-01")
            csv_text = await db.generate_csv_data(start_month)
            csv_file = discord.File(
                io.BytesIO(csv_text.encode("utf-8")),
                filename=f"Perlica_Expenses_{month_str}.csv",
            )
            await message.reply(
                content=f"📄 **Here is your expense export for {now_local.strftime('%B %Y')}:**",
                file=csv_file,
            )
            return

        # 2. UNDO Action with Button Confirmation
        if payload.undo_intent:
            intent = payload.undo_intent
            last_exp = await db.get_last_expense()
            last_task = await db.get_last_task()

            target_type = None
            target_item = None
            if intent in ("EXPENSE", "LAST") and last_exp:
                target_type = "expense"
                target_item = last_exp
            elif intent in ("TASK", "LAST") and last_task:
                target_type = "task"
                target_item = last_task

            if not target_item:
                await message.reply("There is nothing recent to undo.")
                return

            if target_type == "expense":
                item_desc = f"Expense **#{target_item['id']}** — RM {target_item['amount']:.2f} (`{target_item['category']}`: {target_item.get('note') or 'No note'})"
                async def do_undo(interaction: discord.Interaction):
                    deleted = await db.delete_expense(target_item["id"])
                    await interaction.response.edit_message(
                        content=f"🗑️ **Undid:** Deleted Expense #{deleted['id']} (RM {deleted['amount']:.2f} {deleted['category']}).",
                        embed=None,
                        view=None,
                    )
            else:
                item_desc = f"Task **#{target_item['id']}** — `[{target_item['priority']}]` {target_item['description']}"
                async def do_undo(interaction: discord.Interaction):
                    deleted = await db.delete_task(target_item["id"])
                    await interaction.response.edit_message(
                        content=f"🗑️ **Undid:** Deleted Task #{deleted['id']} ({deleted['description']}).",
                        embed=None,
                        view=None,
                    )

            embed = discord.Embed(
                title="⚠️ Confirm Undo Action",
                description=f"Are you sure you want to delete and undo this recent entry?\n\n{item_desc}",
                color=discord.Color.orange(),
            )
            await message.reply(embed=embed, view=ConfirmActionView(on_confirm=do_undo))
            return

        # 3. DELETE Specific Expense / Task / Bill with Button Confirmation
        if payload.delete_expense_id:
            eid = payload.delete_expense_id
            target_exp = await db.get_expense_by_id(eid)
            if not target_exp:
                await message.reply(f"Expense #{eid} was not found.")
                return

            async def do_delete_exp(interaction: discord.Interaction):
                await db.delete_expense(eid)
                await interaction.response.edit_message(
                    content=f"🗑️ **Deleted Expense #{eid}:** RM {target_exp['amount']:.2f} (`{target_exp['category']}`).",
                    embed=None,
                    view=None,
                )

            embed = discord.Embed(
                title="⚠️ Confirm Deletion",
                description=f"Are you sure you want to delete **Expense #{eid}** (RM {target_exp['amount']:.2f} — `{target_exp['category']}`)?",
                color=discord.Color.red(),
            )
            await message.reply(embed=embed, view=ConfirmActionView(on_confirm=do_delete_exp))
            return

        if payload.delete_task_id:
            tid = payload.delete_task_id
            target_task = await db.get_task_by_id(tid)
            if not target_task:
                await message.reply(f"Task #{tid} was not found.")
                return

            async def do_delete_task(interaction: discord.Interaction):
                await db.delete_task(tid)
                await interaction.response.edit_message(
                    content=f"🗑️ **Deleted Task #{tid}:** {target_task['description']}.",
                    embed=None,
                    view=None,
                )

            embed = discord.Embed(
                title="⚠️ Confirm Deletion",
                description=f"Are you sure you want to delete **Task #{tid}** (`[{target_task['priority']}]` {target_task['description']})?",
                color=discord.Color.red(),
            )
            await message.reply(embed=embed, view=ConfirmActionView(on_confirm=do_delete_task))
            return

        if payload.delete_bill_id:
            bid = payload.delete_bill_id
            target_bill = await db.get_recurring_bill_by_id(bid)
            if not target_bill:
                await message.reply(f"Recurring Bill #{bid} was not found.")
                return

            async def do_delete_bill(interaction: discord.Interaction):
                await db.delete_recurring_bill(bid)
                await interaction.response.edit_message(
                    content=f"🗑️ **Deleted Recurring Bill #{bid}:** `{target_bill['name']}` (RM {target_bill['amount']:.2f}).",
                    embed=None,
                    view=None,
                )

            embed = discord.Embed(
                title="⚠️ Confirm Deletion",
                description=f"Are you sure you want to delete **Recurring Bill #{bid}** (`{target_bill['name']}` — RM {target_bill['amount']:.2f})?",
                color=discord.Color.red(),
            )
            await message.reply(embed=embed, view=ConfirmActionView(on_confirm=do_delete_bill))
            return

        # 4. EDIT Expense / Task / Bill with Button Confirmation
        if payload.edit_expense_id:
            eid = payload.edit_expense_id
            target_exp = await db.get_expense_by_id(eid)
            if not target_exp:
                await message.reply(f"Expense #{eid} was not found.")
                return

            new_amt = payload.edit_expense_amount if payload.edit_expense_amount is not None else target_exp["amount"]
            new_cat = payload.edit_expense_category.value if payload.edit_expense_category else target_exp["category"]
            new_note = payload.edit_expense_note if payload.edit_expense_note is not None else target_exp["note"]

            async def do_edit_exp(interaction: discord.Interaction):
                updated = await db.update_expense(eid, new_amt, new_cat, new_note)
                await interaction.response.edit_message(
                    content=f"✏️ **Updated Expense #{eid}:** RM {updated['amount']:.2f} (`{updated['category']}`: {updated.get('note') or 'No note'}).",
                    embed=None,
                    view=None,
                )

            embed = discord.Embed(
                title="⚠️ Confirm Expense Update",
                description=(
                    f"**Expense #{eid} Changes:**\n"
                    f"• Amount: RM {target_exp['amount']:.2f} ➔ **RM {new_amt:.2f}**\n"
                    f"• Category: `{target_exp['category']}` ➔ **`{new_cat}`**\n"
                    f"• Note: {target_exp.get('note') or 'None'} ➔ **{new_note or 'None'}**"
                ),
                color=discord.Color.gold(),
            )
            await message.reply(embed=embed, view=ConfirmActionView(on_confirm=do_edit_exp))
            return

        if payload.edit_task_id:
            tid = payload.edit_task_id
            target_task = await db.get_task_by_id(tid)
            if not target_task:
                await message.reply(f"Task #{tid} was not found.")
                return

            new_desc = payload.edit_task_description or target_task["description"]
            new_prio = payload.edit_task_priority.value if payload.edit_task_priority else target_task["priority"]
            new_due = payload.edit_task_due_date if payload.edit_task_due_date is not None else target_task["due_date"]
            new_time = payload.edit_task_due_time if payload.edit_task_due_time is not None else target_task["due_time"]

            async def do_edit_task(interaction: discord.Interaction):
                updated = await db.update_task(tid, new_desc, new_prio, new_due, new_time)
                await interaction.response.edit_message(
                    content=f"✏️ **Updated Task #{tid}:** `[{updated['priority']}]` {updated['description']} (Due: {updated.get('due_date') or 'None'}).",
                    embed=None,
                    view=None,
                )

            embed = discord.Embed(
                title="⚠️ Confirm Task Update",
                description=(
                    f"**Task #{tid} Changes:**\n"
                    f"• Description: {target_task['description']} ➔ **{new_desc}**\n"
                    f"• Priority: `{target_task['priority']}` ➔ **`{new_prio}`**\n"
                    f"• Due Date: {target_task.get('due_date') or 'None'} ➔ **{new_due or 'None'}**"
                ),
                color=discord.Color.gold(),
            )
            await message.reply(embed=embed, view=ConfirmActionView(on_confirm=do_edit_task))
            return

        if payload.edit_bill_id:
            bid = payload.edit_bill_id
            target_bill = await db.get_recurring_bill_by_id(bid)
            if not target_bill:
                await message.reply(f"Recurring Bill #{bid} was not found.")
                return

            new_amt = payload.edit_bill_amount if payload.edit_bill_amount is not None else target_bill["amount"]
            new_name = payload.edit_bill_name or target_bill["name"]
            new_cat = payload.edit_bill_category.value if payload.edit_bill_category else target_bill["category"]
            new_day = payload.edit_bill_day if payload.edit_bill_day is not None else target_bill["day_of_month"]

            async def do_edit_bill(interaction: discord.Interaction):
                updated = await db.update_recurring_bill(bid, name=new_name, amount=new_amt, category=new_cat, day_of_month=new_day)
                await interaction.response.edit_message(
                    content=f"✏️ **Updated Recurring Bill #{bid}:** `{updated['name']}` (RM {updated['amount']:.2f} — `{updated['category']}`) due on the **{updated['day_of_month']}th**.",
                    embed=None,
                    view=None,
                )

            embed = discord.Embed(
                title="⚠️ Confirm Recurring Bill Update",
                description=(
                    f"**Recurring Bill #{bid} Changes:**\n"
                    f"• Name: `{target_bill['name']}` ➔ **`{new_name}`**\n"
                    f"• Amount: RM {target_bill['amount']:.2f} ➔ **RM {new_amt:.2f}**\n"
                    f"• Category: `{target_bill['category']}` ➔ **`{new_cat}`**\n"
                    f"• Day: {target_bill['day_of_month']}th ➔ **{new_day}th**"
                ),
                color=discord.Color.gold(),
            )
            await message.reply(embed=embed, view=ConfirmActionView(on_confirm=do_edit_bill))
            return

        # 5. REOPEN Task with Button Confirmation
        if payload.reopen_task_id:
            tid = payload.reopen_task_id
            target_task = await db.get_task_by_id(tid)
            if not target_task:
                await message.reply(f"Task #{tid} was not found.")
                return

            async def do_reopen(interaction: discord.Interaction):
                await db.update_task(tid, status="OPEN")
                await interaction.response.edit_message(
                    content=f"🔄 **Reopened Task #{tid}:** {target_task['description']} is now `OPEN`.",
                    embed=None,
                    view=None,
                )

            embed = discord.Embed(
                title="⚠️ Confirm Reopen Task",
                description=f"Are you sure you want to reopen **Task #{tid}** ({target_task['description']}) back to `OPEN`?",
                color=discord.Color.blue(),
            )
            await message.reply(embed=embed, view=ConfirmActionView(on_confirm=do_reopen))
            return

        # 6. Pure Conversational / Casual Chat Handling
        has_actions = bool(
            payload.expenses
            or payload.new_tasks
            or payload.completed_task_ids
            or payload.add_bill_name
            or payload.set_budget_category
            or payload.ambiguous_task_note
            or payload.query
        )

        if not has_actions:
            reply_text = (
                payload.conversational_reply
                or "Got it! Let me know if you'd like to log an expense, add a task, or see a summary."
            )
            await message.reply(reply_text, view=QuickActionView())
            return

        # 7. Query / Immediate Summary / Budget Overview / Tasks Handling
        if payload.query:
            q = payload.query
            today_date = now_local.date()

            if q.query_target == "BUDGETS":
                status = await db.get_budget_status(month_str)
                await message.reply(embed=format_budget_overview(status), view=QuickActionView())
                return

            if q.query_target == "BILLS":
                bills = await db.list_recurring_bills()
                if bills:
                    b_lines = [f"• `[Bill #{b['id']}]` **{b['name']}:** RM {b['amount']:.2f} (`{b['category']}`) due on the **{b['day_of_month']}th**" for b in bills]
                    embed = discord.Embed(title="🔔 Configured Recurring Bills", description="\n".join(b_lines), color=discord.Color.purple())
                else:
                    embed = discord.Embed(title="🔔 Configured Recurring Bills", description="No recurring bills configured yet. Add one with *'Add recurring bill: Netflix RM 55 on the 15th'*.", color=discord.Color.purple())
                await message.reply(embed=embed, view=QuickActionView())
                return

            # Interactive Task Multi-Select Dropdown for open tasks
            if q.query_target == "TASKS":
                tasks_list = await db.get_open_tasks()
                embed = format_task_selector_embed(tasks_list)
                view = TaskMultiSelectView(tasks_list) if tasks_list else None
                await message.reply(embed=embed, view=view)
                return

            if q.timeframe == "TODAY":
                start_d = today_date.strftime("%Y-%m-%d")
                end_d = start_d
                title_time = f"Today — {start_d}"
            elif q.timeframe == "YESTERDAY":
                yesterday_date = today_date - timedelta(days=1)
                start_d = yesterday_date.strftime("%Y-%m-%d")
                end_d = start_d
                title_time = f"Yesterday — {start_d}"
            elif q.timeframe == "THIS_WEEK":
                start_d = (today_date - timedelta(days=today_date.weekday())).strftime("%Y-%m-%d")
                end_d = today_date.strftime("%Y-%m-%d")
                title_time = f"This Week ({start_d} to {end_d})"
            elif q.timeframe == "THIS_MONTH":
                start_d = today_date.strftime("%Y-%m-01")
                end_d = today_date.strftime("%Y-%m-%d")
                title_time = f"This Month ({today_date.strftime('%B %Y')})"
            else:  # ALL_TIME
                start_d, end_d = None, None
                title_time = "All Time"

            # Full Executive Summary Request
            if q.query_target == "SUMMARY":
                snapshot = await db.get_full_snapshot(start_d, end_d)
                budget_status = await db.get_budget_status(month_str)
                ai_digest = await extractor.generate_ai_insight(
                    prompt_topic=f"Executive summary for {title_time}",
                    snapshot_data=snapshot,
                    now_local=now_local,
                )
                embed = format_full_snapshot_summary(snapshot, title_time, ai_digest, budget_status)
                await message.reply(embed=embed, view=QuickActionView())
                return

            # Specific Advice or General Question
            if q.query_target in ("ADVICE", "GENERAL") or q.specific_question:
                snapshot = await db.get_full_snapshot(start_d, end_d)
                ai_answer = await extractor.generate_ai_insight(
                    prompt_topic=q.specific_question or content,
                    snapshot_data=snapshot,
                    now_local=now_local,
                )
                await message.reply(ai_answer, view=QuickActionView())
                return

            # Targeted Expenses Status
            expenses, total, breakdown = await db.get_expenses_summary(start_d, end_d)
            tasks_list = await db.get_open_tasks()

            embed = format_query_results(
                query=q,
                expenses=expenses,
                total_spent=total,
                category_breakdown=breakdown,
                tasks=tasks_list,
            )
            await message.reply(embed=embed, view=QuickActionView())
            return

        # 8. Action Ingestion with 3-Button Confirmation Preview ([Confirm] [Edit] [Reject])
        expenses_preview = [
            {
                "amount": exp.amount,
                "category": exp.category.value if hasattr(exp.category, "value") else str(exp.category),
                "note": exp.note,
                "occurred_date": exp.occurred_date,
            }
            for exp in payload.expenses
        ]
        tasks_preview = [
            {
                "description": task.description,
                "priority": task.priority.value if hasattr(task.priority, "value") else str(task.priority),
                "due_date": task.due_date,
                "due_time": task.due_time,
                "phases": task.phases,
            }
            for task in payload.new_tasks
        ]

        # Callback for [Confirm]
        async def on_confirm_ingest(interaction: discord.Interaction):
            inserted_expenses: List[Dict[str, Any]] = []
            for exp in payload.expenses:
                created_at = (
                    f"{exp.occurred_date} 12:00:00" if exp.occurred_date else now_str
                )
                eid = await db.insert_expense(
                    amount=exp.amount,
                    category=exp.category.value if hasattr(exp.category, "value") else str(exp.category),
                    note=exp.note,
                    created_at=created_at,
                )
                inserted_expenses.append(
                    {
                        "id": eid,
                        "amount": exp.amount,
                        "category": exp.category.value if hasattr(exp.category, "value") else str(exp.category),
                        "note": exp.note,
                        "created_at": created_at,
                    }
                )

            inserted_tasks: List[Dict[str, Any]] = []
            for task in payload.new_tasks:
                priority_val = task.priority.value if hasattr(task.priority, "value") else str(task.priority)

                if task.phases:
                    parent_id, subtasks = await db.insert_task_with_phases(
                        description=task.description,
                        priority=priority_val,
                        phases=task.phases,
                        due_date=task.due_date,
                        due_time=task.due_time,
                        created_at=now_str,
                    )
                    inserted_tasks.append(
                        {
                            "id": parent_id,
                            "description": task.description,
                            "priority": priority_val,
                            "due_date": task.due_date,
                            "due_time": task.due_time,
                            "created_at": now_str,
                            "subphases": subtasks,
                        }
                    )
                else:
                    tid = await db.insert_task(
                        description=task.description,
                        priority=priority_val,
                        due_date=task.due_date,
                        due_time=task.due_time,
                        created_at=now_str,
                    )
                    inserted_tasks.append(
                        {
                            "id": tid,
                            "description": task.description,
                            "priority": priority_val,
                            "due_date": task.due_date,
                            "due_time": task.due_time,
                            "created_at": now_str,
                        }
                    )

            completed_tasks_details: List[Dict[str, Any]] = []
            if payload.completed_task_ids:
                completed_tasks_details = await db.complete_tasks_by_ids(
                    payload.completed_task_ids, completed_at=now_str
                )

            # Recurring Bill Saved on Confirm
            if payload.add_bill_name and payload.add_bill_amount is not None:
                b_name = payload.add_bill_name
                b_amt = payload.add_bill_amount
                b_cat = payload.add_bill_category.value if payload.add_bill_category else "Utilities & Bills"
                b_day = payload.add_bill_day or 1
                await db.add_recurring_bill(b_name, b_amt, b_cat, b_day)

            # Budget Limit Saved on Confirm
            if payload.set_budget_category and payload.set_budget_amount is not None:
                await db.set_budget(payload.set_budget_category, payload.set_budget_amount)

            # Check Budget Utilization & Alerts
            budget_alerts = []
            if inserted_expenses:
                current_budget_status = await db.get_budget_status(month_str)
                for b in current_budget_status:
                    if b["is_overspent"]:
                        budget_alerts.append(f"🚨 **{b['category']}** has exceeded monthly limit! (RM {b['spent']:.2f} / RM {b['limit']:.2f})")
                    elif b["is_warning"]:
                        budget_alerts.append(f"⚠️ **{b['category']}** is at {b['percentage']}% of monthly limit (RM {b['remaining']:.2f} left).")

            streak_info = await db.get_productivity_streak(now_local.strftime("%Y-%m-%d"))

            confirmed_embed = format_action_confirmation(
                payload=payload,
                inserted_expenses=inserted_expenses,
                inserted_tasks=inserted_tasks,
                completed_tasks=completed_tasks_details,
                budget_alerts=budget_alerts,
                streak_info=streak_info,
            )
            await interaction.response.edit_message(
                embed=confirmed_embed, view=QuickActionView()
            )

        # Callback for [Edit]
        async def on_edit_ingest(interaction: discord.Interaction):
            await interaction.response.edit_message(
                content="✏️ Please send your corrected message directly (e.g. *'Spent RM 20 on lunch'* or *'Task due tomorrow 3pm'*).",
                embed=None,
                view=None,
            )

        # Callback for [Reject]
        async def on_reject_ingest(interaction: discord.Interaction):
            await interaction.response.edit_message(
                content="❌ Entry discarded. Nothing was saved.",
                embed=None,
                view=None,
            )

        # Send Preview Embed with 3 Buttons
        preview_embed = format_action_preview(
            payload=payload,
            expenses=expenses_preview,
            tasks=tasks_preview,
            completed_task_ids=payload.completed_task_ids,
        )
        view = ActionIngestionView(
            on_confirm=on_confirm_ingest,
            on_edit=on_edit_ingest,
            on_reject=on_reject_ingest,
        )
        await message.reply(embed=preview_embed, view=view)


def main():
    if not settings.DISCORD_TOKEN:
        logger.error(
            "DISCORD_TOKEN not set in environment or .env. Please configure your bot token."
        )
        return
    bot.run(settings.DISCORD_TOKEN)


if __name__ == "__main__":
    main()
