import re
import discord
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from src.extractor import ExtractedPayload, QueryScope


def render_progress_bar(current: float, limit: float, bar_length: int = 10) -> str:
    """Render a visual Unicode progress bar with color-coded alerts."""
    if limit <= 0:
        return f"RM {current:.2f} / Unlimited"
    ratio = min(max(current / limit, 0.0), 1.0)
    filled_length = int(bar_length * ratio)
    bar = "█" * filled_length + "░" * (bar_length - filled_length)
    pct = round((current / limit) * 100, 1)
    status_emoji = " ⚠️" if 80.0 <= pct <= 100.0 else (" 🚨 OVERSPENT" if pct > 100.0 else "")
    return f"`[{bar}]` **RM {current:.2f} / RM {limit:.2f}** ({pct}%){status_emoji}"


def render_sparkline(values: List[float]) -> str:
    """
    Render a fixed-width monospaced sparkline.
    Enclosed in markdown backticks to guarantee identical rendering across Desktop, iOS, and Android.
    """
    if not values or all(v == 0 for v in values):
        return "`[ - - - - - - - ]`"
    bars = [" ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    min_v = min(values)
    max_v = max(values)
    span = max_v - min_v
    if span == 0:
        return "`[" + ("▅" * len(values)) + "]`"
    chars = []
    for v in values:
        idx = int(((v - min_v) / span) * (len(bars) - 1))
        idx = max(0, min(idx, len(bars) - 1))
        chars.append(bars[idx])
    return "`[" + "".join(chars) + "]`"


def render_category_heatmap(proportions: List[Dict[str, Any]], bar_length: int = 10) -> str:
    """
    Render a monospaced ASCII proportion heatmap.
    Guaranteed uniform pixel alignment on iOS, Android, and Desktop.
    """
    if not proportions:
        return "No categorized expenses recorded."
    lines = []
    for item in proportions[:5]:
        pct = item["percentage"]
        filled = int((pct / 100.0) * bar_length)
        bar = "█" * filled + "░" * (bar_length - filled)
        lines.append(f"`[{bar}]` **{pct}%** {item['category']} _(RM {item['amount']:.2f})_")
    return "\n".join(lines)


def format_action_preview(
    payload: ExtractedPayload,
    expenses: List[Dict[str, Any]],
    tasks: List[Dict[str, Any]],
    completed_task_ids: List[int],
    target_goal_name: Optional[str] = None,
    duplicate_warning: Optional[Dict[str, Any]] = None,
) -> discord.Embed:
    """Build a rich preview embed for user review before committing to database."""
    color = discord.Color.orange() if duplicate_warning else discord.Color.gold()
    embed = discord.Embed(
        title="⚠️ Action Ingestion Preview (Duplicate Warning)" if duplicate_warning else "📋 Action Ingestion Preview",
        description="Please review what will be recorded. Click **Confirm** to save, **Edit** to modify, or **Reject** to discard.",
        color=color,
    )

    # 0. Duplicate Collision Warning Field
    if duplicate_warning:
        mins = duplicate_warning.get("minutes_ago", 0)
        time_text = "just now" if mins == 0 else f"{mins} min ago"
        note_text = f" ({duplicate_warning['note']})" if duplicate_warning.get("note") else ""
        embed.add_field(
            name="⚠️ Potential Duplicate Detected",
            value=(
                f"An identical expense of **RM {duplicate_warning['amount']:.2f}** in `{duplicate_warning['category']}`{note_text} "
                f"was already recorded **{time_text}** (`[#{duplicate_warning['id']}]`).\n"
                f"• If this is a separate purchase, click **[Log Anyway]**.\n"
                f"• If this is an accidental double-entry, click **[Discard Duplicate]**."
            ),
            inline=False,
        )

    # 1. Expenses & Wealth Investments Preview
    if expenses:
        living_exps = [e for e in expenses if e.get("category") != "Investments & Savings"]
        invest_exps = [e for e in expenses if e.get("category") == "Investments & Savings"]

        if living_exps:
            total_living = sum(e["amount"] for e in living_exps)
            lines = []
            for exp in living_exps:
                note_str = f" ({exp['note']})" if exp.get("note") else ""
                date_str = f" `[Date: {exp['occurred_date']}]`" if exp.get("occurred_date") else ""
                lines.append(f"• **RM {exp['amount']:.2f}** — `{exp['category']}`{note_str}{date_str}")
            embed.add_field(
                name=f"💸 Living Expenses to Log (Total: RM {total_living:.2f})",
                value="\n".join(lines),
                inline=False,
            )

        if invest_exps:
            total_invest = sum(e["amount"] for e in invest_exps)
            lines = []
            for exp in invest_exps:
                asset_label = exp.get("asset_name") or exp.get("note") or "Investment"
                date_str = f" `[Date: {exp['occurred_date']}]`" if exp.get("occurred_date") else ""
                link_str = f" _(Linked to DCA Bill #{exp['investment_bill_id']})_" if exp.get("investment_bill_id") else ""
                lines.append(f"• 💎 **RM {exp['amount']:.2f}** — `{asset_label}`{date_str}{link_str}")
            embed.add_field(
                name=f"💎 Wealth & Capital to Deploy (Total: RM {total_invest:.2f})",
                value="\n".join(lines) + "\n_(Asset accumulation — does NOT deduct from living expense runway)_",
                inline=False,
            )

    # 2. Tasks Preview (Single or Multi-phase)
    if tasks:
        lines = []
        for t in tasks:
            due_parts = []
            if t.get("due_date"):
                due_parts.append(t["due_date"])
            if t.get("due_time"):
                due_parts.append(t["due_time"])
            due_str = f" _(Due: {' '.join(due_parts)})_" if due_parts else ""

            phases = t.get("phases", [])
            if phases:
                lines.append(f"• 📁 `[{t['priority']}]` **{t['description']}**{due_str}")
                for idx, p in enumerate(phases, start=1):
                    lines.append(f"   └── ⏳ `Phase {idx}`: {p}")
            else:
                lines.append(f"• `[{t['priority']}]` {t['description']}{due_str}")

        embed.add_field(
            name="📝 Tasks to Create",
            value="\n".join(lines),
            inline=False,
        )

    # 3. Savings Goal Deposit Preview
    if payload.goal_deposit_id and payload.goal_deposit_amount:
        g_name = target_goal_name or f"Goal #{payload.goal_deposit_id}"
        embed.add_field(
            name="🎯 Savings Goal Deposit",
            value=f"• **Deposit RM {payload.goal_deposit_amount:.2f}** to **{g_name}**\n_(Asset accumulation — will not deduct from daily budgets)_",
            inline=False,
        )

    # 4. New Savings Goal Creation Preview
    if payload.goal_create_name and payload.goal_create_target:
        due_text = f" _(Target: {payload.goal_create_date})_" if payload.goal_create_date else ""
        embed.add_field(
            name="🏆 New Savings Goal Setup",
            value=f"• **{payload.goal_create_name}** — Target: **RM {payload.goal_create_target:.2f}**{due_text}",
            inline=False,
        )

    # 5. Recurring Bills Preview
    if payload.add_bill_name and payload.add_bill_amount is not None:
        cat_name = payload.add_bill_category.value if payload.add_bill_category else "Investments & Savings"
        day_str = f"on the {payload.add_bill_day}th" if payload.add_bill_day else "monthly"
        embed.add_field(
            name="🔔 Recurring Bill to Add (Human Reminder Only)",
            value=f"• **{payload.add_bill_name}** — RM {payload.add_bill_amount:.2f} (`{cat_name}`) {day_str}",
            inline=False,
        )

    # 6. Budget Limit Preview
    if payload.set_budget_category and payload.set_budget_amount is not None:
        embed.add_field(
            name="🎯 Monthly Budget to Set",
            value=f"• **{payload.set_budget_category}:** RM {payload.set_budget_amount:.2f} monthly limit",
            inline=False,
        )

    # 7. Completed Tasks Preview
    if completed_task_ids:
        lines = [f"• Task ID `#{tid}` marked `DONE`" for tid in completed_task_ids]
        embed.add_field(
            name="✅ Tasks to Complete",
            value="\n".join(lines),
            inline=False,
        )

    # 8. Ambiguity note if any
    if payload.ambiguous_task_note:
        embed.add_field(
            name="⚠️ Clarification Required",
            value=payload.ambiguous_task_note,
            inline=False,
        )

    return embed


def format_action_confirmation(
    payload: ExtractedPayload,
    inserted_expenses: List[Dict[str, Any]],
    inserted_tasks: List[Dict[str, Any]],
    completed_tasks: List[Dict[str, Any]],
    budget_alerts: Optional[List[str]] = None,
    streak_info: Optional[Dict[str, Any]] = None,
    goal_update_info: Optional[Dict[str, Any]] = None,
    dca_impact_info: Optional[Dict[str, Any]] = None,
    fuel_impact_info: Optional[Dict[str, Any]] = None,
) -> discord.Embed:
    """Build a rich confirmation embed for logged actions with hierarchy, budget alerts, and streak badge."""
    is_breach = bool(budget_alerts and any("exceeded" in a.lower() or "🚨" in a for a in budget_alerts))
    color = discord.Color.red() if is_breach else discord.Color.green()

    embed = discord.Embed(
        title="🚨 Budget Breach Alert" if is_breach else "⚡ Action Processed",
        color=color,
    )

    # 1. Logged Expenses & Wealth Investments
    if inserted_expenses:
        living_exps = [e for e in inserted_expenses if e.get("category") != "Investments & Savings"]
        invest_exps = [e for e in inserted_expenses if e.get("category") == "Investments & Savings"]

        if living_exps:
            total_living = sum(e["amount"] for e in living_exps)
            lines = []
            for exp in living_exps:
                note_str = f" ({exp['note']})" if exp.get("note") else ""
                date_str = f" `[Date: {exp['created_at'][:10]}]`" if exp.get("created_at") else ""
                lines.append(f"• **RM {exp['amount']:.2f}** — `{exp['category']}`{note_str}{date_str}")
            embed.add_field(
                name=f"💸 Logged Expenses (Total: RM {total_living:.2f})",
                value="\n".join(lines),
                inline=False,
            )

        if invest_exps:
            total_invest = sum(e["amount"] for e in invest_exps)
            lines = []
            for exp in invest_exps:
                asset_label = exp.get("asset_name") or exp.get("note") or "Investment"
                date_str = f" `[Date: {exp['created_at'][:10]}]`" if exp.get("created_at") else ""
                lines.append(f"• 💎 **RM {exp['amount']:.2f}** — `{asset_label}`{date_str}")
            dca_note = ""
            if dca_impact_info:
                dca_note = f"\n_DCA Progress: {dca_impact_info.get('status_line', '')}_"
            embed.add_field(
                name=f"💎 Capital Deployed (Total: RM {total_invest:.2f})",
                value="\n".join(lines) + dca_note,
                inline=False,
            )

    # 1.5 Fuel Subsidy Widget
    if fuel_impact_info:
        grade = fuel_impact_info["grade"]
        liters = fuel_impact_info["liters_added"]
        quota_left = fuel_impact_info.get("ron95_quota_remaining", 200.0)
        new_total = fuel_impact_info.get("new_total_ron95_liters", 0.0)
        consumes_sub = fuel_impact_info.get("consumes_subsidy", True)

        if consumes_sub:
            bar = render_progress_bar(new_total, 200.0, bar_length=8)
            fuel_text = f"• **Volume**: **{liters} Litres** ({fuel_impact_info['tier_label']})\n• **Subsidized Quota**:\n  {bar} _({quota_left:.1f}L left)_"
        else:
            fuel_text = f"• **Volume**: **{liters} Litres** ({fuel_impact_info['tier_label']})\n_Unsubsidized grade. 200L RON95 quota untouched!_"

        embed.add_field(
            name=f"🚗 Fuel Logged: {grade}",
            value=fuel_text,
            inline=False,
        )

    # 2. Budget Alerts (Breach or Warning)
    if budget_alerts:
        embed.add_field(
            name="⚠️ Budget Alert",
            value="\n".join(budget_alerts),
            inline=False,
        )

    # 3. Savings Goal Deposit Update
    if goal_update_info:
        bar = render_progress_bar(goal_update_info["current_amount"], goal_update_info["target_amount"])
        rem_str = f"RM {goal_update_info['remaining']:.2f} remaining" if goal_update_info["remaining"] > 0 else "🎉 GOAL ACHIEVED!"
        embed.add_field(
            name=f"🏆 Savings Goal Updated: {goal_update_info['name']}",
            value=f"Added: **+RM {goal_update_info.get('deposited_delta', 0.0):.2f}**\nProgress: {bar}\n_{rem_str}_",
            inline=False,
        )

    # 4. New Tasks (Parent & Phases)
    if inserted_tasks:
        lines = []
        for t in inserted_tasks:
            due_parts = []
            if t.get("due_date"):
                due_parts.append(t["due_date"])
            if t.get("due_time"):
                due_parts.append(t["due_time"])
            due_str = f" _(Due: {' '.join(due_parts)})_" if due_parts else ""

            subphases = t.get("subphases", [])
            if subphases:
                lines.append(f"• 📁 `[{t['priority']}]` **{t['description']}**{due_str} `[ID: #{t['id']}]`")
                for s in subphases:
                    lines.append(f"   └── ⏳ `{s.get('phase_name', 'Phase')}`: {s['description']} `[ID: #{s['id']}]`")
            else:
                prefix = f"   └── ⏳ `{t.get('phase_name')}`: " if t.get("parent_id") else "• "
                lines.append(f"{prefix}`[{t['priority']}]` {t['description']}{due_str} `[ID: #{t['id']}]`")

        embed.add_field(
            name="📝 New Tasks Added",
            value="\n".join(lines),
            inline=False,
        )

    # 5. Completed Tasks
    if completed_tasks:
        lines = []
        for t in completed_tasks:
            phase_str = f" `{t.get('phase_name')}` " if t.get("phase_name") else " "
            lines.append(f"• ~~`[#{t['id']}]`{phase_str}{t['description']}~~")
        embed.add_field(
            name="✅ Completed Tasks",
            value="\n".join(lines),
            inline=False,
        )

    # 6. Recurring Bills Configured
    if payload.add_bill_name and payload.add_bill_amount is not None:
        b_cat = payload.add_bill_category.value if payload.add_bill_category else "Investments & Savings"
        day_str = f"on the {payload.add_bill_day}th" if payload.add_bill_day else "monthly"
        embed.add_field(
            name="🔔 Recurring Bill Configured",
            value=f"• **{payload.add_bill_name}** — RM {payload.add_bill_amount:.2f} (`{b_cat}`) {day_str}\n_(Human-in-the-loop reminder only — will alert you on the {payload.add_bill_day or 1}th)_",
            inline=False,
        )

    # 7. New Savings Goals Created
    if payload.goal_create_name and payload.goal_create_target:
        target_d = f" | Target Date: `{payload.goal_create_date}`" if payload.goal_create_date else ""
        embed.add_field(
            name="🏆 New Savings Goal Created",
            value=f"• **{payload.goal_create_name}** — Target: **RM {payload.goal_create_target:.2f}**{target_d}\n_(Dedicated asset accumulation fund)_",
            inline=False,
        )

    # 8. Monthly Budgets Configured
    if payload.set_budget_category and payload.set_budget_amount is not None:
        embed.add_field(
            name="🎯 Monthly Budget Configured",
            value=f"• **{payload.set_budget_category}:** RM {payload.set_budget_amount:.2f} monthly limit",
            inline=False,
        )

    # 9. Streak Footer
    if streak_info and streak_info.get("streak_days", 0) > 0:
        embed.set_footer(
            text=f"🔥 {streak_info['streak_days']}-Day Logging Streak | {streak_info.get('completed_this_week', 0)} tasks crushed this week!"
        )

    # 10. Conversational Reply fallback
    has_content = bool(
        inserted_expenses
        or inserted_tasks
        or completed_tasks
        or goal_update_info
        or payload.add_bill_name
        or payload.goal_create_name
        or payload.set_budget_category
        or payload.ambiguous_task_note
    )
    if payload.conversational_reply and not has_content:
        embed.description = payload.conversational_reply

    return embed


def format_morning_briefing(
    open_tasks: List[Dict[str, Any]],
    due_bills: List[Dict[str, Any]],
    budget_status: List[Dict[str, Any]],
    date_str: str,
    upcoming_bills: Optional[List[Dict[str, Any]]] = None,
    safe_allowance: Optional[Dict[str, Any]] = None,
    active_goals: Optional[List[Dict[str, Any]]] = None,
    rank_info: Optional[Dict[str, Any]] = None,
) -> discord.Embed:
    """Build a sharp morning kickoff embed at 08:30."""
    rank_title = f" | {rank_info['title']}" if rank_info else ""
    embed = discord.Embed(
        title=f"☀️ Morning Briefing — {date_str}{rank_title}",
        description="Here is your focus and financial outlook for today. Have a productive day!",
        color=discord.Color.orange(),
    )

    # 1. Safe-to-Spend Daily Allowance (Proactive Burn Rate Gauge)
    if safe_allowance and safe_allowance.get("has_budget"):
        if safe_allowance.get("is_overspent"):
            over = safe_allowance.get("overspent_by", 0.0)
            allow_text = f"🚨 **Monthly Limit Exceeded by RM {over:.2f}!**\nSafe Daily Allowance: **RM 0.00 / day**."
        else:
            allow = safe_allowance.get("safe_daily_allowance", 0.0)
            days = safe_allowance.get("days_remaining", 1)
            rem = safe_allowance.get("remaining_budget", 0.0)
            allow_text = f"💳 **Safe Daily Allowance:** **RM {allow:.2f} / day**\n_(RM {rem:.2f} remaining over {days} days to hit your target)_"

        embed.add_field(
            name="💡 Spending Runway Gauge",
            value=allow_text,
            inline=False,
        )

    # 2. Savings Goals Trackers
    if active_goals:
        g_lines = []
        for g in active_goals[:3]:
            bar = render_progress_bar(g["current_amount"], g["target_amount"])
            g_lines.append(f"• **{g['name']}:**\n  {bar} _(RM {g['remaining']:.2f} left)_")
        embed.add_field(
            name="🎯 Active Savings Goals",
            value="\n".join(g_lines),
            inline=False,
        )

    # 3. High Priority & Open Tasks
    if open_tasks:
        lines = []
        high_prio = [t for t in open_tasks if t.get("priority") == "HIGH"]
        other_tasks = [t for t in open_tasks if t.get("priority") != "HIGH"]

        if high_prio:
            lines.append("**🔥 High Priority Focus:**")
            for t in high_prio:
                due = f" _(Due: {t['due_date']})_" if t.get("due_date") else ""
                lines.append(f"• `[#{t['id']}]` {t['description']}{due}")
            lines.append("")

        if other_tasks:
            lines.append("**📋 Other Active Tasks:**")
            for t in other_tasks[:8]:
                lines.append(f"• `[#{t['id']} | {t['priority']}]` {t['description']}")

        embed.add_field(
            name=f"🎯 Tasks to Tackle ({len(open_tasks)})",
            value="\n".join(lines),
            inline=False,
        )
    else:
        embed.add_field(
            name="🎯 Tasks",
            value="🎉 No open tasks! You're completely clear.",
            inline=False,
        )

    # 4. Due Recurring Bills (Human-in-the-loop reminder)
    if due_bills:
        bill_lines = []
        for b in due_bills:
            bill_lines.append(f"• **{b['name']}:** RM {b['amount']:.2f} (`{b['category']}`)")
        bill_lines.append("\n💡 _Reply `Logged <Bill>` or `Spent RM <amount>` when paid._")
        embed.add_field(
            name=f"🔔 Recurring Bills Due Today ({len(due_bills)})",
            value="\n".join(bill_lines),
            inline=False,
        )

    # 5. 3-Day Upcoming Bill Warnings
    if upcoming_bills:
        up_lines = []
        for b in upcoming_bills:
            up_lines.append(f"• **{b['name']}:** RM {b['amount']:.2f} _(in {b['due_in_days']} days — {b['due_date_str']})_")
        embed.add_field(
            name="⏳ Upcoming Bills (Next 3 Days)",
            value="\n".join(up_lines),
            inline=False,
        )

    # 6. Monthly Budget Health
    if budget_status:
        b_lines = []
        for b in budget_status[:5]:
            bar = render_progress_bar(b["spent"], b["limit"])
            b_lines.append(f"• **{b['category']}:**\n  {bar}")
        embed.add_field(
            name="📊 Monthly Budget Overview",
            value="\n".join(b_lines),
            inline=False,
        )

    # 7. Upcoming Selangor & Federal Public Holidays (Long Weekend Detector)
    try:
        cur_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        holidays_list = get_upcoming_malaysian_holidays(cur_date, days_ahead=14, subdiv="SGR")
        if holidays_list:
            h_lines = []
            for h in holidays_list[:3]:
                lw_tag = " — **🏝️ 3-Day Long Weekend!**" if h["is_long_weekend"] else ""
                day_tag = "Today!" if h["days_away"] == 0 else f"in {h['days_away']} days ({h['day_name']}, {h['date']})"
                h_lines.append(f"• **{h['name']}** — {day_tag}{lw_tag}")
            embed.add_field(
                name="🇲🇾 Upcoming Public Holidays (Selangor / Federal)",
                value="\n".join(h_lines),
                inline=False,
            )
    except Exception:
        pass

    return embed


def format_daily_summary(
    expenses: List[Dict[str, Any]],
    total_spent: float,
    open_tasks: List[Dict[str, Any]],
    date_str: str,
    spending_pace: Optional[Dict[str, Any]] = None,
    streak_info: Optional[Dict[str, Any]] = None,
    category_proportions: Optional[List[Dict[str, Any]]] = None,
) -> discord.Embed:
    """Build a rich embed for the automated daily summary at 22:00."""
    embed = discord.Embed(
        title=f"📊 Daily Summary — {date_str}",
        color=discord.Color.gold(),
    )

    # Spending Section & Category Proportion ASCII Heatmap
    if expenses:
        cat_map: Dict[str, float] = {}
        for exp in expenses:
            cat = exp["category"]
            cat_map[cat] = round(cat_map.get(cat, 0.0) + exp["amount"], 2)
        
        breakdown = [f"• **{cat}:** RM {amt:.2f}" for cat, amt in cat_map.items()]
        embed.add_field(
            name=f"💸 Total Spent: RM {total_spent:.2f}",
            value="\n".join(breakdown),
            inline=False,
        )

        if category_proportions:
            heatmap_text = render_category_heatmap(category_proportions)
            embed.add_field(
                name="📊 Category Proportions",
                value=heatmap_text,
                inline=False,
            )
    else:
        embed.add_field(
            name="💸 Total Spent: RM 0.00",
            value="No expenses recorded today.",
            inline=False,
        )

    # 7-Day Pace & Monospaced Sparkline
    if spending_pace:
        sparkline = render_sparkline(spending_pace.get("daily_series", []))
        diff_pct = spending_pace.get("diff_pct", 0.0)
        sign = "+" if diff_pct > 0 else ""
        indicator = "🔴" if diff_pct > 15.0 else ("🟢" if diff_pct < -15.0 else "🟡")
        avg = spending_pace.get("seven_day_avg", 0.0)
        pace_text = f"**RM {total_spent:.2f}** today vs **RM {avg:.2f}** 7-day avg ({sign}{diff_pct}% {indicator})\n7-Day Trend: {sparkline}"
        embed.add_field(
            name="📈 Spending Pace & Trend",
            value=pace_text,
            inline=False,
        )

    # Tasks Section
    if open_tasks:
        lines = []
        for t in open_tasks:
            due_str = f" _(Due: {t['due_date']})_" if t.get("due_date") else ""
            prefix = "   └── ⏳ " if t.get("parent_id") else "• "
            phase_info = f"`{t['phase_name']}`: " if t.get("phase_name") else ""
            lines.append(f"{prefix}`[#{t['id']} | {t['priority']}]` {phase_info}{t['description']}{due_str}")
        embed.add_field(
            name=f"📋 Remaining Open Tasks ({len(open_tasks)})",
            value="\n".join(lines[:20]) + ("\n...and more" if len(open_tasks) > 20 else ""),
            inline=False,
        )
    else:
        embed.add_field(
            name="📋 Open Tasks",
            value="🎉 All tasks completed! Great job!",
            inline=False,
        )

    # Streak Footer
    if streak_info and streak_info.get("streak_days", 0) > 0:
        embed.set_footer(
            text=f"🔥 {streak_info['streak_days']}-Day Logging Streak | {streak_info.get('completed_this_week', 0)} tasks finished this week!"
        )

    return embed


def format_live_dashboard(
    today_spent: float,
    pace_data: Dict[str, Any],
    budget_status: List[Dict[str, Any]],
    safe_allowance: Dict[str, Any],
    due_bills: List[Dict[str, Any]],
    upcoming_bills: List[Dict[str, Any]],
    open_tasks: List[Dict[str, Any]],
    streak_info: Dict[str, Any],
    date_str: str,
    active_goals: Optional[List[Dict[str, Any]]] = None,
    rank_info: Optional[Dict[str, Any]] = None,
    dca_progress: Optional[List[Dict[str, Any]]] = None,
    total_invested_month: float = 0.0,
) -> discord.Embed:
    """Build the single-pane-of-glass Live Dashboard with 1-tap in-place refresh."""
    rank_badge = f" | {rank_info['title']}" if rank_info else ""
    embed = discord.Embed(
        title=f"📌 Live Command Center — {date_str}{rank_badge}",
        description="Your real-time financial, budget, and productivity status. Click **[🔄 Refresh]** anytime to update.",
        color=discord.Color.dark_teal(),
    )

    # 1. Spending & 7-Day Sparkline (Consumptive Living Burn)
    sparkline = render_sparkline(pace_data.get("daily_series", []))
    avg = pace_data.get("seven_day_avg", 0.0)
    diff = pace_data.get("diff_pct", 0.0)
    sign = "+" if diff > 0 else ""
    indicator = "🔴" if diff > 15.0 else ("🟢" if diff < -15.0 else "🟡")
    embed.add_field(
        name=f"💸 Today's Living Spend: RM {today_spent:.2f}",
        value=f"7-Day Avg: **RM {avg:.2f}** ({sign}{diff}% {indicator})\nTrend: {sparkline}",
        inline=False,
    )

    # 2. Safe-to-Spend Allowance
    if safe_allowance.get("has_budget"):
        if safe_allowance.get("is_overspent"):
            over = safe_allowance.get("overspent_by", 0.0)
            allow_str = f"🚨 **Overspent by RM {over:.2f}!** Safe Allowance: **RM 0.00 / day**"
        else:
            allow = safe_allowance.get("safe_daily_allowance", 0.0)
            days = safe_allowance.get("days_remaining", 1)
            rem = safe_allowance.get("remaining_budget", 0.0)
            allow_str = f"**RM {allow:.2f} / day** _(RM {rem:.2f} buffer across {days} days)_"
        embed.add_field(name="💡 Safe-to-Spend Runway", value=allow_str, inline=False)

    # 3. Dedicated Wealth & DCA Progress (Asset Building)
    if dca_progress:
        d_lines = []
        for d in dca_progress[:3]:
            bar = render_progress_bar(d["invested_amount"], d["target_amount"])
            status_tag = "✅ Met" if d["is_fulfilled"] else f"⏳ {d['due_day']}th"
            d_lines.append(f"• **{d['name']}** ({status_tag}):\n  {bar}")
        embed.add_field(
            name=f"💎 Wealth & DCA (RM {total_invested_month:.2f} Deployed)",
            value="\n".join(d_lines),
            inline=False,
        )
    elif total_invested_month > 0:
        embed.add_field(
            name="💎 Wealth & Investments",
            value=f"• Total Capital Deployed: **RM {total_invested_month:.2f}** this month.",
            inline=False,
        )

    # 4. Savings Goals
    if active_goals:
        g_lines = []
        for g in active_goals[:3]:
            bar = render_progress_bar(g["current_amount"], g["target_amount"])
            g_lines.append(f"• **{g['name']}:** {bar}")
        embed.add_field(name="🎯 Savings Goals", value="\n".join(g_lines), inline=False)

    # 5. Monthly Budgets
    if budget_status:
        b_lines = []
        for b in budget_status[:4]:
            bar = render_progress_bar(b["spent"], b["limit"])
            b_lines.append(f"• **{b['category']}:** {bar}")
        embed.add_field(name="📊 Living Budget Health", value="\n".join(b_lines), inline=False)

    # 6. Top Priority Tasks
    if open_tasks:
        lines = []
        for t in open_tasks[:5]:
            due = f" _(Due: {t['due_date']})_" if t.get("due_date") else ""
            lines.append(f"• `[#{t['id']} | {t['priority']}]` {t['description']}{due}")
        embed.add_field(name=f"📋 Focus Tasks ({len(open_tasks)} total)", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="📋 Tasks", value="🎉 All tasks completed!", inline=False)

    # 7. Bills (Due Today or Next 3 Days)
    all_bills = due_bills + upcoming_bills
    if all_bills:
        bill_lines = []
        for b in all_bills[:4]:
            tag = "TODAY" if b in due_bills else f"in {b.get('due_in_days', 1)}d"
            bill_lines.append(f"• **{b['name']}** (RM {b['amount']:.2f}) — `{tag}`")
        embed.add_field(name="🔔 Active Bill Reminders", value="\n".join(bill_lines), inline=False)

    # Footer
    streak_days = streak_info.get("streak_days", 0)
    completed_week = streak_info.get("completed_this_week", 0)
    next_m = rank_info.get("next_milestone", "") if rank_info else ""
    embed.set_footer(text=f"🔥 {streak_days}-Day Streak | {completed_week} tasks finished this week | {next_m}")
    return embed


def format_investments_overview(
    investments_summary: Dict[str, Any],
    dca_progress: List[Dict[str, Any]],
    month_str: str,
) -> discord.Embed:
    """Build dedicated Wealth & Investment tracking overview embed."""
    total_invested = investments_summary.get("total_invested", 0.0)
    asset_breakdown = investments_summary.get("asset_breakdown", [])
    class_breakdown = investments_summary.get("class_breakdown", [])

    embed = discord.Embed(
        title=f"💎 Wealth & Investment Center — {month_str}",
        description="Dedicated asset building & DCA tracker. Investments are isolated and never deduct from your living expense allowance!",
        color=discord.Color.teal(),
    )

    # 1. Total Capital Deployed
    embed.add_field(
        name=f"💰 Capital Deployed This Month: RM {total_invested:.2f}",
        value=f"Total transactions logged: **{investments_summary.get('count', 0)}**",
        inline=False,
    )

    # 2. Monthly DCA Commitments Checklist
    if dca_progress:
        dca_lines = []
        for d in dca_progress:
            bar = render_progress_bar(d["invested_amount"], d["target_amount"])
            status_tag = "✅ Met" if d["is_fulfilled"] else f"⏳ Due on {d['due_day']}th"
            streak_str = f" | 🔥 {d['streak_months']}-mo streak" if d["streak_months"] > 0 else ""
            dca_lines.append(
                f"• **[Bill #{d['bill_id']}] {d['name']}** ({status_tag}{streak_str}):\n"
                f"  {bar}"
            )
        embed.add_field(
            name=f"📈 Monthly DCA Discipline Checklist ({len(dca_progress)})",
            value="\n".join(dca_lines),
            inline=False,
        )
    else:
        embed.add_field(
            name="📈 Monthly DCA Commitments",
            value="No recurring investment commitments set. Create one by typing e.g. *'recurring buy $100 s&p500 on the 27th'*!",
            inline=False,
        )

    # 3. Asset Allocation Breakdown
    if asset_breakdown:
        lines = [f"• **{a['asset_name']}** ({a['asset_class']}): **RM {a['total_amount']:.2f}** ({a['percentage']}%)" for a in asset_breakdown[:6]]
        embed.add_field(
            name="📊 Asset Allocation Breakdown",
            value="\n".join(lines),
            inline=False,
        )

    embed.set_footer(text="Consistency is the mother of compounding. Keep dollar-cost averaging!")
    return embed


def format_goals_overview(goals: List[Dict[str, Any]]) -> discord.Embed:
    """Build dedicated Savings Goals overview embed."""
    embed = discord.Embed(
        title="🎯 Savings Goals Overview",
        description="Dedicated asset accumulation funds. Regular spending does NOT deduct from these targets!",
        color=discord.Color.gold(),
    )
    if not goals:
        embed.description = "No active savings goals found. Create one by saying e.g. *'Create goal Japan Trip target RM 6000'*!"
        return embed

    for g in goals:
        bar = render_progress_bar(g["current_amount"], g["target_amount"])
        target_d = f" | Target Date: `{g['target_date']}`" if g.get("target_date") else ""
        embed.add_field(
            name=f"🏆 [ID: #{g['id']}] {g['name']}{target_d}",
            value=f"{bar}\nRemaining: **RM {g['remaining']:.2f}**\n",
            inline=False,
        )
    return embed

    for g in goals:
        bar = render_progress_bar(g["current_amount"], g["target_amount"])
        target_d = f" | Target Date: `{g['target_date']}`" if g.get("target_date") else ""
        embed.add_field(
            name=f"🏆 [ID: #{g['id']}] {g['name']}{target_d}",
            value=f"{bar}\nRemaining: **RM {g['remaining']:.2f}**\n",
            inline=False,
        )
    return embed


def format_search_results(keyword: str, results: Dict[str, Any]) -> discord.Embed:
    """Build keyword search and filter embed."""
    expenses = results.get("expenses", [])
    tasks = results.get("tasks", [])
    total_spent = results.get("total_spent_on_matches", 0.0)

    embed = discord.Embed(
        title=f"🔍 Search Results for \"{keyword}\"",
        color=discord.Color.blurple(),
    )

    if not expenses and not tasks:
        embed.description = f"No matching expenses or tasks found for *'{keyword}'*."
        return embed

    if expenses:
        exp_lines = []
        for e in expenses[:10]:
            note = f" ({e['note']})" if e.get("note") else ""
            exp_lines.append(f"• `{e['created_at'][:10]}`: **RM {e['amount']:.2f}** — `{e['category']}`{note}")
        embed.add_field(
            name=f"💸 Matching Expenses (Total: RM {total_spent:.2f})",
            value="\n".join(exp_lines),
            inline=False,
        )

    if tasks:
        task_lines = []
        for t in tasks[:10]:
            status_tag = "✅ DONE" if t["status"] == "DONE" else f"⏳ `[{t['priority']}]`"
            task_lines.append(f"• `[#{t['id']}]` {status_tag} {t['description']}")
        embed.add_field(
            name=f"📋 Matching Tasks ({len(tasks)})",
            value="\n".join(task_lines),
            inline=False,
        )

    return embed


def format_calendar_day_view(
    target_date_str: str,
    expenses: List[Dict[str, Any]],
    total_spent: float,
    open_tasks: List[Dict[str, Any]],
) -> discord.Embed:
    """Build 1-day calendar inspector embed."""
    embed = discord.Embed(
        title=f"📅 Day Inspector — {target_date_str}",
        color=discord.Color.blue(),
    )
    if expenses:
        lines = [f"• **RM {e['amount']:.2f}** — `{e['category']}` ({e.get('note') or 'No note'})" for e in expenses]
        embed.add_field(name=f"💸 Expenses (Total: RM {total_spent:.2f})", value="\n".join(lines[:12]), inline=False)
    else:
        embed.add_field(name="💸 Expenses", value="No expenses on this date.", inline=False)

    matching_tasks = [t for t in open_tasks if t.get("due_date") == target_date_str]
    if matching_tasks:
        t_lines = [f"• `[#{t['id']} | {t['priority']}]` {t['description']}" for t in matching_tasks]
        embed.add_field(name=f"📋 Tasks Due on this Day ({len(matching_tasks)})", value="\n".join(t_lines), inline=False)
    else:
        embed.add_field(name="📋 Tasks Due", value="No tasks due on this specific date.", inline=False)

    return embed


def generate_html_report(
    month_str: str,
    expenses: List[Dict[str, Any]],
    total_spent: float,
    proportions: List[Dict[str, Any]],
    budget_status: List[Dict[str, Any]],
    open_tasks: List[Dict[str, Any]],
    completed_tasks: List[Dict[str, Any]],
    goals: List[Dict[str, Any]],
    streak_info: Dict[str, Any],
    rank_info: Dict[str, Any],
    investments_summary: Optional[Dict[str, Any]] = None,
    dca_progress: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Generate a responsive, standalone dark-mode HTML executive report."""
    prop_rows = "".join(
        [
            f"<tr><td>{p['category']}</td><td>RM {p['amount']:.2f}</td><td>{p['percentage']}%</td></tr>"
            for p in proportions
        ]
    ) or "<tr><td colspan='3'>No expenses logged this month.</td></tr>"

    budget_rows = "".join(
        [
            f"<tr><td>{b['category']}</td><td>RM {b['spent']:.2f}</td><td>RM {b['limit']:.2f}</td><td>{b['percentage']}%</td></tr>"
            for b in budget_status
        ]
    ) or "<tr><td colspan='4'>No budgets configured.</td></tr>"

    goal_rows = "".join(
        [
            f"<tr><td>{g['name']}</td><td>RM {g['current_amount']:.2f}</td><td>RM {g['target_amount']:.2f}</td><td>{g['percentage']}%</td></tr>"
            for g in goals
        ]
    ) or "<tr><td colspan='4'>No active savings goals.</td></tr>"

    total_invested = investments_summary.get("total_invested", 0.0) if investments_summary else 0.0
    dca_rows = "".join(
        [
            f"<tr><td>{d['name']}</td><td>RM {d['invested_amount']:.2f}</td><td>RM {d['target_amount']:.2f}</td><td>{d['percentage']}% ({'✅ Met' if d['is_fulfilled'] else '⏳ Pending'})</td></tr>"
            for d in (dca_progress or [])
        ]
    ) or "<tr><td colspan='4'>No recurring DCA commitments.</td></tr>"

    asset_rows = "".join(
        [
            f"<tr><td>{a['asset_name']}</td><td>{a['asset_class']}</td><td>RM {a['total_amount']:.2f}</td><td>{a['percentage']}%</td></tr>"
            for a in (investments_summary.get("asset_breakdown", []) if investments_summary else [])
        ]
    ) or "<tr><td colspan='4'>No investments logged this period.</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Perlica Executive Financial & Productivity Report — {month_str}</title>
    <style>
        body {{
            background-color: #0e1117;
            color: #e0e6ed;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            margin: 0;
            padding: 40px 20px;
        }}
        .container {{
            max-width: 900px;
            margin: auto;
        }}
        h1, h2, h3 {{
            color: #58a6ff;
        }}
        .card-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        .card {{
            background-color: #161b22;
            border: 1px solid #30363d;
            border-radius: 10px;
            padding: 20px;
            text-align: center;
        }}
        .card-val {{
            font-size: 24px;
            font-weight: bold;
            color: #58a6ff;
            margin-top: 10px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 30px;
            background-color: #161b22;
            border-radius: 8px;
            overflow: hidden;
        }}
        th, td {{
            padding: 12px 15px;
            text-align: left;
            border-bottom: 1px solid #30363d;
        }}
        th {{
            background-color: #21262d;
            color: #8b949e;
            text-transform: uppercase;
            font-size: 12px;
            letter-spacing: 0.5px;
        }}
        .badge {{
            display: inline-block;
            padding: 4px 10px;
            background: #238636;
            border-radius: 12px;
            font-size: 12px;
            font-weight: bold;
        }}
        .footer {{
            text-align: center;
            color: #8b949e;
            font-size: 13px;
            margin-top: 50px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 Perlica Executive Report</h1>
        <p>Period: <strong>{month_str}</strong> | Rank: <span class="badge">{rank_info.get('title', 'Apprentice')}</span></p>

        <div class="card-grid">
            <div class="card">
                <div>Living Spend</div>
                <div class="card-val">RM {total_spent:.2f}</div>
            </div>
            <div class="card">
                <div>Capital Invested</div>
                <div class="card-val" style="color: #2ea043;">💎 RM {total_invested:.2f}</div>
            </div>
            <div class="card">
                <div>Logging Streak</div>
                <div class="card-val">🔥 {streak_info.get('streak_days', 0)} Days</div>
            </div>
            <div class="card">
                <div>Tasks Crushed</div>
                <div class="card-val">✅ {len(completed_tasks)}</div>
            </div>
            <div class="card">
                <div>Active Goals</div>
                <div class="card-val">🏆 {len(goals)}</div>
            </div>
        </div>

        <h2>💎 Wealth & Monthly DCA Progress</h2>
        <table>
            <thead><tr><th>DCA Commitment</th><th>Invested</th><th>Target</th><th>Status</th></tr></thead>
            <tbody>{dca_rows}</tbody>
        </table>

        <h2>📊 Asset Allocation Breakdown</h2>
        <table>
            <thead><tr><th>Asset Name</th><th>Asset Class</th><th>Total Deployed</th><th>Share</th></tr></thead>
            <tbody>{asset_rows}</tbody>
        </table>

        <h2>💸 Living Spending by Category</h2>
        <table>
            <thead><tr><th>Category</th><th>Total Amount</th><th>Share</th></tr></thead>
            <tbody>{prop_rows}</tbody>
        </table>

        <h2>💳 Monthly Budget Performance</h2>
        <table>
            <thead><tr><th>Category</th><th>Spent</th><th>Limit</th><th>Utilization</th></tr></thead>
            <tbody>{budget_rows}</tbody>
        </table>

        <h2>🎯 Dedicated Savings Goals</h2>
        <table>
            <thead><tr><th>Goal Name</th><th>Current Saved</th><th>Target</th><th>Progress</th></tr></thead>
            <tbody>{goal_rows}</tbody>
        </table>

        <div class="footer">
            Generated autonomously by Perlica Personal Assistant on {month_str}
        </div>
    </div>
</body>
</html>
"""


def format_weekly_executive_review(
    review_data: Dict[str, Any],
    ai_strategic_kickoff: Optional[str] = None,
) -> discord.Embed:
    """Build a rich Sunday 8:00 PM Weekly Executive Review embed."""
    embed = discord.Embed(
        title=f"🏆 Sunday Executive Review ({review_data['start_date']} to {review_data['end_date']})",
        color=discord.Color.dark_purple(),
    )

    if ai_strategic_kickoff:
        embed.description = f"🎯 **Weekly Strategic Kickoff:**\n{ai_strategic_kickoff}\n"

    # 1. Weekly Finances
    total_spent = review_data.get("total_spent", 0.0)
    cat_breakdown = review_data.get("category_breakdown", {})
    if cat_breakdown:
        sorted_cats = sorted(cat_breakdown.items(), key=lambda x: x[1], reverse=True)
        cat_lines = [f"• **{cat}:** RM {amt:.2f}" for cat, amt in sorted_cats[:5]]
        embed.add_field(
            name=f"💸 Total Spent This Week: RM {total_spent:.2f}",
            value="\n".join(cat_lines),
            inline=False,
        )
    else:
        embed.add_field(
            name="💸 Total Spent This Week",
            value="RM 0.00 logged this week.",
            inline=False,
        )

    # 2. Tasks Performance Ratio
    completed_count = review_data.get("completed_tasks_count", 0)
    open_count = review_data.get("open_tasks_count", 0)
    total_tasks = completed_count + open_count
    ratio_pct = round((completed_count / total_tasks * 100), 1) if total_tasks > 0 else 100.0
    embed.add_field(
        name="📊 Task Execution Rate",
        value=f"• **{completed_count}** tasks completed this week ({ratio_pct}% completion rate)\n• **{open_count}** open tasks carrying into next week",
        inline=False,
    )

    # 3. Monthly Budget Health
    budget_status = review_data.get("budget_status", [])
    if budget_status:
        b_lines = []
        for b in budget_status[:4]:
            bar = render_progress_bar(b["spent"], b["limit"])
            b_lines.append(f"• **{b['category']}:** {bar}")
        embed.add_field(
            name="💳 Monthly Budget Runway",
            value="\n".join(b_lines),
            inline=False,
        )

    embed.set_footer(text="Have a powerful and focused week ahead!")
    return embed


def format_task_selector_embed(open_tasks: List[Dict[str, Any]]) -> discord.Embed:
    """Build task selection guide embed for native dropdown completion."""
    embed = discord.Embed(
        title="📋 Active Open Tasks",
        color=discord.Color.blurple(),
    )
    if not open_tasks:
        embed.description = "🎉 No open tasks! You're completely caught up."
        return embed

    total_count = len(open_tasks)
    showing_count = min(total_count, 25)
    note_extra = f"\n_(Showing top 25 of {total_count} open tasks in dropdown below)_" if total_count > 25 else ""
    embed.description = f"Select tasks from the dropdown menu below to mark them **DONE** in one tap!{note_extra}\n"

    lines = []
    for t in open_tasks[:25]:
        due_str = f" _(Due: {t['due_date']})_" if t.get("due_date") else ""
        lines.append(f"• `[#{t['id']} | {t['priority']}]` {t['description']}{due_str}")

    embed.add_field(
        name=f"Tasks List ({showing_count})",
        value="\n".join(lines),
        inline=False,
    )
    return embed


def format_task_snooze_embed(task: Dict[str, Any]) -> discord.Embed:
    """Build a task snooze / postpone control embed."""
    embed = discord.Embed(
        title=f"⏰ Snooze Task #{task['id']}",
        description=f"**`[{task['priority']}]` {task['description']}**\nCurrent Due Date: **{task.get('due_date') or 'None'}**\n\nChoose an action below:",
        color=discord.Color.orange(),
    )
    return embed


def format_presets_embed() -> discord.Embed:
    """Build a 1-tap quick log presets embed."""
    embed = discord.Embed(
        title="⚡ 1-Tap Quick Log Presets",
        description="Tap any button below to instantly trigger a 3-button confirmation preview for your common everyday entries!",
        color=discord.Color.teal(),
    )
    embed.add_field(
        name="Available Quick Presets",
        value=(
            "• 🍽️ **Mamak Lunch (RM 15.00)** — `Food & Dining`\n"
            "• 🚗 **TNG Card Reload (RM 50.00)** — `Transport`\n"
            "• ☕ **Kopitiam Coffee (RM 12.00)** — `Food & Dining`\n"
            "• 🛒 **99 Speedmart (RM 30.00)** — `Groceries`"
        ),
        inline=False,
    )
    return embed


def format_full_snapshot_summary(
    snapshot: Dict[str, Any],
    timeframe_title: str,
    ai_digest: Optional[str] = None,
    budget_status: Optional[List[Dict[str, Any]]] = None,
) -> discord.Embed:
    """Build a comprehensive on-demand summary embed with financial, task, and budget stats."""
    embed = discord.Embed(
        title=f"📊 Executive Summary ({timeframe_title})",
        color=discord.Color.blue(),
    )

    if ai_digest:
        embed.description = f"💡 **AI Digest:**\n{ai_digest}\n"

    # 1. Finances
    total_spent = snapshot.get("total_spent", 0.0)
    cat_breakdown = snapshot.get("category_breakdown", {})
    if cat_breakdown:
        breakdown_lines = [f"• **{k}:** RM {v:.2f}" for k, v in cat_breakdown.items()]
        embed.add_field(
            name=f"💸 Total Expenditure (RM {total_spent:.2f})",
            value="\n".join(breakdown_lines),
            inline=False,
        )
    else:
        embed.add_field(
            name="💸 Expenditure",
            value="RM 0.00 recorded for this period.",
            inline=False,
        )

    # 2. Budget Utilization
    if budget_status:
        b_lines = []
        for b in budget_status:
            bar = render_progress_bar(b["spent"], b["limit"])
            b_lines.append(f"• **{b['category']}:**\n  {bar}")
        embed.add_field(
            name="📈 Budget Health",
            value="\n".join(b_lines),
            inline=False,
        )

    # 3. Accomplished Tasks
    completed = snapshot.get("completed_tasks", [])
    if completed:
        lines = [f"• ~~`[#{t['id']}]` {t['description']}~~" for t in completed]
        embed.add_field(
            name=f"✅ Completed Items ({len(completed)})",
            value="\n".join(lines[:15]),
            inline=False,
        )

    # 4. Pending Open Tasks
    open_tasks = snapshot.get("open_tasks", [])
    if open_tasks:
        lines = []
        for t in open_tasks:
            due_str = f" _(Due: {t['due_date']})_" if t.get("due_date") else ""
            prefix = "   └── ⏳ " if t.get("parent_id") else "• "
            phase_info = f"`{t['phase_name']}`: " if t.get("phase_name") else ""
            lines.append(f"{prefix}`[#{t['id']} | {t['priority']}]` {phase_info}{t['description']}{due_str}")
        embed.add_field(
            name=f"📋 Active Open Tasks ({len(open_tasks)})",
            value="\n".join(lines[:15]),
            inline=False,
        )
    else:
        embed.add_field(
            name="📋 Active Tasks",
            value="🎉 No open tasks! You are all caught up.",
            inline=False,
        )

    return embed


def format_budget_overview(budget_status: List[Dict[str, Any]]) -> discord.Embed:
    """Build a dedicated budget status embed."""
    embed = discord.Embed(
        title="📊 Monthly Budget Overview",
        color=discord.Color.teal(),
    )
    if not budget_status:
        embed.description = "No category budgets configured yet. Set one anytime by saying e.g. *'Set monthly food budget to RM 800'*!"
        return embed

    lines = []
    for b in budget_status:
        bar = render_progress_bar(b["spent"], b["limit"])
        rem_str = f"RM {b['remaining']:.2f} left" if b['remaining'] >= 0 else f"RM {abs(b['remaining']):.2f} OVER"
        lines.append(f"• **{b['category']}** ({rem_str}):\n  {bar}\n")

    embed.description = "\n".join(lines)
    return embed


def format_query_results(
    query: QueryScope,
    expenses: Optional[List[Dict[str, Any]]] = None,
    total_spent: Optional[float] = None,
    category_breakdown: Optional[Dict[str, float]] = None,
    tasks: Optional[List[Dict[str, Any]]] = None,
    ai_answer: Optional[str] = None,
) -> discord.Embed:
    """Format on-demand query results."""
    embed = discord.Embed(
        title=f"🔍 Status Report ({query.query_target} - {query.timeframe})",
        color=discord.Color.blurple(),
    )

    if ai_answer:
        embed.description = ai_answer

    if query.query_target in ("EXPENSES", "SUMMARY") and expenses is not None:
        if expenses:
            cat_lines = [f"• **{k}:** RM {v:.2f}" for k, v in (category_breakdown or {}).items()]
            embed.add_field(
                name=f"💸 Spending Total: RM {(total_spent or 0.0):.2f}",
                value="\n".join(cat_lines) if cat_lines else "No categorized expenses.",
                inline=False,
            )
        else:
            embed.add_field(
                name="💸 Expenses",
                value="No expenses found for this timeframe.",
                inline=False,
            )

    if query.query_target in ("TASKS", "SUMMARY") and tasks is not None:
        if tasks:
            lines = []
            for t in tasks:
                prefix = "   └── ⏳ " if t.get("parent_id") else "• "
                lines.append(f"{prefix}`[#{t['id']} | {t['priority']}]` {t['description']}")
            embed.add_field(
                name=f"📋 Active Tasks ({len(tasks)})",
                value="\n".join(lines[:20]),
                inline=False,
            )
        else:
            embed.add_field(
                name="📋 Tasks",
                value="No open tasks found.",
                inline=False,
            )

    return embed


def format_help_guide() -> discord.Embed:
    """Build a comprehensive guide embed showing how to interact with the bot."""
    embed = discord.Embed(
        title="📖 Perlica Personal Agent — Command & Feature Guide",
        description="Send natural messages, voice notes, receipt photos, or use native `/` slash commands!",
        color=discord.Color.teal(),
    )

    embed.add_field(
        name="⚡ Daily Productivity & Slash Commands",
        value=(
            "• `/dashboard` or `dashboard` *(Live command center)*\n"
            "• `/focus` *(Daily single-task focus widget with skip & snooze)*\n"
            "• `/history` *(Paginated transaction explorer with 1-tap delete dropdown)*\n"
            "• `/tasks` *(Batch task completion dropdown)*\n"
            "• `/budgets` *(Adjust limits via interactive popup modal)*\n"
            "• `/goals` *(Savings goals overview & 1-tap deposit)*\n"
            "• `/investments` *(Dedicated Wealth & DCA portfolio)*\n"
            "• `/holidays` or `holidays` *(Selangor & Federal holiday countdowns)*\n"
            "• `/category` *(Itemized category inspector with autocomplete)*\n"
            "• `/report` *(Download standalone Executive HTML report)*"
        ),
        inline=False,
    )

    embed.add_field(
        name="🤖 Evidence-Grounded Knowledge & Code Copilot",
        value=(
            "• `/ask query:... [in_source:...]` *(Grounded Q&A with deep citations & raw inspector)*\n"
            "• `/repo sync repo:owner/name` *(Incremental Git SHA reconciliation)*\n"
            "• `/ingest source_type:web|pdf target:...` *(SSRF-safe webpage & PDF extractor)*\n"
            "• `/note content:... [title:...]` *(Instant indexed knowledge snippets)*\n"
            "• `/sources` *(Live status, coverage ratio & 1-tap purge)*"
        ),
        inline=False,
    )

    embed.add_field(
        name="🇲🇾 Natural Malaysian & Manglish Ingestion",
        value=(
            "• `tapau nasi kandar rm 14.50 semalam` *(Auto-dates to yesterday)*\n"
            "• `isi minyak petronas rm 50` *(Tracks 200L RON95 subsidy @ RM 1.99/L)*\n"
            "• `bayar bil tnb rm 120 kelmarin` *(Utilities & Bills)*\n"
            "• 🎙️ *Send voice notes while driving!*\n"
            "• 📸 *Send receipt photos for automatic Vision OCR!*"
        ),
        inline=False,
    )

    embed.add_field(
        name="💎 Wealth & DCA Commitments (Budget-Immune)",
        value=(
            "• `bought $100 s&p500` / `dca 400 into voo`\n"
            "• `recurring buy RM 400 s&p500 on 27th`\n"
            "• *Investments never deduct from your living expense allowance!*"
        ),
        inline=False,
    )

    embed.add_field(
        name="🛡️ Zero-Assumption Guardrails",
        value=(
            "• **3-Button Action Gate**: Review preview before saving (`[Confirm]` / `[Edit]` / `[Reject]`)\n"
            "• **Double-Tap Protection**: 5-minute duplicate collision warning (`[Log Anyway]` / `[Discard]`)\n"
            "• **Quick Undo**: 10-second rollback toast on every logged action"
        ),
        inline=False,
    )

    embed.set_footer(text="Tip: Type / for native slash command autocomplete anytime.")
    return embed


def format_milestone_celebration(milestone: Dict[str, Any]) -> discord.Embed:
    """Build a rich celebratory embed when a financial or productivity milestone is unlocked."""
    embed = discord.Embed(
        title=f"🎖️ {milestone['title']}",
        description=f"### {milestone['badge']}\n{milestone['description']}",
        color=discord.Color.gold(),
    )
    embed.set_footer(text="Consistency is the engine of wealth. Keep going!")
    return embed


def format_category_filtered_view(
    category: str,
    expenses: List[Dict[str, Any]],
    total_spent: float,
    month_str: str,
) -> discord.Embed:
    """Build an itemized category inspector embed for interactive dropdown filtering."""
    embed = discord.Embed(
        title=f"📂 Category Inspector: {category}",
        description=f"Spending breakdown for **{month_str}** | Total: **RM {total_spent:.2f}** ({len(expenses)} transactions)",
        color=discord.Color.blue(),
    )
    if not expenses:
        embed.add_field(
            name="No Transactions",
            value=f"No expenses logged under `{category}` for this period.",
            inline=False,
        )
        return embed

    lines = []
    for exp in expenses[:15]:
        note = f" — {exp['note']}" if exp.get("note") else ""
        date_str = f" `[{exp['created_at'][:10]}]`" if exp.get("created_at") else ""
        lines.append(f"• **RM {exp['amount']:.2f}**{note}{date_str}")

    embed.add_field(
        name="Recent Transactions",
        value="\n".join(lines) + ("\n...and more" if len(expenses) > 15 else ""),
        inline=False,
    )
    return embed


def format_voice_transcription_preview(transcription_text: str) -> discord.Embed:
    """Build an immediate visual card showing transcribed voice audio."""
    embed = discord.Embed(
        title="🎙️ Voice Note Transcribed",
        description=f"> *\"{transcription_text}\"*",
        color=discord.Color.teal(),
    )
    embed.set_footer(text="Parsing actions with zero-assumption engine...")
    return embed


def format_bill_reminder_embed(bill: Dict[str, Any], due_tag: str, is_paid: bool = False) -> discord.Embed:
    """Build an actionable reminder card for recurring bills and DCA investments."""
    status_icon = "✅" if is_paid else "🔔"
    cat_tag = "💎 Wealth & DCA" if bill.get("category") == "Investments & Savings" else f"`{bill['category']}`"
    
    embed = discord.Embed(
        title=f"{status_icon} Recurring Commitment Reminder: {bill['name']}",
        description=(
            f"• **Amount**: **RM {bill['amount']:.2f}**\n"
            f"• **Category**: {cat_tag}\n"
            f"• **Status**: `{due_tag}`"
        ),
        color=discord.Color.green() if is_paid else discord.Color.gold(),
    )
    if not is_paid:
        embed.set_footer(text="Click [Log & Pay Now] below to record instantly with 0 typing.")
    return embed


def get_time_aware_greeting(now_dt: datetime) -> str:
    """Return an atmospheric greeting matching the user's local hour."""
    hour = now_dt.hour
    if 5 <= hour < 12:
        return "🌅 Good morning Daniel"
    elif 12 <= hour < 18:
        return "☀️ Good afternoon Daniel"
    elif 18 <= hour < 23:
        return "🌆 Good evening Daniel"
    else:
        return "🌙 Late night mode"


def format_transaction_page(
    expenses: List[Dict[str, Any]],
    page: int,
    total_pages: int,
    total_count: int,
    month_str: str,
) -> discord.Embed:
    """Build a clean paginated transaction explorer embed with defensive non-negative bounds."""
    embed = discord.Embed(
        title=f"📜 Transaction Explorer — {month_str}",
        description=f"Showing Page **{page}** of **{total_pages}** ({total_count} total records)",
        color=discord.Color.blue(),
    )
    if not expenses or total_count == 0:
        embed.description = f"No expenses recorded for **{month_str}**."
        embed.add_field(
            name="Empty Month",
            value="Log an expense anytime by typing e.g. *'RM 15 lunch'*.",
            inline=False,
        )
        return embed

    lines = []
    for exp in expenses:
        note_str = f" — {exp['note']}" if exp.get("note") else ""
        date_str = f" `[{exp['created_at'][:10]}]`" if exp.get("created_at") else ""
        cat_str = f"`{exp['category']}`"
        lines.append(f"• `[#{exp['id']}]` **RM {exp['amount']:.2f}** ({cat_str}){note_str}{date_str}")

    embed.add_field(
        name="Expenses on this Page",
        value="\n".join(lines),
        inline=False,
    )
    embed.set_footer(text="Use [◀️ Prev] [Next ▶️] or select an expense below to delete.")
    return embed


def format_focus_task_embed(
    task: Optional[Dict[str, Any]], current_idx: int, total_open: int
) -> discord.Embed:
    """Build a high-impact single-task daily focus card."""
    if not task or total_open == 0:
        embed = discord.Embed(
            title="🎉 All Focus Tasks Clear!",
            description="You have 0 pending open tasks. Excellent productivity momentum!",
            color=discord.Color.green(),
        )
        return embed

    prio_emoji = "🔴" if task.get("priority") == "HIGH" else ("🟡" if task.get("priority") == "MEDIUM" else "🟢")
    due_str = f"📅 Due: `{task['due_date']}`" if task.get("due_date") else "📅 No deadline"
    phase_str = f"\n🧩 **Phase**: `{task['phase_name']}`" if task.get("phase_name") else ""

    embed = discord.Embed(
        title=f"🎯 Daily Focus — Task {current_idx + 1} of {total_open}",
        description=(
            f"### `[#{task['id']}]` {task['description']}\n"
            f"• **Priority**: {prio_emoji} `{task.get('priority', 'MEDIUM')}`\n"
            f"• **Deadline**: {due_str}{phase_str}"
        ),
        color=discord.Color.brand_green() if task.get("priority") != "HIGH" else discord.Color.gold(),
    )
    embed.set_footer(text="Tap [Complete] when done, or [Skip] to rotate focus.")
    return embed


def get_upcoming_malaysian_holidays(
    base_date: Any, days_ahead: int = 30, subdiv: str = "SGR"
) -> List[Dict[str, Any]]:
    """
    Fetch upcoming Federal and Selangor public holidays using the holidays library.
    Correctly accounts for Islamic lunar shifts, Hindu lunisolar shifts, and state holidays.
    """
    import holidays
    if isinstance(base_date, str):
        base_date = datetime.strptime(base_date, "%Y-%m-%d").date()
    elif isinstance(base_date, datetime):
        base_date = base_date.date()

    my_holidays = holidays.Malaysia(years=[base_date.year, base_date.year + 1], subdiv=subdiv)
    upcoming = []

    for hol_date, hol_name in sorted(my_holidays.items()):
        delta = (hol_date - base_date).days
        if 0 <= delta <= days_ahead:
            weekday = hol_date.weekday()
            is_long_weekend = weekday in (0, 4)  # Monday or Friday
            upcoming.append({
                "name": hol_name,
                "date": hol_date.strftime("%Y-%m-%d"),
                "days_away": delta,
                "is_long_weekend": is_long_weekend,
                "day_name": hol_date.strftime("%A"),
            })

    return upcoming


def format_fuel_receipt_embed(
    amount: float,
    fuel_details: Dict[str, Any],
) -> discord.Embed:
    """Build a rich Malaysian fuel receipt and RON95 quota tracking embed."""
    grade = fuel_details["grade"]
    liters = fuel_details["liters_added"]
    tier_label = fuel_details["tier_label"]
    consumes_sub = fuel_details["consumes_subsidy"]
    quota_left = fuel_details.get("ron95_quota_remaining", 200.0)
    new_total = fuel_details.get("new_total_ron95_liters", 0.0)

    embed = discord.Embed(
        title=f"🚗 Fuel Logged — {grade} ({liters} Litres)",
        description=f"• **Amount**: **RM {amount:.2f}**\n• **Pricing Tier**: `{tier_label}`",
        color=discord.Color.teal() if consumes_sub else discord.Color.blue(),
    )

    if consumes_sub:
        bar = render_progress_bar(new_total, 200.0, bar_length=10)
        embed.add_field(
            name="🇲🇾 Subsidized RON95 Monthly Quota (200L @ RM 1.99/L)",
            value=f"{bar}\n_Quota remaining this month: **{quota_left:.2f} L**_",
            inline=False,
        )
    else:
        embed.add_field(
            name="⛽ Unsubsidized Fuel Note",
            value=f"_{grade} is unsubsidized market floating rate. Your 200L RON95 subsidy quota remains untouched!_",
            inline=False,
        )

    return embed


def format_upcoming_holidays_embed(
    holidays_list: List[Dict[str, Any]], base_date_str: str
) -> discord.Embed:
    """Build an on-demand upcoming public holidays & long weekend guide."""
    embed = discord.Embed(
        title="🇲🇾 Malaysian Public Holidays & Long Weekends",
        description=f"Showing upcoming Federal & Selangor state holidays from **{base_date_str}**:",
        color=discord.Color.gold(),
    )
    if not holidays_list:
        embed.description = f"No public holidays in the next 60 days from **{base_date_str}**."
        return embed

    lines = []
    for h in holidays_list:
        lw_tag = " — **🏝️ 3-Day Long Weekend!**" if h["is_long_weekend"] else ""
        if h["days_away"] == 0:
            day_tag = "**Today!**"
        elif h["days_away"] == 1:
            day_tag = "**Tomorrow!**"
        else:
            day_tag = f"in **{h['days_away']} days**"

        lines.append(f"• **{h['name']}**\n  📅 `{h['date']}` ({h['day_name']}) — {day_tag}{lw_tag}")

    embed.add_field(
        name="Upcoming Holidays Calendar",
        value="\n\n".join(lines[:8]),
        inline=False,
    )
    embed.set_footer(text="Powered by official Malaysian lunar, lunisolar, and state calendar engine.")
    return embed


KNOWN_HTML_TAGS = r"(?:script|style|iframe|object|embed|br|hr|p|div|span|b|i|u|s|strong|em|ul|ol|li|table|thead|tbody|tr|td|th|h[1-6]|font|a|img|meta|link|pre|code|form|input|button)"


def split_code_and_prose(text: str) -> List[Tuple[bool, str]]:
    """
    Split markdown text into a sequence of (is_code, content) segments.
    Preserves both multi-line fenced code blocks (```...```) and inline code (`...`) byte-for-byte.
    """
    if not text:
        return []

    # Match fenced code blocks (```...```) or inline code (`...`)
    pattern = re.compile(r"(```[\s\S]*?```|`[^`\n]+`)", re.MULTILINE)
    parts: List[Tuple[bool, str]] = []
    last_end = 0

    for match in pattern.finditer(text):
        start, end = match.span()
        if start > last_end:
            prose = text[last_end:start]
            if prose:
                parts.append((False, prose))
        parts.append((True, match.group(0)))
        last_end = end

    if last_end < len(text):
        prose = text[last_end:]
        if prose:
            parts.append((False, prose))

    return parts


def convert_markdown_tables_to_bullets(text: str) -> str:
    """Convert markdown tables to clean structured bullet lists with bold keys."""
    if not text or "|" not in text:
        return text

    lines = text.split("\n")
    formatted_lines: List[str] = []
    in_table = False
    table_headers: List[str] = []

    for line in lines:
        stripped = line.strip()
        # Check for table separator row e.g. |---|---|
        if re.match(r"^\|?\s*[-:\s|]+\s*\|?$", stripped) and "-" in stripped and "|" in stripped:
            in_table = True
            continue

        # Check for pipe table row
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1] if c.strip()]
            if not cells:
                continue
            if not in_table and not table_headers:
                table_headers = cells
                formatted_lines.append(f"**{' / '.join(cells)}**")
                continue
            elif in_table or table_headers:
                if len(cells) == 1:
                    formatted_lines.append(f"• {cells[0]}")
                elif table_headers and len(table_headers) == len(cells):
                    item_title = cells[0]
                    sub_details = [f"{table_headers[idx]}: {cells[idx]}" for idx in range(1, len(cells))]
                    formatted_lines.append(f"• **{item_title}** (" + ", ".join(sub_details) + ")")
                else:
                    formatted_lines.append(f"• **{cells[0]}**: " + " — ".join(cells[1:]))
                continue
        else:
            in_table = False
            table_headers = []
            formatted_lines.append(line)

    return "\n".join(formatted_lines)


