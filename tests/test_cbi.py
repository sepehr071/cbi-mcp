import datetime

import httpx
import pytest
from conftest import FIXTURES, fixture

from cbi_mcp import http
from cbi_mcp.bank import _jalali_to_gregorian
from cbi_mcp.stats import monetary_aggregates

pytestmark = pytest.mark.anyio

TOOLS = {
    "cb_exchange_rates",
    "cb_rate_history",
    "cb_inflation",
    "cb_policy_rates",
    "cb_news",
    "cb_news_item",
    "cb_liquidity",
    "cb_publications",
    "cb_statistics",
    "cb_bond_auctions",
    "cb_open_market",
}


def test_jalali_to_gregorian():
    assert _jalali_to_gregorian(1404, 1, 1) == datetime.date(2025, 3, 21)
    assert _jalali_to_gregorian(1405, 8, 9) == datetime.date(2026, 10, 31)
    assert _jalali_to_gregorian(1403, 12, 30) == datetime.date(2025, 3, 20)  # leap year end


async def test_all_tools_are_read_only_and_described(client):
    tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == TOOLS
    for t in tools:
        assert t.annotations.read_only_hint is True, t.name
        assert t.title and t.description.splitlines()[0].endswith("."), t.name


async def test_exchange_rates_filter_and_parse(client, site):
    site["/exrates/rates_fa.aspx"] = fixture("rates.html")
    r = (await client.call_tool("cb_exchange_rates", {"currencies": ["usd", "JPY"]})).structured_content
    assert r["date"] == "1405/08/09" and r["count"] == 2
    usd, jpy = r["rates"]
    assert (usd["code"], usd["rate_irr"], usd["per_units"], usd["trend"]) == ("USD", 1871664, 1, "up")
    assert (jpy["code"], jpy["per_units"], jpy["rate_irr"]) == ("JPY", 100, 1188497)
    assert "date" not in site.calls[0].url.params


async def test_exchange_rates_jalali_date_becomes_gregorian_query(client, site):
    site["/exrates/rates_fa.aspx"] = fixture("rates.html")
    await client.call_tool("cb_exchange_rates", {"date": "1404/01/01", "currencies": ["USD"]})
    assert site.calls[0].url.params["date"] == "2025/03/21"


async def test_exchange_rates_empty_day(client, site):
    site["/exrates/rates_fa.aspx"] = "<html><body>nothing</body></html>"
    r = (await client.call_tool("cb_exchange_rates", {"date": "2030-01-01"})).structured_content
    assert r["count"] == 0 and r["rates"] == [] and "note" in r


async def test_rate_history_default_uses_rss(client, site):
    site["/exrates/rates_fa.aspx"] = fixture("rates.html")
    site["/ExRatesRSS.aspx"] = fixture("rss_usd.xml")
    r = (await client.call_tool("cb_rate_history", {"currency": "usd"})).structured_content
    assert site.calls[1].url.params["cid"] == "1"
    assert r["currency"] == "USD" and len(r["points"]) == 7
    assert r["points"][0]["date"] == "1405/8/3" and r["points"][-1] == {"date": "1405/8/9", "rate_irr": 1871664}
    assert r["change_pct"] == 1.0


async def test_rate_history_with_dates_samples_the_archive(client, site):
    page = fixture("rates.html")
    site["/exrates/rates_fa.aspx"] = lambda req: (
        httpx.Response(200, text="<html></html>")
        if req.url.params["date"] == "2026/10/24"
        else httpx.Response(200, text=page)
    )
    r = (
        await client.call_tool(
            "cb_rate_history", {"currency": "USD", "from_date": "2026-10-22", "to_date": "2026-10-26", "step_days": 2}
        )
    ).structured_content
    assert sorted(c.url.params["date"] for c in site.calls) == ["2026/10/22", "2026/10/24", "2026/10/26"]
    assert len(r["points"]) == 2  # 10/24 had no rates


