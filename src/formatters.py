import discord
from typing import List, Dict, Any, Optional
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
) -> discord.Embed:
    """Build a rich preview embed for user review before committing to database."""
    embed = discord.Embed(
        title="📋 Action Ingestion Preview",
        description="Please review what will be recorded. Click **Confirm** to save, **Edit** to modify, or **Reject** to discard.",
        color=discord.Color.gold(),
    )

    # 1. Expenses Preview
    if expenses:
        total = sum(e["amount"] for e in expenses)
        lines = []
        for exp in expenses:
            note_str = f" ({exp['note']})" if exp.get("note") else ""
            date_str = f" `[Date: {exp['occurred_date']}]`" if exp.get("occurred_date") else ""
            lines.append(f"• **RM {exp['amount']:.2f}** — `{exp['category']}`{note_str}{date_str}")
        embed.add_field(
            name=f"💸 Expenses to Log (Total: RM {total:.2f})",
            value="\n".join(lines),
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
) -> discord.Embed:
    """Build a rich confirmation embed for logged actions with hierarchy, budget alerts, and streak badge."""
    is_breach = bool(budget_alerts and any("exceeded" in a.lower() or "🚨" in a for a in budget_alerts))
    color = discord.Color.red() if is_breach else discord.Color.green()

    embed = discord.Embed(
        title="🚨 Budget Breach Alert" if is_breach else "⚡ Action Processed",
        color=color,
    )

    # 1. Logged Expenses
    if inserted_expenses:
        total = sum(e["amount"] for e in inserted_expenses)
        lines = []
        for exp in inserted_expenses:
            note_str = f" ({exp['note']})" if exp.get("note") else ""
            date_str = f" `[Date: {exp['created_at'][:10]}]`" if exp.get("created_at") else ""
            lines.append(f"• **RM {exp['amount']:.2f}** — `{exp['category']}`{note_str}{date_str}")
        embed.add_field(
            name=f"💸 Logged Expenses (Total: RM {total:.2f})",
            value="\n".join(lines),
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
) -> discord.Embed:
    """Build the single-pane-of-glass Live Dashboard with 1-tap in-place refresh."""
    rank_badge = f" | {rank_info['title']}" if rank_info else ""
    embed = discord.Embed(
        title=f"📌 Live Command Center — {date_str}{rank_badge}",
        description="Your real-time financial, budget, and productivity status. Click **[🔄 Refresh]** anytime to update.",
        color=discord.Color.dark_teal(),
    )

    # 1. Spending & 7-Day Sparkline
    sparkline = render_sparkline(pace_data.get("daily_series", []))
    avg = pace_data.get("seven_day_avg", 0.0)
    diff = pace_data.get("diff_pct", 0.0)
    sign = "+" if diff > 0 else ""
    indicator = "🔴" if diff > 15.0 else ("🟢" if diff < -15.0 else "🟡")
    embed.add_field(
        name=f"💸 Today's Spending: RM {today_spent:.2f}",
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

    # 3. Savings Goals
    if active_goals:
        g_lines = []
        for g in active_goals[:3]:
            bar = render_progress_bar(g["current_amount"], g["target_amount"])
            g_lines.append(f"• **{g['name']}:** {bar}")
        embed.add_field(name="🎯 Savings Goals", value="\n".join(g_lines), inline=False)

    # 4. Monthly Budgets
    if budget_status:
        b_lines = []
        for b in budget_status[:4]:
            bar = render_progress_bar(b["spent"], b["limit"])
            b_lines.append(f"• **{b['category']}:** {bar}")
        embed.add_field(name="📊 Budget Health", value="\n".join(b_lines), inline=False)

    # 5. Top Priority Tasks
    if open_tasks:
        lines = []
        for t in open_tasks[:5]:
            due = f" _(Due: {t['due_date']})_" if t.get("due_date") else ""
            lines.append(f"• `[#{t['id']} | {t['priority']}]` {t['description']}{due}")
        embed.add_field(name=f"📋 Focus Tasks ({len(open_tasks)} total)", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="📋 Tasks", value="🎉 All tasks completed!", inline=False)

    # 6. Bills (Due Today or Next 3 Days)
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
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
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
            font-size: 26px;
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
                <div>Total Spent</div>
                <div class="card-val">RM {total_spent:.2f}</div>
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

        <h2>💸 Spending by Category</h2>
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
        title="📖 Perlica Personal Agent — Quick Start Guide",
        description="Just send natural sentences, voice notes, receipt photos, or use `/` slash commands!",
        color=discord.Color.teal(),
    )

    embed.add_field(
        name="🎯 Savings Goals (Never deducted by expenses)",
        value=(
            "• `Create goal Japan Trip target RM 6000`\n"
            "• `Saved RM 500 for Japan Trip`\n"
            "• `/goals` or `goals` *(View goal progress meters)*"
        ),
        inline=False,
    )

    embed.add_field(
        name="⚡ Interactive Controls & Modals",
        value=(
            "• 📌 `dashboard` or `/dashboard` *(Pinned live command center)*\n"
            "• ✏️ Click **[Edit]** on any preview for a **Popup Edit Modal**\n"
            "• ⚡ `presets` or `/presets` *(1-tap common expenses)*\n"
            "• 📅 `calendar` *(7-Day Day Inspector)*\n"
            "• 🔍 `find <keyword>` *(Search expenses and tasks)*\n"
            "• 📊 `/report` *(Download HTML Financial Report)*"
        ),
        inline=False,
    )

    embed.add_field(
        name="💸 Logging Expenses & Receipts",
        value=(
            "• `RM 15.50 chicken rice for lunch`\n"
            "• `Reload TNG RM 50` / `99 Speedmart RM 28`\n"
            "• `recurring buy $100 s&p500 on 27th`\n"
            "• 🎙️ *Send a voice note while driving!*\n"
            "• 📸 *Send a photo of a receipt!*"
        ),
        inline=False,
    )

    embed.add_field(
        name="📊 Budget Limits & Safe Allowance",
        value=(
            "• `Set monthly food budget to RM 800`\n"
            "• `Check my budget status`\n"
            "• ☀️ *Morning Briefing calculates your Safe-to-Spend runway!*"
        ),
        inline=False,
    )

    embed.add_field(
        name="📝 Multi-Phase Tasks & Dropdowns",
        value=(
            "• `Create task 'App Launch' with 3 phases: 1. Wireframes, 2. Design, 3. Testing`\n"
            "• `What are my open tasks?` *(Native 1-tap select dropdown)*\n"
            "• `Done task #1`"
        ),
        inline=False,
    )

    return embed