def sanitize_prose_segment(text: str) -> str:
    """Sanitize a prose segment without altering code blocks or generic angle brackets."""
    if not text:
        return ""
    # Strip script/style/iframe/hostile blocks
    cleaned = re.sub(r"<(?:script|style|iframe|object|embed)[^>]*>.*?</(?:script|style|iframe|object|embed)>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Convert HTML line breaks <br>, <br/> to newlines
    cleaned = re.sub(r"<br\s*/?>", "\n", cleaned, flags=re.IGNORECASE)
    # Convert <hr> to markdown divider
    cleaned = re.sub(r"<hr\s*/?>", "\n---\n", cleaned, flags=re.IGNORECASE)
    # Convert HTML list items <li> to bullets
    cleaned = re.sub(r"<li\s*>(.*?)</li>", r"• \1\n", cleaned, flags=re.IGNORECASE | re.DOTALL)
    # Strip only known HTML tags, preserving generics like List<T>, <int>, or <token>
    cleaned = re.sub(rf"</?(?:{KNOWN_HTML_TAGS})(?:\s+[^>]*)?>", "", cleaned, flags=re.IGNORECASE)
    # Convert prose markdown tables to structured bullets
    cleaned = convert_markdown_tables_to_bullets(cleaned)
    # Convert deep subheadings (#### or #####) into clean bold bullet/prose lines
    cleaned = re.sub(r"(?m)^#{4,6}\s*(.+)$", r"**\1**", cleaned)
    return cleaned


def balance_code_fences(content: str) -> str:
    """Ensure all open code fences (```) in a content block are properly closed."""
    if not content:
        return ""
    fence_count = len(re.findall(r"(?m)^```", content))
    if fence_count % 2 != 0:
        return content.rstrip() + "\n```"
    return content


def sanitize_discord_response_markdown(text: str) -> str:
    """
    Clean up markdown text to render cleanly and safely inside Discord embeds.
    Code blocks (```...```) and inline code (`...`) are preserved byte-for-byte intact.
    """
    if not text:
        return ""

    balanced_text = balance_code_fences(text)
    segments = split_code_and_prose(balanced_text)
    sanitized_parts = []
    for is_code, content in segments:
        if is_code:
            sanitized_parts.append(content)
        else:
            sanitized_parts.append(sanitize_prose_segment(content))

    result = "".join(sanitized_parts)
    # Normalize excessive blank lines
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def format_citations_field(citations: Any) -> Optional[Dict[str, str]]:
    """Format deterministic source citations into a clean grouped Discord embed field."""
    if not citations:
        return None

    grouped: Dict[str, List[Tuple[str, Optional[str]]]] = {}
    total_citations = len(citations)

    for cit in citations[:6]:
        label = (
            (cit.get("label") or cit.get("citation"))
            if isinstance(cit, dict)
            else (getattr(cit, "label", None) or getattr(cit, "citation", None))
        ) or "Source"
        permalink = cit.get("permalink") if isinstance(cit, dict) else getattr(cit, "permalink", None)
        file_path = cit.get("file_path") if isinstance(cit, dict) else getattr(cit, "file_path", "")
        loc = cit.get("location") if isinstance(cit, dict) else getattr(cit, "location", "")

        # Fallback to parse file and location from label if file_path is empty
        if not file_path:
            if "#" in label:
                parts = label.split("#", 1)
                file_path = parts[0].strip()
                if not loc:
                    loc = f"L{parts[1].strip()}" if not parts[1].strip().startswith("L") else parts[1].strip()
            elif ":" in label and not label.startswith("http"):
                parts = label.rsplit(":", 1)
                file_path = parts[0].strip()
                if not loc:
                    loc = parts[1].strip()
            elif " > " in label:
                parts = label.split(" > ", 1)
                file_path = parts[0].strip()
                if not loc:
                    loc = parts[1].strip()
            else:
                file_path = label
                if not loc:
                    loc = "ref"

        if not loc:
            loc = "ref"

        if loc.startswith("LL"):
            loc = loc[1:]

        if file_path not in grouped:
            grouped[file_path] = []
        grouped[file_path].append((loc, permalink))

    if not grouped:
        return None

    lines = []
    for f_path, locs in grouped.items():
        loc_badges = []
        for loc, plink in locs:
            if plink:
                loc_badges.append(f"[{loc}]({plink})")
            else:
                loc_badges.append(f"`{loc}`")
        badge_str = " · ".join(loc_badges)
        lines.append(f"• **`{f_path}`**\n  ↳ {badge_str}")

    title = "📚 Top Grounded Source Citations"
    if total_citations > 6:
        title = f"📚 Top Grounded Source Citations (showing top 6 of {total_citations})"

    return {
        "name": title[:256],
        "value": "\n".join(lines)[:1024],
        "inline": False,
    }


def format_coverage_badge(coverage: Any) -> str:
    """Format footer text for source coverage and grounding guarantee."""
    if not coverage:
        return "Grounded strictly in verified evidence | Zero ghost chunks guarantee"

    status = coverage.get("status") if isinstance(coverage, dict) else getattr(coverage, "status", "COMPLETE")
    ratio = coverage.get("ratio") if isinstance(coverage, dict) else getattr(coverage, "ratio", None)
    target = coverage.get("target_source") if isinstance(coverage, dict) else getattr(coverage, "target_source", None)

    if status == "PARTIAL":
        details = f" ({ratio})" if ratio else ""
        src_info = f" in {target}" if target else ""
        return f"🟡 Partial Coverage{src_info}{details} · Absence from index does not prove absence"
    elif status == "EMPTY":
        return "⚪ No matching evidence found in indexed knowledge"
    else:
        details = f" · {ratio}" if ratio else ""
        return f"🟢 Complete Coverage{details} · Grounded strictly in verified evidence"


def chunk_section_content_safely(text: str, max_chars: int = 950) -> List[str]:
    """
    Chunk section text into sub-blocks <= max_chars without corrupting fenced code blocks.
    If a split must occur inside a code block, safely closes the open fence (```) at the end
    of the chunk and reopens it (```<lang>) at the start of the next chunk.
    """
    if len(text) <= max_chars:
        return [balance_code_fences(text)]

    lines = text.split("\n")
    chunks: List[str] = []
    current_lines: List[str] = []
    current_char_count = 0
    in_code_block = False
    code_lang = ""

    for line in lines:
        line_len = len(line) + 1
        fence_match = re.match(r"^```(\w*)", line.strip())

        # Check if adding this line exceeds the budget
        if current_char_count + line_len > max_chars and current_lines:
            if in_code_block:
                current_lines.append("```")
                chunks.append("\n".join(current_lines))
                current_lines = [f"```{code_lang}"] if code_lang else ["```"]
                current_char_count = len(current_lines[0]) + 1
            else:
                chunks.append("\n".join(current_lines))
                current_lines = []
                current_char_count = 0

        current_lines.append(line)
        current_char_count += line_len

        if fence_match:
            if not in_code_block:
                in_code_block = True
                code_lang = fence_match.group(1)
            else:
                in_code_block = False
                code_lang = ""

    if current_lines:
        if in_code_block:
            current_lines.append("```")
        chunks.append("\n".join(current_lines))

    return [balance_code_fences(c) for c in chunks]


def format_answer_sections(text: str) -> List[Tuple[str, str]]:
    """Parse markdown text into structured (title, content) sections with 1024-char field chunking."""
    if not text:
        return []

    lines = text.split("\n")
    sections: List[Tuple[str, str]] = []
    current_title = ""
    current_lines: List[str] = []
    in_code_block = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            current_lines.append(line)
            continue

        # Header detection only applies OUTSIDE active code blocks!
        header_match = (
            re.match(r"^(?:#{1,6}\s+|\*\*)([^*\n#]+)(?:\*\*|:)?\s*$", stripped)
            if not in_code_block
            else None
        )
        if header_match and len(current_lines) > 0:
            content_str = "\n".join(current_lines).strip()
            if content_str:
                sections.append((current_title, balance_code_fences(content_str)))
            current_title = header_match.group(1).strip()
            current_lines = []
        elif header_match and not current_lines:
            current_title = header_match.group(1).strip()
        else:
            current_lines.append(line)

    if current_lines:
        content_str = "\n".join(current_lines).strip()
        if content_str:
            sections.append((current_title, balance_code_fences(content_str)))

    if not sections and text.strip():
        sections = [("", balance_code_fences(text.strip()))]

    # Chunk any section whose content exceeds 1024 characters safely
    chunked_sections: List[Tuple[str, str]] = []
    for title, content in sections:
        if len(content) <= 1024:
            chunked_sections.append((title, balance_code_fences(content)))
        else:
            sub_chunks = chunk_section_content_safely(content, max_chars=950)
            for idx, sc in enumerate(sub_chunks):
                sub_title = title if idx == 0 else f"{title} (Part {idx+1})"
                chunked_sections.append((sub_title, balance_code_fences(sc[:1024])))

    return chunked_sections


def format_copilot_answer_embeds(answer_data: Any) -> List[discord.Embed]:
    """Format a CopilotAnswer into one or more Discord embeds adhering strictly to all API limits."""
    status = answer_data.get("status") if isinstance(answer_data, dict) else getattr(answer_data, "status", "SUCCESS")
    query = answer_data.get("query") if isinstance(answer_data, dict) else getattr(answer_data, "query", "")
    raw_response = (
        (answer_data.get("answer") or answer_data.get("response", ""))
        if isinstance(answer_data, dict)
        else getattr(answer_data, "answer", getattr(answer_data, "response", ""))
    )
    citations = answer_data.get("citations", []) if isinstance(answer_data, dict) else getattr(answer_data, "citations", [])
    coverage = answer_data.get("coverage") if isinstance(answer_data, dict) else getattr(answer_data, "coverage", None)

    response = sanitize_discord_response_markdown(raw_response)

    if status == "ABSTAINED":
        embed = discord.Embed(
            title="🔍 Evidence Copilot: Abstention Notice",
            description=response[:4096],
            color=discord.Color.dark_grey(),
        )
        embed.set_footer(text=format_coverage_badge(coverage)[:2048])
        return [embed]

    embeds: List[discord.Embed] = []
    title_str = f"🤖 Copilot: {query[:60]}..." if len(query) > 60 else f"🤖 Copilot: {query}"

    # Check if answer fits in a single embed description
    if len(response) <= 2000 and "\n#" not in response:
        embed = discord.Embed(
            title=title_str,
            description=response[:4096],
            color=discord.Color.teal(),
        )
        cit_field = format_citations_field(citations)
        if cit_field:
            embed.add_field(name=cit_field["name"][:256], value=cit_field["value"][:1024], inline=False)
        embed.set_footer(text=format_coverage_badge(coverage)[:2048])
        return [embed]

    # Section-based formatting for rich structured answers
    sections = format_answer_sections(response)
    current_embed = discord.Embed(
        title=title_str,
        color=discord.Color.teal(),
    )
    current_char_count = len(title_str)

    first_title, first_content = sections[0] if sections else ("", "")
    if not first_title and first_content:
        current_embed.description = first_content[:4096]
        current_char_count += len(current_embed.description)
        sections = sections[1:]

    for title, content in sections:
        field_name = title[:256] if title else "Overview"
        field_val = content[:1024] if content else "..."

        if len(current_embed.fields) >= 24 or (current_char_count + len(field_name) + len(field_val)) > 5500:
            embeds.append(current_embed)
            current_embed = discord.Embed(
                title=f"{title_str} (Continued)",
                color=discord.Color.teal(),
            )
            current_char_count = len(current_embed.title or "")

        current_embed.add_field(name=field_name, value=field_val, inline=False)
        current_char_count += len(field_name) + len(field_val)

    # Add citations field
    cit_field = format_citations_field(citations)
    if cit_field:
        if len(current_embed.fields) >= 25 or (current_char_count + len(cit_field["name"]) + len(cit_field["value"])) > 5800:
            embeds.append(current_embed)
            current_embed = discord.Embed(
                title=f"{title_str} (Citations)",
                color=discord.Color.teal(),
            )
        current_embed.add_field(name=cit_field["name"][:256], value=cit_field["value"][:1024], inline=False)

    current_embed.set_footer(text=format_coverage_badge(coverage)[:2048])
    embeds.append(current_embed)
    return embeds


def format_copilot_answer_embed(answer_data: Any) -> discord.Embed:
    """
    Format a CopilotAnswer into a primary Discord embed (returns main embed).

    .. deprecated::
       Prefer `format_copilot_answer_embeds` which returns `List[discord.Embed]`
       to avoid silently truncating long answers that span multiple embeds.
    """
    embeds = format_copilot_answer_embeds(answer_data)
    return embeds[0]


def format_sources_dashboard_embed(sources_summary: List[Dict[str, Any]]) -> discord.Embed:
    """Format the /sources dashboard overview."""
    embed = discord.Embed(
        title="📚 Knowledge & Codebase Sources Dashboard",
        description="Active indexed knowledge repositories, documents, and notes:",
        color=discord.Color.blurple(),
    )

    if not sources_summary:
        embed.description = "No knowledge sources indexed yet. Use `/repo sync` or `/note` to add sources!"
        return embed

    for s in sources_summary:
        s_type = s.get("source_type", "UNKNOWN")
        badge = "🐙" if s_type == "GITHUB" else ("📄" if s_type == "PDF" else ("🌐" if s_type == "WEB" else "📝"))
        status = s.get("status", "COMPLETE")
        status_tag = (
            "🟢 Complete" if status == "COMPLETE"
            else ("🔄 Indexing..." if status == "INDEXING"
            else ("🟡 Partial" if status == "PARTIAL"
            else "🔴 Failed"))
        )
        
        actual_files = s.get("actual_files_count", 0)
        eligible = s.get("eligible_count") or actual_files
        indexed = s.get("indexed_count") if (s.get("indexed_count", 0) > 0 or status == "COMPLETE") else actual_files
        chunks = s.get("total_chunks_count", 0)
        last_sync = s.get("last_sync_at") or s.get("created_at") or "Never"

        details = [
            f"**Status**: {status_tag}",
            f"**Files**: `{indexed} / {eligible}` eligible indexed (`{actual_files}` files active)",
            f"**Chunks**: `{chunks}` semantic vectors",
            f"**Last Sync**: `{last_sync}`",
        ]
        if s.get("last_error"):
            details.append(f"⚠️ **Error**: `{s['last_error'][:60]}`")

        embed.add_field(
            name=f"{badge} {s['name']} (`{s['source_ref']}`)",
            value="\n".join(details),
            inline=False,
        )

    embed.set_footer(text="Manifest-based incremental sync | Zero ghost chunks guarantee")
    return embed


class RawEvidenceModal(discord.ui.Modal, title="📄 Verbatim Evidence Excerpt"):
    evidence_text = discord.ui.TextInput(
        label="Raw Source Text",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=4000,
    )

    def __init__(self, raw_content: str, citation_label: str):
        super().__init__(title=f"📄 {citation_label[:40]}")
        self.evidence_text.default = raw_content[:4000]

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)


