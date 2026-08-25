import json
import re
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from typing import Optional, List, Dict, Any, Tuple
import httpx
from src.config import settings

from groq import AsyncGroq
from src.extractor import GROQ_MODEL_CANDIDATES

logger = logging.getLogger(__name__)

MAX_WIZARD_TIMEOUT_SECONDS = 900  # 15 minutes


@dataclass
class GoalWizardState:
    user_id: int
    step: int = 0
    goal_name: str = ""
    goal_category: str = "Custom"
    target_amount: float = 0.0
    current_amount: float = 0.0
    target_date: Optional[str] = None
    target_date_human: str = ""
    notes: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    milestones: List[Dict[str, Any]] = field(default_factory=list)
    conversation_history: List[Dict[str, str]] = field(default_factory=list)
    is_ready_for_review: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GoalWizardState":
        return cls(
            user_id=data.get("user_id", 0),
            step=data.get("step", 0),
            goal_name=data.get("goal_name", ""),
            goal_category=data.get("goal_category", "Custom"),
            target_amount=float(data.get("target_amount", 0.0) or 0.0),
            current_amount=float(data.get("current_amount", 0.0) or 0.0),
            target_date=data.get("target_date"),
            target_date_human=data.get("target_date_human", ""),
            notes=data.get("notes"),
            metadata=data.get("metadata", {}),
            milestones=data.get("milestones", []),
            conversation_history=data.get("conversation_history", []),
            is_ready_for_review=bool(data.get("is_ready_for_review", False)),
        )


def normalize_iso_date(raw_date_str: Optional[str], fallback_human: str = "") -> Tuple[Optional[str], str]:
    """
    Deterministically normalizes natural language date strings to strict ISO-8601 YYYY-MM-DD.
    Returns (iso_str, human_display_str).
    """
    if not raw_date_str:
        return None, fallback_human

    clean = raw_date_str.strip()

    # Exact YYYY-MM-DD match
    if re.match(r"^\d{4}-\d{2}-\d{2}$", clean):
        try:
            datetime.strptime(clean, "%Y-%m-%d")
            return clean, fallback_human or clean
        except ValueError:
            pass

    # YYYY-MM (e.g. 2027-01 -> 2027-01-31)
    if re.match(r"^\d{4}-\d{2}$", clean):
        try:
            dt = datetime.strptime(f"{clean}-01", "%Y-%m-%d")
            # compute month end
            if dt.month == 12:
                next_month = dt.replace(year=dt.year + 1, month=1, day=1)
            else:
                next_month = dt.replace(month=dt.month + 1, day=1)
            month_end = next_month - date.resolution
            return month_end.strftime("%Y-%m-%d"), fallback_human or clean
        except Exception:
            pass

    return None, fallback_human or clean


async def call_groq_llm(messages: List[Dict[str, str]], groq_api_key: str) -> str:
    """Invoke Groq API with robust shared model candidates and backoff."""
    client = AsyncGroq(api_key=groq_api_key)
    candidate_models = [settings.GROQ_MODEL] if settings.GROQ_MODEL else []
    for m in GROQ_MODEL_CANDIDATES:
        if m and m not in candidate_models:
            candidate_models.append(m)

    last_err = None
    for model in candidate_models:
        try:
            res = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            content = res.choices[0].message.content
            if content:
                return content
        except Exception as e:
            last_err = str(e)
            logger.warning(f"Groq goal wizard model '{model}' failed: {e}")
            continue

    raise RuntimeError(f"All Groq model attempts failed for Goal Wizard: {last_err}")


