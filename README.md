<!-- mcp-name: io.github.sepehr071/cbi-mcp -->

<div align="center">

# cbi-mcp: Central Bank of Iran (بانک مرکزی) exchange rates, inflation and policy rates MCP server for AI agents

**cbi-mcp is an unofficial, read-only MCP server that gives AI agents the public data of the Central Bank of Iran (cbi.ir).**<br>
Its 8 tools read the official reference exchange rates and their history, inflation, the weekly interbank and policy rates,<br>
the bank's news and liquidity (نقدینگی) statements, and list its reports with download links.<br>
No API key or account is needed. It works in Claude, Cursor, Copilot and any other MCP client.

[![PyPI](https://img.shields.io/pypi/v/cbi-mcp?color=2563eb)](https://pypi.org/project/cbi-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/cbi-mcp)](https://pypi.org/project/cbi-mcp/)
[![License: MIT](https://img.shields.io/badge/license-MIT-16a34a)](https://github.com/sepehr071/cbi-mcp/blob/main/LICENSE)

[For AI agents](#for-ai-agents) · [Quick start](#quick-start) · [What it can do](#what-it-can-do) · [Tools](#tools) · [FAQ](#faq) · [فارسی](#فارسی)

</div>

---

## For AI agents

- **What:** unofficial MCP server (model context protocol) for cbi.ir: official exchange rates (about 48 currencies, archive back to 1383), inflation by month and year, weekly interbank/standing-facility rates, news and announcements, liquidity statements, reports (Economic Trends, monthly monetary statistics, annual review).
- **Install:** `uvx cbi-mcp` or `claude mcp add cbi -- uvx cbi-mcp`. Transport: stdio. Needs [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python 3.10+).
- **Auth:** none. Optional `CBI_MCP_PROXY` (HTTP proxy URL); system proxy settings are ignored.
- **Access:** read-only, no confirmation needed before calling any tool.
- **Tools:** 11, all prefixed `cb_`. Typical sequences: `cb_exchange_rates`; `cb_rate_history`; `cb_inflation`; `cb_news` -> `cb_news_item`; `cb_liquidity`; `cb_statistics`; `cb_bond_auctions`; `cb_policy_rates` + `cb_open_market`.
- **Use when:** the user asks for the official dollar/euro/dirham rate on a day, how a rate moved, Iran's inflation, interest or interbank rates, liquidity growth, or what the central bank announced.
- **Do not use when:** the user wants the free-market (bazaar) rate, which the CBI does not publish, or a series from the CBI time-series database (tsd.cbi.ir did not answer when this was built).
- **Output:** compact JSON. Rates are rials per currency unit (`per_units`: JPY 100, KRW 1000...). Dates in answers are Jalali; arguments accept Jalali (1404/01/01) or Gregorian (2025-03-21). Text is Persian.
- **Machine-readable:** [`server.json`](https://github.com/sepehr071/cbi-mcp/blob/main/server.json).

## Why

The Central Bank's site is a classic ASP.NET website: no API, one rate table per date, inflation behind a year dropdown,
liquidity buried in news and Excel files. `cbi-mcp` reads those pages for the agent:

> **You:** What was the official dollar rate on 1 Farvardin 1404, and what is it now?
>
> **Agent:** *calls* `cb_exchange_rates(date="1404/01/01", currencies=["USD"])` and `cb_exchange_rates(currencies=["USD"])`
>
> 579,503 rials on 1404/01/01 and 1,871,664 rials on 1405/08/09.

<sub>Real values read from cbi.ir on 2026-10-31.</sub>

## What it can do

- **Official exchange rates** for a day, today or from the archive, all currencies or a chosen few
- **Rate history** of one currency: last 7 days in one call, or a sampled series over months or years with total change
- **Inflation**: the 12-month rate for each month of any year since 1361 plus annual inflation back to 1316
- **Policy rates**: weekly interbank rate, minimum repo rate and the standing lending/deposit corridor
- **News** titles, filterable by a word, and the full text of any item
- **Liquidity** as numbers (liquidity, money, quasi-money, monetary base with yearly growth) from the newest monthly Excel, plus the governor's latest statements
- **Monthly statistics tables** read from the Excel files: monetary, balance of payments (foreign debt, monthly average dollar in the official, free and preferential markets), fiscal, capital market
- **Government sukuk auctions**: offered, sold and yield per bond, every week
- **Open market operations**: the weekly repo auction figures
- **Publications** with PDF/Excel links: Economic Trends, monthly monetary statistics, annual economic review, interbank market review

Example prompts:

- "What is the CBI's official euro rate today, and how much did it change over the last week?"
- "Show the dollar rate every month since Farvardin 1404."
- "What is Iran's inflation now and how did it move in 1403?"
- "What did the central bank say about liquidity growth recently?"
- <span dir="rtl">نرخ رسمی دلار بانک مرکزی امروز چقدر است؟</span>
- <span dir="rtl">آخرین گزارش نماگرهای اقتصادی را بده.</span>

## Quick start

```bash
claude mcp add cbi -- uvx cbi-mcp
```

Any client: command `uvx`, args `["cbi-mcp"]`. Behind a proxy set `CBI_MCP_PROXY=http://host:port`.

## Tools

| Tool | What it returns |
|---|---|
| `cb_exchange_rates` | Official rates of ~48 currencies for a date (default latest), optional currency filter |
| `cb_rate_history` | One currency over time with change %; last 7 days, or sampled between two dates |
| `cb_inflation` | Monthly 12-month inflation for a Jalali year and annual inflation history |
| `cb_policy_rates` | Weekly interbank, repo and standing-facility rates for a year |
| `cb_news` | News titles (id, date), optional word filter and year |
| `cb_news_item` | Full text and attachments of one news item |
| `cb_liquidity` | Liquidity, money, quasi-money and monetary base (balance, yearly growth) from the newest monthly Excel, plus latest statements |
| `cb_publications` | Newest Economic Trends, monthly monetary reports, annual or interbank reviews with file links |
| `cb_statistics` | Rows of any sheet of the monthly Excel tables: monetary, balance_of_payments, fiscal, capital_market (year, month, sheet) |
| `cb_bond_auctions` | Results of the newest weekly government sukuk auctions per bond: offered, sold, yield |
| `cb_open_market` | Weekly open market operation reports with the newest repo auction figures |

## FAQ

**Is this the market rate?** No. These are the CBI's official reference rates; the free-market rate is not published there.

**Why no time-series search?** The bank's series database (tsd.cbi.ir) did not answer from any network tried.

**Does it read the Excel/PDF reports?** The monthly Excel statistics tables (`cb_statistics`, `cb_liquidity`), the weekly repo report and the auction results are read as numbers. Other Excel and PDF reports come back as links.

## License

MIT. Unofficial; not affiliated with the Central Bank of the Islamic Republic of Iran. Data belongs to its publisher.

Built by Sepehr Radmard (سپهر رادمرد): github.com/sepehr071 · linkedin.com/in/sepehr-radmard

## فارسی

`cbi-mcp` یک سرور MCP غیررسمی و فقط‌خواندنی برای سایت بانک مرکزی جمهوری اسلامی ایران (cbi.ir) است: نرخ رسمی ارز و تاریخچه آن، نرخ تورم، نرخ‌های سود بازار بین‌بانکی، اخبار و اظهارات نقدینگی و فهرست گزارش‌ها با پیوند دانلود. نیازی به کلید یا حساب نیست.
نصب: `claude mcp add cbi -- uvx cbi-mcp`
