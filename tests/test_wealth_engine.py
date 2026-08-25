import datetime
import pytest
import pytest_asyncio
from src.database import DatabaseManager, normalize_canonical_asset
from src.formatters import format_investments_overview


@pytest_asyncio.fixture
async def db(tmp_path):
    db_file = str(tmp_path / "test_wealth_tracker.db")
    manager = DatabaseManager(db_file)
    await manager.init_db()
    return manager


def test_canonical_asset_normalization_resolutions():
    """Verify raw strings, synonyms, and tickers resolve deterministically to canonical names."""
    # US Equities
    assert normalize_canonical_asset("VOO") == ("S&P 500", "US Equities")
    assert normalize_canonical_asset("SPY") == ("S&P 500", "US Equities")
    assert normalize_canonical_asset("S&P 500") == ("S&P 500", "US Equities")
    assert normalize_canonical_asset("S&P500") == ("S&P 500", "US Equities")
    assert normalize_canonical_asset("SNP500") == ("S&P 500", "US Equities")
    assert normalize_canonical_asset("QQQ") == ("Nasdaq 100", "US Equities")
    assert normalize_canonical_asset("Nasdaq") == ("Nasdaq 100", "US Equities")

    # Crypto
    assert normalize_canonical_asset("BTC") == ("Bitcoin (BTC)", "Crypto")
    assert normalize_canonical_asset("Bitcoin") == ("Bitcoin (BTC)", "Crypto")
    assert normalize_canonical_asset("Satoshis") == ("Bitcoin (BTC)", "Crypto")
    assert normalize_canonical_asset("ETH") == ("Ethereum (ETH)", "Crypto")
    assert normalize_canonical_asset("Ethereum") == ("Ethereum (ETH)", "Crypto")
    assert normalize_canonical_asset("Solana") == ("Solana (SOL)", "Crypto")

    # Malaysian platforms & funds
    assert normalize_canonical_asset("ASB") == ("ASB (Amanah Saham)", "Fixed Yield")
    assert normalize_canonical_asset("EPF") == ("EPF / KWSP", "Retirement")
    assert normalize_canonical_asset("KWSP") == ("EPF / KWSP", "Retirement")
    assert normalize_canonical_asset("Versa") == ("Versa Cash", "Money Market")
    assert normalize_canonical_asset("Versa Cash") == ("Versa Cash", "Money Market")
    assert normalize_canonical_asset("StashAway") == ("StashAway", "Robo-Advisor")
    assert normalize_canonical_asset("Wahed") == ("Wahed Invest", "Robo-Advisor")
    assert normalize_canonical_asset("Gold") == ("Gold", "Commodities")


@pytest.mark.asyncio
async def test_asset_fragmentation_trap_defeated(db: DatabaseManager):
    """
    CRITICAL TEST: Verify that logging different tickers/synonyms (VOO, S&P 500, SNP500)
    normalizes cleanly in SQLite so GROUP BY asset_name returns ONE unified asset row.
    """
    await db.insert_expense(500.0, "Investments & Savings", "VOO ETF", "2026-08-01 10:00:00", asset_name="VOO")
    await db.insert_expense(300.0, "Investments & Savings", "S&P 500 topup", "2026-08-15 10:00:00", asset_name="S&P 500")
    await db.insert_expense(200.0, "Investments & Savings", "SNP500 monthly", "2026-08-25 10:00:00", asset_name="SNP500")
    
    await db.insert_expense(400.0, "Investments & Savings", "BTC DCA", "2026-08-20 10:00:00", asset_name="BTC")

    summary = await db.get_investments_summary()
    assert summary["total_invested"] == 1400.0
    assert summary["count"] == 4

    assert len(summary["asset_breakdown"]) == 2

    snp_entry = next(a for a in summary["asset_breakdown"] if a["asset_name"] == "S&P 500")
    assert snp_entry["total_amount"] == 1000.0
    assert snp_entry["count"] == 3
    assert snp_entry["percentage"] == 71.4

    btc_entry = next(a for a in summary["asset_breakdown"] if a["asset_name"] == "Bitcoin (BTC)")
    assert btc_entry["total_amount"] == 400.0
    assert btc_entry["count"] == 1
    assert btc_entry["percentage"] == 28.6