class CopilotAnswerView(discord.ui.View):
    """Stateless persistent view for Copilot answers that survives bot restarts."""

    def __init__(self, answer_id: Optional[int] = None, db_manager: Any = None):
        super().__init__(timeout=None)
        self.answer_id = answer_id
        self.db = db_manager

        self.clear_items()
        btn_cid = f"perlica:copilot:raw:{answer_id}" if answer_id else "perlica:copilot:raw:none"
        btn = discord.ui.Button(
            label="📄 View Raw Source",
            style=discord.ButtonStyle.secondary,
            emoji="🔍",
            custom_id=btn_cid,
        )
        btn.callback = self.view_raw_source_button
        self.add_item(btn)

    async def view_raw_source_button(self, interaction: discord.Interaction):
        if not self.answer_id:
            await interaction.response.send_message("No evidence snapshots recorded for this answer.", ephemeral=True)
            return

        if self.db:
            snapshots = await self.db.get_answer_evidence_snapshots(self.answer_id)
            if snapshots:
                first_snap = snapshots[0]
                modal = RawEvidenceModal(
                    raw_content=first_snap.get("raw_text", "No raw content found."),
                    citation_label=first_snap.get("citation", "Evidence Source"),
                )
                await interaction.response.send_modal(modal)
                return
        await interaction.response.send_message("Evidence snapshots could not be found.", ephemeral=True)


