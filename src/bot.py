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
    format_query_results,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("discord_agent")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

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
    """Background scheduled job dispatching daily spending and task summaries."""
    if not settings.DISCORD_CHANNEL_ID:
        logger.warning("No DISCORD_CHANNEL_ID configured for daily summary dispatch.")
        return

    channel = bot.get_channel(settings.DISCORD_CHANNEL_ID)
    if not channel:
        try:
            channel = await bot.fetch_channel(settings.DISCORD_CHANNEL_ID)
        except Exception as e:
            logger.error(f"Failed to fetch channel for daily summary: {e}")
            return

    now_local = datetime.datetime.now(settings.tz)
    today_str = now_local.strftime("%Y-%m-%d")

    expenses, total_spent, open_tasks = await db.get_daily_summary(today_str)
    embed = format_daily_summary(expenses, total_spent, open_tasks, today_str)

    try:
        await channel.send(embed=embed)
        logger.info(f"Dispatched daily summary for {today_str}.")
    except Exception as e:
        logger.error(f"Failed to send daily summary embed: {e}")


@bot.event
async def on_message(message: discord.Message):
    # FR-1.2: Ignore bot messages
    if message.author.bot or message.author.id == bot.user.id:
        return

    # FR-1.1 & Security: Channel and User ID filtering
    if settings.DISCORD_CHANNEL_ID and message.channel.id != settings.DISCORD_CHANNEL_ID:
        return

    if settings.ALLOWED_USER_ID and message.author.id != settings.ALLOWED_USER_ID:
        return

    content = message.content.strip()
    if not content:
        return

    # Visual feedback: typing indicator
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

        # 1. Pure Conversational / Unparseable Handling
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
                or "Got it! Let me know if you have an expense or task to log."
            )
            await message.reply(reply_text)
            return

        # 2. Query Handling
        if payload.query:
            q = payload.query
            today_date = now_local.date()

            if q.timeframe == "TODAY":
                start_d = today_date.strftime("%Y-%m-%d")
                end_d = start_d
            elif q.timeframe == "THIS_WEEK":
                start_d = (today_date - timedelta(days=today_date.weekday())).strftime("%Y-%m-%d")
                end_d = today_date.strftime("%Y-%m-%d")
            elif q.timeframe == "THIS_MONTH":
                start_d = today_date.strftime("%Y-%m-01")
                end_d = today_date.strftime("%Y-%m-%d")
            else:  # ALL_TIME
                start_d, end_d = None, None

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
