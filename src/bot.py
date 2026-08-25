import io
import re
import asyncio
import logging
import datetime
from typing import Optional, List, Dict, Any, Callable, Tuple
from datetime import timedelta
import time
import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.config import settings
from src.database import (
    DatabaseManager,
    normalize_canonical_asset,
    classify_fuel_expense,
    calculate_fuel_details,
)
from src.extractor import (
    ExtractionEngine,
    ExtractedPayload,
    ExpenseItem,
    TaskItem,
    ExpenseCategory,
    TaskPriority,
    resolve_category_from_text,
)
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
    format_live_dashboard,
    format_task_snooze_embed,
    format_presets_embed,
    format_goals_overview,
    format_search_results,
    format_calendar_day_view,
    format_investments_overview,
    format_milestone_celebration,
    format_category_filtered_view,
    format_voice_transcription_preview,
    format_bill_reminder_embed,
    format_transaction_page,
    format_focus_task_embed,
    get_time_aware_greeting,
    get_upcoming_malaysian_holidays,
    format_fuel_receipt_embed,
    generate_html_report,
    render_progress_bar,
    format_copilot_answer_embed,
    format_copilot_answer_embeds,
    format_sources_dashboard_embed,
    format_ingest_hub_embed,
    CopilotAnswerView,
    SourcesDashboardView,
)
from src.security import is_user_authorized_for_copilot, scan_content_for_secrets
from src.github_sync import (
    fetch_github_repo_tree,
    fetch_github_blob_content,
    is_eligible_repo_file,
    chunk_python_code,
    chunk_generic_code,
    MAX_REPO_FILES,
)
from src.pdf_parser import parse_pdf_file, MAX_PDF_SIZE_BYTES
from src.web_scraper import scrape_webpage
from src.rag_engine import (
    synthesize_copilot_answer,
    compute_embeddings_batch,
    chunk_markdown_text,
    MODEL_ID,
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


# --- NUMERIC & CATEGORY HELPERS ---

def clean_float_input(val_str: Optional[str], default: float = 0.0) -> float:
    """Safely strip RM, $, commas, and whitespace from user input and parse as float."""
    if not val_str:
        return default
    cleaned = re.sub(r"[^\d.]", "", val_str)
    try:
        return float(cleaned) if cleaned else default
    except ValueError:
        return default


# --- NATIVE DISCORD POPUP EDIT MODALS ---

class ExpenseEditModal(discord.ui.Modal, title="✏️ Edit Expense Entry"):
    """Popup form modal using native Discord TextInputs with auto-category resolution."""

    amount_input = discord.ui.TextInput(
        label="Amount (RM / Number)",
        placeholder="e.g. 15.50",
        required=True,
        max_length=20,
    )
    category_input = discord.ui.TextInput(
        label="Category (Food, Transport, Bills, etc.)",
        placeholder="e.g. Food & Dining, Shopping, Transport",
        required=False,
        max_length=50,
    )
    note_input = discord.ui.TextInput(
        label="Description / Vendor Note",
        placeholder="e.g. Chicken rice lunch",
        required=False,
        max_length=150,
    )

    def __init__(self, payload: ExtractedPayload, parent_view: "ActionIngestionView"):
        super().__init__()
        self.payload = payload
        self.parent_view = parent_view

        if payload.expenses:
            exp = payload.expenses[0]
            self.amount_input.default = f"{exp.amount:.2f}"
            cat_val = exp.category.value if hasattr(exp.category, "value") else str(exp.category)
            self.category_input.default = cat_val
            self.note_input.default = exp.note or ""
        else:
            self.category_input.default = ExpenseCategory.OTHER.value

    async def on_submit(self, interaction: discord.Interaction):
        amt = clean_float_input(self.amount_input.value, default=0.0)
        cat_text = self.category_input.value.strip() or "Other"
        cat = resolve_category_from_text(cat_text)
        note = self.note_input.value.strip() or None

        if self.payload.expenses:
            self.payload.expenses[0].amount = amt
            self.payload.expenses[0].category = cat
            self.payload.expenses[0].note = note
        else:
            self.payload.expenses.append(ExpenseItem(amount=amt, category=cat, note=note))

        expenses_preview = [
            {
                "amount": e.amount,
                "category": e.category.value if hasattr(e.category, "value") else str(e.category),
                "note": e.note,
                "occurred_date": e.occurred_date,
            }
            for e in self.payload.expenses
        ]
        tasks_preview = [
            {
                "description": t.description,
                "priority": t.priority.value if hasattr(t.priority, "value") else str(t.priority),
                "due_date": t.due_date,
                "due_time": t.due_time,
                "phases": t.phases,
            }
            for t in self.payload.new_tasks
        ]

        new_embed = format_action_preview(
            payload=self.payload,
            expenses=expenses_preview,
            tasks=tasks_preview,
            completed_task_ids=self.payload.completed_task_ids,
        )
        await interaction.response.edit_message(
            content="✏️ *Preview updated from your popup edits:*",
            embed=new_embed,
            view=self.parent_view,
        )


class TaskEditModal(discord.ui.Modal, title="✏️ Edit Task Entry"):
    """Popup form modal using native Discord TextInputs."""

    desc_input = discord.ui.TextInput(
        label="Task Description",
        placeholder="e.g. Finish research paper",
        required=True,
        max_length=200,
    )
    priority_input = discord.ui.TextInput(
        label="Priority (HIGH, MEDIUM, LOW)",
        placeholder="e.g. HIGH",
        required=False,
        max_length=20,
    )
    due_date_input = discord.ui.TextInput(
        label="Due Date (YYYY-MM-DD)",
        placeholder="e.g. 2026-08-30",
        required=False,
        max_length=20,
    )

    def __init__(self, payload: ExtractedPayload, parent_view: "ActionIngestionView"):
        super().__init__()
        self.payload = payload
        self.parent_view = parent_view

        if payload.new_tasks:
            t = payload.new_tasks[0]
            self.desc_input.default = t.description
            prio_val = t.priority.value if hasattr(t.priority, "value") else str(t.priority)
            self.priority_input.default = prio_val
            self.due_date_input.default = t.due_date or ""
        else:
            self.priority_input.default = "MEDIUM"

    async def on_submit(self, interaction: discord.Interaction):
        desc = self.desc_input.value.strip()
        prio_raw = self.priority_input.value.strip().upper()
        prio = TaskPriority.HIGH if "HIGH" in prio_raw else (TaskPriority.LOW if "LOW" in prio_raw else TaskPriority.MEDIUM)
        due = self.due_date_input.value.strip() or None

        if self.payload.new_tasks:
            self.payload.new_tasks[0].description = desc
            self.payload.new_tasks[0].priority = prio
            self.payload.new_tasks[0].due_date = due
        else:
            self.payload.new_tasks.append(TaskItem(description=desc, priority=prio, due_date=due))

        expenses_preview = [
            {
                "amount": e.amount,
                "category": e.category.value if hasattr(e.category, "value") else str(e.category),
                "note": e.note,
                "occurred_date": e.occurred_date,
            }
            for e in self.payload.expenses
        ]
        tasks_preview = [
            {
                "description": t.description,
                "priority": t.priority.value if hasattr(t.priority, "value") else str(t.priority),
                "due_date": t.due_date,
                "due_time": t.due_time,
                "phases": t.phases,
            }
            for t in self.payload.new_tasks
        ]

        new_embed = format_action_preview(
            payload=self.payload,
            expenses=expenses_preview,
            tasks=tasks_preview,
            completed_task_ids=self.payload.completed_task_ids,
        )
        await interaction.response.edit_message(
            content="✏️ *Preview updated from your popup edits:*",
            embed=new_embed,
            view=self.parent_view,
        )


class BillEditModal(discord.ui.Modal, title="✏️ Edit Recurring Bill"):
    """Popup form modal using native Discord TextInputs for Recurring Bills."""

    name_input = discord.ui.TextInput(
        label="Bill / Investment Name",
        placeholder="e.g. Unifi, Netflix, S&P500",
        required=True,
        max_length=100,
    )
    amount_input = discord.ui.TextInput(
        label="Monthly Amount (RM)",
        placeholder="e.g. 100.00",
        required=True,
        max_length=20,
    )
    category_input = discord.ui.TextInput(
        label="Category (Investments, Utilities, etc.)",
        placeholder="e.g. Investments & Savings",
        required=False,
        max_length=50,
    )
    day_input = discord.ui.TextInput(
        label="Day of Month (1-31)",
        placeholder="e.g. 27",
        required=True,
        max_length=4,
    )

    def __init__(self, payload: ExtractedPayload, parent_view: "ActionIngestionView"):
        super().__init__()
        self.payload = payload
        self.parent_view = parent_view

        self.name_input.default = payload.add_bill_name or ""
        self.amount_input.default = f"{payload.add_bill_amount:.2f}" if payload.add_bill_amount is not None else ""
        current_category = payload.add_bill_category if isinstance(payload.add_bill_category, ExpenseCategory) else (
            resolve_category_from_text(str(payload.add_bill_category)) if payload.add_bill_category else ExpenseCategory.INVESTMENT
        )
        self.category_input.default = current_category.value
        self.day_input.default = str(payload.add_bill_day or 1)

    async def on_submit(self, interaction: discord.Interaction):
        self.payload.add_bill_name = self.name_input.value.strip()
        self.payload.add_bill_amount = clean_float_input(self.amount_input.value, default=0.0)
        cat_text = self.category_input.value.strip() or "Investments & Savings"
        self.payload.add_bill_category = resolve_category_from_text(cat_text)
        try:
            self.payload.add_bill_day = max(1, min(31, int(self.day_input.value.strip())))
        except ValueError:
            self.payload.add_bill_day = 1

        expenses_preview = [
            {
                "amount": e.amount,
                "category": e.category.value if hasattr(e.category, "value") else str(e.category),
                "note": e.note,
                "occurred_date": e.occurred_date,
            }
            for e in self.payload.expenses
        ]
        tasks_preview = [
            {
                "description": t.description,
                "priority": t.priority.value if hasattr(t.priority, "value") else str(t.priority),
                "due_date": t.due_date,
                "due_time": t.due_time,
                "phases": t.phases,
            }
            for t in self.payload.new_tasks
        ]

        new_embed = format_action_preview(
            payload=self.payload,
            expenses=expenses_preview,
            tasks=tasks_preview,
            completed_task_ids=self.payload.completed_task_ids,
        )
        await interaction.response.edit_message(
            content="✏️ *Preview updated from your popup edits:*",
            embed=new_embed,
            view=self.parent_view,
        )


class GoalDepositModal(discord.ui.Modal):
    """Popup form modal to deposit savings directly into an active goal."""

    def __init__(self, goal_id: int, goal_name: str = "Savings Goal"):
        super().__init__(title=f"➕ Deposit to {goal_name[:20]}")
        self.goal_id = goal_id
        self.amount_input = discord.ui.TextInput(
            label="Deposit Amount (RM)",
            placeholder="e.g. 500.00",
            required=True,
            max_length=20,
        )
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction):
        amt = clean_float_input(self.amount_input.value, default=0.0)
        if amt <= 0:
            await interaction.response.send_message("Please enter a valid deposit amount greater than 0.", ephemeral=True)
            return

        res = await db.deposit_to_goal(self.goal_id, amt)
        if res:
            bar = render_progress_bar(res["current_amount"], res["target_amount"])
            embed = discord.Embed(
                title=f"🎯 Goal Deposit Recorded: {res['name']}",
                description=(
                    f"• **Deposited**: **+RM {amt:.2f}**\n"
                    f"• **Total Saved**: **RM {res['current_amount']:.2f}** / RM {res['target_amount']:.2f}\n"
                    f"• **Progress**:\n{bar}\n"
                    f"• **Remaining**: **RM {res['remaining']:.2f}**"
                ),
                color=discord.Color.green(),
            )
            now_local = datetime.datetime.now(settings.tz)
            today_str = now_local.strftime("%Y-%m-%d")
            month_str = now_local.strftime("%Y-%m")
            milestones = await db.check_new_milestones(today_str, month_str)

            await interaction.response.send_message(embed=embed)
            for m in milestones:
                await interaction.followup.send(embed=format_milestone_celebration(m))
        else:
            await interaction.response.send_message(f"Goal #{self.goal_id} not found.", ephemeral=True)


class GoalCreateModal(discord.ui.Modal, title="🏆 Create Savings Goal"):
    """Popup form modal to create a new dedicated savings goal."""

    name_input = discord.ui.TextInput(
        label="Goal Name",
        placeholder="e.g. Japan Trip, Emergency Fund, MacBook Pro",
        required=True,
        max_length=100,
    )
    target_input = discord.ui.TextInput(
        label="Target Amount (RM)",
        placeholder="e.g. 6000.00",
        required=True,
        max_length=20,
    )
    date_input = discord.ui.TextInput(
        label="Target Completion Date (Optional YYYY-MM-DD)",
        placeholder="e.g. 2026-12-31",
        required=False,
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction):
        name = self.name_input.value.strip()
        target = clean_float_input(self.target_input.value, default=0.0)
        target_date = self.date_input.value.strip() or None
        if target <= 0 or not name:
            await interaction.response.send_message("Please provide a valid goal name and target amount > 0.", ephemeral=True)
            return

        now_str = datetime.datetime.now(settings.tz).strftime("%Y-%m-%d %H:%M:%S")
        gid = await db.create_goal(name=name, target_amount=target, target_date=target_date, created_at=now_str)
        embed = discord.Embed(
            title=f"🏆 Savings Goal Created: {name}",
            description=(
                f"• **Goal ID**: `#{gid}`\n"
                f"• **Target**: **RM {target:.2f}**\n"
                f"• **Target Date**: `{target_date or 'No deadline'}`\n"
                f"• **Asset Accumulation**: Protected from living expense runway!"
            ),
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed)


class BillCustomAmountModal(discord.ui.Modal, title="✏️ Custom Payment Amount"):
    """Popup form modal to pay a recurring bill or DCA with a custom amount."""

    def __init__(self, bill_id: int):
        super().__init__()
        self.bill_id = bill_id
        self.amount_input = discord.ui.TextInput(
            label="Payment / Investment Amount (RM)",
            placeholder="e.g. 500.00",
            required=True,
            max_length=20,
        )
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction):
        amt = clean_float_input(self.amount_input.value, default=0.0)
        if amt <= 0:
            await interaction.response.send_message("Please enter an amount > 0.", ephemeral=True)
            return

        bill = await db.get_recurring_bill_by_id(self.bill_id)
        if not bill:
            await interaction.response.send_message(f"Bill #{self.bill_id} was not found.", ephemeral=True)
            return

        now_local = datetime.datetime.now(settings.tz)
        now_str = now_local.strftime("%Y-%m-%d %H:%M:%S")
        c_name, _ = normalize_canonical_asset(bill["name"])

        await db.insert_expense(
            amount=amt,
            category=bill["category"],
            note=f"Paid {bill['name']}",
            created_at=now_str,
            asset_name=c_name if bill["category"] == "Investments & Savings" else None,
            recurring_bill_id=self.bill_id,
        )
        embed = format_bill_reminder_embed(bill, due_tag="PAID", is_paid=True)
        await interaction.response.edit_message(embed=embed, view=None)

        milestones = await db.check_new_milestones(now_local.strftime("%Y-%m-%d"), now_local.strftime("%Y-%m"))
        for m in milestones:
            await interaction.followup.send(embed=format_milestone_celebration(m))


