import pytest

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_live_exchange_rates(client):
    r = (await client.call_tool("cb_exchange_rates", {"currencies": ["USD", "EUR"]})).structured_content
    assert {x["code"] for x in r["rates"]} == {"USD", "EUR"}
    assert all(x["rate_irr"] > 100_000 for x in r["rates"]), r


async def test_live_exchange_rates_past_day(client):
    r = (await client.call_tool("cb_exchange_rates", {"date": "1404/01/01", "currencies": ["USD"]})).structured_content
    assert r["date"] == "1404/01/01" and r["rates"][0]["rate_irr"] == 579503


async def test_live_rate_history_recent(client):
    r = (await client.call_tool("cb_rate_history", {"currency": "USD"})).structured_content
    assert 2 <= len(r["points"]) <= 7 and r["points"][-1]["rate_irr"] > 100_000


async def test_live_rate_history_range(client):
    args = {"currency": "EUR", "from_date": "1404/01/01", "to_date": "1404/03/01", "step_days": 30}
    r = (await client.call_tool("cb_rate_history", args)).structured_content
    assert len(r["points"]) >= 2 and r["points"][0]["date"].startswith("1404/")


async def test_live_inflation(client):
    r = (await client.call_tool("cb_inflation", {"annual_years": 5})).structured_content
    assert r["latest"]["rate_pct"] > 0 and len(r["annual"]) == 5
    old = (await client.call_tool("cb_inflation", {"year": 1403, "annual_years": 0})).structured_content
    assert len(old["monthly"]) == 12 and old["latest"]["rate_pct"] == 35.8


async def test_live_policy_rates(client):
    r = (await client.call_tool("cb_policy_rates", {"limit": 3})).structured_content
    assert len(r["rows"]) == 3 and 0 < r["rows"][0]["interbank_pct"] < 100


async def test_live_news_and_item(client):
    r = (await client.call_tool("cb_news", {"limit": 5})).structured_content
    assert r["count"] == 5
    item = (await client.call_tool("cb_news_item", {"id": r["items"][0]["id"]})).structured_content
    assert item["title"] and item["text"]


async def test_live_liquidity(client):
    r = (await client.call_tool("cb_liquidity", {"limit": 2})).structured_content
    assert r["statements"] and "نقدینگی" in r["statements"][0]["title"]
    assert r["monthly_report"]["files"]


async def test_live_publications(client):
    for kind in ("economic_trends", "monetary_reports", "annual_review"):
        r = (await client.call_tool("cb_publications", {"kind": kind, "limit": 2})).structured_content
        assert r["items"], kind


async def test_live_liquidity_numbers(client):
    r = (await client.call_tool("cb_liquidity", {"limit": 1})).structured_content
    assert r["monetary_aggregates"]["liquidity"]["balance"] > 50_000, r


async def test_live_statistics_bop_dollar_rates(client):
    args = {"section": "balance_of_payments", "sheet": "نرخ دلار"}
    r = (await client.call_tool("cb_statistics", args)).structured_content
    assert r["rows"][-1][0] > 100_000 and len(r["sheets"]) >= 3, r


async def test_live_statistics_capital_market_other_year(client):
    r = (
        await client.call_tool("cb_statistics", {"section": "capital_market", "year": 1404, "month": 12})
    ).structured_content
    assert r["period"] == "1404 اسفند" and r["total_rows"] > 10


async def test_live_bond_auctions(client):
    r = (await client.call_tool("cb_bond_auctions", {"limit": 1})).structured_content
    a = r["auctions"][0]
    assert len(a["bonds"]) >= 3 and a["total_sold_all_auctions_this_year"] > 1000, a


async def test_live_open_market(client):
    r = (await client.call_tool("cb_open_market", {"limit": 2})).structured_content
    assert len(r["items"]) == 2 and r["latest"]["repo"]["min_repo_rate_pct"] > 5, r
