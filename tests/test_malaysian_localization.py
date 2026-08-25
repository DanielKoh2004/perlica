import datetime
import pytest
import pytest_asyncio
from src.database import (
    DatabaseManager,
    classify_fuel_expense,
    calculate_fuel_details,
)
from src.formatters import (
    get_upcoming_malaysian_holidays,
    format_fuel_receipt_embed,
)
from src.extractor import build_system_prompt


@pytest_asyncio.fixture
async def db(tmp_path):
    db_file = str(tmp_path / "test_malaysian_loc.db")
    manager = DatabaseManager(db_file)
    await manager.init_db()
    return manager


def test_minyak_masak_and_shell_out_exclusions():
    """
    CRITICAL TEST: Verify that cooking oil (minyak masak) and restaurants (Shell Out)
    are NEVER classified as vehicle fuel, preventing false subsidy deductions.
    """
    # Negative cases
    assert classify_fuel_expense("Groceries", "Beli minyak masak kat Speedmart RM 15") is None
    assert classify_fuel_expense("Food & Dining", "Shell Out dinner RM 120") is None
    assert classify_fuel_expense("Groceries", "minyak kelapa sawit 5kg") is None
    assert classify_fuel_expense("Food & Dining", "Mee Goreng Mamak") is None
    assert classify_fuel_expense("Transport", None) is None

    # Positive vehicle fuel cases
    ron95_info = classify_fuel_expense("Transport", "Isi minyak petronas RM 50")
    assert ron95_info is not None
    assert ron95_info["grade"] == "RON95"
    assert ron95_info["consumes_subsidy"] is True


def test_ron97_and_diesel_grade_isolation():
    """
    CRITICAL TEST: Verify that RON97 and Diesel calculate market rate liters
    and DO NOT touch or deduct from the 200L RON95 subsidized quota.
    """
    # 1. RON97 Check (~RM 3.47/L)
    ron97_info = classify_fuel_expense("Transport", "Pump RON97 at Shell RM 100")
    assert ron97_info is not None
    assert ron97_info["grade"] == "RON97"
    assert ron97_info["consumes_subsidy"] is False

    calc_97 = calculate_fuel_details(100.0, ron97_info, prior_ron95_liters=50.0)
    assert calc_97["liters_added"] == round(100.0 / 3.47, 2)  # ~28.82L
    assert calc_97["consumes_subsidy"] is False
    assert calc_97["new_total_ron95_liters"] == 50.0  # UNTOUCHED!
    assert calc_97["ron95_quota_remaining"] == 150.0

    # 2. Diesel Check (~RM 3.35/L)
    diesel_info = classify_fuel_expense("Transport", "Euro 5 Diesel Petronas RM 80")
    assert diesel_info is not None
    assert diesel_info["grade"] == "Diesel"
    assert diesel_info["consumes_subsidy"] is False

    calc_diesel = calculate_fuel_details(80.0, diesel_info, prior_ron95_liters=50.0)
    assert calc_diesel["liters_added"] == round(80.0 / 3.35, 2)  # ~23.88L
    assert calc_diesel["new_total_ron95_liters"] == 50.0  # UNTOUCHED!


def test_ron95_two_tier_subsidy_boundary_math():
    """
    CRITICAL TEST: Verify exact two-tier calculations:
    1. Subsidized Tier (First 200L @ RM 1.99/L)
    2. Split Boundary (190L prior -> part @ 1.99, part @ 2.60)
    3. Unsubsidized Tier (>200L @ RM 2.60/L)
    """
    ron95_info = {"grade": "RON95", "price_per_liter": 1.99, "consumes_subsidy": True}

    # Case 1: RM 50 from 0L prior
    c1 = calculate_fuel_details(50.0, ron95_info, prior_ron95_liters=0.0)
    assert c1["liters_added"] == round(50.0 / 1.99, 2)  # 25.13L
    assert c1["new_total_ron95_liters"] == 25.13
    assert c1["ron95_quota_remaining"] == 174.87
    assert "Subsidized Tier" in c1["tier_label"]

    # Case 2: RM 50 from 190.0L prior (Split Boundary)
    # Remaining sub = 10L (cost: 10 * 1.99 = RM 19.90)
    # Remaining spend = 50 - 19.90 = RM 30.10 (unsub: 30.10 / 2.60 = 11.58L)
    # Total liters = 10 + 11.58 = 21.58L
    c2 = calculate_fuel_details(50.0, ron95_info, prior_ron95_liters=190.0)
    assert c2["liters_added"] == 21.58
    assert c2["new_total_ron95_liters"] == 211.58
    assert c2["ron95_quota_remaining"] == 0.0
    assert "Split:" in c2["tier_label"]

    # Case 3: RM 50 from 210.0L prior (Fully Unsubsidized @ RM 2.60/L)
    c3 = calculate_fuel_details(50.0, ron95_info, prior_ron95_liters=210.0)
    assert c3["liters_added"] == round(50.0 / 2.60, 2)  # 19.23L
    assert c3["new_total_ron95_liters"] == 229.23
    assert c3["ron95_quota_remaining"] == 0.0
    assert "Unsubsidized Tier" in c3["tier_label"]


