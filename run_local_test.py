"""Interactive CLI Test Harness for Task & Expense Automation Agent.

Allows testing prompts against local SQLite DB and Groq LLM without Discord.
Usage:
    python run_local_test.py
"""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from src.config import settings
from src.database import DatabaseManager
from src.extractor import ExtractionEngine


async def main():
    print("=" * 60)
    print("🤖 Personal Task & Expense Agent — Local CLI Test Harness")
    print(f"Timezone: {settings.TIMEZONE}")
    print(f"Model: {settings.GROQ_MODEL}")
    print(f"Database: {settings.DATABASE_PATH}")
    print("Type 'exit' or 'quit' to stop.")
    print("=" * 60)

    db = DatabaseManager(settings.DATABASE_PATH)
    await db.init_db()

    extractor = ExtractionEngine()

    while True:
        try:
            user_input = input("\n📝 Enter prompt: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                print("Exiting test harness. Goodbye!")
                break

            now_local = datetime.now(settings.tz)
            open_tasks = await db.get_open_tasks()

            print(f"\n⏳ Processing at {now_local.strftime('%Y-%m-%d %H:%M:%S')}...")
            payload = await extractor.extract_information(user_input, now_local, open_tasks)

            print("\n📦 Extracted Payload:")
            print(payload.model_dump_json(indent=2))

            # Execute DB operations
            now_str = now_local.strftime("%Y-%m-%d %H:%M:%S")

            if payload.expenses:
                for exp in payload.expenses:
                    created_at = f"{exp.occurred_date} 12:00:00" if exp.occurred_date else now_str
                    eid = await db.insert_expense(
                        exp.amount,
                        exp.category.value if hasattr(exp.category, "value") else str(exp.category),
                        exp.note,
                        created_at,
                    )
                    print(f"💸 [DB INSERT] Expense #{eid}: RM {exp.amount:.2f} ({exp.category})")

            if payload.new_tasks:
                for t in payload.new_tasks:
                    tid = await db.insert_task(
                        t.description,
                        t.priority.value if hasattr(t.priority, "value") else str(t.priority),
                        t.due_date,
                        t.due_time,
                        now_str,
                    )
                    print(f"📝 [DB INSERT] Task #{tid}: {t.description} (Due: {t.due_date})")

            if payload.completed_task_ids:
                completed = await db.complete_tasks_by_ids(payload.completed_task_ids, now_str)
                for c in completed:
                    print(f"✅ [DB UPDATE] Task #{c['id']} marked DONE: {c['description']}")

            if payload.ambiguous_task_note:
                print(f"⚠️ [AMBIGUITY]: {payload.ambiguous_task_note}")

            if payload.conversational_reply:
                print(f"💬 [REPLY]: {payload.conversational_reply}")

        except (KeyboardInterrupt, EOFError):
            print("\nStopping.")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
