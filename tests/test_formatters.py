import discord
from src.extractor import ExtractedPayload, QueryScope
from src.formatters import (
    render_progress_bar,
    format_action_preview,
    format_action_confirmation,
    format_daily_summary,
    format_morning_briefing,
    format_full_snapshot_summary,
    format_budget_overview,
    format_query_results,
    format_help_guide,
)


def test_render_progress_bar():
    bar1 = render_progress_bar(50.0, 100.0)
    assert "50.0%" in bar1
    assert "RM 50.00 / RM 100.00" in bar1

    bar_warn = render_progress_bar(85.0, 100.0)
    assert "⚠️" in bar_warn

    bar_over = render_progress_bar(120.0, 100.0)
    assert "OVERSPENT" in bar_over


def test_format_morning_briefing():
    open_tasks = [
        {"id": 1, "description": "High priority task", "priority": "HIGH", "due_date": "2026-08-25"},
        {"id": 2, "description": "Regular task", "priority": "MEDIUM"},
    ]
    due_bills = [
        {"name": "Unifi", "amount": 139.0, "category": "Utilities & Bills"}
    ]
    budget_status = [
        {"category": "Food & Dining", "spent": 400.0, "limit": 500.0, "percentage": 80.0, "remaining": 100.0}
    ]

    embed = format_morning_briefing(open_tasks, due_bills, budget_status, "2026-08-25")
    assert isinstance(embed, discord.Embed)
    assert "Morning Briefing" in embed.title
    field_names = [f.name for f in embed.fields]
    assert any("Tasks to Tackle" in name for name in field_names)
    assert any("Recurring Bills Due" in name for name in field_names)
    assert any("Monthly Budget Overview" in name for name in field_names)


def test_format_budget_overview():
    budget_status = [
        {"category": "Food & Dining", "spent": 400.0, "limit": 500.0, "percentage": 80.0, "remaining": 100.0}
    ]
    embed = format_budget_overview(budget_status)
    assert isinstance(embed, discord.Embed)
    assert "Monthly Budget Overview" in embed.title
    assert "Food & Dining" in embed.description


def test_format_action_preview():
    payload = ExtractedPayload()
    expenses = [{"amount": 15.50, "category": "Food & Dining", "note": "Chicken rice"}]
    tasks = [{"description": "Launch App", "priority": "HIGH", "due_date": "2026-08-30", "due_time": "17:00", "phases": ["Phase 1", "Phase 2"]}]
    completed = [1]

    embed = format_action_preview(payload, expenses, tasks, completed)
    assert isinstance(embed, discord.Embed)
    assert "Action Ingestion Preview" in embed.title


def test_format_action_confirmation():
    payload = ExtractedPayload(ambiguous_task_note="Ambiguity note test")
    inserted_expenses = [
        {"id": 1, "amount": 15.50, "category": "Food & Dining", "note": "Chicken rice"}
    ]
    inserted_tasks = [
        {"id": 1, "description": "Call client", "priority": "HIGH", "due_date": "2026-08-25", "due_time": "17:00"}
    ]
    completed_tasks = [
        {"id": 2, "description": "Buy groceries"}
    ]

    embed = format_action_confirmation(
        payload=payload,
        inserted_expenses=inserted_expenses,
        inserted_tasks=inserted_tasks,
        completed_tasks=completed_tasks,
        budget_alerts=["⚠️ Food is at 85% of limit!"],
    )
    assert isinstance(embed, discord.Embed)
    assert embed.title == "⚡ Action Processed"
    field_names = [f.name for f in embed.fields]
    assert any("Budget Alert" in name for name in field_names)


def test_sanitize_discord_response_markdown_and_copilot_embed():
    from src.formatters import sanitize_discord_response_markdown, format_copilot_answer_embed

    raw_table_and_html = (
        "Here is the breakdown:<br><br>"
        "| Step | Action | Details |<br>"
        "|---|---|---|<br>"
        "| 1. Ingestion | Tree crawler | Fetches Git blobs |<br>"
        "| 2. Vectorization | FastEmbed | Local ONNX vectors |<br>"
        "<b>Summary complete.</b>"
    )
    cleaned = sanitize_discord_response_markdown(raw_table_and_html)
    assert "<br>" not in cleaned
    assert "<b>" not in cleaned
    assert "• **1. Ingestion** (Action: Tree crawler, Details: Fetches Git blobs)" in cleaned
    assert "• **2. Vectorization** (Action: FastEmbed, Details: Local ONNX vectors)" in cleaned

    answer_data = {
        "status": "SUCCESS",
        "query": "How does ingestion work?",
        "response": raw_table_and_html,
        "citations": [{"citation": "src/github_sync.py:L10-L20", "permalink": "https://github.com/..."}],
    }
    embed = format_copilot_answer_embed(answer_data)
    assert isinstance(embed, discord.Embed)
    assert "<br>" not in embed.description
    assert len(embed.fields) == 1
    assert "Grounded Source Citations" in embed.fields[0].name


