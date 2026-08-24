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
    format_action_confirmation,
    format_daily_summary,
    format_full_snapshot_summary,
    format_query_results,
    format_help_guide,
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


class ConfirmActionView(discord.ui.View):
    """Interactive Discord confirmation buttons for destructive or modifying actions."""

    def __init__(
        self,
        on_confirm: Callable[[discord.Interaction], Any],
        on_cancel: Optional[Callable[[discord.Interaction], Any]] = None,
        timeout: float = 60.0,
    ):
        super().__init__(timeout=timeout)
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green, emoji="✅")
    async def confirm_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        for child in self.children:
            child.disabled = True
        await self.on_confirm(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red, emoji="❌")
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


@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await db.init_db()
    logger.info("Database schema initialized successfully.")

    if not daily_summary_loop.is_running():
        daily_summary_loop.start()
        logger.info(
            f"Daily summary loop scheduled at {settings.DAILY_SUMMARY_TIME} ({settings.TIMEZONE})."
        )


hour, minute = settings.summary_hour_minute
summary_time = datetime.time(hour=hour, minute=minute, tzinfo=settings.tz)


@tasks.loop(time=summary_time)
async def daily_summary_loop():
    """Background scheduled job dispatching daily spending and task summaries via DM."""
    target_user = None

    if settings.ALLOWED_USER_ID:
        target_user = bot.get_user(settings.ALLOWED_USER_ID)
        if not target_user:
            try:
                target_user = await bot.fetch_user(settings.ALLOWED_USER_ID)
            except Exception as e:
                logger.error(f"Failed to fetch user {settings.ALLOWED_USER_ID} for DM summary: {e}")
                return

    now_local = datetime.datetime.now(settings.tz)
    today_str = now_local.strftime("%Y-%m-%d")

    expenses, total_spent, open_tasks = await db.get_daily_summary(today_str)
    embed = format_daily_summary(expenses, total_spent, open_tasks, today_str)

    try:
        if target_user:
            await target_user.send(embed=embed)
            logger.info(f"Dispatched daily summary DM to user {target_user.name} for {today_str}.")
        elif settings.DISCORD_CHANNEL_ID:
            channel = bot.get_channel(settings.DISCORD_CHANNEL_ID) or await bot.fetch_channel(settings.DISCORD_CHANNEL_ID)
            if channel:
                await channel.send(embed=embed)
                logger.info(f"Dispatched daily summary to channel for {today_str}.")
        else:
            logger.warning("No ALLOWED_USER_ID or DISCORD_CHANNEL_ID configured for daily summary dispatch.")
    except Exception as e:
        logger.error(f"Failed to send daily summary embed: {e}")


@bot.event
async def on_message(message: discord.Message):
    # FR-1.2: Ignore bot messages
    if message.author.bot or message.author.id == bot.user.id:
        return

    # Restrict to Direct Messages (DMs) only
    if message.guild is not None:
        return

    # Security: Restrict exclusively to the designated user ID
    if settings.ALLOWED_USER_ID and message.author.id != settings.ALLOWED_USER_ID:
        return

    content = message.content.strip()
    if not content:
        return

    # Direct Help Command Check
    if content.lower() in ("!help", "help", "guide", "how to use", "/help", "commands"):
        await message.reply(embed=format_help_guide())
        return

    # Visual feedback: typing indicator in DM
    async with message.channel.typing():
        now_local = datetime.datetime.now(settings.tz)
        open_tasks = await db.get_open_tasks()

        # Extract structured payload via LLM layer
        payload: ExtractedPayload = await extractor.extract_information(
            text=content,
            now_local=now_local,
            open_tasks=open_tasks,
        )

        now_str = now_local.strftime("%Y-%m-%d %H:%M:%S")

        # 0. Zero-Assumption Clarification Prompt
        if payload.needs_clarification and payload.clarification_prompt:
            await message.reply(payload.clarification_prompt)
            return

        # 1. UNDO Action with Button Confirmation
        if payload.undo_intent:
            intent = payload.undo_intent
            last_exp = await db.get_last_expense()
            last_task = await db.get_last_task()

            # Determine what to undo
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

        # 2. DELETE Specific Expense / Task with Button Confirmation
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

        # 3. EDIT Expense / Task with Button Confirmation
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

        # 4. REOPEN Task with Button Confirmation
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

        # 5. Pure Conversational / Casual Chat Handling
        has_actions = bool(
            payload.expenses
            or payload.new_tasks
            or payload.completed_task_ids
            or payload.ambiguous_task_note
            or payload.query
        )

        if not has_actions:
            reply_text = (
                payload.conversational_reply
                or "Got it! Let me know if you'd like to log an expense, add a task, or see a summary."
            )
            await message.reply(reply_text)
            return

        # 6. Query / Immediate Summary / Advice Handling
        if payload.query:
            q = payload.query
            today_date = now_local.date()

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
                ai_digest = await extractor.generate_ai_insight(
                    prompt_topic=f"Executive summary for {title_time}",
                    snapshot_data=snapshot,
                    now_local=now_local,
                )
                embed = format_full_snapshot_summary(snapshot, title_time, ai_digest)
                await message.reply(embed=embed)
                return

            # Specific Advice or General Question
            if q.query_target in ("ADVICE", "GENERAL") or q.specific_question:
                snapshot = await db.get_full_snapshot(start_d, end_d)
                ai_answer = await extractor.generate_ai_insight(
                    prompt_topic=q.specific_question or content,
                    snapshot_data=snapshot,
                    now_local=now_local,
                )
                await message.reply(ai_answer)
                return

            # Targeted Expenses or Tasks Status
            expenses, total, breakdown = await db.get_expenses_summary(start_d, end_d)
            tasks_list = await db.get_open_tasks()

            embed = format_query_results(
                query=q,
                expenses=expenses,
                total_spent=total,
                category_breakdown=breakdown,
                tasks=tasks_list,
            )
            await message.reply(embed=embed)
            return

        # 7. Action Ingestion (Expenses, Single/Multi-Phase Tasks, Completions)
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

            # Check if task has multiple phases
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

        # Build and send structured confirmation embed
        embed = format_action_confirmation(
            payload=payload,
            inserted_expenses=inserted_expenses,
            inserted_tasks=inserted_tasks,
            completed_tasks=completed_tasks_details,
        )
        await message.reply(embed=embed)


def main():
    if not settings.DISCORD_TOKEN:
        logger.error(
            "DISCORD_TOKEN not set in environment or .env. Please configure your bot token."
        )
        return
    bot.run(settings.DISCORD_TOKEN)


if __name__ == "__main__":
    main()