def build_wizard_system_prompt(now_local: datetime) -> str:
    today_str = now_local.strftime("%Y-%m-%d (%A)")
    current_year = now_local.year
    return f"""You are the intelligent Goal Interview Architect for Perlica, a Malaysian personal finance and productivity assistant.
Current Local Date: {today_str} (Year: {current_year})

Your job is to conduct a short, highly tailored, dynamic 2-3 turn conversation to help the user design a rich, actionable Goal Blueprint.

### CORE GOAL TYPES & DOMAIN RULES:
1. **Travel / Vacation (e.g. Japan trip, Europe trip)**:
   - Identify destination, target month/year, total estimated budget.
   - Ask about flights (booked? cost included?), accommodation, shopping/food cash.
   - Create clear subtasks: "Book flight tickets", "Reserve accommodation", "Exchange local currency / cash".
2. **Gadget / Purchase (e.g. MacBook Pro, Phone, Camera)**:
   - Identify model/item, expected retail price, target timeline.
   - Ask if waiting for seasonal sales (11.11, 12.12, Black Friday) or looking for specific discounts.
   - Create subtasks: "Monitor sale promotions", "Save required deposit", "Purchase item".
3. **Emergency Fund / Financial Milestones**:
   - Monthly target, target safety buffer (e.g. 3-6 months expenses), high-yield storage (e.g. KDI, Versa, ASB).
4. **Project / Education / Career**:
   - Course fees, certification exams, equipment needed, deadline.

### TURN LOGIC:
- If this is the first turn or essential details are missing (Target Amount in RM/MYR, Target Date, Key Subtasks), ask 1-2 focused, conversational questions. Set "is_ready_for_review": false.
- If the user has provided enough information (usually after 2 turns or if they gave a comprehensive first prompt), generate the complete Goal Blueprint with "is_ready_for_review": true.
- Keep questions friendly, concise, and natural (under 3 sentences).

### RESPONSE SCHEMA (Strict JSON):
{{
  "is_ready_for_review": false,
  "question": "Awesome! When in 2027 do you plan to go to Japan, and have you secured flight tickets yet?",
  "goal_name": "Japan Trip 2027",
  "category": "Travel",
  "target_amount": 6000.0,
  "target_date_iso": "2027-01-31",
  "target_date_human": "January 2027",
  "notes": "Looking for flight under RM1,800 and 20% hotel discount",
  "metadata": {{
     "destination": "Japan",
     "target_season": "Winter 2027",
     "expected_discount": "20%"
  }},
  "milestones": [
     {{"title": "Book return flight tickets", "estimated_cost": 1800.0, "is_completed": false}},
     {{"title": "Reserve Tokyo & Kyoto accommodation", "estimated_cost": 2000.0, "is_completed": false}},
     {{"title": "Exchange JPY cash for food & shopping", "estimated_cost": 2200.0, "is_completed": false}}
  ]
}}
"""


async def process_wizard_turn(
    user_id: int,
    user_message: str,
    db_manager: Any,
    groq_api_key: str,
) -> Tuple[GoalWizardState, str]:
    """
    Process a single turn of the Goal Wizard interview.
    Returns: (updated_state, message_to_user)
    """
    now_local = datetime.now(settings.tz)
    session_data = await db_manager.get_wizard_session(user_id, max_age_seconds=MAX_WIZARD_TIMEOUT_SECONDS)

    if session_data:
        state = GoalWizardState.from_dict(session_data)
        state.step += 1
    else:
        state = GoalWizardState(user_id=user_id, step=1)

    state.conversation_history.append({"role": "user", "content": user_message})

    # Prepare LLM messages
    sys_prompt = build_wizard_system_prompt(now_local)
    messages = [{"role": "system", "content": sys_prompt}]
    for turn in state.conversation_history:
        messages.append(turn)

    raw_response = await call_groq_llm(messages, groq_api_key)
    try:
        data = json.loads(raw_response)
    except Exception as e:
        logger.error(f"Failed to parse LLM wizard response JSON: {raw_response} ({e})")
        data = {
            "is_ready_for_review": False,
            "question": "Could you share how much you target to save for this goal and your planned deadline?",
            "goal_name": state.goal_name or user_message[:40],
            "category": "Custom",
            "target_amount": state.target_amount or 0.0,
            "target_date_iso": None,
            "target_date_human": "",
            "notes": "",
            "metadata": {},
            "milestones": [],
        }

    # Update state
    state.is_ready_for_review = bool(data.get("is_ready_for_review", False))
    if data.get("goal_name"):
        state.goal_name = str(data["goal_name"]).strip()
    elif not state.goal_name:
        state.goal_name = user_message.strip()[:50]

    if data.get("category"):
        state.goal_category = str(data["category"]).strip()

    if data.get("target_amount") is not None:
        try:
            state.target_amount = float(data["target_amount"])
        except (ValueError, TypeError):
            pass

    raw_iso = data.get("target_date_iso")
    raw_human = data.get("target_date_human") or ""
    state.target_date, state.target_date_human = normalize_iso_date(raw_iso, raw_human)

    if data.get("notes"):
        state.notes = str(data["notes"]).strip()
    if data.get("metadata"):
        state.metadata.update(data["metadata"])

    if data.get("milestones"):
        state.milestones = data["milestones"]

    bot_reply = data.get("question") or "Got it! Let me organize this goal for you."
    state.conversation_history.append({"role": "assistant", "content": bot_reply})

    # Save to SQLite
    await db_manager.save_wizard_session(user_id, state.to_dict())

    return state, bot_reply