def test_copilot_answer_dataclasses_and_coverage_rendering():
    from src.rag_engine import CopilotAnswer, CopilotCitation, CopilotCoverage
    from src.formatters import format_copilot_answer_embed, format_copilot_answer_embeds

    citations = [
        CopilotCitation(
            label="src/github_sync.py:L35-L57",
            permalink="https://github.com/DanielKoh2004/perlica/blob/abc/src/github_sync.py#L35-L57",
            source_name="DanielKoh2004/perlica",
            source_type="GITHUB",
            location="L35-L57",
            chunk_id=101,
        )
    ]
    coverage = CopilotCoverage(
        status="COMPLETE",
        eligible_count=11,
        indexed_count=11,
        failed_count=0,
        ratio="11 / 11 files indexed",
        target_source="DanielKoh2004/perlica",
    )
    answer = CopilotAnswer(
        answer="This is a clean grounded technical response.",
        query="How does reconciliation work?",
        citations=citations,
        evidence_ids=[101],
        coverage=coverage,
        answer_id=42,
        status="SUCCESS",
    )

    embeds = format_copilot_answer_embeds(answer)
    assert len(embeds) == 1
    emb = embeds[0]
    assert emb.title == "🤖 Copilot: How does reconciliation work?"
    assert "This is a clean grounded technical response." in emb.description
    assert "🟢 Complete Coverage" in emb.footer.text
    assert "11 / 11 files indexed" in emb.footer.text

    # Verify field formatting
    assert len(emb.fields) == 1
    assert "Grounded Source Citations" in emb.fields[0].name
    assert "• **`src/github_sync.py`**" in emb.fields[0].value
    assert "[L35-L57](https://github.com/DanielKoh2004/perlica/blob/abc/src/github_sync.py#L35-L57)" in emb.fields[0].value


def test_long_answer_section_splitting_and_1024_char_limit():
    from src.rag_engine import CopilotAnswer, CopilotCoverage
    from src.formatters import format_copilot_answer_embeds

    # Generate a massive section exceeding 1024 characters
    giant_content = "This is detailed line explanation. " * 45  # ~1575 chars
    raw_markdown = (
        "## Architecture Overview\n"
        "Here is the high level intro.\n\n"
        "## Ingestion Engine\n"
        f"{giant_content}\n\n"
        "## Vector Search\n"
        "Vector search uses FastEmbed ONNX."
    )

    answer = CopilotAnswer(
        answer=raw_markdown,
        query="Explain architecture in depth",
        coverage=CopilotCoverage(status="COMPLETE"),
    )

    embeds = format_copilot_answer_embeds(answer)
    assert len(embeds) >= 1
    # Verify no field exceeds 1024 characters
    for emb in embeds:
        for f in emb.fields:
            assert len(f.name) <= 256
            assert len(f.value) <= 1024


def test_multi_embed_overflow_protection_over_25_fields():
    from src.rag_engine import CopilotAnswer, CopilotCoverage
    from src.formatters import format_copilot_answer_embeds

    # Build 30 distinct markdown sections
    sections = [f"## Section {i}\nContent for section number {i}." for i in range(1, 31)]
    raw_markdown = "\n\n".join(sections)

    answer = CopilotAnswer(
        answer=raw_markdown,
        query="List all 30 features",
        coverage=CopilotCoverage(status="COMPLETE"),
    )

    embeds = format_copilot_answer_embeds(answer)
    assert len(embeds) >= 2  # Must split across embeds because max 25 fields per embed
    for emb in embeds:
        assert len(emb.fields) <= 25


def test_hostile_html_and_script_sanitization():
    from src.formatters import sanitize_discord_response_markdown

    hostile_input = (
        "<script>alert('pwned');</script>"
        "<iframe>evil.com</iframe>"
        "<style>body { display: none; }</style>"
        "<b>Important Note:</b><br>"
        "<p>Safe explanation of <a href='https://example.com'>link</a></p>"
    )
    cleaned = sanitize_discord_response_markdown(hostile_input)
    assert "alert" not in cleaned
    assert "script" not in cleaned
    assert "iframe" not in cleaned
    assert "style" not in cleaned
    assert "<p>" not in cleaned
    assert "<b>" not in cleaned
    assert "Important Note:" in cleaned