class AddGoalMilestoneModal(discord.ui.Modal, title="➕ Add Subtask to Goal"):
    """Modal to add a subtask / milestone to an existing goal."""

    def __init__(self, goal_id: int, on_complete_cb: Optional[Any] = None):
        super().__init__()
        self.goal_id = goal_id
        self.on_complete_cb = on_complete_cb
        self.title_input = discord.ui.TextInput(
            label="Subtask Title",
            placeholder="e.g. Book return flights, Reserve hotel, Buy case",
            required=True,
            max_length=150,
        )
        self.cost_input = discord.ui.TextInput(
            label="Estimated Sub-Budget (Optional RM)",
            placeholder="e.g. 1800.00",
            required=False,
            max_length=20,
        )
        self.add_item(self.title_input)
        self.add_item(self.cost_input)

    async def on_submit(self, interaction: discord.Interaction):
        title = self.title_input.value.strip()
        cost = clean_float_input(self.cost_input.value, default=0.0)
        await db.add_goal_milestone(self.goal_id, title=title, estimated_cost=cost)
        updated_goal = await db.get_goal_with_milestones(self.goal_id)
        if updated_goal and self.on_complete_cb:
            await self.on_complete_cb(interaction, updated_goal)
        else:
            await interaction.response.send_message(f"✅ Added subtask **{title}** to Goal #{self.goal_id}!", ephemeral=True)


class EditGoalModal(discord.ui.Modal, title="✏️ Edit Goal Parameters"):
    """Modal to edit target amount, date, notes, or category of a goal."""

    def __init__(self, goal: Dict[str, Any], on_complete_cb: Optional[Any] = None):
        super().__init__()
        self.goal_id = goal["id"]
        self.on_complete_cb = on_complete_cb
        self.name_input = discord.ui.TextInput(
            label="Goal Name",
            default=goal["name"],
            required=True,
            max_length=100,
        )
        self.target_input = discord.ui.TextInput(
            label="Target Amount (RM)",
            default=f"{goal['target_amount']:.2f}",
            required=True,
            max_length=20,
        )
        self.date_input = discord.ui.TextInput(
            label="Target Date (YYYY-MM-DD)",
            default=goal.get("target_date") or "",
            required=False,
            max_length=20,
        )
        self.notes_input = discord.ui.TextInput(
            label="Strategy Notes",
            default=goal.get("notes") or "",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=300,
        )
        self.add_item(self.name_input)
        self.add_item(self.target_input)
        self.add_item(self.date_input)
        self.add_item(self.notes_input)

    async def on_submit(self, interaction: discord.Interaction):
        name = self.name_input.value.strip()
        target = clean_float_input(self.target_input.value, default=0.0)
        target_d = self.date_input.value.strip() or None
        notes = self.notes_input.value.strip() or None
        updated = await db.update_goal_details(self.goal_id, name=name, target_amount=target, target_date=target_d, notes=notes)
        if updated and self.on_complete_cb:
            await self.on_complete_cb(interaction, updated)
        else:
            await interaction.response.send_message(f"✅ Updated Goal #{self.goal_id} parameters!", ephemeral=True)


class AddCustomTaskToWizardModal(discord.ui.Modal, title="➕ Add Subtask to Blueprint"):
    """Modal to add an extra subtask during the Goal Creation Wizard review step."""

    def __init__(self, user_id: int, on_complete_cb: Any):
        super().__init__()
        self.user_id = user_id
        self.on_complete_cb = on_complete_cb
        self.title_input = discord.ui.TextInput(
            label="Custom Subtask Title",
            placeholder="e.g. Apply for visa, Buy travel insurance",
            required=True,
            max_length=150,
        )
        self.cost_input = discord.ui.TextInput(
            label="Estimated Sub-Budget (Optional RM)",
            placeholder="e.g. 300.00",
            required=False,
            max_length=20,
        )
        self.add_item(self.title_input)
        self.add_item(self.cost_input)

    async def on_submit(self, interaction: discord.Interaction):
        title = self.title_input.value.strip()
        cost = clean_float_input(self.cost_input.value, default=0.0)
        session = await db.get_wizard_session(self.user_id)
        if session:
            milestones = session.get("milestones", [])
            milestones.append({"title": title, "estimated_cost": cost, "is_completed": False})
            session["milestones"] = milestones
            await db.save_wizard_session(self.user_id, session)
            await self.on_complete_cb(interaction, session)
        else:
            await interaction.response.send_message("Session expired.", ephemeral=True)


class GoalWizardReviewView(discord.ui.View):
    """Review & confirmation view for AI-generated Goal Blueprint."""

    def __init__(self, user_id: int, state_dict: Dict[str, Any], db_manager: Any):
        super().__init__(timeout=900.0)
        self.user_id = user_id
        self.state_dict = state_dict
        self.db = db_manager

    @discord.ui.button(label="Create Goal", style=discord.ButtonStyle.success, emoji="✅", custom_id="perlica:wizard:confirm")
    async def on_confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        now_str = datetime.datetime.now(settings.tz).strftime("%Y-%m-%d %H:%M:%S")
        gid = await self.db.create_goal_with_milestones(
            name=self.state_dict.get("goal_name", "Goal"),
            category=self.state_dict.get("goal_category", "Custom"),
            target_amount=float(self.state_dict.get("target_amount", 0.0) or 0.0),
            target_date=self.state_dict.get("target_date"),
            notes=self.state_dict.get("notes"),
            metadata=self.state_dict.get("metadata"),
            milestones=self.state_dict.get("milestones"),
            created_at=now_str,
        )
        await self.db.delete_wizard_session(self.user_id)
        goal_data = await self.db.get_goal_with_milestones(gid)
        embed = format_rich_goal_detail_embed(goal_data)
        all_goals = await self.db.get_active_goals_with_milestones()
        view = GoalExplorerView(goals=all_goals, current_goal_id=gid, db_manager=self.db)
        await interaction.response.edit_message(content=f"🎉 **Goal #{gid} Successfully Created!**", embed=embed, view=view)

    @discord.ui.button(label="Add Subtask", style=discord.ButtonStyle.primary, emoji="➕", custom_id="perlica:wizard:add_subtask")
    async def on_add_subtask(self, interaction: discord.Interaction, button: discord.ui.Button):
        async def on_subtask_added(inter: discord.Interaction, updated_session: Dict[str, Any]):
            self.state_dict = updated_session
            embed = format_goal_wizard_preview_embed(updated_session)
            await inter.response.edit_message(embed=embed, view=self)

        await interaction.response.send_modal(AddCustomTaskToWizardModal(user_id=self.user_id, on_complete_cb=on_subtask_added))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="❌", custom_id="perlica:wizard:cancel")
    async def on_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.db.delete_wizard_session(self.user_id)
        await interaction.response.edit_message(content="❌ **Goal planning session cancelled.**", embed=None, view=None)


class GoalExplorerView(discord.ui.View):
    """Interactive Goal Explorer with drilldown, checklist toggling, deposits, and AI wizard."""

    def __init__(self, goals: List[Dict[str, Any]], current_goal_id: Optional[int] = None, db_manager: Any = None):
        super().__init__(timeout=None)
        self.goals = goals
        self.current_goal_id = current_goal_id or (goals[0]["id"] if goals else None)
        self.db = db_manager
        self._rebuild_items()

    def _rebuild_items(self):
        self.clear_items()
        if self.goals:
            options = []
            for g in self.goals[:25]:
                cat = g.get("category", "Custom")
                pct = g.get("percentage", 0.0)
                is_selected = (g["id"] == self.current_goal_id)
                options.append(
                    discord.SelectOption(
                        label=f"#{g['id']} {g['name'][:20]} ({pct}%)",
                        value=str(g["id"]),
                        description=f"RM {g['current_amount']:.0f}/{g['target_amount']:.0f} • {cat}",
                        emoji="🎯",
                        default=is_selected,
                    )
                )

            select = discord.ui.Select(
                placeholder="🎯 Select a goal to inspect details & subtasks...",
                options=options,
                custom_id="perlica:goal:explorer:select",
                row=0,
            )

            async def on_goal_selected(interaction: discord.Interaction):
                self.current_goal_id = int(select.values[0])
                goal_data = await self.db.get_goal_with_milestones(self.current_goal_id)
                self.goals = await self.db.get_active_goals_with_milestones()
                self._rebuild_items()
                embed = format_rich_goal_detail_embed(goal_data)
                await interaction.response.edit_message(embed=embed, view=self)

            select.callback = on_goal_selected
            self.add_item(select)

        # Action Buttons row 1
        if self.current_goal_id:
            dep_btn = discord.ui.Button(label="Log Deposit", style=discord.ButtonStyle.success, emoji="💰", custom_id=f"perlica:goal:dep:{self.current_goal_id}", row=1)

            async def dep_cb(interaction: discord.Interaction):
                target_g = await self.db.get_goal_by_id(self.current_goal_id)
                gname = target_g["name"] if target_g else "Goal"
                await interaction.response.send_modal(GoalDepositModal(goal_id=self.current_goal_id, goal_name=gname))

            dep_btn.callback = dep_cb
            self.add_item(dep_btn)

            subtask_btn = discord.ui.Button(label="Add Subtask", style=discord.ButtonStyle.primary, emoji="➕", custom_id=f"perlica:goal:add_task:{self.current_goal_id}", row=1)

            async def add_task_cb(interaction: discord.Interaction):
                async def on_task_added(inter: discord.Interaction, updated_goal: Dict[str, Any]):
                    self.goals = await self.db.get_active_goals_with_milestones()
                    self._rebuild_items()
                    embed = format_rich_goal_detail_embed(updated_goal)
                    await inter.response.edit_message(embed=embed, view=self)

                await interaction.response.send_modal(AddGoalMilestoneModal(goal_id=self.current_goal_id, on_complete_cb=on_task_added))

            subtask_btn.callback = add_task_cb
            self.add_item(subtask_btn)

            edit_btn = discord.ui.Button(label="Edit Goal", style=discord.ButtonStyle.secondary, emoji="✏️", custom_id=f"perlica:goal:edit:{self.current_goal_id}", row=1)

            async def edit_cb(interaction: discord.Interaction):
                target_g = await self.db.get_goal_with_milestones(self.current_goal_id)

                async def on_edited(inter: discord.Interaction, updated_goal: Dict[str, Any]):
                    self.goals = await self.db.get_active_goals_with_milestones()
                    self._rebuild_items()
                    embed = format_rich_goal_detail_embed(updated_goal)
                    await inter.response.edit_message(embed=embed, view=self)

                if target_g:
                    await interaction.response.send_modal(EditGoalModal(goal=target_g, on_complete_cb=on_edited))

            edit_btn.callback = edit_cb
            self.add_item(edit_btn)

        # Action Buttons row 2
        wizard_btn = discord.ui.Button(label="New AI Goal Wizard", style=discord.ButtonStyle.primary, emoji="✨", custom_id="perlica:goal:wizard_start", row=2)

        async def wizard_cb(interaction: discord.Interaction):
            initial_state = {
                "user_id": interaction.user.id,
                "step": 0,
                "goal_name": "",
                "goal_category": "Custom",
                "target_amount": 0.0,
                "conversation_history": [],
                "milestones": [],
                "is_ready_for_review": False,
            }
            await self.db.save_wizard_session(interaction.user.id, initial_state)
            await interaction.response.send_message(
                "🎯 **Goal Planning Session Started!**\n"
                "What is your goal? (e.g. *'I want to go to Japan in 2027'*, *'Buy a MacBook Pro'*, *'Build a 6-month emergency fund'*).\n"
                "Type directly in our chat to begin!",
                ephemeral=True,
            )

        wizard_btn.callback = wizard_cb
        self.add_item(wizard_btn)

        refresh_btn = discord.ui.Button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄", custom_id="perlica:goal:refresh", row=2)

        async def refresh_cb(interaction: discord.Interaction):
            self.goals = await self.db.get_active_goals_with_milestones()
            if self.current_goal_id:
                goal_data = await self.db.get_goal_with_milestones(self.current_goal_id)
                if goal_data:
                    self._rebuild_items()
                    embed = format_rich_goal_detail_embed(goal_data)
                    await interaction.response.edit_message(embed=embed, view=self)
                    return
            self._rebuild_items()
            embed = format_goals_overview(self.goals)
            await interaction.response.edit_message(embed=embed, view=self)

        refresh_btn.callback = refresh_cb
        self.add_item(refresh_btn)


class GoalDisambiguationView(discord.ui.View):
    """Interactive 1-tap disambiguation view for multiple matching goals."""

    def __init__(self, matched_goals: List[Dict[str, Any]], deposit_amount: float, db_manager: Any):
        super().__init__(timeout=180.0)
        self.matched_goals = matched_goals
        self.deposit_amount = deposit_amount
        self.db = db_manager

        for g in matched_goals[:5]:
            gid = g["id"]
            btn = discord.ui.Button(
                label=f"#{gid} {g['name'][:18]}",
                style=discord.ButtonStyle.primary,
                emoji="🎯",
                custom_id=f"perlica:goal:disambig:{gid}",
            )

            def create_callback(target_gid: int):
                async def callback(interaction: discord.Interaction):
                    res = await self.db.deposit_to_goal(target_gid, self.deposit_amount)
                    if res:
                        bar = render_progress_bar(res["current_amount"], res["target_amount"])
                        embed = discord.Embed(
                            title=f"🎯 Goal Deposit Recorded: {res['name']}",
                            description=(
                                f"• **Deposited**: **+RM {self.deposit_amount:.2f}**\n"
                                f"• **Total Saved**: **RM {res['current_amount']:.2f}** / RM {res['target_amount']:.2f}\n"
                                f"• **Progress**:\n{bar}\n"
                                f"• **Remaining**: **RM {res['remaining']:.2f}**"
                            ),
                            color=discord.Color.green(),
                        )
                        await interaction.response.edit_message(embed=embed, view=None)
                    else:
                        await interaction.response.send_message("Goal not found.", ephemeral=True)
                return callback

            btn.callback = create_callback(gid)
            self.add_item(btn)


GoalsDashboardView = GoalExplorerView


class CategoryFilterDropdownView(discord.ui.View):
    """Interactive 1-tap category inspector dropdown view."""

    def __init__(self, current_month_str: str):
        super().__init__(timeout=180.0)
        self.month_str = current_month_str
        categories = [
            ("Food & Dining", "🍔"),
            ("Transport", "🚗"),
            ("Groceries", "🛒"),
            ("Utilities & Bills", "⚡"),
            ("Entertainment", "🎮"),
            ("Shopping", "🛍️"),
            ("Health & Personal", "💊"),
            ("Investments & Savings", "💎"),
            ("Other", "📦"),
        ]
        options = [
            discord.SelectOption(label=cat, emoji=emoji, value=cat)
            for cat, emoji in categories
        ]
        select = discord.ui.Select(
            placeholder="📂 Filter by Category...",
            options=options,
            min_values=1,
            max_values=1,
        )

        async def on_category_select(interaction: discord.Interaction):
            cat = select.values[0]
            start_month = f"{self.month_str}-01"
            exps, subtotal = await db.get_expenses_by_category(cat, start_month)
            embed = format_category_filtered_view(cat, exps, subtotal, self.month_str)
            await interaction.response.edit_message(embed=embed, view=self)

        select.callback = on_category_select
        self.add_item(select)


class BillActionView(discord.ui.View):
    """Stateless action view for 1-tap bill and DCA logging with custom_id encoded parameters."""

    def __init__(self, bill_id: int, amount: float, category: str):
        super().__init__(timeout=None)
        is_invest = (category == "Investments & Savings")
        label = f"Log RM {amount:.2f} DCA" if is_invest else f"Pay RM {amount:.2f}"
        emoji = "💎" if is_invest else "✅"

        self.add_item(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.success,
                emoji=emoji,
                custom_id=f"perlica:bill:pay:{bill_id}",
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Custom Amount",
                style=discord.ButtonStyle.primary,
                emoji="✏️",
                custom_id=f"perlica:bill:custom:{bill_id}",
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Snooze 24h",
                style=discord.ButtonStyle.secondary,
                emoji="⏰",
                custom_id=f"perlica:bill:snooze:{bill_id}",
            )
        )


