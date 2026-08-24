import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field, ValidationError
from groq import AsyncGroq
import instructor

from src.config import settings

logger = logging.getLogger(__name__)


class ExpenseCategory(str, Enum):
    FOOD = "Food & Dining"
    TRANSPORT = "Transport"
    GROCERIES = "Groceries"
    UTILITIES = "Utilities & Bills"
    ENTERTAINMENT = "Entertainment"
    SHOPPING = "Shopping"
    HEALTH = "Health & Personal"
    OTHER = "Other"


class ExpenseItem(BaseModel):
    amount: float = Field(
        ...,
        description="Monetary value in MYR as a number (e.g. 15.50). Strip 'RM', '$', or currency symbols.",
    )
    category: ExpenseCategory = Field(
        default=ExpenseCategory.OTHER,
        description="Normalized expense category.",
    )
    note: Optional[str] = Field(
        default=None,
        description="Brief context or item description (e.g. 'Chicken rice lunch', 'Grab ride').",
    )
    occurred_date: Optional[str] = Field(
        default=None,
        description="Date in YYYY-MM-DD format if the expense occurred on a specific date (e.g. yesterday). Otherwise None.",
    )


class TaskPriority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class TaskItem(BaseModel):
    description: str = Field(
        ...,
        description="Clear, actionable task description.",
    )
    priority: TaskPriority = Field(
        default=TaskPriority.MEDIUM,
        description="Priority level: LOW, MEDIUM, or HIGH.",
    )
    due_date: Optional[str] = Field(
        default=None,
        description="Due date in YYYY-MM-DD format calculated strictly from local reference frame. Otherwise None.",
    )
    due_time: Optional[str] = Field(
        default=None,
        description="Due time in HH:MM (24-hour) format if mentioned (e.g. '17:00' for 5pm). Otherwise None.",
    )


class QueryScope(BaseModel):
    query_target: Literal["EXPENSES", "TASKS", "SUMMARY"] = Field(
        ...,
        description="Target dataset to view.",
    )
    timeframe: Literal["TODAY", "THIS_WEEK", "THIS_MONTH", "ALL_TIME"] = Field(
        default="TODAY",
        description="Timeframe for the query.",
    )


class ExtractedPayload(BaseModel):
    expenses: List[ExpenseItem] = Field(
        default_factory=list,
        description="Expenses to record. Leave empty list if none.",
    )
    new_tasks: List[TaskItem] = Field(
        default_factory=list,
        description="Brand new to-do tasks to create. Leave empty list if none.",
    )
    completed_task_ids: List[int] = Field(
        default_factory=list,
        description="Exact integer IDs of open tasks from the provided context that the user completed. Leave empty list if none.",
    )
    ambiguous_task_note: Optional[str] = Field(
        default=None,
        description="Populate ONLY if the user intended to complete a task but multiple active tasks match or the reference is ambiguous.",
    )
    query: Optional[QueryScope] = Field(
        default=None,
        description="Populate ONLY if the user is explicitly requesting a summary, report, or status list. Otherwise None.",
    )
    conversational_reply: Optional[str] = Field(
        default=None,
        description="Friendly response ONLY if the message was general conversation, greeting, status update, or unparseable input.",
    )