def test_coverage_badge_complete_partial_empty():
    from src.rag_engine import CopilotCoverage
    from src.formatters import format_coverage_badge

    cov_complete = CopilotCoverage(status="COMPLETE", ratio="11 / 11 files indexed")
    assert "🟢 Complete Coverage" in format_coverage_badge(cov_complete)

    cov_partial = CopilotCoverage(
        status="PARTIAL",
        ratio="243 / 247 files indexed",
        target_source="DanielKoh2004/perlica",
    )
    assert "🟡 Partial Coverage" in format_coverage_badge(cov_partial)
    assert "DanielKoh2004/perlica" in format_coverage_badge(cov_partial)

    cov_empty = CopilotCoverage(status="EMPTY")
    assert "⚪ No matching evidence" in format_coverage_badge(cov_empty)


def test_abstained_state_rendering():
    from src.rag_engine import CopilotAnswer, CopilotCoverage
    from src.formatters import format_copilot_answer_embeds

    answer = CopilotAnswer(
        answer="⚠️ No relevant evidence found in indexed sources.",
        query="How many quantum computers do we have?",
        coverage=CopilotCoverage(status="EMPTY"),
        status="ABSTAINED",
    )

    embeds = format_copilot_answer_embeds(answer)
    assert len(embeds) == 1
    emb = embeds[0]
    assert "Abstention Notice" in emb.title
    assert "No relevant evidence found" in emb.description
    assert "⚪ No matching evidence" in emb.footer.text


def test_table_conversion_does_not_modify_fenced_code_blocks():
    from src.formatters import sanitize_discord_response_markdown

    input_markdown = (
        "Here is the comparison:\n\n"
        "| Feature | Status |\n"
        "|---|---|\n"
        "| Hybrid RAG | Enabled |\n\n"
        "And here is the Python source code:\n\n"
        "```python\n"
        "# ASCII diagram inside code block must NOT be modified!\n"
        "headers = ['|', '|']\n"
        "table_mock = '''\n"
        "|---|---|\n"
        "| A | B |\n"
        "'''\n"
        "```\n\n"
        "Also test inline `| a | b |` snippet."
    )

    cleaned = sanitize_discord_response_markdown(input_markdown)

    # Invariant 1: Prose table is converted
    assert "• **Hybrid RAG** (Status: Enabled)" in cleaned

    # Invariant 2: Fenced code block content is preserved 100% byte-for-byte!
    assert "```python\n# ASCII diagram inside code block must NOT be modified!\nheaders = ['|', '|']\ntable_mock = '''\n|---|---|\n| A | B |\n'''\n```" in cleaned

    # Invariant 3: Inline code is preserved
    assert "`| a | b |`" in cleaned


def test_html_sanitizer_preserves_technical_generics_and_angle_brackets():
    from src.formatters import sanitize_discord_response_markdown

    technical_text = (
        "Perlica supports List<T>, Map<K, V>, and std::vector<int>.<br>"
        "Tokens such as <token_id> and <BEGIN UNTRUSTED EVIDENCE> should not be stripped if used in technical explanations.<br>"
        "<b>Important:</b> <script>alert(1)</script> Only actual HTML tags get stripped."
    )

    cleaned = sanitize_discord_response_markdown(technical_text)

    # Generics and technical angle brackets in prose are preserved
    assert "List<T>" in cleaned
    assert "Map<K, V>" in cleaned
    assert "std::vector<int>" in cleaned
    assert "<token_id>" in cleaned

    # HTML tags are stripped/converted
    assert "<br>" not in cleaned
    assert "<b>" not in cleaned
    assert "<script>" not in cleaned
    assert "alert" not in cleaned


def test_top_citations_header_label_when_more_than_six_citations():
    from src.rag_engine import CopilotAnswer, CopilotCitation, CopilotCoverage
    from src.formatters import format_copilot_answer_embeds

    # Generate 8 citations
    citations = [
        CopilotCitation(
            label=f"src/file_{i}.py:L1-L10",
            permalink=f"https://github.com/DanielKoh2004/perlica/blob/main/src/file_{i}.py",
            source_name="DanielKoh2004/perlica",
            source_type="GITHUB",
            location="L1-L10",
            chunk_id=i,
        )
        for i in range(1, 9)
    ]

    answer = CopilotAnswer(
        answer="Overview of multiple components across files.",
        query="Where are components defined?",
        citations=citations,
        evidence_ids=list(range(1, 9)),
        coverage=CopilotCoverage(status="COMPLETE"),
        status="SUCCESS",
    )

    embeds = format_copilot_answer_embeds(answer)
    assert len(embeds) == 1
    emb = embeds[0]
    cit_field = [f for f in emb.fields if "Citations" in f.name][0]
    assert "Top Grounded Source Citations (showing top 6 of 8)" in cit_field.name


