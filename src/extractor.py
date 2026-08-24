import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field, ValidationError
from groq import AsyncGroq
import instructor

from src.config import settings

logger = logging.getLogger(__name__)

# Ordered list of active models available on user's Groq tier
GROQ_MODEL_CANDIDATES = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.6-27b",
    "groq/compound-mini",
    "allam-2-7b",
    "llama-3.1-8b-instant",
]


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
        description="Clear, actionable task or parent project title.",
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
    phases: List[str] = Field(
        default_factory=list,
        description="List of sub-tasks or phases if the user specified a multi-step task (e.g. ['Phase 1: Wireframes', 'Phase 2: UI Design', 'Phase 3: Testing']).",
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
        description="Brand new to-do tasks to create (including multi-phase projects). Leave empty list if none.",
    )
    completed_task_ids: List[int] = Field(
        default_factory=list,
        description="Exact integer IDs of open tasks or sub-phases from the provided context that the user completed. Leave empty list if none.",
    )
    # Undo / Delete / Edit Actions
    undo_intent: Optional[Literal["EXPENSE", "TASK", "LAST", "NONE"]] = Field(
        default=None,
        description="Populate if user asks to undo the last action (e.g. 'undo', 'cancel last entry', 'undo last expense').",
    )
    delete_expense_id: Optional[int] = Field(
        default=None,
        description="ID of specific expense to delete (e.g. 'delete expense #3').",
    )
    delete_task_id: Optional[int] = Field(
        default=None,
        description="ID of specific task to delete (e.g. 'delete task #5').",
    )
    edit_expense_id: Optional[int] = Field(
        default=None,
        description="ID of expense being modified.",
    )
    edit_expense_amount: Optional[float] = Field(
        default=None,
        description="New amount for the expense.",
    )
    edit_expense_category: Optional[ExpenseCategory] = Field(
        default=None,
        description="New category for the expense.",
    )
    edit_expense_note: Optional[str] = Field(
        default=None,
        description="New note/description for the expense.",
    )
    edit_task_id: Optional[int] = Field(
        default=None,
        description="ID of task being modified.",
    )
    edit_task_description: Optional[str] = Field(
        default=None,
        description="New description for the task.",
    )
    edit_task_priority: Optional[TaskPriority] = Field(
        default=None,
        description="New priority for the task.",
    )
    edit_task_due_date: Optional[str] = Field(
        default=None,
        description="New due date in YYYY-MM-DD.",
    )
    edit_task_due_time: Optional[str] = Field(
        default=None,
        description="New due time in HH:MM.",
    )
    reopen_task_id: Optional[int] = Field(
        default=None,
        description="ID of task to reopen from DONE back to OPEN (e.g. 'reopen task #2').",
    )
    needs_clarification: bool = Field(
        default=False,
        description="Set to true if user input was underspecified or ambiguous (e.g. logged amount without item/category) to avoid making assumptions.",
    )
    clarification_prompt: Optional[str] = Field(
        default=None,
        description="A polite question asking the user for missing details (e.g. 'What was the RM 50 spent on?').",
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
        lines = []
        for t in open_tasks:
            parent_info = f" [Sub-phase of #{t['parent_id']}]" if t.get("parent_id") else ""
            lines.append(f"- [ID: {t['id']}] {t['description']} (Priority: {t.get('priority', 'MEDIUM')}){parent_info}")
        tasks_formatted = "\n".join(lines)
    else:
        tasks_formatted = "No active open tasks."

    return f"""You are Perlica, an intelligent, zero-friction Discord personal assistant tracking expenses, multi-phase tasks, edits/undo, and giving smart advice.

LOCAL TIME REFERENCE:
- Current Local Timestamp: {now_local.strftime('%Y-%m-%d %H:%M:%S')}
- TODAY is: {today_str}
- TOMORROW is: {tomorrow_str}
- YESTERDAY is: {yesterday_str}

ACTIVE OPEN TASKS IN DATABASE:
{tasks_formatted}

EXTRACTION & ZERO-ASSUMPTION RULES:
1. MALAYSIAN LOCAL CONTEXT & VENDOR MAPPING:
   - **Transport**: TNG, Touch 'n Go, Touch n Go reload/topup, RFID, Tolls (PLUS, LDP, MEX, SMART), Parking, Petrol/Fuel (RON95, RON97, Diesel, Shell, Petronas, Caltex, BHP, Petron), Grab ride, AirAsia Ride, LRT, MRT, Monorail, KTM, RapidKL.
   - **Food & Dining**: Mamak, Kopitiam, Hawker, Nasi Kandar, Roti Canai, Teh Tarik, Nasi Lemak, GrabFood, FoodPanda, ShopeeFood, Cafes, Restaurants.
   - **Groceries**: 99 Speedmart, Speedmart, Lotus's, Jaya Grocer, Village Grocer, Aeon, Econsave, Mydin, NSK, Pasar Malam, Wet Market.
   - **Utilities & Bills**: TNB (electricity), Air Selangor / Syabas (water), Indah Water (IWK), Astro, Unifi, TIME, Maxis, CelcomDigi, U Mobile, prepaid/postpaid phone reload.
   - **Shopping**: Shopee, Lazada, TikTok Shop, Taobao, MR DIY, Uniqlo, Retail stores.
   - **Health & Personal**: Watsons, Guardian, Caring, Big Pharmacy, Klinik, Hospital, Gym, Haircut.

2. ZERO-ASSUMPTION POLICY (NEEDS CLARIFICATION):
   - If the user provides an expense amount without ANY item, vendor, or category context (e.g. "Spent RM 50", "Paid 30", "RM 100 spent"), DO NOT guess or assume it's food. Set needs_clarification=True, clarification_prompt="What did you spend the RM 50 on? (e.g. Food & Dining, Groceries, Transport / TNG, Shopping, Utilities)?", and leave expenses=[].
   - If the user provides clear local context (e.g. "Reload TNG RM 50", "99 Speedmart RM 32", "RON95 RM 40", "Mamak lunch RM 12"), log it immediately under the correct category without asking.

3. UNDO, DELETE, EDIT & REOPEN:
   - If user says "undo", "cancel that", "undo last": set undo_intent="LAST" (or "EXPENSE"/"TASK").
   - If user says "delete expense #3": set delete_expense_id=3.
   - If user says "delete task #5": set delete_task_id=5.
   - If user says "change expense #2 amount to 25": set edit_expense_id=2, edit_expense_amount=25.0.
   - If user says "update task #4 due date to tomorrow": set edit_task_id=4, edit_task_due_date calculated from tomorrow.
   - If user says "reopen task #1" or "mark task #1 open": set reopen_task_id=1.

4. MULTI-PHASE TASKS:
   - If the user specifies sub-steps or phases (e.g. "Create task 'Website launch' with 3 phases: 1. Wireframes, 2. Frontend, 3. Testing"), populate TaskItem with description="Website launch" and phases=["Wireframes", "Frontend", "Testing"].

5. ON-DEMAND SUMMARIES & RECAPS:
   - If the user asks for a summary (e.g. 'summarize today', 'recap my day', 'how did I do today?', 'summary of this week'), populate query with query_target='SUMMARY' and appropriate timeframe.

6. TASK COMPLETIONS:
   - Match completed tasks against ACTIVE OPEN TASKS by exact integer ID. If ambiguous, explain in ambiguous_task_note with conflicting task IDs.

7. CASUAL CONVERSATION:
   - For greetings, check-ins, or questions without data logging (e.g. 'I just woke up', 'hello', 'how can you help me?'), provide a warm, concise conversational_reply.
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
        """Extract structured task, expense, query, or conversational information with automatic model fallback."""
        if not text or not text.strip():
            return ExtractedPayload(
                conversational_reply="I received an empty message."
            )

        client = self._get_client()
        system_prompt = build_system_prompt(now_local, open_tasks)

        # Build candidate list starting with preferred model
        models_to_try = [self.model] + [m for m in GROQ_MODEL_CANDIDATES if m != self.model]

        last_error = None
        for candidate_model in models_to_try:
            try:
                payload: ExtractedPayload = await client.chat.completions.create(
                    model=candidate_model,
                    response_model=ExtractedPayload,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text},
                    ],
                    temperature=0.1,
                    max_retries=1,
                )
                self.model = candidate_model
                return payload
            except ValidationError as ve:
                logger.error(f"Pydantic Validation Error in LLM output ({candidate_model}): {ve}")
                return ExtractedPayload(
                    conversational_reply="I couldn't structure that input. If you're logging an expense or task, please check the wording."
                )
            except Exception as e:
                err_str = str(e)
                logger.warning(f"Model {candidate_model} failed ({err_str[:100]}). Trying next candidate if available...")
                last_error = e
                continue

        logger.error(f"All Groq model candidates failed: {last_error}", exc_info=True)
        return ExtractedPayload(
            conversational_reply="I'm having trouble reaching the extraction service. Please check your Groq API key or try again in a moment."
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

        models_to_try = [self.model] + [m for m in GROQ_MODEL_CANDIDATES if m != self.model]
        for candidate in models_to_try:
            try:
                response = await groq_client.chat.completions.create(
                    model=candidate,
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
                logger.warning(f"Insight candidate {candidate} failed. Trying next...")
                continue

        return "Keep up the great momentum! Let me know whenever you want to log new tasks or expenses."