@pytest.mark.asyncio
async def test_database_monthly_ron95_accumulator(db: DatabaseManager):
    """Verify that get_monthly_ron95_liters accumulates only RON95 expenses."""
    # 1. Insert RON95 RM 50
    await db.insert_expense(50.0, "Transport", "Petronas Primax 95", "2026-08-05 10:00:00")
    # 2. Insert RON97 RM 100 (Should NOT count towards RON95 liters)
    await db.insert_expense(100.0, "Transport", "Shell V-Power RON97", "2026-08-08 10:00:00")
    # 3. Insert Cooking Oil RM 20 (Should NOT count)
    await db.insert_expense(20.0, "Groceries", "Minyak masak Seri Murni", "2026-08-10 10:00:00")
    # 4. Insert another RON95 RM 50
    await db.insert_expense(50.0, "Transport", "Caltex Techron RON95", "2026-08-15 10:00:00")

    total_ron95_liters = await db.get_monthly_ron95_liters("2026-08")
    expected_liters = round(round(50.0 / 1.99, 2) + round(50.0 / 1.99, 2), 2)  # 25.13 + 25.13 = 50.26L
    assert total_ron95_liters == expected_liters


def test_selangor_state_and_federal_public_holidays():
    """
    CRITICAL TEST: Verify that holidays.Malaysia(subdiv='SGR') captures
    both Federal and Selangor state holidays (Thaipusam, Nuzul Al-Quran, Sultan's Birthday).
    """
    import holidays
    sgr_holidays = holidays.Malaysia(years=2026, subdiv="SGR")
    holiday_names = list(sgr_holidays.values())

    # Check Selangor specific state holidays
    assert any("Thaipusam" in name for name in holiday_names), "Thaipusam missing for Selangor"
    assert any("Nuzul Al-Quran" in name for name in holiday_names), "Nuzul Al-Quran missing for Selangor"
    assert any("Sultan" in name for name in holiday_names), "Sultan of Selangor's Birthday missing"

    # Check Federal holidays
    assert any("Tahun Baharu Cina" in name for name in holiday_names), "CNY missing"
    assert any("Hari Raya" in name for name in holiday_names), "Hari Raya missing"
    assert any("Hari Kebangsaan" in name for name in holiday_names), "Merdeka Day missing"

    # Test get_upcoming_malaysian_holidays near Merdeka Day 2026 (2026-08-25)
    base_date = datetime.date(2026, 8, 25)
    upcoming = get_upcoming_malaysian_holidays(base_date, days_ahead=10, subdiv="SGR")
    assert len(upcoming) >= 1
    merdeka = next(h for h in upcoming if "Kebangsaan" in h["name"] or "Merdeka" in h["name"])
    assert merdeka["date"] == "2026-08-31"
    assert merdeka["days_away"] == 6
    assert merdeka["is_long_weekend"] is True  # 31 Aug 2026 is Monday!


def test_manglish_system_prompt_rules():
    """Verify system prompt contains Malaysian vernacular parsing rules."""
    now_dt = datetime.datetime(2026, 8, 25, 10, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
    prompt = build_system_prompt(
        open_tasks=[],
        recurring_bills=[],
        active_goals=[],
        now_local=now_dt,
    )
    assert "MALAYSIAN VERNACULAR" in prompt
    assert "tapau" in prompt
    assert "isi minyak" in prompt
    assert "bil tnb" in prompt
    assert "semalam" in prompt
    assert "kelmarin" in prompt


def test_upcoming_holidays_embed_formatter():
    """Verify format_upcoming_holidays_embed outputs beautiful cards."""
    from src.formatters import format_upcoming_holidays_embed
    holidays_data = [
        {"name": "Hari Kebangsaan", "date": "2026-08-31", "days_away": 6, "is_long_weekend": True, "day_name": "Monday"},
        {"name": "Hari Malaysia", "date": "2026-09-16", "days_away": 22, "is_long_weekend": False, "day_name": "Wednesday"},
    ]
    embed = format_upcoming_holidays_embed(holidays_data, "2026-08-25")
    assert "Malaysian Public Holidays" in embed.title
    assert "Hari Kebangsaan" in embed.fields[0].value
    assert "3-Day Long Weekend!" in embed.fields[0].value
