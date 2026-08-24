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
    query_target: Literal["EXPENSES", "TASKS", "SUMMARY", "ADVICE", "GENERAL"] = Field(
        ...,
        description="Target dataset or intent to view/analyze.",
    )
    timeframe: Literal["TODAY", "YESTERDAY", "THIS_WEEK", "THIS_MONTH", "ALL_TIME"] = Field(
        default="TODAY",
        description="Timeframe for the query or summary.",
    )
    specific_question: Optional[str] = Field(
        default=None,
        description="The specific question the user asked (e.g. 'how much did I spend on food?' or 'what should I work on next?').",
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
        description="Populate if the user is requesting a summary, report, status list, spending analysis, or asking a question about tasks/budget.",
    )
    conversational_reply: Optional[str] = Field(
        default=None,
        description="Friendly response if the message was general conversation, greeting, status update, advice request, or casual chat.",
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

    return f"""You are an intelligent, zero-friction Discord personal task & expense tracking assistant and conversational companion.

LOCAL TIME REFERENCE:
- Current Local Timestamp: {now_local.strftime('%Y-%m-%d %H:%M:%S')}
- TODAY is: {today_str}
- TOMORROW is: {tomorrow_str}
- YESTERDAY is: {yesterday_str}

ACTIVE OPEN TASKS IN DATABASE:
{tasks_formatted}

EXTRACTION & INTENT RULES:
1. ON-DEMAND SUMMARIES & RECAPS:
   - If the user asks for a summary (e.g. 'summarize today', 'give me a recap of my day', 'how did I do today?', 'summary of this week'), populate query with query_target='SUMMARY' and appropriate timeframe.

2. QUESTIONS ABOUT EXPENSES & TASKS:
   - If the user asks specific questions (e.g. 'how much did I spend on food?', 'what tasks are due tomorrow?', 'did I finish the report?'), populate query with query_target='EXPENSES' or 'TASKS' or 'ADVICE', and capture the specific_question.

3. CASUAL CONVERSATION & CHAT:
   - If the user sends casual messages, greetings, check-ins, or questions without data logging (e.g. 'I just woke up', 'hello', 'how can you help me?', 'feeling overwhelmed'), set expenses=[], new_tasks=[], completed_task_ids=[], and provide an engaging, empathetic conversational_reply.

4. EXPENSES:
   - Extract amount as a numeric float in MYR (e.g., 'RM 15.50 lunch' -> amount=15.50, category='Food & Dining', note='lunch').
   - Categorize accurately into: Food & Dining, Transport, Groceries, Utilities & Bills, Entertainment, Shopping, Health & Personal, Other.

5. NEW TASKS:
   - Extract action items as new_tasks with appropriate priority (HIGH, MEDIUM, LOW).
   - If a due date is specified (e.g., 'tomorrow', 'tonight', 'next Monday'), calculate the exact YYYY-MM-DD strictly from the LOCAL TIME REFERENCE.

6. TASK COMPLETIONS & DISAMBIGUATION:
   - Match completed tasks against ACTIVE OPEN TASKS by exact integer ID.
   - If ambiguous, explain in ambiguous_task_note with the conflicting task IDs.
"""


class ExtractionEngine:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or settings.GROQ_API_KEY
        self.model = model or settings.GROQ_MODEL
        self._groq_client = None
        self._instructor_client = None

    def _get_groq_client(self) -> AsyncGroq:
        if not self._groq_client:
            self._groq_client = AsyncGroq(api_key=self.api_key)
        return self._groq_client

    def _get_client(self):
        if not self._instructor_client:
            raw_client = self._get_groq_client()
            self._instructor_client = instructor.from_groq(
                raw_client, mode=instructor.Mode.JSON
            )
        return self._instructor_client

    async def extract_information(
        self,
        text: str,
        now_local: datetime,
        open_tasks: List[Dict[str, Any]],
    ) -> ExtractedPayload:
        """Extract structured task, expense, query, or conversational information."""
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

    async def generate_ai_insight(
        self,
        prompt_topic: str,
        snapshot_data: Dict[str, Any],
        now_local: datetime,
    ) -> str:
        """Generate smart AI commentary or answer based on real-time database snapshot."""
        groq_client = self._get_groq_client()

        expenses_summary = ", ".join(
            [f"{cat}: RM {amt:.2f}" for cat, amt in snapshot_data.get("category_breakdown", {}).items()]
        ) or "None"

        completed_tasks_str = ", ".join(
            [t["description"] for t in snapshot_data.get("completed_tasks", [])]
        ) or "None"

        open_tasks_str = ", ".join(
            [f"[{t['priority']}] {t['description']}" for t in snapshot_data.get("open_tasks", [])]
        ) or "None"

        context = f"""Current Local Time: {now_local.strftime('%Y-%m-%d %H:%M:%S')}
Total Spent: RM {snapshot_data.get('total_spent', 0.0):.2f}
Spending by Category: {expenses_summary}
Tasks Completed: {completed_tasks_str}
Active Open Tasks: {open_tasks_str}
"""

        try:
            response = await groq_client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are Perlica, an encouraging, sharp personal productivity and financial AI companion. "
                            "Given the user's live financial and task data, provide a concise, engaging, and highly helpful response. "
                            "Keep it under 3-4 sentences. Highlight achievements and remind them gently of high-priority tasks."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Context:\n{context}\n\nUser Question/Request: {prompt_topic}",
                    },
                ],
                temperature=0.5,
                max_tokens=250,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Failed to generate AI insight: {e}")
            return "Keep up the momentum! Let me know whenever you want to log new tasks or expenses."