class TransactionExplorerView(discord.ui.View):
    """Stateless paginated transaction explorer view with delete select menu."""

    def __init__(
        self,
        expenses: List[Dict[str, Any]],
        month_str: str,
        page: int,
        total_pages: int,
    ):
        super().__init__(timeout=None)
        self.month_str = month_str
        self.page = page
        self.total_pages = total_pages

        prev_btn = discord.ui.Button(
            label="Prev",
            style=discord.ButtonStyle.secondary,
            emoji="◀️",
            custom_id=f"perlica:tx:p:{month_str}:{max(1, page - 1)}",
            disabled=(page <= 1),
        )
        self.add_item(prev_btn)

        page_btn = discord.ui.Button(
            label=f"{page}/{total_pages}",
            style=discord.ButtonStyle.primary,
            disabled=True,
            custom_id=f"perlica:tx:info:{page}",
        )
        self.add_item(page_btn)

        next_btn = discord.ui.Button(
            label="Next",
            style=discord.ButtonStyle.secondary,
            emoji="▶️",
            custom_id=f"perlica:tx:p:{month_str}:{min(total_pages, page + 1)}",
            disabled=(page >= total_pages),
        )
        self.add_item(next_btn)

        if expenses:
            options = [
                discord.SelectOption(
                    label=f"#{e['id']} RM {e['amount']:.2f} — {e['category']}"[:100],
                    description=f"{e['note'][:40]} | {e['created_at'][:10]}" if e.get("note") else e['created_at'][:10],
                    value=str(e["id"]),
                    emoji="🗑️",
                )
                for e in expenses[:25]
            ]
            select = discord.ui.Select(
                placeholder="🗑️ Select an expense on this page to delete...",
                options=options,
                custom_id=f"perlica:tx:d:{month_str}:{page}",
                min_values=1,
                max_values=1,
            )
            self.add_item(select)


class DailyFocusView(discord.ui.View):
    """Stateless modulo-indexed focus mode view."""

    def __init__(self, task_id: int, next_index: int):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Complete Focus Task",
                style=discord.ButtonStyle.success,
                emoji="✅",
                custom_id=f"perlica:foc:done:{task_id}",
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Skip to Next",
                style=discord.ButtonStyle.primary,
                emoji="⏩",
                custom_id=f"perlica:foc:i:{next_index}",
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Snooze +1D",
                style=discord.ButtonStyle.secondary,
                emoji="⏰",
                custom_id=f"perlica:foc:snz:{task_id}",
            )
        )


class BudgetAdjustModal(discord.ui.Modal, title="✏️ Set / Adjust Monthly Budget"):
    """Popup modal to set or adjust category budget limits."""

    category_input = discord.ui.TextInput(
        label="Category (Food & Dining, Transport, etc.)",
        placeholder="e.g. Food & Dining",
        required=True,
        max_length=50,
    )
    limit_input = discord.ui.TextInput(
        label="Monthly Limit (RM)",
        placeholder="e.g. 800.00",
        required=True,
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction):
        cat_text = self.category_input.value.strip()
        limit = clean_float_input(self.limit_input.value, default=0.0)
        cat_enum = resolve_category_from_text(cat_text)
        cat_name = cat_enum.value if cat_enum else cat_text

        if limit < 0:
            await interaction.response.send_message("Budget limit must be >= 0.", ephemeral=True)
            return

        await db.set_budget(cat_name, limit)
        now_local = datetime.datetime.now(settings.tz)
        month_str = now_local.strftime("%Y-%m")
        status = await db.get_budget_status(month_str)

        embed = format_budget_overview(status)
        await interaction.response.send_message(
            content=f"✅ Monthly budget for **{cat_name}** set to **RM {limit:.2f}**!",
            embed=embed,
            view=BudgetDashboardView(),
        )


class BudgetDashboardView(discord.ui.View):
    """View attached to /budgets with 1-tap budget adjustment modal button."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Adjust / Set Budget", style=discord.ButtonStyle.primary, emoji="✏️", custom_id="perlica:btn:adjust_budget")
    async def adjust_budget_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BudgetAdjustModal())


# --- INTERACTIVE DISCORD UI VIEWS ---

class QuickUndoView(discord.ui.View):
    """10-second ephemeral quick undo button toast with deterministic rollback."""

    def __init__(
        self,
        expense_ids: List[int],
        task_ids: List[int],
        goal_deposit: Optional[Tuple[int, float]] = None,
        created_goal_id: Optional[int] = None,
        timeout: float = 10.0,
    ):
        super().__init__(timeout=timeout)
        self.expense_ids = expense_ids
        self.task_ids = task_ids
        self.goal_deposit = goal_deposit
        self.created_goal_id = created_goal_id
        self.message: Optional[discord.Message] = None

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=None)
            except Exception as e:
                logger.debug(f"QuickUndoView on_timeout edit note: {e}")

    @discord.ui.button(label="Quick Undo (10s)", style=discord.ButtonStyle.danger, emoji="↩️", custom_id="btn_quick_undo")
    async def undo_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        
        await db.delete_expenses_by_ids(self.expense_ids)
        await db.delete_tasks_by_ids(self.task_ids)
        if self.goal_deposit:
            gid, amt = self.goal_deposit
            await db.revert_goal_deposit(gid, amt)
        if self.created_goal_id:
            await db.delete_goal(self.created_goal_id)

        await interaction.response.edit_message(
            content="↩️ **Action Undone:** Reverted your latest entries without saving.",
            embed=None,
            view=None,
        )


class CalendarStripView(discord.ui.View):
    """
    7-day calendar day inspector view.
    Partitioned across Row 0 (5 days max) and Row 1 (2 days) to guarantee zero Discord HTTP 400 crashes!
    """

    def __init__(self, base_date: datetime.date, timeout: float = 180.0):
        super().__init__(timeout=timeout)
        self.base_date = base_date

        for i in range(7):
            day_dt = base_date + timedelta(days=i)
            day_str = day_dt.strftime("%Y-%m-%d")
            day_label = day_dt.strftime("%a %d")
            row_idx = 0 if i < 5 else 1
            btn = discord.ui.Button(
                label=day_label,
                style=discord.ButtonStyle.primary if i == 0 else discord.ButtonStyle.secondary,
                emoji="📅",
                custom_id=f"cal_day_{day_str}",
                row=row_idx,
            )
            btn.callback = self.make_day_callback(day_str)
            self.add_item(btn)

    def make_day_callback(self, date_str: str):
        async def callback(interaction: discord.Interaction):
            expenses, total_spent, open_tasks = await db.get_daily_summary(date_str)
            embed = format_calendar_day_view(date_str, expenses, total_spent, open_tasks)
            await interaction.response.edit_message(embed=embed, view=self)
        return callback


class ActionIngestionView(discord.ui.View):
    """3-button interactive view: Confirm, Edit Modal, or Reject new entries."""

    def __init__(
        self,
        on_confirm: Callable[[discord.Interaction], Any],
        payload: ExtractedPayload,
        timeout: float = 300.0,
        is_duplicate: bool = False,
    ):
        super().__init__(timeout=timeout)
        self.on_confirm = on_confirm
        self.payload = payload
        self.is_duplicate = is_duplicate

        if is_duplicate:
            self.confirm_button.label = "Log Anyway"
            self.confirm_button.style = discord.ButtonStyle.primary
            self.confirm_button.emoji = "⚠️"
            self.reject_button.label = "Discard Duplicate"
            self.reject_button.style = discord.ButtonStyle.danger
            self.reject_button.emoji = "🗑️"

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green, emoji="✅", custom_id="btn_confirm_ingest")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await self.on_confirm(interaction)

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.secondary, emoji="✏️", custom_id="btn_edit_ingest")
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if self.payload.add_bill_name or self.payload.add_bill_amount is not None:
                await interaction.response.send_modal(BillEditModal(self.payload, self))
            elif self.payload.new_tasks and not self.payload.expenses:
                await interaction.response.send_modal(TaskEditModal(self.payload, self))
            else:
                await interaction.response.send_modal(ExpenseEditModal(self.payload, self))
        except Exception as e:
            logger.error(f"Error opening edit modal: {e}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.response.send_message(f"⚠️ Could not open edit modal: {e}", ephemeral=True)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.red, emoji="❌", custom_id="btn_reject_ingest")
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="❌ Entry discarded. Nothing was saved.", embed=None, view=None)


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
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await self.on_confirm(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red, emoji="❌", custom_id="btn_cancel_action")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        if self.on_cancel:
            await self.on_cancel(interaction)
        else:
            await interaction.response.edit_message(content="❌ Action cancelled.", embed=None, view=None)


class TaskSelectMenu(discord.ui.Select):
    """Native Discord Select Menu for 1-tap batch task completion (capped to top 25)."""

    def __init__(self, open_tasks: List[Dict[str, Any]]):
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


class TaskSnoozeView(discord.ui.View):
    """1-tap task reschedule control view."""

    def __init__(self, task_id: int, timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.task_id = task_id

    @discord.ui.button(label="+1 Day", style=discord.ButtonStyle.primary, emoji="⏰", custom_id="btn_snooze_1d")
    async def snooze_1d(self, interaction: discord.Interaction, button: discord.ui.Button):
        updated = await db.snooze_task(self.task_id, days_to_add=1)
        if updated:
            await interaction.response.edit_message(
                content=f"⏰ **Snoozed Task #{self.task_id}:** New due date is **{updated.get('due_date')}**.",
                embed=None,
                view=None,
            )
        else:
            await interaction.response.edit_message(content="Task not found.", embed=None, view=None)

    @discord.ui.button(label="Push to Weekend", style=discord.ButtonStyle.secondary, emoji="📅", custom_id="btn_snooze_weekend")
    async def snooze_weekend(self, interaction: discord.Interaction, button: discord.ui.Button):
        now_local = datetime.datetime.now(settings.tz)
        days_to_sat = (5 - now_local.weekday()) % 7
        if days_to_sat == 0:
            days_to_sat = 7
        updated = await db.snooze_task(self.task_id, days_to_add=days_to_sat)
        if updated:
            await interaction.response.edit_message(
                content=f"📅 **Rescheduled Task #{self.task_id} to Saturday:** Due date is **{updated.get('due_date')}**.",
                embed=None,
                view=None,
            )
        else:
            await interaction.response.edit_message(content="Task not found.", embed=None, view=None)

    @discord.ui.button(label="Mark Done", style=discord.ButtonStyle.green, emoji="✅", custom_id="btn_snooze_done")
    async def mark_done(self, interaction: discord.Interaction, button: discord.ui.Button):
        now_str = datetime.datetime.now(settings.tz).strftime("%Y-%m-%d %H:%M:%S")
        res = await db.complete_task_by_id(self.task_id, now_str)
        if res:
            await interaction.response.edit_message(
                content=f"✅ **Marked Task #{self.task_id} as DONE:** {res['description']}.",
                embed=None,
                view=None,
            )
        else:
            await interaction.response.edit_message(content="Task not found.", embed=None, view=None)


class QuickActionView(discord.ui.View):
    """Persistent 5-button quick action bar."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Budget Health", style=discord.ButtonStyle.primary, emoji="📊", custom_id="perlica:btn:budgets")
    async def budget_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        now_local = datetime.datetime.now(settings.tz)
        month_str = now_local.strftime("%Y-%m")
        status = await db.get_budget_status(month_str)
        embed = format_budget_overview(status)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Investments", style=discord.ButtonStyle.primary, emoji="💎", custom_id="perlica:btn:investments")
    async def investments_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        now_local = datetime.datetime.now(settings.tz)
        month_str = now_local.strftime("%Y-%m")
        start_month = now_local.strftime("%Y-%m-01")
        invest_summary = await db.get_investments_summary(start_month)
        dca_progress = await db.get_dca_progress(month_str)
        embed = format_investments_overview(invest_summary, dca_progress, month_str)
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