def build_system_prompt(now_local: datetime, open_tasks: List[Dict[str, Any]]) -> str:
    """Build a comprehensive system prompt with local temporal anchors and open tasks."""
    today_str = now_local.strftime("%Y-%m-%d (%A)")
    tomorrow_str = (now_local + timedelta(days=1)).strftime("%Y-%m-%d (%A)")
    yesterday_str = (now_local - timedelta(days=1)).strftime("%Y-%m-%d (%A)")

    if open_tasks:
        tasks_formatted = "\n".join(
            [
                f"- [ID: {t['id']}] {t['description']} (Priority: {t.get('priority', 'MEDIUM')})"
                for t in open_tasks
            ]
        )
    else:
        tasks_formatted = "No active open tasks."

    return f"""You are an intelligent, zero-friction Discord personal assistant tracking expenses and daily tasks.

LOCAL TIME REFERENCE:
- Current Local Timestamp: {now_local.strftime('%Y-%m-%d %H:%M:%S')}
- TODAY is: {today_str}
- TOMORROW is: {tomorrow_str}
- YESTERDAY is: {yesterday_str}

ACTIVE OPEN TASKS IN DATABASE:
{tasks_formatted}

EXTRACTION RULES:
1. CASUAL / NON-ACTIONABLE MESSAGES:
   - If the user sends casual conversation, greetings, jokes, or status updates with no actionable expenses, tasks, or queries (e.g., 'I just woke up', 'hello bot', 'good morning', 'thanks!'), DO NOT invent or create dummy expenses or tasks.
   - Set expenses=[], new_tasks=[], completed_task_ids=[], query=None, and provide a warm, brief conversational_reply.

2. EXPENSES:
   - Extract amount as a numeric float in MYR (e.g., "RM 15.50 lunch" -> amount=15.50, category='Food & Dining', note='lunch').
   - Categorize accurately into: Food & Dining, Transport, Groceries, Utilities & Bills, Entertainment, Shopping, Health & Personal, Other.
   - If the expense occurred yesterday, set occurred_date to yesterday's YYYY-MM-DD.

3. NEW TASKS:
   - Extract action items as new_tasks with appropriate priority (HIGH, MEDIUM, LOW).
   - If a due date is specified (e.g., "tomorrow", "tonight", "next Monday"), calculate the exact YYYY-MM-DD strictly from the LOCAL TIME REFERENCE.

4. TASK COMPLETIONS & DISAMBIGUATION:
   - If the user states they completed a task, check the ACTIVE OPEN TASKS list.
   - If there is an exact or unambiguous match, add its integer ID to completed_task_ids.
   - If multiple active tasks match the user's description (e.g. "done with the call" when tasks exist for "Call client A" and "Call client B"), DO NOT guess. Leave completed_task_ids empty and explain in ambiguous_task_note which task IDs are matching so the user can clarify.

5. STATUS QUERIES:
   - If the user asks to view expenses or tasks (e.g. "what are my open tasks?", "how much did I spend today?"), populate the query field.

6. STRICT GUARDRAIL:
   - Never populate query, new_tasks, or completed_task_ids unless explicitly mentioned or requested in the user's input.
"""


class ExtractionEngine:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or settings.GROQ_API_KEY
        self.model = model or settings.GROQ_MODEL
        self._groq_client = None
        self._instructor_client = None

    def _get_client(self):
        if not self._instructor_client:
            self._groq_client = AsyncGroq(api_key=self.api_key)
            self._instructor_client = instructor.from_groq(
                self._groq_client, mode=instructor.Mode.JSON
            )
        return self._instructor_client

    async def extract_information(
        self,
        text: str,
        now_local: datetime,
        open_tasks: List[Dict[str, Any]],
    ) -> ExtractedPayload:
        """Extract structured task and expense information from raw message."""
        if not text or not text.strip():
            return ExtractedPayload(
                conversational_reply="I received an empty message."
            )

        client = self._get_client()
        system_prompt = build_system_prompt(now_local, open_tasks)

        try:
            payload: ExtractedPayload = await client.chat.completions.create(
                model=self.model,
                response_model=ExtractedPayload,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                temperature=0.1,
                max_retries=2,
            )
            return payload
        except ValidationError as ve:
            logger.error(f"Pydantic Validation Error in LLM output: {ve}")
            return ExtractedPayload(
                conversational_reply="I couldn't structure that input. If you're logging an expense or task, please check the wording."
            )
        except Exception as e:
            logger.error(f"Groq Extraction API Error: {e}", exc_info=True)
            return ExtractedPayload(
                conversational_reply="I'm having trouble reaching the extraction service. Please try again shortly."
            )