async def test_rate_history_unknown_currency(client, site):
    site["/exrates/rates_fa.aspx"] = fixture("rates.html")
    r = await client.call_tool("cb_rate_history", {"currency": "XXX"})
    assert r.is_error


async def test_inflation_default(client, site):
    site["/Inflation/Inflation_FA.aspx"] = fixture("inflation.html")
    r = (await client.call_tool("cb_inflation", {"annual_years": 3})).structured_content
    assert r["year"] == 1405 and r["monthly"][0] == {"month": "فروردین", "month_no": 1, "rate_pct": 50.6}
    assert r["latest"]["month"] == "شهریور" and r["latest"]["rate_pct"] == 68.4
    assert r["annual"] == [
        {"year": 1404, "rate_pct": 48.3},
        {"year": 1403, "rate_pct": 35.8},
        {"year": 1402, "rate_pct": 47.4},
    ]


async def test_inflation_other_year_posts_the_year_selector(client, site):
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            seen.update(dict(httpx.QueryParams(req.content.decode())))
        return httpx.Response(200, text=fixture("inflation.html"))

    site["/Inflation/Inflation_FA.aspx"] = handler
    await client.call_tool("cb_inflation", {"year": 1403})
    assert seen["__EVENTTARGET"] == "ctl00$ucBody$ucContent$ctl00$ddlYear"
    assert seen["ctl00$ucBody$ucContent$ctl00$ddlYear"] == "1403" and "__VIEWSTATE" in seen


async def test_inflation_year_out_of_list(client, site):
    site["/Inflation/Inflation_FA.aspx"] = fixture("inflation.html")
    assert (await client.call_tool("cb_inflation", {"year": 1500})).is_error


async def test_policy_rates(client, site):
    site["/PolicyRates/policyrates_fa.aspx"] = fixture("policy.html")
    r = (await client.call_tool("cb_policy_rates", {"limit": 2})).structured_content
    assert r["year"] == 1405 and r["count"] >= 30 and len(r["rows"]) == 2
    assert r["rows"][0] == {
        "date": "1405/08/07",
        "interbank_pct": 24.0,
        "repo_min_pct": 23.0,
        "standing_lending_pct": 24.0,
        "standing_deposit_pct": 17.0,
    }


async def test_news_filter_and_year_archive(client, site):
    site["/category/News.aspx"] = fixture("news_home.html")
    r = (await client.call_tool("cb_news", {"query": "نقدینگی"})).structured_content
    assert [i["id"] for i in r["items"]] == [36620]
    assert r["items"][0]["date"] == "1405/08/07"
    site["/datedlist/29393.aspx"] = fixture("news_list.html")
    r = (await client.call_tool("cb_news", {"year": 1403, "limit": 2})).structured_content
    assert r["count"] == 2
    assert (await client.call_tool("cb_news", {"year": 1300})).is_error


async def test_news_item_text_and_no_footer_links(client, site):
    site["/showitem/36620.aspx"] = fixture("news_item.html")
    r = (await client.call_tool("cb_news_item", {"id": 36620, "max_chars": 300})).structured_content
    assert r["title"].startswith("رشد سالانه نقدینگی") and r["date"] == "7 آبان 1405"
    assert "50.9 درصد" in r["text"] and r["text"].endswith("...")
    assert r["files"] == []


async def test_news_item_missing(client, site):
    site["/showitem/1.aspx"] = "<html>nope</html>"
    assert (await client.call_tool("cb_news_item", {"id": 1})).is_error


async def test_publications(client, site):
    site["/category/EconomicTrends_fa.aspx"] = fixture("trends.html")
    site["/simplelist/36043.aspx"] = fixture("trends_item.html")
    r = (await client.call_tool("cb_publications", {"kind": "economic_trends", "limit": 2})).structured_content
    first = r["items"][0]
    assert first["title"].startswith("نماگرهای اقتصادی شماره 123")
    assert [(f["title"], f["size"]) for f in first["files"]] == [("فرمت pdf", "1.01 MB"), ("فرمت Excel", "245 KB")]
    assert first["files"][0]["url"] == "https://www.cbi.ir/page/36044.aspx" and "files" not in r["items"][1]
    site["/datedlist/AnnualReview_fa.aspx"] = fixture("annual.html")
    r = (await client.call_tool("cb_publications", {"kind": "annual_review", "limit": 1})).structured_content
    assert r["items"][0]["published"] == "1405/07/18" and r["items"][0]["size"] == "3.08 MB"