class SourcesDashboardView(discord.ui.View):
    """Stateless persistent view for the Knowledge Base /sources dashboard with 1-tap live refresh."""

    def __init__(self, db_manager: Any = None):
        super().__init__(timeout=None)
        self.db = db_manager

    @discord.ui.button(label="🔄 Refresh Dashboard", style=discord.ButtonStyle.secondary, custom_id="perlica:sources:refresh")
    async def on_refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.db:
            sources_summary = await self.db.get_knowledge_sources_summary()
            embed = format_sources_dashboard_embed(sources_summary)
            await interaction.response.edit_message(embed=embed, view=self)


def format_ingest_hub_embed() -> discord.Embed:
    """Format the interactive Knowledge Base Ingestion Session Hub embed."""
    embed = discord.Embed(
        title="📚 Knowledge Base Ingestion Hub",
        description=(
            "Welcome to the **Copilot Knowledge Ingestion Hub**!\n"
            "Select a source type below to index new content into your private search engine:"
        ),
        color=discord.Color.blue(),
    )
    embed.add_field(
        name="📄 PDF Document",
        value="Click **Upload PDF** to start a 60s session, then drag-and-drop your PDF into chat.",
        inline=False,
    )
    embed.add_field(
        name="🌐 Web Page URL",
        value="Click **Ingest Web URL** to enter any documentation link or online article.",
        inline=False,
    )
    embed.add_field(
        name="🐙 GitHub Repository",
        value="Click **Sync GitHub Repo** for AST-level code indexing and daily auto-sync.",
        inline=False,
    )
    embed.add_field(
        name="📝 Quick Knowledge Note",
        value="Click **Add Quick Note** to save instant snippets, guidelines, or instructions.",
        inline=False,
    )
    embed.set_footer(text="All indexed sources are searchable via ? <query> or /ask")
    return embed

