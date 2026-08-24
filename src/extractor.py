import logging
import io
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional, Literal, Dict, Any, Tuple
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
    INVESTMENT = "Investments & Savings"
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
    query_target: Literal["EXPENSES", "TASKS", "SUMMARY", "ADVICE", "BUDGETS", "BILLS", "GENERAL"] = Field(
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
    undo_intent: Optional[Literal["EXPENSE", "TASK", "BILL", "LAST", "NONE"]] = Field(
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
    # Budget Configuration
    set_budget_category: Optional[str] = Field(
        default=None,
        description="Category name to set monthly budget limit for (e.g. 'Food & Dining', 'Entertainment', 'Transport', or 'Total').",
    )
    set_budget_amount: Optional[float] = Field(
        default=None,
        description="Monthly budget limit amount as a number (e.g. 800.0). Extract numeric value even if written with $, RM, or words.",
    )
    # Recurring Bill Configuration & Editing (Human in the loop reminders only)
    add_bill_name: Optional[str] = Field(
        default=None,
        description="Name of recurring bill or investment (e.g. 'Unifi', 'Netflix', 'Rent', 'S&P500').",
    )
    add_bill_amount: Optional[float] = Field(
        default=None,
        description="Numeric amount of recurring bill or investment (e.g. 100.0). Strip $, RM, USD.",
    )
    add_bill_category: Optional[ExpenseCategory] = Field(
        default=None,
        description="Category of the recurring bill.",
    )
    add_bill_day: Optional[int] = Field(
        default=None,
        description="Day of the month the bill is due (1-31).",
    )
    edit_bill_id: Optional[int] = Field(
        default=None,
        description="Exact integer ID of active recurring bill being edited from ACTIVE RECURRING BILLS IN DATABASE.",
    )
    edit_bill_name: Optional[str] = Field(
        default=None,
        description="New name for recurring bill.",
    )
    edit_bill_amount: Optional[float] = Field(
        default=None,
        description="New amount for recurring bill as a number (e.g. 400.0).",
    )
    edit_bill_category: Optional[ExpenseCategory] = Field(
        default=None,
        description="New category for recurring bill.",
    )
    edit_bill_day: Optional[int] = Field(
        default=None,
        description="New day of the month for recurring bill.",
    )
    delete_bill_id: Optional[int] = Field(
        default=None,
        description="Exact integer ID of recurring bill to delete from ACTIVE RECURRING BILLS IN DATABASE.",
    )
    # CSV Data Export
    export_csv: bool = Field(
        default=False,
        description="Set to true if user wants to download or export their expenses as a CSV/spreadsheet file.",
    )
    # Clarification
    needs_clarification: bool = Field(
        default=False,
        description="Set to true ONLY if the message is an expense with ZERO item or vendor (e.g. 'Spent RM 50'). For recurring bills, tasks, or when item is known, keep false.",
    )
    clarification_prompt: Optional[str] = Field(
        default=None,
        description="A polite question asking the user for missing details.",
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


def build_system_prompt(
    now_local: datetime,
    open_tasks: List[Dict[str, Any]],
    recurring_bills: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Build a comprehensive system prompt with local temporal anchors, open tasks, and active recurring bills."""
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

    if recurring_bills:
        b_lines = []
        for b in recurring_bills:
            b_lines.append(f"- [Bill ID: {b['id']}] {b['name']}: RM {b['amount']:.2f} (Category: {b['category']}) on the {b['day_of_month']}th")
        bills_formatted = "\n".join(b_lines)
    else:
        bills_formatted = "No active recurring bills."

    return f"""You are Perlica, an intelligent, zero-friction Discord personal assistant tracking expenses, multi-phase tasks, budgets, recurring bills, and giving smart advice.

LOCAL TIME REFERENCE:
- Current Local Timestamp: {now_local.strftime('%Y-%m-%d %H:%M:%S')}
- TODAY is: {today_str}
- TOMORROW is: {tomorrow_str}
- YESTERDAY is: {yesterday_str}

ACTIVE OPEN TASKS IN DATABASE:
{tasks_formatted}

ACTIVE RECURRING BILLS IN DATABASE:
{bills_formatted}

EXTRACTION & ZERO-ASSUMPTION RULES:
1. MALAYSIAN LOCAL CONTEXT & VENDOR MAPPING:
   - **Transport**: TNG, Touch 'n Go, Touch n Go reload/topup, RFID, Tolls (PLUS, LDP, MEX, SMART), Parking, Petrol/Fuel (RON95, RON97, Diesel, Shell, Petronas, Caltex, BHP, Petron), Grab ride, AirAsia Ride, LRT, MRT, Monorail, KTM, RapidKL.
   - **Food & Dining**: Mamak, Kopitiam, Hawker, Nasi Kandar, Roti Canai, Teh Tarik, Nasi Lemak, GrabFood, FoodPanda, ShopeeFood, Cafes, Restaurants.
   - **Groceries**: 99 Speedmart, Speedmart, Lotus's, Jaya Grocer, Village Grocer, Aeon, Econsave, Mydin, NSK, Pasar Malam, Wet Market.
   - **Utilities & Bills**: TNB (electricity), Air Selangor / Syabas (water), Indah Water (IWK), Astro, Unifi, TIME, Maxis, CelcomDigi, U Mobile, prepaid/postpaid phone reload.
   - **Entertainment**: In-game top-ups, monthly cards & passes (e.g. Endfield / Arknights Endfield, Arknights, Genshin Welkin, HSR Express Pass, ZZZ, Wuthering Waves, Blue Archive, Nikke, FGO, MLBB diamonds, Valorant Points, Roblox Robux, Battle Pass, Season Pass), Gaming stores & platforms (Codashop, UniPin, Razer Gold, Steam, PlayStation PSN, Nintendo eShop, Epic Games), Subscriptions (Discord Nitro, Spotify, Netflix, YouTube Premium, Disney+, cinema tickets, board games).
   - **Investments & Savings**: S&P 500 / S&P500 / SNP 500, ETFs, Stocks, Mutual Funds, Crypto, Bitcoin, ETH, ASB, EPF / KWSP, Tabung Haji, Gold, StashAway, Versa, Wahed, Luno, monthly DCA / recurring investment buys.
   - **Shopping**: Shopee, Lazada, TikTok Shop, Taobao, MR DIY, Uniqlo, Retail stores, Gadgets, Clothes, Physical goods.
   - **Health & Personal**: Medical/Doctor/Clinic/Klinik/Hospital, Pharmacy (Watsons, Guardian, Caring, Big Pharmacy), Vitamins/Supplements, Skincare, Haircut, Grooming, Gym membership. (Note: In-game passes or monthly cards are NEVER Health & Personal).

2. CURRENCY & AMOUNTS:
   - Extract numeric amounts directly into amount fields (e.g. "$100", "RM 100", "100 USD", "100" -> 100.0).
   - DO NOT convert currencies. If user says '$100' or 'RM 100', record 100.0.
   - NEVER trigger needs_clarification if a number or currency figure is present with an item/vendor.

3. ZERO-ASSUMPTION POLICY:
   - Only set needs_clarification=True if the message contains ONLY a raw number with NO context or vendor (e.g. "Spent 50", "Paid 30").
   - If an item or vendor is present (e.g. "recurring buy $100 worth of s&p500 on the 27th"), set needs_clarification=False and extract all fields.

4. RECURRING BILLS & INVESTMENTS EXAMPLES:
   - "recurring buy $100 worth of s&p500 on the 27th on every month":
     add_bill_name="S&P500", add_bill_amount=100.0, add_bill_category=ExpenseCategory.INVESTMENT, add_bill_day=27, needs_clarification=False
   - "edit the recurring buy to 400":
     edit_bill_id=<matched_id>, edit_bill_amount=400.0, needs_clarification=False
   - "delete recurring bill #1":
     delete_bill_id=1, needs_clarification=False

4. BUDGET COMMANDS:
   - "Set monthly food budget to 800": set set_budget_category="Food & Dining", set_budget_amount=800.0.

5. CSV EXPORT:
   - "export expenses", "download csv", "export to excel", "export this month": set export_csv=True.

6. UNDO, DELETE, EDIT & REOPEN:
   - "undo", "cancel that", "undo last": set undo_intent="LAST" (or "EXPENSE"/"TASK"/"BILL").
   - "delete expense #3": set delete_expense_id=3.
   - "delete task #5": set delete_task_id=5.
   - "change expense #2 amount to 25": set edit_expense_id=2, edit_expense_amount=25.0.
   - "update task #4 due date to tomorrow": set edit_task_id=4, edit_task_due_date calculated from tomorrow.
   - "reopen task #1" or "mark task #1 open": set reopen_task_id=1.

7. MULTI-PHASE TASKS:
   - "Create task 'Website launch' with 3 phases: 1. Wireframes, 2. Frontend, 3. Testing": TaskItem with description="Website launch" and phases=["Wireframes", "Frontend", "Testing"].

8. ON-DEMAND SUMMARIES & RECAPS:
   - "summarize today", "recap my day", "summary of this week": query with query_target='SUMMARY' and timeframe.

9. TASK COMPLETIONS:
   - Match completed tasks against ACTIVE OPEN TASKS by exact integer ID.

10. CASUAL CONVERSATION:
   - For greetings, check-ins, or questions without data logging, provide a warm conversational_reply.
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

    async def transcribe_audio(self, audio_file_tuple: Tuple[str, bytes]) -> str:
        """
        Transcribe voice note or audio bytes using Groq's whisper-large-v3-turbo.
        Properly handles Discord mobile .ogg (Opus) files and other formats.
        """
        import io
        groq_client = self._get_groq_client()
        raw_filename, file_bytes = audio_file_tuple

        lower_name = raw_filename.lower()
        if lower_name.endswith(".ogg") or "voice-message" in lower_name:
            filename = "audio.ogg"
        elif lower_name.endswith(".mp3"):
            filename = "audio.mp3"
        elif lower_name.endswith(".m4a"):
            filename = "audio.m4a"
        elif lower_name.endswith(".wav"):
            filename = "audio.wav"
        else:
            filename = "audio.ogg"

        try:
            audio_buffer = io.BytesIO(file_bytes)
            audio_buffer.name = filename

            transcription = await groq_client.audio.transcriptions.create(
                file=audio_buffer,
                model="whisper-large-v3-turbo",
                response_format="json",
            )
            return transcription.text.strip()
        except Exception as e:
            logger.error(f"Whisper transcription failed for {filename}: {e}", exc_info=True)
            return ""

    async def extract_information(
        self,
        text: str,
        now_local: datetime,
        open_tasks: List[Dict[str, Any]],
        recurring_bills: Optional[List[Dict[str, Any]]] = None,
    ) -> ExtractedPayload:
        """Extract structured task, expense, query, or conversational information with automatic model fallback."""
        if not text or not text.strip():
            return ExtractedPayload(
                conversational_reply="I received an empty message."
            )

        client = self._get_client()
        system_prompt = build_system_prompt(now_local, open_tasks, recurring_bills)

        models_to_try = list(GROQ_MODEL_CANDIDATES)

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

    async def extract_from_image(
        self,
        image_bytes: bytes,
        filename: str,
        now_local: datetime,
        open_tasks: List[Dict[str, Any]],
        recurring_bills: Optional[List[Dict[str, Any]]] = None,
    ) -> ExtractedPayload:
        """Extract expense data directly from a receipt, invoice, or screenshot image using Groq Vision."""
        import base64
        import json

        groq_client = self._get_groq_client()
        ext = filename.lower().split(".")[-1] if "." in filename else "jpeg"
        mime_type = f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext}"
        b64_img = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:{mime_type};base64,{b64_img}"

        vision_models = ["llama-3.2-11b-vision-preview", "llama-3.2-90b-vision-preview"]
        system_prompt = build_system_prompt(now_local, open_tasks, recurring_bills)

        ocr_prompt = (
            "Analyze this receipt or bill image. Identify the merchant/store name, total amount spent in MYR (or currency number), "
            "and appropriate category (Food & Dining, Groceries, Transport, Utilities & Bills, Entertainment, Shopping, Health & Personal, Investments & Savings). "
            "Return structured JSON matching: "
            '{"expenses": [{"amount": <number>, "category": "<Category>", "note": "<Store/Item name>"}]}'
        )

        for v_model in vision_models:
            try:
                response = await groq_client.chat.completions.create(
                    model=v_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": ocr_prompt},
                                {"type": "image_url", "image_url": {"url": data_url}},
                            ],
                        },
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                )
                raw_json = json.loads(response.choices[0].message.content)
                expenses = []
                for item in raw_json.get("expenses", []):
                    cat_val = item.get("category", "Other")
                    matched_cat = ExpenseCategory.OTHER
                    for c in ExpenseCategory:
                        if c.value.lower() == str(cat_val).lower():
                            matched_cat = c
                            break
                    expenses.append(
                        ExpenseItem(
                            amount=float(item.get("amount", 0.0)),
                            category=matched_cat,
                            note=item.get("note") or "Receipt scan",
                        )
                    )
                if expenses:
                    return ExtractedPayload(expenses=expenses)
            except Exception as e:
                logger.warning(f"Vision model {v_model} failed: {e}. Trying next vision model if available...")
                continue

        # If vision models unavailable or failed
        return ExtractedPayload(
            conversational_reply="I received your image, but couldn't read the receipt details. You can log it by typing e.g. *'99 Speedmart RM 35.50'*."
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

        models_to_try = list(GROQ_MODEL_CANDIDATES)
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