async def test_liquidity_combines_news_and_monthly_report(client, site):
    site["/category/News.aspx"] = fixture("news_home.html")
    site["/showitem/36620.aspx"] = fixture("news_item.html")
    site["/category/2692.aspx"] = fixture("monetary.html")
    site["/simplelist/35569.aspx"] = fixture("monetary_item.html")
    r = (await client.call_tool("cb_liquidity", {"limit": 1})).structured_content
    assert r["statements"][0]["id"] == 36620 and "50.9" in r["statements"][0]["lead"]
    assert r["monthly_report"]["title"] == "1404 اسفند"
    assert [f["size"] for f in r["monthly_report"]["files"]] == ["196 KB", "349 KB", "292 KB"]


async def test_refuses_other_hosts_and_reports_http_errors(site):
    with pytest.raises(http.ApiError, match="unexpected host"):
        await http.get("https://example.com/")
    with pytest.raises(http.ApiError, match="HTTP 404"):
        await http.get("/nothing.aspx")
    site["/boom"] = lambda req: httpx.Response(503)
    with pytest.raises(http.ApiError, match="server error"):
        await http.get("/boom")
    assert len(site.calls) == 1 + http.MAX_RETRIES + 1  # one 404, then the 503 retried


def _file(name):
    return lambda req: httpx.Response(200, content=(FIXTURES / name).read_bytes())


async def test_statistics_reads_newest_month_and_sheet_by_name(client, site):
    site["/category/2693.aspx"] = fixture("stats_category.html")
    site["/simplelist/36577.aspx"] = fixture("stats_month.html")
    site["/page/36579.aspx"] = _file("bop.xlsx")
    r = (
        await client.call_tool("cb_statistics", {"section": "balance_of_payments", "sheet": "نرخ دلار"})
    ).structured_content
    assert r["period"] == "1405 مرداد" and r["sheet"] == "نرخ دلار" and len(r["sheets"]) == 3
    assert r["rows"][-1][:2] == [1889636.9565, 1850210.7826] and r["rows"][-1][-1] == "مرداد"
    first = (
        await client.call_tool("cb_statistics", {"section": "balance_of_payments", "max_rows": 3})
    ).structured_content
    assert first["sheet"].startswith("بدهي") and first["rows"][2][:3] == [4266.6942, 1604.7832, 2661.911]


async def test_statistics_year_postback_and_errors(client, site):
    seen = []

    def category(req):
        seen.append((req.method, req.content.decode()))
        return httpx.Response(200, text=fixture("stats_category.html"))

    site["/category/2693.aspx"] = category
    site["/simplelist/36573.aspx"] = fixture("stats_month.html")
    site["/page/36579.aspx"] = _file("bop.xlsx")
    r = await client.call_tool("cb_statistics", {"section": "balance_of_payments", "year": 1404, "month": 4})
    assert r.structured_content["period"] == "1404 تیر"
    assert seen[1][0] == "POST" and "__EVENTTARGET=ctl00" in seen[1][1] and "__VIEWSTATE=vs1" in seen[1][1]
    bad = await client.call_tool("cb_statistics", {"section": "balance_of_payments", "year": 1390})
    assert bad.is_error and "Available" in bad.content[0].text
    bad = await client.call_tool("cb_statistics", {"section": "balance_of_payments", "month": "آذر"})
    assert bad.is_error and "No آذر file yet" in bad.content[0].text