def test_code_block_safe_section_chunking_preserves_syntax_fences():
    from src.formatters import chunk_section_content_safely, format_answer_sections

    # Build a section with a large code block that exceeds 1000 characters
    long_code_lines = [f"    line_{i} = calculate_value({i})  # step logic {i}" for i in range(1, 40)]
    long_code = "\n".join(long_code_lines)

    raw_section = f"Intro explanation of the logic:\n```python\n{long_code}\n```\nConcluding summary."

    chunks = chunk_section_content_safely(raw_section, max_chars=950)
    assert len(chunks) >= 2

    # Invariant: Chunk 1 MUST end with closing ```
    assert chunks[0].endswith("```")
    # Invariant: Chunk 2 MUST start with ```python
    assert chunks[1].startswith("```python")

    # Invariant: All chunks must be <= 1024 chars
    for c in chunks:
        assert len(c) <= 1024


def test_grouped_citations_field_by_source_file():
    """Verify format_citations_field groups multiple chunks from the same file into unified headers."""
    from src.rag_engine import CopilotCitation
    from src.formatters import format_citations_field

    citations = [
        CopilotCitation(
            label="src/database.py#1021-1061",
            permalink="https://github.com/DanielKoh2004/perlica/blob/main/src/database.py#L1021-L1061",
            file_path="src/database.py",
            location="L1021-1061",
            chunk_id=1,
        ),
        CopilotCitation(
            label="src/database.py#971-1019",
            permalink="https://github.com/DanielKoh2004/perlica/blob/main/src/database.py#L971-L1019",
            file_path="src/database.py",
            location="L971-1019",
            chunk_id=2,
        ),
        CopilotCitation(
            label="src/formatters.py#515-598",
            permalink="https://github.com/DanielKoh2004/perlica/blob/main/src/formatters.py#L515-L598",
            file_path="src/formatters.py",
            location="L515-598",
            chunk_id=3,
        ),
    ]

    field = format_citations_field(citations)
    assert field is not None
    assert "Top Grounded Source Citations" in field["name"]
    # Verify file grouping: src/database.py appears once as bold header
    val = field["value"]
    assert "• **`src/database.py`**" in val
    assert "[L1021-1061](https://github.com/DanielKoh2004/perlica/blob/main/src/database.py#L1021-L1061)" in val
    assert "[L971-1019](https://github.com/DanielKoh2004/perlica/blob/main/src/database.py#L971-L1019)" in val
    assert "• **`src/formatters.py`**" in val


def test_code_block_fences_never_leak_prose_into_code():
    """Verify that unclosed code blocks are automatically balanced and never leak prose into code."""
    from src.formatters import format_answer_sections, sanitize_discord_response_markdown

    # LLM produces an unclosed code block followed by a header and prose
    raw_text = (
        "## Section 1\n"
        "Here is the handler:\n"
        "```python\n"
        "async def on_message(message: discord.Message):\n"
        "# reply with embeds and interactive views\n"
        "    await message.channel.send('hello')\n"
        "```\n\n"
        "## Section 2\n"
        "This is standard prose that should NOT be in a code block."
    )

    sections = format_answer_sections(raw_text)
    assert len(sections) == 2
    assert sections[0][0] == "Section 1"
    assert sections[1][0] == "Section 2"

    # Invariant: Section 2 content must not start or end with a stray backtick fence
    assert "```" not in sections[1][1]
    assert "This is standard prose" in sections[1][1]


def test_python_comments_inside_code_are_not_split_as_headers():
    """Verify Python comments starting with # inside code blocks do not trigger section headers."""
    from src.formatters import format_answer_sections

    raw_text = (
        "## Overview\n"
        "```python\n"
        "# 1. Compute total\n"
        "total = sum(items)\n"
        "# 2. Log result\n"
        "logger.info(total)\n"
        "```\n"
        "All done."
    )

    sections = format_answer_sections(raw_text)
    # Only 1 section named Overview, comments must not be split out as separate sections
    assert len(sections) == 1
    assert sections[0][0] == "Overview"
    assert "# 1. Compute total" in sections[0][1]
    assert "# 2. Log result" in sections[0][1]