class LiveDashboardView(discord.ui.View):
    """Pinned live dashboard view with 1-click in-place refresh."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Refresh Dashboard", style=discord.ButtonStyle.secondary, emoji="🔄", custom_id="perlica:dash:refresh")
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        now_local = datetime.datetime.now(settings.tz)
        today_str = now_local.strftime("%Y-%m-%d")
        month_str = now_local.strftime("%Y-%m")
        start_month = now_local.strftime("%Y-%m-01")

        _, today_spent, _ = await db.get_daily_summary(today_str)
        pace_data = await db.get_spending_pace(today_str)
        budget_status = await db.get_budget_status(month_str)
        safe_allowance = await db.get_safe_daily_allowance(now_local)
        due_bills = await db.get_due_recurring_bills(now_local.date())
        upcoming_bills = await db.get_upcoming_recurring_bills(now_local.date(), days_ahead=3)
        open_tasks = await db.get_open_tasks()
        streak_info = await db.get_productivity_streak(today_str)
        active_goals = await db.get_active_goals()
        rank_info = await db.get_productivity_rank(today_str)
        dca_progress = await db.get_dca_progress(month_str)
        invest_summary = await db.get_investments_summary(start_month)

        new_embed = format_live_dashboard(
            today_spent=today_spent,
            pace_data=pace_data,
            budget_status=budget_status,
            safe_allowance=safe_allowance,
            due_bills=due_bills,
            upcoming_bills=upcoming_bills,
            open_tasks=open_tasks,
            streak_info=streak_info,
            date_str=today_str,
            active_goals=active_goals,
            rank_info=rank_info,
            dca_progress=dca_progress,
            total_invested_month=invest_summary.get("total_invested", 0.0),
        )
        await interaction.response.edit_message(embed=new_embed, view=self)

    @discord.ui.button(label="Budgets", style=discord.ButtonStyle.primary, emoji="📊", custom_id="perlica:dash:budgets")
    async def budget_sub(self, interaction: discord.Interaction, button: discord.ui.Button):
        now_local = datetime.datetime.now(settings.tz)
        month_str = now_local.strftime("%Y-%m")
        status = await db.get_budget_status(month_str)
        await interaction.response.send_message(embed=format_budget_overview(status), ephemeral=True)

    @discord.ui.button(label="Wealth", style=discord.ButtonStyle.primary, emoji="💎", custom_id="perlica:dash:wealth")
    async def wealth_sub(self, interaction: discord.Interaction, button: discord.ui.Button):
        now_local = datetime.datetime.now(settings.tz)
        month_str = now_local.strftime("%Y-%m")
        start_month = now_local.strftime("%Y-%m-01")
        invest_summary = await db.get_investments_summary(start_month)
        dca_progress = await db.get_dca_progress(month_str)
        await interaction.response.send_message(embed=format_investments_overview(invest_summary, dca_progress, month_str), ephemeral=True)

    @discord.ui.button(label="Tasks", style=discord.ButtonStyle.primary, emoji="📋", custom_id="perlica:dash:tasks")
    async def task_sub(self, interaction: discord.Interaction, button: discord.ui.Button):
        open_tasks = await db.get_open_tasks()
        embed = format_task_selector_embed(open_tasks)
        view = TaskMultiSelectView(open_tasks) if open_tasks else None
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Advice", style=discord.ButtonStyle.success, emoji="💡", custom_id="perlica:dash:advice")
    async def advice_sub(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        now_local = datetime.datetime.now(settings.tz)
        snapshot = await db.get_full_snapshot()
        advice = await extractor.generate_ai_insight(
            prompt_topic="Live dashboard insight request.",
            snapshot_data=snapshot,
            now_local=now_local,
        )
        await interaction.followup.send(content=f"💡 **Live Advice:**\n{advice}", ephemeral=True)


class QuickLogPresetView(discord.ui.View):
    """1-tap quick log preset buttons view."""

    def __init__(self, on_trigger_preset: Callable[[ExtractedPayload, discord.Interaction], Any]):
        super().__init__(timeout=180.0)
        self.on_trigger_preset = on_trigger_preset

    @discord.ui.button(label="Mamak RM 15", style=discord.ButtonStyle.secondary, emoji="🍽️", custom_id="btn_preset_mamak")
    async def preset_mamak(self, interaction: discord.Interaction, button: discord.ui.Button):
        payload = ExtractedPayload(
            expenses=[ExpenseItem(amount=15.0, category=ExpenseCategory.FOOD, note="Mamak Lunch")]
        )
        await self.on_trigger_preset(payload, interaction)

    @discord.ui.button(label="TNG Reload RM 50", style=discord.ButtonStyle.secondary, emoji="🚗", custom_id="btn_preset_tng")
    async def preset_tng(self, interaction: discord.Interaction, button: discord.ui.Button):
        payload = ExtractedPayload(
            expenses=[ExpenseItem(amount=50.0, category=ExpenseCategory.TRANSPORT, note="TNG Reload")]
        )
        await self.on_trigger_preset(payload, interaction)

    @discord.ui.button(label="Coffee RM 12", style=discord.ButtonStyle.secondary, emoji="☕", custom_id="btn_preset_coffee")
    async def preset_coffee(self, interaction: discord.Interaction, button: discord.ui.Button):
        payload = ExtractedPayload(
            expenses=[ExpenseItem(amount=12.0, category=ExpenseCategory.FOOD, note="Coffee / Kopi")]
        )
        await self.on_trigger_preset(payload, interaction)

    @discord.ui.button(label="99 Speedmart RM 30", style=discord.ButtonStyle.secondary, emoji="🛒", custom_id="btn_preset_speedmart")
    async def preset_speedmart(self, interaction: discord.Interaction, button: discord.ui.Button):
        payload = ExtractedPayload(
            expenses=[ExpenseItem(amount=30.0, category=ExpenseCategory.GROCERIES, note="99 Speedmart groceries")]
        )
        await self.on_trigger_preset(payload, interaction)


# --- GLOBAL STATELESS COMPONENT INTERACTION ROUTER ---

@bot.event
async def on_interaction(interaction: discord.Interaction):
    """
    Global stateless component interaction router.
    Parses dynamic parameters encoded directly into custom_id (e.g. perlica:bill:pay:42).
    Guarantees buttons survive infinite bot restarts without transient memory errors!
    """
    if interaction.type == discord.InteractionType.component:
        cid = interaction.data.get("custom_id", "")

        # 1-Tap Bill / DCA Payment
        if cid.startswith("perlica:bill:pay:"):
            bill_id = int(cid.split(":")[-1])
            bill = await db.get_recurring_bill_by_id(bill_id)
            if bill:
                now_local = datetime.datetime.now(settings.tz)
                now_str = now_local.strftime("%Y-%m-%d %H:%M:%S")
                c_name, _ = normalize_canonical_asset(bill["name"])
                is_invest = (bill["category"] == "Investments & Savings")
                
                await db.insert_expense(
                    amount=bill["amount"],
                    category=bill["category"],
                    note=f"Paid {bill['name']}",
                    created_at=now_str,
                    asset_name=c_name if is_invest else None,
                    recurring_bill_id=bill_id,
                )
                embed = format_bill_reminder_embed(bill, due_tag="PAID", is_paid=True)
                await interaction.response.edit_message(embed=embed, view=None)

                milestones = await db.check_new_milestones(now_local.strftime("%Y-%m-%d"), now_local.strftime("%Y-%m"))
                for m in milestones:
                    try:
                        await interaction.followup.send(embed=format_milestone_celebration(m))
                    except Exception:
                        pass
            else:
                await interaction.response.send_message(f"Bill #{bill_id} not found.", ephemeral=True)
            return

        # 1-Tap Bill Snooze
        elif cid.startswith("perlica:bill:snooze:"):
            await interaction.response.edit_message(
                content="⏰ **Reminder snoozed for 24 hours.**",
                embed=None,
                view=None,
            )
            return

        # 1-Tap Custom Amount Bill Modal
        elif cid.startswith("perlica:bill:custom:"):
            bill_id = int(cid.split(":")[-1])
            await interaction.response.send_modal(BillCustomAmountModal(bill_id=bill_id))
            return

        # 1-Tap Goal Creation Modal
        elif cid == "perlica:btn:create_goal":
            await interaction.response.send_modal(GoalCreateModal())
            return

        # 1-Tap Adjust Budget Modal
        elif cid == "perlica:btn:adjust_budget":
            await interaction.response.send_modal(BudgetAdjustModal())
            return

        # Transaction Explorer Pagination: perlica:tx:p:<YYYY-MM>:<page>
        elif cid.startswith("perlica:tx:p:"):
            parts = cid.split(":")
            month_str = parts[3]
            page = int(parts[4])
            expenses, safe_page, total_pages, total_count = await db.get_paginated_expenses(month_str, page=page)
            embed = format_transaction_page(expenses, safe_page, total_pages, total_count, month_str)
            view = TransactionExplorerView(expenses, month_str, safe_page, total_pages)
            await interaction.response.edit_message(embed=embed, view=view)
            return

        # Transaction Explorer Deletion Dropdown: perlica:tx:d:<YYYY-MM>:<page>
        elif cid.startswith("perlica:tx:d:"):
            parts = cid.split(":")
            month_str = parts[3]
            page = int(parts[4])
            values = interaction.data.get("values", [])
            if values:
                exp_id = int(values[0])
                await db.delete_expenses_by_ids([exp_id])

            expenses, safe_page, total_pages, total_count = await db.get_paginated_expenses(month_str, page=page)
            embed = format_transaction_page(expenses, safe_page, total_pages, total_count, month_str)
            view = TransactionExplorerView(expenses, month_str, safe_page, total_pages)
            await interaction.response.edit_message(embed=embed, view=view)
            return

        # Focus Task Complete: perlica:foc:done:<task_id>
        elif cid.startswith("perlica:foc:done:"):
            task_id = int(cid.split(":")[-1])
            now_local = datetime.datetime.now(settings.tz)
            now_str = now_local.strftime("%Y-%m-%d %H:%M:%S")
            await db.complete_tasks_by_ids([task_id], completed_at=now_str)

            tasks = await db.get_highest_priority_tasks()
            if not tasks:
                embed = format_focus_task_embed(None, 0, 0)
                await interaction.response.edit_message(embed=embed, view=None)
            else:
                embed = format_focus_task_embed(tasks[0], 0, len(tasks))
                next_idx = 1 % len(tasks)
                view = DailyFocusView(task_id=tasks[0]["id"], next_index=next_idx)
                await interaction.response.edit_message(embed=embed, view=view)
            return

        # Focus Task Modulo Index Cycle: perlica:foc:i:<target_idx>
        elif cid.startswith("perlica:foc:i:"):
            target_idx = int(cid.split(":")[-1])
            tasks = await db.get_highest_priority_tasks()
            if not tasks:
                embed = format_focus_task_embed(None, 0, 0)
                await interaction.response.edit_message(embed=embed, view=None)
            else:
                safe_index = max(0, target_idx) % len(tasks)
                focus_task = tasks[safe_index]
                next_idx = (safe_index + 1) % len(tasks)
                embed = format_focus_task_embed(focus_task, safe_index, len(tasks))
                view = DailyFocusView(task_id=focus_task["id"], next_index=next_idx)
                await interaction.response.edit_message(embed=embed, view=view)
            return

        # Focus Task Snooze: perlica:foc:snz:<task_id>
        elif cid.startswith("perlica:foc:snz:"):
            task_id = int(cid.split(":")[-1])
            await db.snooze_task(task_id, days_to_add=1)
            tasks = await db.get_highest_priority_tasks()
            if not tasks:
                embed = format_focus_task_embed(None, 0, 0)
                await interaction.response.edit_message(embed=embed, view=None)
            else:
                embed = format_focus_task_embed(tasks[0], 0, len(tasks))
                next_idx = 1 % len(tasks)
                view = DailyFocusView(task_id=tasks[0]["id"], next_index=next_idx)
                await interaction.response.edit_message(embed=embed, view=view)
            return

        # 1-Tap Knowledge Sources Live Refresh: perlica:sources:refresh
        elif cid == "perlica:sources:refresh":
            sources_summary = await db.get_knowledge_sources_summary()
            embed = format_sources_dashboard_embed(sources_summary)
            view = SourcesDashboardView(db_manager=db)
            await interaction.response.edit_message(embed=embed, view=view)
            return

        # 1-Tap Copilot Raw Excerpt Inspection: perlica:copilot:raw:<answer_id>
        elif cid.startswith("perlica:copilot:raw:"):
            raw_ans_id = cid.split(":")[-1]
            if raw_ans_id.isdigit():
                answer_id = int(raw_ans_id)
                snapshots = await db.get_answer_evidence_snapshots(answer_id)
                if snapshots:
                    first_snap = snapshots[0]
                    modal = RawEvidenceModal(
                        raw_content=first_snap.get("raw_text", "No raw content found."),
                        citation_label=first_snap.get("citation", "Evidence Source"),
                    )
                    await interaction.response.send_modal(modal)
                    return
            await interaction.response.send_message("Evidence snapshots could not be found for this answer.", ephemeral=True)
            return


# --- SLASH COMMAND AUTOCOMPLETES (STRICT 25 CAP) ---

async def task_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    """Autocomplete for open tasks with strict Discord 25-choice API limit cap."""
    tasks = await db.get_open_tasks()
    if current.strip():
        filtered = [t for t in tasks if current.lower() in t["description"].lower()]
    else:
        filtered = tasks
    return [
        app_commands.Choice(
            name=f"#{t['id']} [{t['priority']}] {t['description'][:75]}",
            value=str(t["id"]),
        )
        for t in filtered[:25]
    ]


async def category_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    """Autocomplete for expense categories with strict 25 cap."""
    categories = [
        "Food & Dining", "Transport", "Groceries", "Utilities & Bills",
        "Entertainment", "Shopping", "Health & Personal", "Investments & Savings", "Other"
    ]
    if current.strip():
        filtered = [c for c in categories if current.lower() in c.lower()]
    else:
        filtered = categories
    return [
        app_commands.Choice(name=c, value=c)
        for c in filtered[:25]
    ]


async def source_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    """Autocomplete for active knowledge sources with strict 25 cap."""
    sources = await db.get_knowledge_sources_summary()
    choices = []
    for s in sources:
        label = f"{s['name']} ({s['source_type']})"
        ref = s["source_ref"]
        if not current.strip() or current.lower() in label.lower() or current.lower() in ref.lower():
            choices.append(app_commands.Choice(name=label[:100], value=ref))
    return choices[:25]


# --- KNOWLEDGE COPILOT ASYNCHRONOUS INGESTION WORKERS ---

async def run_repo_sync_job(job_id: int, repo_name: str, branch: str = "main"):
    """Background worker to synchronize a GitHub repository with manifest diffing."""
    try:
        token_set = bool(settings.GITHUB_TOKEN and settings.GITHUB_TOKEN.strip())
        logger.info(f"Starting repo sync job #{job_id} for '{repo_name}' (branch='{branch}'). GITHUB_TOKEN set: {token_set}")
        await db.update_ingestion_job(job_id, "RUNNING", progress_text=f"Fetching tree for {repo_name}...")
        commit_sha, tree_entries, is_truncated = await fetch_github_repo_tree(repo_name, branch=branch)

        # Critical Invariant: Never execute deletion reconciliation from an incomplete/truncated remote manifest
        if is_truncated:
            raise RuntimeError(
                f"GitHub returned a truncated tree for '{repo_name}'. "
                "Aborting sync without purging to prevent accidental data loss."
            )

        source_ref = f"github:{repo_name}"
        source_id = await db.get_or_create_source(name=repo_name, source_type="GITHUB", source_ref=source_ref)

        manifest = await db.get_source_files_manifest(source_id)

        eligible_files = []
        for entry in tree_entries:
            if entry.get("type") != "blob":
                continue
            path = entry.get("path", "")
            size = entry.get("size", 0)
            eligible, _ = is_eligible_repo_file(path, size)
            if eligible:
                eligible_files.append(entry)

        # Truthful coverage accounting & Cap-Aware Manifest tracking
        total_eligible_count = len(eligible_files)
        files_to_process = eligible_files[:MAX_REPO_FILES]
        capped_files = eligible_files[MAX_REPO_FILES:]
        process_count = len(files_to_process)
        indexed_count = 0

        # Mark source as actively indexing with total eligible count for live /sources feedback
        await db.update_source_status(
            source_id,
            eligible_count=total_eligible_count,
            indexed_count=0,
            status="INDEXING",
        )

        for idx, entry in enumerate(files_to_process):
            path = entry["path"]
            blob_sha = entry["sha"]

            # Check if unchanged
            prev_file = manifest.get(path)
            if prev_file and prev_file.get("blob_sha") == blob_sha and prev_file.get("status") == "INDEXED":
                async with db.get_connection() as conn:
                    await conn.execute("UPDATE source_files SET last_seen_sync_id = ? WHERE id = ?", (job_id, prev_file["id"]))
                    await conn.commit()
                indexed_count += 1
                continue

            # Fetch content
            await db.update_ingestion_job(job_id, "RUNNING", progress_text=f"Indexing ({idx+1}/{process_count}): {path}")
            raw_code = await fetch_github_blob_content(repo_name, path, commit_sha)
            if raw_code is None:
                # Invariant: Transient fetch failures update manifest without deleting existing valid chunks
                await db.mark_source_file_failed(source_id, path, blob_sha, sync_id=job_id, status="FAILED_FETCH")
                continue

            # Secret inspection
            if scan_content_for_secrets(raw_code):
                # Invariant: Secret files update manifest as EXCLUDED_SECRET and purge chunks
                await db.mark_source_file_secret_excluded(source_id, path, blob_sha, sync_id=job_id)
                continue

            # Chunk
            if path.endswith(".py"):
                chunks = chunk_python_code(raw_code, repo_name, path, commit_sha)
            else:
                chunks = chunk_generic_code(raw_code, repo_name, path, commit_sha)

            if not chunks:
                await db.mark_source_file_failed(source_id, path, blob_sha, sync_id=job_id, status="FAILED_PARSE")
                continue

            # Embed & Atomic Commit
            try:
                texts = [c["content"] for c in chunks]
                embs = await asyncio.to_thread(compute_embeddings_batch, texts)
                emb_tuples = [(MODEL_ID, e) for e in embs]

                # Short atomic commit
                await db.commit_file_reconciliation(
                    source_id=source_id,
                    file_path=path,
                    blob_sha=blob_sha,
                    sync_id=job_id,
                    chunks=chunks,
                    embeddings=emb_tuples,
                )
                indexed_count += 1
            except Exception as file_err:
                logger.warning(f"Failed to embed/commit file '{path}': {file_err}")
                await db.mark_source_file_failed(source_id, path, blob_sha, sync_id=job_id, status="FAILED_EMBED")
                continue
            finally:
                # Keep memory strictly bounded on Railway free/hobby containers (512MB RAM ceiling)
                import gc
                gc.collect()
                await asyncio.sleep(0.02)

        # Track eligible files beyond 250 cap so they are not mistakenly purged as deleted
        if capped_files:
            capped_tuples = [(f["path"], f["sha"]) for f in capped_files]
            await db.mark_source_files_excluded_cap(source_id, capped_tuples, sync_id=job_id)

        # Purge genuinely deleted files (those not present in the remote tree manifest at all)
        await db.purge_unseen_source_files(source_id, current_sync_id=job_id)

        # Truthful coverage status: only COMPLETE if ALL eligible files in the repo were indexed and within cap
        status_tag = "COMPLETE" if (indexed_count == total_eligible_count and total_eligible_count <= MAX_REPO_FILES) else "PARTIAL"
        await db.update_source_status(
            source_id,
            eligible_count=total_eligible_count,
            indexed_count=indexed_count,
            status=status_tag,
        )
        await db.update_ingestion_job(
            job_id,
            "COMPLETED",
            progress_text=f"Sync finished ({indexed_count}/{total_eligible_count} eligible files indexed, status={status_tag}).",
        )

    except Exception as e:
        logger.error(f"Repo sync job #{job_id} failed: {e}", exc_info=True)
        await db.update_ingestion_job(job_id, "FAILED", error_message=str(e))
        source = await db.get_source_by_ref(f"github:{repo_name}")
        if source:
            await db.update_source_status(source["id"], eligible_count=0, indexed_count=0, status="FAILED", last_error=str(e))


async def run_web_ingest_job(job_id: int, url: str):
    """Background worker to ingest a web page."""
    try:
        await db.update_ingestion_job(job_id, "RUNNING", progress_text=f"Scraping {url}...")
        chunks = await scrape_webpage(url)
        if not chunks:
            raise ValueError("No extractable content found or secret content blocked.")

        source_ref = f"web:{url}"
        source_id = await db.get_or_create_source(name=url[:40], source_type="WEB", source_ref=source_ref)

        texts = [c["content"] for c in chunks]
        embs = await asyncio.to_thread(compute_embeddings_batch, texts)
        emb_tuples = [(MODEL_ID, e) for e in embs]

        await db.commit_file_reconciliation(
            source_id=source_id,
            file_path=url,
            blob_sha="web",
            sync_id=job_id,
            chunks=chunks,
            embeddings=emb_tuples,
        )

        await db.update_source_status(source_id, eligible_count=1, indexed_count=1, status="COMPLETE")
        await db.update_ingestion_job(job_id, "COMPLETED", progress_text=f"Webpage indexed ({len(chunks)} chunks).")

    except Exception as e:
        logger.error(f"Web ingest job #{job_id} failed: {e}", exc_info=True)
        await db.update_ingestion_job(job_id, "FAILED", error_message=str(e))


async def run_pdf_ingest_job(job_id: int, file_path: str):
    """Background worker to ingest a local PDF file."""
    try:
        await db.update_ingestion_job(job_id, "RUNNING", progress_text=f"Parsing PDF {file_path}...")
        chunks = await parse_pdf_file(file_path)
        if not chunks:
            raise ValueError("No extractable text found in PDF.")

        filename = os.path.basename(file_path)
        source_ref = f"pdf:{filename}"
        source_id = await db.get_or_create_source(name=filename, source_type="PDF", source_ref=source_ref)

        texts = [c["content"] for c in chunks]
        embs = await asyncio.to_thread(compute_embeddings_batch, texts)
        emb_tuples = [(MODEL_ID, e) for e in embs]

        await db.commit_file_reconciliation(
            source_id=source_id,
            file_path=filename,
            blob_sha="pdf",
            sync_id=job_id,
            chunks=chunks,
            embeddings=emb_tuples,
        )

        await db.update_source_status(source_id, eligible_count=1, indexed_count=1, status="COMPLETE")
        await db.update_ingestion_job(job_id, "COMPLETED", progress_text=f"PDF indexed ({len(chunks)} pages/chunks).")

    except Exception as e:
        logger.error(f"PDF ingest job #{job_id} failed: {e}", exc_info=True)
        await db.update_ingestion_job(job_id, "FAILED", error_message=str(e))


# --- DISCORD SLASH COMMANDS (APPLICATION TREE) ---

@bot.tree.command(name="ask", description="Ask an evidence-grounded question across indexed repositories and documents")
@app_commands.describe(query="Your question", in_source="Optional scope to search only within a specific source")
@app_commands.autocomplete(in_source=source_autocomplete)
async def slash_ask(
    interaction: discord.Interaction,
    query: str,
    in_source: Optional[str] = None,
):
    if not is_user_authorized_for_copilot(interaction.user.id):
        await interaction.response.send_message("⛔ You are not authorized to query the private knowledge base.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    answer_data = await synthesize_copilot_answer(
        db=db,
        query=query,
        source_scope=in_source,
        user_id=str(interaction.user.id),
    )

    embeds = format_copilot_answer_embeds(answer_data)
    ans_id = answer_data.answer_id if hasattr(answer_data, "answer_id") else answer_data.get("answer_id")
    view = CopilotAnswerView(answer_id=ans_id, db_manager=db)

    if len(embeds) == 1:
        await interaction.followup.send(embed=embeds[0], view=view)
    else:
        for idx, emb in enumerate(embeds):
            if idx == len(embeds) - 1:
                await interaction.followup.send(embed=emb, view=view)
            else:
                await interaction.followup.send(embed=emb)


active_sync_targets: set = set()


async def execute_ingestion_job(job_id: int, source_type: str, target_ref: str):
    """Execute a single ingestion job durably with per-target concurrency serialization."""
    lock_key = f"{source_type}:{target_ref}"
    if lock_key in active_sync_targets:
        logger.info(f"Target '{lock_key}' is already actively syncing. Marking duplicate job #{job_id} as CANCELLED.")
        await db.update_ingestion_job(job_id, "CANCELLED", progress_text="Superseded by concurrent active sync.")
        return

    active_sync_targets.add(lock_key)
    try:
        if source_type == "GITHUB":
            clean_ref = target_ref.replace("github:", "")
            parts = clean_ref.split(":")
            repo = parts[0]
            branch = parts[1] if len(parts) > 1 else "main"
            await run_repo_sync_job(job_id, repo, branch)
        elif source_type == "WEB":
            await run_web_ingest_job(job_id, target_ref)
        elif source_type == "PDF":
            await run_pdf_ingest_job(job_id, target_ref)
        else:
            await db.update_ingestion_job(job_id, "FAILED", error_message=f"Unknown source type: {source_type}")
    except Exception as e:
        logger.error(f"Error executing ingestion job #{job_id}: {e}", exc_info=True)
        await db.update_ingestion_job(job_id, "FAILED", error_message=str(e)[:300])
    finally:
        active_sync_targets.discard(lock_key)


async def recover_and_resume_ingestion_jobs():
    """Startup recovery: resets interrupted RUNNING jobs and resumes unique PENDING jobs."""
    try:
        pending_jobs = await db.recover_interrupted_ingestion_jobs()
        if pending_jobs:
            # Deduplicate: if multiple jobs exist for the same target, keep only the latest
            latest_jobs_by_target: Dict[Tuple[str, str], Dict[str, Any]] = {}
            for job in pending_jobs:
                key = (job["source_type"], job["target_ref"])
                if key in latest_jobs_by_target:
                    old_job = latest_jobs_by_target[key]
                    await db.update_ingestion_job(
                        old_job["id"],
                        "CANCELLED",
                        progress_text="Superseded by newer job on startup.",
                    )
                latest_jobs_by_target[key] = job

            logger.info(
                f"Recovered {len(latest_jobs_by_target)} unique ingestion jobs on startup "
                f"(superseded {len(pending_jobs) - len(latest_jobs_by_target)} duplicate jobs)."
            )
            for job in latest_jobs_by_target.values():
                asyncio.create_task(execute_ingestion_job(job["id"], job["source_type"], job["target_ref"]))
    except Exception as e:
        logger.warning(f"Failed to recover ingestion jobs on startup: {e}")


@bot.tree.command(name="repo", description="Manage GitHub repository indexing and reconciliation")
@app_commands.describe(action="Action to perform", repo="Repository in owner/repo format (e.g. DanielKoh2004/perlica)", branch="Branch to sync (default: main)")
@app_commands.choices(action=[
    app_commands.Choice(name="🔄 Sync / Reconcile", value="sync"),
    app_commands.Choice(name="🗑️ Purge", value="purge"),
    app_commands.Choice(name="ℹ️ Info", value="info"),
])
async def slash_repo(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
    repo: str,
    branch: str = "main",
):
    if not is_user_authorized_for_copilot(interaction.user.id):
        await interaction.response.send_message("⛔ You are not authorized to manage knowledge sources.", ephemeral=True)
        return

    repo_clean = repo.strip().replace("https://github.com/", "").strip("/")
    source_ref = f"github:{repo_clean}"

    if action.value == "purge":
        deleted = await db.delete_knowledge_source(source_ref)
        if deleted:
            await interaction.response.send_message(f"🗑️ Successfully purged repository `{repo_clean}` and all its index chunks.", ephemeral=True)
        else:
            await interaction.response.send_message(f"Source `{repo_clean}` was not found in the index.", ephemeral=True)
        return

    if action.value == "info":
        source = await db.get_source_by_ref(source_ref)
        if not source:
            await interaction.response.send_message(f"Repository `{repo_clean}` is not currently indexed.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"🐙 **{source['name']}**\n"
            f"• Status: `{source['status']}`\n"
            f"• Coverage: `{source['indexed_count']} / {source['eligible_count']}` files indexed\n"
            f"• Last Synced: `{source['last_sync_at'] or 'Never'}`",
            ephemeral=True
        )
        return

    # Action: sync -> Dispatch background job
    job_id = await db.create_ingestion_job(source_type="GITHUB", target_ref=source_ref)
    asyncio.create_task(execute_ingestion_job(job_id, "GITHUB", source_ref))

    await interaction.response.send_message(
        f"🚀 **Repository Sync Queued (Job #{job_id})**\n"
        f"Indexing `{repo_clean}` (`{branch}`) with incremental Git SHA reconciliation in the background.\n"
        f"Use `/sources` to view live status.",
        ephemeral=True
    )


active_ingest_sessions: Dict[int, float] = {}


class WebIngestModal(discord.ui.Modal, title="🌐 Ingest Webpage URL"):
    """Modal to enter a webpage URL for SSRF-safe ingestion."""

    url_input = discord.ui.TextInput(
        label="Webpage URL",
        placeholder="https://docs.example.com/guide",
        required=True,
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        url_clean = self.url_input.value.strip()
        job_id = await db.create_ingestion_job(source_type="WEB", target_ref=url_clean)
        asyncio.create_task(execute_ingestion_job(job_id, "WEB", url_clean))
        await interaction.response.send_message(
            f"🌐 **Web Ingestion Queued (Job #{job_id})**\n"
            f"Fetching and parsing `{url_clean}` with SSRF safety in the background.\n"
            f"Use `/sources` to view live status or `? <query>` to search!",
            ephemeral=True,
        )


class RepoSyncModal(discord.ui.Modal, title="🐙 Sync GitHub Repository"):
    """Modal to enter repository details for Git SHA reconciliation and AST indexing."""

    repo_input = discord.ui.TextInput(
        label="GitHub Repository (owner/repo)",
        placeholder="e.g. DanielKoh2004/perlica",
        required=True,
        max_length=150,
    )
    branch_input = discord.ui.TextInput(
        label="Branch",
        default="main",
        required=False,
        max_length=50,
    )

    async def on_submit(self, interaction: discord.Interaction):
        repo_clean = self.repo_input.value.strip()
        branch_clean = self.branch_input.value.strip() or "main"
        target_ref = f"{repo_clean}:{branch_clean}"
        job_id = await db.create_ingestion_job(source_type="GITHUB", target_ref=target_ref)
        asyncio.create_task(execute_ingestion_job(job_id, "GITHUB", target_ref))
        await interaction.response.send_message(
            f"🚀 **Repository Sync Queued (Job #{job_id})**\n"
            f"Indexing `{repo_clean}` (`{branch_clean}`) with incremental Git SHA reconciliation in the background.\n"
            f"Use `/sources` to view live status.",
            ephemeral=True,
        )


class QuickNoteModal(discord.ui.Modal, title="📝 Add Knowledge Note"):
    """Modal to enter instant knowledge notes or snippets."""

    title_input = discord.ui.TextInput(
        label="Note Title / Topic",
        default="Quick Note",
        required=True,
        max_length=100,
    )
    content_input = discord.ui.TextInput(
        label="Content / Snippet",
        placeholder="Type reference notes, guidelines, API endpoints, or cheat sheets...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=3000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        title = self.title_input.value.strip()
        content = self.content_input.value.strip()

        # P0: Unified security boundary — Secret scanning before persistence
        if scan_content_for_secrets(title) or scan_content_for_secrets(content):
            await interaction.response.send_message(
                "⛔ **Security Warning**: Note rejected. Content matches private keys or sensitive credential patterns.",
                ephemeral=True,
            )
            return

        # P1: Vectorize BEFORE database insert to guarantee zero unindexed orphan chunks
        try:
            embs = await asyncio.to_thread(compute_embeddings_batch, [f"{title}\n\n{content}"])
            if not embs or len(embs) == 0:
                raise ValueError("Empty embedding vector returned.")
            emb_tuple = (MODEL_ID, embs[0])
        except Exception as e:
            logger.error(f"Failed to embed quick note: {e}", exc_info=True)
            await interaction.response.send_message(
                "❌ **Embedding Error**: Failed to vectorize note. The note was not saved to maintain index integrity.",
                ephemeral=True,
            )
            return

        note_id = await db.store_quick_note(content=content, section_title=title)
        await db.store_chunk_embeddings([emb_tuple], [note_id])

        await interaction.response.send_message(
            f"✅ **Knowledge Note Saved (#{note_id})**\n"
            f"**{title}** is now indexed in SQLite and searchable via `? <query>`!",
            ephemeral=True,
        )


class KnowledgeIngestSessionView(discord.ui.View):
    """Interactive Hub View for Copilot Knowledge Base Ingestion."""

    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="📄 Upload PDF", style=discord.ButtonStyle.primary, custom_id="perlica:ingest:pdf_session")
    async def upload_pdf_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_user_authorized_for_copilot(interaction.user.id):
            await interaction.response.send_message("⛔ Unauthorized.", ephemeral=True)
            return

        active_ingest_sessions[interaction.user.id] = time.time() + 60
        await interaction.response.send_message(
            "📎 **PDF Ingestion Session Active (60s)**\n"
            "Please **drag-and-drop or upload your `.pdf` file** into this chat now.\n"
            "Perlica will automatically index it into your Copilot Knowledge Base!",
            ephemeral=True,
        )

    @discord.ui.button(label="🌐 Ingest Web URL", style=discord.ButtonStyle.secondary, custom_id="perlica:ingest:web_modal")
    async def web_url_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_user_authorized_for_copilot(interaction.user.id):
            await interaction.response.send_message("⛔ Unauthorized.", ephemeral=True)
            return
        await interaction.response.send_modal(WebIngestModal())

    @discord.ui.button(label="🐙 Sync GitHub Repo", style=discord.ButtonStyle.secondary, custom_id="perlica:ingest:repo_modal")
    async def sync_repo_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_user_authorized_for_copilot(interaction.user.id):
            await interaction.response.send_message("⛔ Unauthorized.", ephemeral=True)
            return
        await interaction.response.send_modal(RepoSyncModal())

    @discord.ui.button(label="📝 Add Quick Note", style=discord.ButtonStyle.secondary, custom_id="perlica:ingest:note_modal")
    async def add_note_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_user_authorized_for_copilot(interaction.user.id):
            await interaction.response.send_message("⛔ Unauthorized.", ephemeral=True)
            return
        await interaction.response.send_modal(QuickNoteModal())


@bot.tree.command(name="ingest", description="Open the Knowledge Base Ingestion Hub to index PDFs, web URLs, repos, or notes")
async def slash_ingest(interaction: discord.Interaction):
    if not is_user_authorized_for_copilot(interaction.user.id):
        await interaction.response.send_message("⛔ You are not authorized to ingest sources.", ephemeral=True)
        return

    embed = format_ingest_hub_embed()
    view = KnowledgeIngestSessionView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="note", description="Quickly save an instant note into the SQLite knowledge index")
@app_commands.describe(content="Note content or snippet", title="Optional note section title")
async def slash_note(
    interaction: discord.Interaction,
    content: str,
    title: str = "Quick Note",
):
    if not is_user_authorized_for_copilot(interaction.user.id):
        await interaction.response.send_message("⛔ You are not authorized to save notes.", ephemeral=True)
        return

    content_clean = content.strip()
    title_clean = title.strip()

    # P0: Unified security boundary — Secret scanning before persistence
    if scan_content_for_secrets(title_clean) or scan_content_for_secrets(content_clean):
        await interaction.response.send_message(
            "⛔ **Security Warning**: Note rejected. Content matches private keys or sensitive credential patterns.",
            ephemeral=True,
        )
        return

    # P1: Vectorize BEFORE database insert to guarantee zero unindexed orphan chunks
    try:
        embs = await asyncio.to_thread(compute_embeddings_batch, [f"{title_clean}\n\n{content_clean}"])
        if not embs or len(embs) == 0:
            raise ValueError("Empty embedding vector returned.")
        emb_tuple = (MODEL_ID, embs[0])
    except Exception as e:
        logger.error(f"Failed to embed quick note: {e}", exc_info=True)
        await interaction.response.send_message(
            "❌ **Embedding Error**: Failed to vectorize note. The note was not saved to maintain index integrity.",
            ephemeral=True,
        )
        return

    chunk_id = await db.store_quick_note(content=content_clean, section_title=title_clean)
    await db.store_chunk_embeddings([emb_tuple], [chunk_id])

    await interaction.response.send_message(
        f"📝 **Note Saved & Indexed!**\n"
        f"• **Title**: `{title_clean}`\n"
        f"• **Content**: {content_clean[:100]}{'...' if len(content_clean) > 100 else ''}\n"
        f"Searchable instantly via `? <query>` or `/ask`!",
        ephemeral=True,
    )


@bot.tree.command(name="sources", description="View and manage indexed knowledge repositories, documents, and notes")
async def slash_sources(interaction: discord.Interaction):
    if not is_user_authorized_for_copilot(interaction.user.id):
        await interaction.response.send_message("⛔ You are not authorized to view knowledge sources.", ephemeral=True)
        return

    sources_summary = await db.get_knowledge_sources_summary()
    embed = format_sources_dashboard_embed(sources_summary)
    view = SourcesDashboardView(db_manager=db)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="dashboard", description="Open your live interactive command center dashboard")
async def slash_dashboard(interaction: discord.Interaction):
    now_local = datetime.datetime.now(settings.tz)
    today_str = now_local.strftime("%Y-%m-%d")
    month_str = now_local.strftime("%Y-%m")
    start_month = now_local.strftime("%Y-%m-01")

    _, today_spent, _ = await db.get_daily_summary(today_str)
    pace_data = await db.get_spending_pace(today_str)
    budget_status = await db.get_budget_status(month_str)
    safe_allowance = await db.get_safe_daily_allowance(now_local)
    due_bills = await db.get_due_recurring_bills(now_local.date())
    upcoming_bills = await db.get_upcoming_recurring_bills(now_local.date(), days_ahead=3)
    open_tasks = await db.get_open_tasks()
    streak_info = await db.get_productivity_streak(today_str)
    active_goals = await db.get_active_goals()
    rank_info = await db.get_productivity_rank(today_str)
    dca_progress = await db.get_dca_progress(month_str)
    invest_summary = await db.get_investments_summary(start_month)

    embed = format_live_dashboard(
        today_spent=today_spent,
        pace_data=pace_data,
        budget_status=budget_status,
        safe_allowance=safe_allowance,
        due_bills=due_bills,
        upcoming_bills=upcoming_bills,
        open_tasks=open_tasks,
        streak_info=streak_info,
        date_str=today_str,
        active_goals=active_goals,
        rank_info=rank_info,
        dca_progress=dca_progress,
        total_invested_month=invest_summary.get("total_invested", 0.0),
    )
    await interaction.response.send_message(embed=embed, view=LiveDashboardView())


@bot.tree.command(name="history", description="Browse past transactions with interactive pagination and deletion")
async def slash_history(interaction: discord.Interaction):
    now_local = datetime.datetime.now(settings.tz)
    month_str = now_local.strftime("%Y-%m")
    expenses, safe_page, total_pages, total_count = await db.get_paginated_expenses(month_str, page=1)
    embed = format_transaction_page(expenses, safe_page, total_pages, total_count, month_str)
    view = TransactionExplorerView(expenses, month_str, safe_page, total_pages)
    await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="holidays", description="View upcoming Selangor and Federal public holidays & long weekends")
@app_commands.describe(days="Days ahead to inspect (default: 60)")
async def slash_holidays(interaction: discord.Interaction, days: int = 60):
    now_local = datetime.datetime.now(settings.tz)
    today_str = now_local.strftime("%Y-%m-%d")
    holidays_list = get_upcoming_malaysian_holidays(now_local.date(), days_ahead=max(1, days), subdiv="SGR")
    embed = format_upcoming_holidays_embed(holidays_list, today_str)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="focus", description="Open daily single-task focus mode widget")
async def slash_focus(interaction: discord.Interaction):
    tasks = await db.get_highest_priority_tasks()
    if not tasks:
        embed = format_focus_task_embed(None, 0, 0)
        await interaction.response.send_message(embed=embed)
    else:
        embed = format_focus_task_embed(tasks[0], 0, len(tasks))
        next_idx = 1 % len(tasks)
        view = DailyFocusView(task_id=tasks[0]["id"], next_index=next_idx)
        await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="help", description="Open comprehensive Perlica guide and feature list")
async def slash_help(interaction: discord.Interaction):
    embed = format_help_guide()
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="snooze", description="Snooze a task by ID or search")
@app_commands.autocomplete(task_id=task_autocomplete)
@app_commands.describe(task_id="Select task to snooze", days="Days to postpone (default: 1)")
async def slash_snooze(interaction: discord.Interaction, task_id: str, days: int = 1):
    try:
        tid = int(task_id)
    except ValueError:
        await interaction.response.send_message("Please select a valid task.", ephemeral=True)
        return

    res = await db.snooze_task(tid, days_to_add=max(1, days))
    if res:
        await interaction.response.send_message(f"⏰ Task `[#{tid}]` snoozed by +{days} days! (New Due Date: `{res.get('due_date')}`)")
    else:
        await interaction.response.send_message(f"Task #{tid} not found.", ephemeral=True)


@bot.tree.command(name="investments", description="View dedicated Wealth & DCA Investment Portfolio")
async def slash_investments(interaction: discord.Interaction):
    now_local = datetime.datetime.now(settings.tz)
    month_str = now_local.strftime("%Y-%m")
    start_month = now_local.strftime("%Y-%m-01")
    invest_summary = await db.get_investments_summary(start_month)
    dca_progress = await db.get_dca_progress(month_str)
    embed = format_investments_overview(invest_summary, dca_progress, month_str)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="tasks", description="View active open tasks with 1-tap completion dropdown")
async def slash_tasks(interaction: discord.Interaction):
    open_tasks = await db.get_open_tasks()
    embed = format_task_selector_embed(open_tasks)
    view = TaskMultiSelectView(open_tasks) if open_tasks else None
    await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="budgets", description="View current monthly budget progress bars and adjust limits")
async def slash_budgets(interaction: discord.Interaction):
    now_local = datetime.datetime.now(settings.tz)
    month_str = now_local.strftime("%Y-%m")
    status = await db.get_budget_status(month_str)
    embed = format_budget_overview(status)
    view = BudgetDashboardView()
    await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="goals", description="Open the Rich Goals Explorer with visual progress bars and action checklists")
async def slash_goals(interaction: discord.Interaction):
    goals = await db.get_active_goals_with_milestones()
    if goals:
        embed = format_rich_goal_detail_embed(goals[0])
        view = GoalExplorerView(goals=goals, current_goal_id=goals[0]["id"], db_manager=db)
    else:
        embed = format_goals_overview([])
        view = GoalExplorerView(goals=[], current_goal_id=None, db_manager=db)
    await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="goal", description="Manage goals: create with dynamic AI wizard, list, view, or deposit")
@app_commands.describe(
    action="Action to perform",
    goal_id="Optional goal ID for view/deposit/delete",
    amount="Optional deposit amount for action='deposit'",
)
@app_commands.choices(action=[
    app_commands.Choice(name="✨ Create with AI Wizard", value="create"),
    app_commands.Choice(name="📂 List / Explore All Goals", value="list"),
    app_commands.Choice(name="💰 Deposit Savings", value="deposit"),
    app_commands.Choice(name="🗑️ Delete Goal", value="delete"),
])
async def slash_goal(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
    goal_id: Optional[int] = None,
    amount: Optional[float] = None,
):
    if action.value == "create":
        initial_state = {
            "user_id": interaction.user.id,
            "step": 0,
            "goal_name": "",
            "goal_category": "Custom",
            "target_amount": 0.0,
            "conversation_history": [],
            "milestones": [],
            "is_ready_for_review": False,
        }
        await db.save_wizard_session(interaction.user.id, initial_state)
        await interaction.response.send_message(
            "🎯 **Dynamic AI Goal Planning Session Started!**\n"
            "What is your new goal? (e.g. *'I want to plan a Japan trip in 2027'*, *'Buy a MacBook Pro'*, *'Build a 6-month emergency fund'*).\n\n"
            "💬 **Type your reply in this chat to begin!** _(Type `cancel` anytime to abort)_",
            ephemeral=True,
        )
    elif action.value == "list":
        goals = await db.get_active_goals_with_milestones()
        if goals:
            embed = format_rich_goal_detail_embed(goals[0])
            view = GoalExplorerView(goals=goals, current_goal_id=goals[0]["id"], db_manager=db)
        else:
            embed = format_goals_overview([])
            view = GoalExplorerView(goals=[], current_goal_id=None, db_manager=db)
        await interaction.response.send_message(embed=embed, view=view)
    elif action.value == "deposit":
        if goal_id and amount and amount > 0:
            res = await db.deposit_to_goal(goal_id, amount)
            if res:
                embed = format_rich_goal_detail_embed(res)
                await interaction.response.send_message(f"💰 Deposited **+RM {amount:.2f}** into Goal #{goal_id}!", embed=embed)
            else:
                await interaction.response.send_message(f"Goal #{goal_id} not found.", ephemeral=True)
        elif goal_id:
            g = await db.get_goal_by_id(goal_id)
            if g:
                await interaction.response.send_modal(GoalDepositModal(goal_id=goal_id, goal_name=g["name"]))
            else:
                await interaction.response.send_message(f"Goal #{goal_id} not found.", ephemeral=True)
        else:
            goals = await db.get_active_goals_with_milestones()
            if goals:
                await interaction.response.send_modal(GoalDepositModal(goal_id=goals[0]["id"], goal_name=goals[0]["name"]))
            else:
                await interaction.response.send_message("No active goals found. Use `/goal action:create` to start one!", ephemeral=True)
    elif action.value == "delete":
        if not goal_id:
            await interaction.response.send_message("Please provide `goal_id` to delete.", ephemeral=True)
            return
        deleted = await db.delete_goal(goal_id)
        if deleted:
            await interaction.response.send_message(f"🗑️ Goal `#{goal_id}` ({deleted['name']}) has been deleted.")
        else:
            await interaction.response.send_message(f"Goal `#{goal_id}` not found.", ephemeral=True)


@bot.tree.command(name="category", description="Inspect monthly expenses filtered by specific category")
@app_commands.autocomplete(category=category_autocomplete)
@app_commands.describe(category="Select category to filter by (optional)")
async def slash_category(interaction: discord.Interaction, category: Optional[str] = None):
    now_local = datetime.datetime.now(settings.tz)
    month_str = now_local.strftime("%Y-%m")
    if category:
        start_month = f"{month_str}-01"
        exps, subtotal = await db.get_expenses_by_category(category, start_month)
        embed = format_category_filtered_view(category, exps, subtotal, month_str)
        view = CategoryFilterDropdownView(current_month_str=month_str)
        await interaction.response.send_message(embed=embed, view=view)
    else:
        embed = discord.Embed(
            title="📂 Interactive Category Inspector",
            description="Select a category from the dropdown menu below to view an itemized breakdown for this month.",
            color=discord.Color.blue(),
        )
        view = CategoryFilterDropdownView(current_month_str=month_str)
        await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="calendar", description="Open interactive 7-day calendar day inspector")
async def slash_calendar(interaction: discord.Interaction):
    now_local = datetime.datetime.now(settings.tz)
    today_str = now_local.strftime("%Y-%m-%d")
    expenses, total_spent, open_tasks = await db.get_daily_summary(today_str)
    embed = format_calendar_day_view(today_str, expenses, total_spent, open_tasks)
    view = CalendarStripView(base_date=now_local.date())
    await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="search", description="Search historical expenses and tasks by keyword")
@app_commands.describe(keyword="Word or store name to search for (e.g. food, grab, macbook)")
async def slash_search(interaction: discord.Interaction, keyword: str):
    results = await db.search_records(keyword)
    embed = format_search_results(keyword, results)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="report", description="Generate and download a standalone HTML executive report")
async def slash_report(interaction: discord.Interaction):
    await interaction.response.defer()
    now_local = datetime.datetime.now(settings.tz)
    month_str = now_local.strftime("%Y-%m")
    start_month = now_local.strftime("%Y-%m-01")

    expenses, total_spent, _ = await db.get_expenses_summary(start_month)
    proportions = await db.get_category_proportions(start_month)
    budget_status = await db.get_budget_status(month_str)
    open_tasks = await db.get_open_tasks()
    completed_tasks = await db.get_completed_tasks()
    goals = await db.get_active_goals()
    streak_info = await db.get_productivity_streak(now_local.strftime("%Y-%m-%d"))
    rank_info = await db.get_productivity_rank(now_local.strftime("%Y-%m-%d"))
    invest_summary = await db.get_investments_summary(start_month)
    dca_progress = await db.get_dca_progress(month_str)

    html_content = generate_html_report(
        month_str=month_str,
        expenses=expenses,
        total_spent=total_spent,
        proportions=proportions,
        budget_status=budget_status,
        open_tasks=open_tasks,
        completed_tasks=completed_tasks,
        goals=goals,
        streak_info=streak_info,
        rank_info=rank_info,
        investments_summary=invest_summary,
        dca_progress=dca_progress,
    )
    report_file = discord.File(
        io.BytesIO(html_content.encode("utf-8")),
        filename=f"Perlica_Executive_Report_{month_str}.html",
    )
    await interaction.followup.send(
        content=f"📊 **Here is your Executive Financial & Productivity HTML Report for {now_local.strftime('%B %Y')}:**\n_(Download and open in any browser)_",
        file=report_file,
    )


@bot.tree.command(name="export", description="Download your monthly expense spreadsheet (.csv)")
async def slash_export(interaction: discord.Interaction):
    now_local = datetime.datetime.now(settings.tz)
    month_str = now_local.strftime("%Y-%m")
    start_month = now_local.strftime("%Y-%m-01")
    csv_text = await db.generate_csv_data(start_month)
    csv_file = discord.File(
        io.BytesIO(csv_text.encode("utf-8")),
        filename=f"Perlica_Expenses_{month_str}.csv",
    )
    await interaction.response.send_message(
        content=f"📄 **Here is your expense export for {now_local.strftime('%B %Y')}:**",
        file=csv_file,
    )


@bot.tree.command(name="presets", description="Display 1-tap quick log presets")
async def slash_presets(interaction: discord.Interaction):
    async def trigger_preset(payload: ExtractedPayload, inter: discord.Interaction):
        await handle_action_preview_flow(inter, payload, from_interaction=True)

    await interaction.response.send_message(embed=format_presets_embed(), view=QuickLogPresetView(on_trigger_preset=trigger_preset))


# --- BOT LIFECYCLE HOOKS & SCHEDULED LOOPS ---

async def bot_setup_hook():
    """Execute startup tasks and perform singleton tree sync safely without reconnect rate-limits."""
    if not getattr(bot, "_has_synced_tree", False):
        try:
            await bot.tree.sync()
            bot._has_synced_tree = True
            logger.info("Global slash commands synced successfully via setup_hook.")
        except Exception as e:
            logger.warning(f"Global slash command sync note: {e}")

bot.setup_hook = bot_setup_hook


@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await db.init_db()
    logger.info("Database schema initialized successfully.")

    await recover_and_resume_ingestion_jobs()

    bot.add_view(QuickActionView())
    bot.add_view(LiveDashboardView())
    bot.add_view(SourcesDashboardView(db_manager=db))

    if not daily_summary_loop.is_running():
        daily_summary_loop.start()
        logger.info(f"Daily summary loop scheduled at {settings.DAILY_SUMMARY_TIME} ({settings.TIMEZONE}).")

    if not morning_briefing_loop.is_running():
        morning_briefing_loop.start()
        logger.info(f"Morning briefing loop scheduled at {settings.MORNING_BRIEFING_TIME} ({settings.TIMEZONE}).")

    if not weekly_review_loop.is_running():
        weekly_review_loop.start()
        logger.info(f"Weekly executive review loop scheduled on Sundays at {settings.WEEKLY_REVIEW_TIME} ({settings.TIMEZONE}).")

    if settings.REPO_AUTO_SYNC_ENABLED and not repo_auto_sync_loop.is_running():
        repo_auto_sync_loop.start()
        logger.info(f"Daily repo auto-sync loop scheduled at {settings.REPO_AUTO_SYNC_TIME} ({settings.TIMEZONE}).")


summary_h, summary_m = settings.summary_hour_minute
summary_time = datetime.time(hour=summary_h, minute=summary_m, tzinfo=settings.tz)

morning_h, morning_m = settings.morning_hour_minute
morning_time = datetime.time(hour=morning_h, minute=morning_m, tzinfo=settings.tz)

weekly_h, weekly_m = settings.weekly_review_hour_minute
weekly_time = datetime.time(hour=weekly_h, minute=weekly_m, tzinfo=settings.tz)

repo_sync_h, repo_sync_m = settings.repo_auto_sync_hour_minute
repo_sync_time = datetime.time(hour=repo_sync_h, minute=repo_sync_m, tzinfo=settings.tz)


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
    safe_allowance = await db.get_safe_daily_allowance(now_local)
    active_goals = await db.get_active_goals()
    rank_info = await db.get_productivity_rank(today_str)

    embed = format_morning_briefing(
        open_tasks=open_tasks,
        due_bills=due_bills,
        budget_status=budget_status,
        date_str=today_str,
        upcoming_bills=upcoming_bills,
        safe_allowance=safe_allowance,
        active_goals=active_goals,
        rank_info=rank_info,
    )
    try:
        await target_user.send(embed=embed, view=QuickActionView())
        logger.info(f"Dispatched morning briefing DM for {today_str}.")

        # Send 1-tap actionable cards for bills/DCA due today
        for b in due_bills:
            due_embed = format_bill_reminder_embed(b, due_tag="DUE TODAY 🚨", is_paid=False)
            bill_view = BillActionView(bill_id=b["id"], amount=b["amount"], category=b["category"])
            await target_user.send(embed=due_embed, view=bill_view)
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
    cat_proportions = await db.get_category_proportions(today_str, today_str)

    embed = format_daily_summary(
        expenses=expenses,
        total_spent=total_spent,
        open_tasks=open_tasks,
        date_str=today_str,
        spending_pace=spending_pace,
        streak_info=streak_info,
        category_proportions=cat_proportions,
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
        prompt_topic="Sunday Weekly Executive Review kickoff.",
        snapshot_data=await db.get_full_snapshot(start_of_week, end_of_week),
        now_local=now_local,
    )

    embed = format_weekly_executive_review(review_data, ai_strategic_kickoff=ai_kickoff)
    try:
        await target_user.send(embed=embed, view=QuickActionView())
        logger.info(f"Dispatched Sunday Weekly Review DM for {start_of_week} to {end_of_week}.")
    except Exception as e:
        logger.error(f"Failed to send weekly review DM: {e}")


@tasks.loop(time=repo_sync_time)
async def repo_auto_sync_loop():
    """Daily background scheduled job reconciling all indexed GitHub repositories at 04:00 AM."""
    if not settings.REPO_AUTO_SYNC_ENABLED:
        return

    logger.info("Starting automated daily GitHub repository re-sync...")
    try:
        github_sources = await db.get_github_sources()
        if not github_sources:
            logger.info("No GitHub sources registered for auto-sync.")
            return

        for source in github_sources:
            source_ref = source.get("source_ref", "")
            repo_name = source_ref.replace("github:", "").strip()
            if not repo_name:
                continue

            logger.info(f"Auto-syncing repository '{repo_name}'...")
            job_id = await db.create_ingestion_job(source_type="GITHUB", target_ref=source_ref)
            await run_repo_sync_job(job_id, repo_name, branch="main")
            logger.info(f"Auto-sync completed for repository '{repo_name}' (Job #{job_id}).")

    except Exception as e:
        logger.error(f"Error during automated daily repo sync: {e}", exc_info=True)


# --- ACTION INGESTION HANDLER HELPER ---

async def handle_action_preview_flow(target: Any, payload: ExtractedPayload, from_interaction: bool = False):
    """Shared handler to render 3-button Action Ingestion preview and commit changes on confirm."""
    now_local = datetime.datetime.now(settings.tz)
    now_str = now_local.strftime("%Y-%m-%d %H:%M:%S")
    month_str = now_local.strftime("%Y-%m")

    # Deterministic Goal Disambiguation
    if payload.goal_deposit_query and not payload.goal_deposit_id and payload.goal_deposit_amount:
        res_type, matched = await db.resolve_goal_by_name_or_query(payload.goal_deposit_query)
        if res_type in ("EXACT", "SINGLE"):
            payload.goal_deposit_id = matched["id"]
        elif res_type == "AMBIGUOUS":
            emb = format_goal_disambiguation_embed(matched, payload.goal_deposit_amount)
            view = GoalDisambiguationView(matched, payload.goal_deposit_amount, db_manager=db)
            if from_interaction:
                await target.response.send_message(embed=emb, view=view)
            else:
                await target.reply(embed=emb, view=view)
            return

    target_goal_name = None
    if payload.goal_deposit_id:
        target_goal = await db.get_goal_by_id(payload.goal_deposit_id)
        if target_goal:
            target_goal_name = target_goal["name"]

    expenses_preview = [
        {
            "amount": exp.amount,
            "category": exp.category.value if hasattr(exp.category, "value") else str(exp.category),
            "note": exp.note,
            "occurred_date": exp.occurred_date,
            "asset_name": exp.asset_name,
            "investment_bill_id": exp.investment_bill_id,
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

    async def on_confirm(interaction: discord.Interaction):
        inserted_expenses: List[Dict[str, Any]] = []
        created_expense_ids: List[int] = []
        for exp in payload.expenses:
            created_at = f"{exp.occurred_date} 12:00:00" if exp.occurred_date else now_str
            cat_val = exp.category.value if hasattr(exp.category, "value") else str(exp.category)
            eid = await db.insert_expense(
                amount=exp.amount,
                category=cat_val,
                note=exp.note,
                created_at=created_at,
                asset_name=exp.asset_name,
                recurring_bill_id=exp.investment_bill_id,
            )
            created_expense_ids.append(eid)
            inserted_expenses.append(
                {
                    "id": eid,
                    "amount": exp.amount,
                    "category": cat_val,
                    "note": exp.note,
                    "created_at": created_at,
                    "asset_name": exp.asset_name,
                    "recurring_bill_id": exp.investment_bill_id,
                }
            )

        inserted_tasks: List[Dict[str, Any]] = []
        created_task_ids: List[int] = []
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
                created_task_ids.append(parent_id)
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
                created_task_ids.append(tid)
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
            completed_tasks_details = await db.complete_tasks_by_ids(payload.completed_task_ids, completed_at=now_str)

        # Handle Savings Goals Deposits (Isolated Asset Accumulation)
        goal_update_info = None
        goal_deposit_tuple = None
        if payload.goal_deposit_id and payload.goal_deposit_amount:
            goal_res = await db.deposit_to_goal(payload.goal_deposit_id, payload.goal_deposit_amount)
            if goal_res:
                goal_update_info = dict(goal_res)
                goal_update_info["deposited_delta"] = payload.goal_deposit_amount
                goal_deposit_tuple = (payload.goal_deposit_id, payload.goal_deposit_amount)

        # Handle Savings Goals Creation
        created_goal_id = None
        if payload.goal_create_name and payload.goal_create_target:
            created_goal_id = await db.create_goal(
                name=payload.goal_create_name,
                target_amount=payload.goal_create_target,
                target_date=payload.goal_create_date,
                created_at=now_str,
            )

        # Handle Goal Milestone Subtask Completion & Foreign Key Linking
        if payload.goal_milestone_completed_title:
            target_gid = payload.goal_milestone_goal_id or (goal_update_info["id"] if goal_update_info else None)
            if not target_gid:
                active_gs = await db.get_active_goals_with_milestones()
                if active_gs:
                    target_gid = active_gs[0]["id"]
            if target_gid:
                g_with_m = await db.get_goal_with_milestones(target_gid)
                if g_with_m and g_with_m.get("milestones"):
                    m_title_q = payload.goal_milestone_completed_title.lower()
                    for m in g_with_m["milestones"]:
                        if m_title_q in m["title"].lower() or m["title"].lower() in m_title_q:
                            linked_eid = created_expense_ids[0] if created_expense_ids else None
                            if linked_eid:
                                await db.complete_milestone_with_expense(m["id"], linked_eid, completed_at=now_str)
                            else:
                                await db.toggle_goal_milestone(m["id"], is_completed=1)
                            break

        if payload.add_bill_name and payload.add_bill_amount is not None:
            b_cat = payload.add_bill_category.value if payload.add_bill_category else "Investments & Savings"
            await db.add_recurring_bill(payload.add_bill_name, payload.add_bill_amount, b_cat, payload.add_bill_day or 1)

        if payload.set_budget_category and payload.set_budget_amount is not None:
            await db.set_budget(payload.set_budget_category, payload.set_budget_amount)

        budget_alerts = []
        if inserted_expenses:
            current_budget_status = await db.get_budget_status(month_str)
            for b in current_budget_status:
                if b["is_overspent"]:
                    budget_alerts.append(f"🚨 **{b['category']}** exceeded monthly limit! (RM {b['spent']:.2f} / RM {b['limit']:.2f})")
                elif b["is_warning"]:
                    budget_alerts.append(f"⚠️ **{b['category']}** at {b['percentage']}% of monthly limit (RM {b['remaining']:.2f} left).")

        streak_info = await db.get_productivity_streak(now_local.strftime("%Y-%m-%d"))

        dca_impact_info = None
        has_investments = any(e.get("category") == "Investments & Savings" for e in inserted_expenses)
        if has_investments:
            dca_progress = await db.get_dca_progress(month_str)
            if dca_progress:
                fulfilled_count = sum(1 for d in dca_progress if d["is_fulfilled"])
                dca_impact_info = {
                    "status_line": f"{fulfilled_count}/{len(dca_progress)} Monthly DCA Commitments Met ✅"
                }

        # Check vehicle fuel subsidy tracking
        fuel_impact_info = None
        for exp in inserted_expenses:
            f_info = classify_fuel_expense(exp["category"], exp.get("note"))
            if f_info:
                prior_liters = await db.get_monthly_ron95_liters(month_str)
                fuel_impact_info = calculate_fuel_details(exp["amount"], f_info, prior_liters)
                break

        confirmed_embed = format_action_confirmation(
            payload=payload,
            inserted_expenses=inserted_expenses,
            inserted_tasks=inserted_tasks,
            completed_tasks=completed_tasks_details,
            budget_alerts=budget_alerts,
            streak_info=streak_info,
            goal_update_info=goal_update_info,
            dca_impact_info=dca_impact_info,
            fuel_impact_info=fuel_impact_info,
        )

        quick_undo_view = QuickUndoView(
            expense_ids=created_expense_ids,
            task_ids=created_task_ids,
            goal_deposit=goal_deposit_tuple,
            created_goal_id=created_goal_id,
        )
        await interaction.response.edit_message(embed=confirmed_embed, view=quick_undo_view)
        try:
            quick_undo_view.message = await interaction.original_response()
        except Exception:
            pass

        # Check and celebrate newly unlocked milestones (atomic anti-spam ledger)
        new_milestones = await db.check_new_milestones(now_local.strftime("%Y-%m-%d"), month_str)
        for m in new_milestones:
            try:
                await interaction.followup.send(embed=format_milestone_celebration(m))
            except Exception as e:
                logger.debug(f"Milestone celebratory followup note: {e}")

        # Check for potential duplicate expense collisions within 5 minutes
    duplicate_warning = None
    if payload.expenses:
        first_exp = payload.expenses[0]
        cat_val = first_exp.category.value if hasattr(first_exp.category, "value") else str(first_exp.category)
        dup = await db.find_recent_similar_expense(first_exp.amount, cat_val, window_minutes=5, now_local=now_local)
        if dup:
            duplicate_warning = dup

    preview_embed = format_action_preview(
        payload=payload,
        expenses=expenses_preview,
        tasks=tasks_preview,
        completed_task_ids=payload.completed_task_ids,
        target_goal_name=target_goal_name,
        duplicate_warning=duplicate_warning,
    )
    view = ActionIngestionView(on_confirm=on_confirm, payload=payload, is_duplicate=bool(duplicate_warning))

    if from_interaction:
        await target.response.send_message(embed=preview_embed, view=view)
    else:
        await target.reply(embed=preview_embed, view=view)


# --- MESSAGE DISPATCHER & INGESTION ---

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.author.id == bot.user.id:
        return
    if message.guild is not None:
        return
    if settings.ALLOWED_USER_ID and message.author.id != settings.ALLOWED_USER_ID:
        return

    content = message.content.strip()
    image_attachment = None

    if message.attachments:
        for att in message.attachments:
            lower_name = att.filename.lower()
            if any(lower_name.endswith(ext) for ext in [".ogg", ".mp3", ".m4a", ".wav", ".webm"]):
                async with message.channel.typing():
                    audio_bytes = await att.read()
                    transcribed = await extractor.transcribe_audio((att.filename, audio_bytes))
                    if transcribed:
                        await message.reply(embed=format_voice_transcription_preview(transcribed))
                        content = f"{content} {transcribed}".strip()
                    else:
                        await message.reply("⚠️ Could not transcribe the audio file.")
            elif any(lower_name.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp"]):
                image_attachment = att
            elif lower_name.endswith(".pdf"):
                if att.size and att.size > MAX_PDF_SIZE_BYTES:
                    await message.reply(f"⚠️ **File Size Limit**: PDF exceeds maximum limit (5 MB). File size: {att.size / (1024*1024):.1f} MB.")
                    return

                now_time = time.time()
                if active_ingest_sessions.get(message.author.id, 0) > now_time:
                    active_ingest_sessions.pop(message.author.id, None)
                    if not is_user_authorized_for_copilot(message.author.id):
                        await message.reply("⛔ You are not authorized to ingest sources.")
                        return
                    async with message.channel.typing():
                        pdf_bytes = await att.read()
                        os.makedirs(settings.KNOWLEDGE_DIR, exist_ok=True)
                        safe_filename = os.path.basename(att.filename)
                        dest_path = os.path.join(settings.KNOWLEDGE_DIR, safe_filename)
                        with open(dest_path, "wb") as f:
                            f.write(pdf_bytes)
                        job_id = await db.create_ingestion_job(source_type="PDF", target_ref=dest_path)
                        asyncio.create_task(execute_ingestion_job(job_id, "PDF", dest_path))
                        await message.reply(
                            f"📄 **Knowledge Ingestion Session: Processing Started! (Job #{job_id})**\n"
                            f"Extracting and vector-indexing `{safe_filename}` in the background.\n"
                            f"Use `/sources` to view live status or `? <query>` to search!"
                        )
                    return

                async with message.channel.typing():
                    pdf_bytes = await att.read()
                    from pypdf import PdfReader
                    now_local = datetime.datetime.now(settings.tz)
                    try:
                        reader = PdfReader(io.BytesIO(pdf_bytes))
                        extracted_text = ""
                        for page in reader.pages[:10]:
                            extracted_text += (page.extract_text() or "") + "\n"
                        extracted_text = extracted_text.strip()
                    except Exception as e:
                        logger.warning(f"Failed to read PDF pages: {e}")
                        extracted_text = ""

                    if extracted_text:
                        combined_prompt = f"{content}\n\nInvoice/Receipt PDF Document text:\n{extracted_text[:3000]}"
                        payload = await extractor.extract_from_text(combined_prompt, now_local)
                        if payload and (payload.expenses or payload.tasks or payload.recurring_bills):
                            await handle_action_preview_flow(message, payload)
                            return

                    await message.reply(
                        "⚠️ **No expense or bill details detected in this PDF.**\n"
                        "• All direct uploads in chat are treated as receipts/invoices for expense tracking.\n"
                        "• To index a document into your searchable **Knowledge Base**, click **Upload PDF** in `/ingest`!"
                    )
                return

    if not content and not image_attachment:
        return

    # Check for active Goal Creation Wizard session (SQLite-persisted state)
    wizard_session = await db.get_wizard_session(message.author.id, max_age_seconds=900)
    if wizard_session:
        if content.lower() in ("cancel", "exit", "stop", "abort"):
            await db.delete_wizard_session(message.author.id)
            await message.reply("❌ **Goal planning session cancelled.**")
            return

        async with message.channel.typing():
            from src.goal_wizard import process_wizard_turn
            state, reply_text = await process_wizard_turn(
                user_id=message.author.id,
                user_message=content,
                db_manager=db,
                groq_api_key=settings.GROQ_API_KEY,
            )
            if state.is_ready_for_review:
                embed = format_goal_wizard_preview_embed(state.to_dict())
                view = GoalWizardReviewView(user_id=message.author.id, state_dict=state.to_dict(), db_manager=db)
                await message.reply(embed=embed, view=view)
            else:
                await message.reply(reply_text)
        return

    # Direct Ingest Hub Command Check
    if content.lower().strip() in ("ingest", "!ingest", "knowledge", "kb"):
        if not is_user_authorized_for_copilot(message.author.id):
            await message.reply("⛔ You are not authorized to ingest sources.")
            return
        embed = format_ingest_hub_embed()
        view = KnowledgeIngestSessionView()
        await message.reply(embed=embed, view=view)
        return

    # Direct Help Command Check
    if content.lower() in ("!help", "help", "guide", "how to use", "/help", "commands", "perlica"):
        await message.reply(embed=format_help_guide(), view=QuickActionView())
        return

    # Direct Live Dashboard Command Check
    if content.lower() in ("dashboard", "!dashboard", "status", "center"):
        now_local = datetime.datetime.now(settings.tz)
        today_str = now_local.strftime("%Y-%m-%d")
        month_str = now_local.strftime("%Y-%m")

        _, today_spent, _ = await db.get_daily_summary(today_str)
        pace_data = await db.get_spending_pace(today_str)
        budget_status = await db.get_budget_status(month_str)
        safe_allowance = await db.get_safe_daily_allowance(now_local)
        due_bills = await db.get_due_recurring_bills(now_local.date())
        upcoming_bills = await db.get_upcoming_recurring_bills(now_local.date(), days_ahead=3)
        open_tasks = await db.get_open_tasks()
        streak_info = await db.get_productivity_streak(today_str)
        active_goals = await db.get_active_goals()
        rank_info = await db.get_productivity_rank(today_str)

        embed = format_live_dashboard(
            today_spent=today_spent,
            pace_data=pace_data,
            budget_status=budget_status,
            safe_allowance=safe_allowance,
            due_bills=due_bills,
            upcoming_bills=upcoming_bills,
            open_tasks=open_tasks,
            streak_info=streak_info,
            date_str=today_str,
            active_goals=active_goals,
            rank_info=rank_info,
        )
        await message.reply(embed=embed, view=LiveDashboardView())
        return

    # Direct Goals Command Check
    if content.lower() in ("goals", "!goals", "/goals", "goal", "!goal", "my goals"):
        goals = await db.get_active_goals_with_milestones()
        if goals:
            embed = format_rich_goal_detail_embed(goals[0])
            view = GoalExplorerView(goals=goals, current_goal_id=goals[0]["id"], db_manager=db)
        else:
            embed = format_goals_overview([])
            view = GoalExplorerView(goals=[], current_goal_id=None, db_manager=db)
        await message.reply(embed=embed, view=view)
        return

    # Direct Investments & DCA Command Check
    if content.lower() in ("investments", "!investments", "/investments", "wealth", "portfolio", "dca", "my investments"):
        now_local = datetime.datetime.now(settings.tz)
        month_str = now_local.strftime("%Y-%m")
        start_month = now_local.strftime("%Y-%m-01")
        invest_summary = await db.get_investments_summary(start_month)
        dca_progress = await db.get_dca_progress(month_str)
        embed = format_investments_overview(invest_summary, dca_progress, month_str)
        await message.reply(embed=embed, view=QuickActionView())
        return

    # Direct Calendar Command Check
    if content.lower() in ("calendar", "!calendar", "week", "days"):
        now_local = datetime.datetime.now(settings.tz)
        today_str = now_local.strftime("%Y-%m-%d")
        expenses, total_spent, open_tasks = await db.get_daily_summary(today_str)
        embed = format_calendar_day_view(today_str, expenses, total_spent, open_tasks)
        view = CalendarStripView(base_date=now_local.date())
        await message.reply(embed=embed, view=view)
        return

    # Direct Report Command Check
    if content.lower() in ("report", "!report", "/report", "html report"):
        async with message.channel.typing():
            now_local = datetime.datetime.now(settings.tz)
            month_str = now_local.strftime("%Y-%m")
            start_month = now_local.strftime("%Y-%m-01")

            expenses, total_spent, _ = await db.get_expenses_summary(start_month)
            proportions = await db.get_category_proportions(start_month)
            budget_status = await db.get_budget_status(month_str)
            open_tasks = await db.get_open_tasks()
            completed_tasks = await db.get_completed_tasks()
            goals = await db.get_active_goals()
            streak_info = await db.get_productivity_streak(now_local.strftime("%Y-%m-%d"))
            rank_info = await db.get_productivity_rank(now_local.strftime("%Y-%m-%d"))
            invest_summary = await db.get_investments_summary(start_month)
            dca_progress = await db.get_dca_progress(month_str)

            html_content = generate_html_report(
                month_str=month_str,
                expenses=expenses,
                total_spent=total_spent,
                proportions=proportions,
                budget_status=budget_status,
                open_tasks=open_tasks,
                completed_tasks=completed_tasks,
                goals=goals,
                streak_info=streak_info,
                rank_info=rank_info,
                investments_summary=invest_summary,
                dca_progress=dca_progress,
            )
            report_file = discord.File(
                io.BytesIO(html_content.encode("utf-8")),
                filename=f"Perlica_Executive_Report_{month_str}.html",
            )
            await message.reply(
                content=f"📊 **Here is your Executive HTML Report for {now_local.strftime('%B %Y')}:**",
                file=report_file,
            )
            return

    # Direct Search Command Check (e.g. "find grab" or "search food")
    search_match = re.match(r"^(?:find|search)\s+(.+)$", content.lower())
    if search_match:
        keyword = search_match.group(1).strip()
        results = await db.search_records(keyword)
        await message.reply(embed=format_search_results(keyword, results))
        return

    # Direct Presets Command Check
    if content.lower() in ("presets", "!presets", "quick"):
        async def trigger_preset(payload: ExtractedPayload, inter: discord.Interaction):
            await handle_action_preview_flow(inter, payload, from_interaction=True)

        await message.reply(embed=format_presets_embed(), view=QuickLogPresetView(on_trigger_preset=trigger_preset))
        return

    # Direct Snooze Command Check (e.g. "snooze 1" or "!snooze 1")
    snooze_match = re.match(r"^!?snooze\s+(\d+)$", content.lower())
    if snooze_match:
        task_id = int(snooze_match.group(1))
        task = await db.get_task_by_id(task_id)
        if task:
            await message.reply(embed=format_task_snooze_embed(task), view=TaskSnoozeView(task_id))
        else:
            await message.reply(f"Task #{task_id} not found.")
        return

    # Manual Slash Command Force Sync
    if content.lower() == "!sync":
        try:
            synced = await bot.tree.sync()
            await message.reply(f"⚡ Global application slash commands synced ({len(synced)} commands registered).")
        except Exception as e:
            await message.reply(f"⚠️ Sync failed: {e}")
        return

    # Direct Holidays Command Check (e.g. "holidays", "upcoming holidays", "cuti")
    if content.lower().strip() in ("holidays", "upcoming holidays", "holiday", "cuti", "!holidays", "!cuti", "long weekend", "public holidays"):
        now_local = datetime.datetime.now(settings.tz)
        today_str = now_local.strftime("%Y-%m-%d")
        holidays_list = get_upcoming_malaysian_holidays(now_local.date(), days_ahead=60, subdiv="SGR")
        await message.reply(embed=format_upcoming_holidays_embed(holidays_list, today_str))
        return

    # Direct Help Command Check (e.g. "help", "!help", "guide")
    if content.lower().strip() in ("help", "!help", "guide", "commands", "/help"):
        await message.reply(embed=format_help_guide())
        return

    # Direct Ask / Copilot Query Check (e.g. "ask: how does auth work" or "? how is fuel calculated")
    ask_match = re.match(r"^(?:ask|copilot|\?)\s*[:\s]\s*(.+)$", content, re.IGNORECASE)
    if ask_match:
        if not is_user_authorized_for_copilot(message.author.id):
            await message.reply("⛔ You are not authorized to query the private knowledge base.")
            return

        query_text = ask_match.group(1).strip()
        async with message.channel.typing():
            answer_data = await synthesize_copilot_answer(db=db, query=query_text, user_id=str(message.author.id))
            embeds = format_copilot_answer_embeds(answer_data)
            ans_id = answer_data.answer_id if hasattr(answer_data, "answer_id") else answer_data.get("answer_id")
            view = CopilotAnswerView(answer_id=ans_id, db_manager=db)
            if len(embeds) == 1:
                await message.reply(embed=embeds[0], view=view)
            else:
                for idx, emb in enumerate(embeds):
                    if idx == len(embeds) - 1:
                        await message.reply(embed=emb, view=view)
                    else:
                        await message.reply(embed=emb)
        return

    # Direct Sources Command Check
    if content.lower().strip() in ("sources", "!sources", "/sources", "knowledge", "kb"):
        if not is_user_authorized_for_copilot(message.author.id):
            await message.reply("⛔ You are not authorized to view knowledge sources.")
            return
        sources_summary = await db.get_knowledge_sources_summary()
        embed = format_sources_dashboard_embed(sources_summary)
        view = SourcesDashboardView(db_manager=db)
        await message.reply(embed=embed, view=view)
        return

    # Visual feedback: typing indicator in DM
    async with message.channel.typing():
        now_local = datetime.datetime.now(settings.tz)
        month_str = now_local.strftime("%Y-%m")
        open_tasks = await db.get_open_tasks()
        recurring_bills = await db.list_recurring_bills()
        active_goals = await db.get_active_goals()

        # Receipt Image OCR extraction
        if image_attachment and not content:
            img_bytes = await image_attachment.read()
            payload: ExtractedPayload = await extractor.extract_from_image(
                image_bytes=img_bytes,
                filename=image_attachment.filename,
                now_local=now_local,
                open_tasks=open_tasks,
                recurring_bills=recurring_bills,
                active_goals=active_goals,
            )
        else:
            payload: ExtractedPayload = await extractor.extract_information(
                text=content,
                now_local=now_local,
                open_tasks=open_tasks,
                recurring_bills=recurring_bills,
                active_goals=active_goals,
            )

        # 0. Clarification Prompt
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

        # 3. DELETE Specific Expense / Task / Bill / Goal with Button Confirmation
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

        if payload.delete_goal_id:
            gid = payload.delete_goal_id
            target_goal = await db.get_goal_by_id(gid)
            if not target_goal:
                await message.reply(f"Savings Goal #{gid} was not found.")
                return

            async def do_delete_goal(interaction: discord.Interaction):
                await db.delete_goal(gid)
                await interaction.response.edit_message(
                    content=f"🗑️ **Deleted Savings Goal #{gid}:** `{target_goal['name']}` (Target: RM {target_goal['target_amount']:.2f}).",
                    embed=None,
                    view=None,
                )

            embed = discord.Embed(
                title="⚠️ Confirm Goal Deletion",
                description=f"Are you sure you want to delete **Savings Goal #{gid}** (`{target_goal['name']}` — Target: RM {target_goal['target_amount']:.2f})?",
                color=discord.Color.red(),
            )
            await message.reply(embed=embed, view=ConfirmActionView(on_confirm=do_delete_goal))
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
            or payload.goal_create_name
            or payload.goal_deposit_id
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

        # 7. Query / Immediate Summary / Budget Overview / Tasks / Goals Handling
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

            if q.query_target == "TASKS":
                tasks_list = await db.get_open_tasks()
                embed = format_task_selector_embed(tasks_list)
                view = TaskMultiSelectView(tasks_list) if tasks_list else None
                await message.reply(embed=embed, view=view)
                return

            if q.query_target == "GOALS":
                goals = await db.get_active_goals()
                embed = format_goals_overview(goals)
                await message.reply(embed=embed, view=QuickActionView())
                return

            if q.query_target == "INVESTMENTS":
                invest_summary = await db.get_investments_summary(start_d, end_d)
                dca_progress = await db.get_dca_progress(month_str)
                embed = format_investments_overview(invest_summary, dca_progress, month_str)
                await message.reply(embed=embed, view=QuickActionView())
                return

            if q.query_target == "HOLIDAYS":
                holidays_list = get_upcoming_malaysian_holidays(now_local.date(), days_ahead=60, subdiv="SGR")
                await message.reply(embed=format_upcoming_holidays_embed(holidays_list, now_local.strftime("%Y-%m-%d")), view=QuickActionView())
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

            if q.query_target in ("ADVICE", "GENERAL") or q.specific_question:
                snapshot = await db.get_full_snapshot(start_d, end_d)
                ai_answer = await extractor.generate_ai_insight(
                    prompt_topic=q.specific_question or content,
                    snapshot_data=snapshot,
                    now_local=now_local,
                )
                await message.reply(ai_answer, view=QuickActionView())
                return

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

        # 8. Action Ingestion with 3-Button Confirmation Preview ([Confirm] [Edit Modal] [Reject])
        await handle_action_preview_flow(message, payload, from_interaction=False)


def main():
    if not settings.DISCORD_TOKEN:
        logger.error(
            "DISCORD_TOKEN not set in environment or .env. Please configure your bot token."
        )
        return
    bot.run(settings.DISCORD_TOKEN)


if __name__ == "__main__":
    main()
