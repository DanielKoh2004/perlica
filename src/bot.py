import logging
import datetime
from typing import Optional, List, Dict, Any
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

        # 1. Pure Conversational / Casual Chat Handling
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

        # 2. Query / Immediate Summary / Advice Handling
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

        # 3. Action Ingestion (Expenses, New Tasks, Task Completions)
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
            tid = await db.insert_task(
                description=task.description,
                priority=task.priority.value if hasattr(task.priority, "value") else str(task.priority),
                due_date=task.due_date,
                due_time=task.due_time,
                created_at=now_str,
            )
            inserted_tasks.append(
                {
                    "id": tid,
                    "description": task.description,
                    "priority": task.priority.value if hasattr(task.priority, "value") else str(task.priority),
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