@pytest.mark.asyncio
async def test_dca_commitments_matching_and_streaks(db: DatabaseManager):
    """Verify recurring investment bills properly link to DCA logs and compute consecutive streaks."""
    snp_bill_id = await db.add_recurring_bill(
        name="S&P500 DCA",
        amount=500.0,
        category="Investments & Savings",
        day_of_month=27,
    )
    versa_bill_id = await db.add_recurring_bill(
        name="Versa Cash",
        amount=200.0,
        category="Investments & Savings",
        day_of_month=15,
    )

    await db.insert_expense(500.0, "Investments & Savings", "S&P500 DCA", "2026-07-27 12:00:00", asset_name="S&P 500", recurring_bill_id=snp_bill_id)
    await db.insert_expense(500.0, "Investments & Savings", "S&P500 DCA", "2026-08-25 12:00:00", asset_name="S&P 500", recurring_bill_id=snp_bill_id)
    await db.insert_expense(100.0, "Investments & Savings", "Versa partial", "2026-08-10 12:00:00", asset_name="Versa Cash", recurring_bill_id=versa_bill_id)

    dca_august = await db.get_dca_progress("2026-08")
    assert len(dca_august) == 2

    snp_progress = next(d for d in dca_august if d["bill_id"] == snp_bill_id)
    assert snp_progress["invested_amount"] == 500.0
    assert snp_progress["target_amount"] == 500.0
    assert snp_progress["is_fulfilled"] is True
    assert snp_progress["streak_months"] == 2

    versa_progress = next(d for d in dca_august if d["bill_id"] == versa_bill_id)
    assert versa_progress["invested_amount"] == 100.0
    assert versa_progress["target_amount"] == 200.0
    assert versa_progress["percentage"] == 50.0
    assert versa_progress["is_fulfilled"] is False


@pytest.mark.asyncio
async def test_investment_budget_immunity(db: DatabaseManager):
    """
    Verify that large capital deployments into Investments & Savings NEVER deduct
    from the daily living allowance or trigger budget overspend alerts.
    """
    now_dt = datetime.datetime(2026, 8, 15, 12, 0, 0)
    
    await db.set_budget("Food & Dining", 600.0)
    await db.set_budget("Transport", 400.0)

    await db.insert_expense(100.0, "Food & Dining", "Groceries & Meal", "2026-08-10 12:00:00")
    await db.insert_expense(50.0, "Transport", "Fuel", "2026-08-12 12:00:00")

    await db.insert_expense(10000.0, "Investments & Savings", "Lump sum S&P 500", "2026-08-15 10:00:00", asset_name="S&P 500")

    allowance = await db.get_safe_daily_allowance(now_dt)
    assert allowance["has_budget"] is True
    assert allowance["is_overspent"] is False
    assert allowance["total_budget"] == 1000.0
    assert allowance["total_spent"] == 150.0
    assert allowance["remaining_budget"] == 850.0
    assert allowance["safe_daily_allowance"] > 0.0


def test_wealth_formatters():
    """Verify format_investments_overview renders correctly."""
    invest_summary = {
        "total_invested": 1200.0,
        "count": 3,
        "asset_breakdown": [
            {"asset_name": "S&P 500", "asset_class": "US Equities", "total_amount": 1000.0, "percentage": 83.3},
            {"asset_name": "Versa Cash", "asset_class": "Money Market", "total_amount": 200.0, "percentage": 16.7},
        ],
        "class_breakdown": [
            {"asset_class": "US Equities", "total_amount": 1000.0, "percentage": 83.3},
            {"asset_class": "Money Market", "total_amount": 200.0, "percentage": 16.7},
        ],
    }
    dca_progress = [
        {
            "bill_id": 1,
            "name": "S&P500 DCA",
            "target_amount": 1000.0,
            "invested_amount": 1000.0,
            "percentage": 100.0,
            "is_fulfilled": True,
            "due_day": 27,
            "streak_months": 3,
        }
    ]

    embed = format_investments_overview(invest_summary, dca_progress, "2026-08")
    assert "💎 Wealth & Investment Center — 2026-08" in embed.title
    assert "RM 1200.00" in embed.fields[0].name
    assert "S&P500 DCA" in embed.fields[1].value
    assert "🔥 3-mo streak" in embed.fields[1].value
    assert "US Equities" in embed.fields[2].value