def test_monetary_aggregates_from_major_variables_sheet():
    rows = [
        ["مانده برخي از متغيرهاي عمده", "اسفند1404", "(هزار ميليارد ريال)"],
        ["پايه پولي (1)", "پايه پولي از سمت منابع(کاملا مقدماتی)", 10921.3, 13594.3, 21959.3, 24.5, 61.5, 24.5, 61.5],
        ["نقدينگي", "نقدينگي برحسب عوامل موثر بر عرضه", 78774.5, 101659.5, 155812.2, 29.1, 53.3, 29.1, 53.3],
        ["پول", 19146.4, 26317.3, 39948.4, 37.5, 51.8, 0.3, 0.3],
        ["شبه پول", 59628.1, 75342.2, 115863.8, 26.4, 53.8, 0.7, 0.7],
        ["ضريب فزاينده نقدينگي", 7.2, 7.478, 7.095, 3.7, -5.1],
    ]
    r = monetary_aggregates(rows)
    assert r["period"] == "اسفند1404"
    assert r["liquidity"] == {"balance": 155812.2, "previous_year_balance": 101659.5, "growth_pct": 53.3}
    assert r["money_m1"]["balance"] == 39948.4 and r["quasi_money"]["balance"] == 115863.8
    assert r["monetary_base"]["growth_pct"] == 61.5 and r["money_multiplier"]["growth_pct"] == -5.1


async def test_bond_auctions_parse_result_table(client, site):
    site["/category/News.aspx"] = fixture("news_home.html")
    site["/showitem/36648.aspx"] = fixture("auction_item.html")
    r = (await client.call_tool("cb_bond_auctions", {"limit": 1})).structured_content
    a = r["auctions"][0]
    assert (
        a["id"] == 36648 and a["total_sold_this_auction"] == 50.0 and a["total_sold_all_auctions_this_year"] == 4293.8
    )
    bond = next(b for b in a["bonds"] if b["symbol"] == "اراد299")
    assert (bond["offered"], bond["sold"], bond["yield_public_pct"]) == (69.5, 50.0, 39.6)
    assert [b["symbol"] for b in a["bonds"]] == ["اراد296", "اراد297", "اراد299", "اراد313", "اراد314"]


async def test_open_market_lists_reports_and_reads_latest_pdf(client, site):
    site["/category/31094.aspx"] = fixture("omo_list.html")
    site["/page/36570.aspx"] = _file("omo.pdf")
    r = (await client.call_tool("cb_open_market", {"limit": 2})).structured_content
    assert len(r["items"]) == 2 and r["items"][0]["date"] == "1405/07/27"
    assert r["latest"]["auction_no"] == 29
    assert r["latest"]["repo"] == {
        "banks_participating": 14,
        "orders_sent": 3.2582,
        "orders_accepted": 700.0,
        "repo_days": 7,
        "min_repo_rate_pct": 23.0,
        "matured": 703.1,
        "outstanding": 700.0,
    }


async def test_requests_send_chrome_navigation_headers(site):
    site["/p.aspx"] = "ok"
    site["/f.xlsx"] = "ok"
    await http.get("/p.aspx")
    await http.get_file("/f.xlsx")
    await http.post_form("/p.aspx", {"a": "b"})
    page, file, post = (c.headers for c in site.calls)
    assert page["sec-fetch-mode"] == "navigate" and page["sec-fetch-site"] == "none" and "referer" not in page
    assert page["accept"].startswith("text/html") and page["upgrade-insecure-requests"] == "1"
    assert file["sec-fetch-site"] == "same-origin" and file["referer"] == http.SITE + "/"
    assert post["origin"] == http.SITE and post["referer"].endswith("/p.aspx") and post["sec-fetch-dest"] == "document"
    assert post["content-type"] == "application/x-www-form-urlencoded"


async def test_retry_after_and_proxy_pool(site, monkeypatch):
    assert http._retry_after(httpx.Response(429, headers={"retry-after": "3"})) == 3
    assert http._retry_after(httpx.Response(500, headers={"retry-after": "3"})) is None
    monkeypatch.setenv("CBI_MCP_PROXY", "http://a:1, http://b:2")
    assert http._proxies() == ["http://a:1", "http://b:2"]
