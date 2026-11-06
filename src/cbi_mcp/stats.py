"""Central Bank of Iran statistics tools that read the bank's monthly Excel tables, auction results and weekly reports."""

from __future__ import annotations

import asyncio
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import Annotated, Any, Literal

from pydantic import Field

from .bank import (
    MONTHS,
    _ascii,
    _hidden,
    _line,
    _links,
    _news_page,
    _news_rows,
    _norm,
    cb_news_item,
)
from .http import ApiError, get, get_file, post_form, url_for
from .registry import tool

SECTIONS = {"monetary": 2692, "balance_of_payments": 2693, "fiscal": 2694, "capital_market": 4130}
_M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


# ---- Excel reading (no dependency for .xlsx; xlrd for the older .xls the monetary tables use) --------------------


def _clean(v: Any) -> Any:
    if isinstance(v, float):
        return int(v) if v == int(v) else round(v, 4)
    return re.sub(r"\s+", " ", v).strip() if isinstance(v, str) else v


def _xlsx_sheets(data: bytes) -> list[tuple[str, list[list[Any]]]]:
    z = zipfile.ZipFile(io.BytesIO(data))
    strings = [
        "".join(t.text or "" for t in si.iter(_M + "t"))
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(_M + "si")
    ]
    names = [s.get("name") for s in ET.fromstring(z.read("xl/workbook.xml")).iter(_M + "sheet")]
    out = []
    for i, name in enumerate(names, 1):
        rows = []
        # shortcut: assumes sheetN.xml follows workbook order (true for the CBI files); read the rels if that breaks.
        for row in ET.fromstring(z.read(f"xl/worksheets/sheet{i}.xml")).iter(_M + "row"):
            cells = []
            for c in row:
                v = c.find(_M + "v")
                if v is None or v.text is None or c.get("t") in ("e", "b"):  # error cells (#REF!) are skipped
                    continue
                cells.append(
                    strings[int(v.text)] if c.get("t") == "s" else v.text if c.get("t") == "str" else float(v.text)
                )
            rows.append([_clean(x) for x in cells])
        out.append((name or str(i), rows))
    return out


def _xls_sheets(data: bytes) -> list[tuple[str, list[list[Any]]]]:
    import xlrd

    book = xlrd.open_workbook(file_contents=data)
    return [(s.name, [[_clean(c) for c in s.row_values(r) if c != ""] for r in range(s.nrows)]) for s in book.sheets()]


def _sheets(data: bytes) -> list[tuple[str, list[list[Any]]]]:
    try:
        sheets = _xlsx_sheets(data) if data[:2] == b"PK" else _xls_sheets(data)
    except Exception as e:  # corrupt file or a layout change; the caller gets a clear message
        raise ApiError(f"Could not read the Excel file ({type(e).__name__}). Use its link instead.") from e
    return [(n.strip(), [r for r in rows if r]) for n, rows in sheets]


# ---- monthly statistics pages -------------------------------------------------------------------------------------


async def _month_page(section: int, year: int | None) -> tuple[str, str]:
    """The category page for a year (default: the newest year the page shows) and that year's label."""
    path = f"/category/{section}.aspx"
    page = await get(path)
    sel = re.search(r'<select[^>]*name="([^"]*ddlYearList)"[^>]*>(.*?)</select>', page, re.S | re.I)
    if not sel:
        return page, ""
    options = re.findall(r'<option[^>]*value="(\d+)"[^>]*>\s*(\d{4})\s*</option>', sel.group(2))
    if year is None or str(year) == options[0][1]:
        return page, options[0][1]
    ids = {y: v for v, y in options}
    if str(year) not in ids:
        raise ApiError(f"No statistics for {year}. Available: {options[-1][1]} to {options[0][1]}.")
    form = {**_hidden(page), "__EVENTTARGET": sel.group(1), sel.group(1): ids[str(year)]}
    return await post_form(path, form), str(year)


def _month_name(month: int | str) -> str:
    if isinstance(month, int) or str(month).isdigit():
        n = int(month)
        if not 1 <= n <= 12:
            raise ApiError("month must be 1-12 or a Persian month name such as مرداد.")
        return MONTHS[n - 1]
    m = _norm(str(month))
    for name in MONTHS:
        if _norm(name) == m:
            return name
    raise ApiError("month must be 1-12 or a Persian month name such as مرداد.")


@tool("Monthly statistics tables")
async def cb_statistics(
    section: Annotated[
        Literal["monetary", "balance_of_payments", "fiscal", "capital_market"],
        Field(
            description="monetary (money, credit, bank balance sheets), balance_of_payments (foreign debt, monthly dollar rates), fiscal (budget, taxes), capital_market (stock and sukuk indicators)."
        ),
    ],
    year: Annotated[int | None, Field(description="Jalali year, e.g. 1404. Default: newest.", ge=1380, le=1500)] = None,
    month: Annotated[
        int | str | None, Field(description="Month 1-12 or Persian name (مرداد). Default: newest.")
    ] = None,
    sheet: Annotated[
        str | None,
        Field(description="Sheet number or part of its name. Default: first.", max_length=60),
    ] = None,
    max_rows: Annotated[int, Field(description="Max rows (1-200).", ge=1, le=200)] = 60,
) -> dict[str, Any]:
    """Read one table of the Central Bank's monthly statistics Excel file as rows of numbers and Persian labels.

    Numbers the site publishes only as spreadsheets. Check `period`: the newest month differs per section and lags.
    `sheets` lists every sheet name; call again with sheet to read another. Rows keep file order, empty cells
    dropped, title and header rows first. Money is mostly trillion rials. For liquidity use cb_liquidity.
    """
    page, y = await _month_page(SECTIONS[section], year)
    months = [(h, t) for h, t in _links(page, r"/simplelist/\d+\.aspx") if t in MONTHS]
    if not months:
        raise ApiError(f"No monthly files for {section} in {y or 'that year'}.")
    if month is None:
        path, name = months[-1]
    else:
        name = _month_name(month)
        path = next((h for h, t in months if t == name), "")
        if not path:
            raise ApiError(f"No {name} file yet. Months listed for {y}: {', '.join(t for _, t in months)}.")
    files = await _files(path)
    excel = next((f for f in files if "excel" in f["title"].lower() or "xls" in f["title"].lower()), None)
    if not excel:
        raise ApiError(f"{name} {y} has no Excel file. Files: {', '.join(f['title'] for f in files)}.")
    sheets = _sheets(await get_file(excel["url"]))
    names = [n for n, _ in sheets]
    pick = 0
    if sheet:
        if sheet.isdigit() and 1 <= int(sheet) <= len(sheets):
            pick = int(sheet) - 1
        else:
            q = _norm(sheet)
            hit = [i for i, n in enumerate(names) if q == _norm(n)] or [i for i, n in enumerate(names) if q in _norm(n)]
            if not hit:
                raise ApiError(f"No sheet matches '{sheet}'. Sheets: {', '.join(names)}.")
            pick = hit[0]
    rows = sheets[pick][1]
    cut = [[(c[:240] + "..." if isinstance(c, str) and len(c) > 240 else c) for c in r] for r in rows[:max_rows]]
    return {
        "section": section,
        "period": f"{y} {name}".strip(),
        "file": excel["url"],
        "sheets": names,
        "sheet": names[pick],
        "rows": cut,
        "total_rows": len(rows),
    }


async def _files(path: str) -> list[dict[str, Any]]:
    """Download links of a monthly statistics page (Excel, Pdf, overview)."""
    block = (await get(path)).split('<ul class="simplelist">', 1)[-1].split("</ul>", 1)[0]
    return [{"title": t, "url": url_for(h)} for h, t in _links(block, r"/page/\d+\.aspx")]


# ---- liquidity aggregates from the monthly monetary file ----------------------------------------------------------

_AGGREGATES = [
    ("liquidity", "نقدینگی برحسب عوامل"),
    ("money_m1", "پول"),
    ("quasi_money", "شبه پول"),
    ("monetary_base", "پایه پولی از سمت منابع"),
    ("money_multiplier", "ضریب فزاینده نقدینگی"),
]


def monetary_aggregates(sheet_rows: list[list[Any]]) -> dict[str, Any]:
    """Pick liquidity, money, quasi-money and monetary base out of the 'major variables' sheet (balance, growth)."""
    period = next(
        (c for r in sheet_rows[:8] for c in r if isinstance(c, str) and re.fullmatch(r"\D+\s*\d{4}", _ascii(c))), None
    )
    out: dict[str, Any] = {"period": _ascii(period).replace(" ", "") if period else None}
    for r in sheet_rows:
        nums = [c for c in r if isinstance(c, int | float)]
        words = [_norm(c) for c in r if isinstance(c, str)]
        if len(nums) < 5 or not words:
            continue
        for key, want in _AGGREGATES:
            if key not in out and (
                words[-1] == _norm(want) if key == "money_m1" else words[-1].startswith(_norm(want))
            ):
                out[key] = {"balance": nums[2], "previous_year_balance": nums[1], "growth_pct": nums[4]}
    return out


# ---- government bond auctions -------------------------------------------------------------------------------------

_AUCTION_ROWS = [
    ("offered", r"مبلغ عرضه"),
    ("banks_participating", r"تعداد بانک"),
    ("orders_sent", r"ارزش سفارش ?های ارسال"),
    ("orders_accepted", r"ارزش سفارش ?های پذیرفته"),
    ("sold_to_other_buyers", r"ارزش اوراق فروش رفته"),
    ("yield_public_pct", r"نرخ بازده تا سررسید \(عرضه عمومی\)"),
    ("yield_primary_dealers_pct", r"نرخ بازده تا سررسید \(معامله"),
    ("sold", r"ارزش فروش اوراق به"),
]


def parse_auction(text: str) -> dict[str, Any]:
    """Parse the result table of a government Islamic securities auction (symbols x rows, trillion rials)."""
    t = _norm(text)
    head = re.search(r"نماد اوراق((?: اراد ?\d+)+)", t)
    symbols = re.findall(r"اراد ?\d+", head.group(1)) if head else []
    cuts = [(k, m.start()) for k, p in _AUCTION_ROWS if (m := re.search(p, t))]
    totals = re.findall(r"ارزش کل فروش[^()]*\([^)]*\)\s*([\d.]+)", t)
    cuts.sort(key=lambda x: x[1])
    ends = [c[1] for c in cuts[1:]] + [len(t)]
    rows: dict[str, list[float | None]] = {}
    for (k, start), end in zip(cuts, ends, strict=True):
        seg = t[start:end].split(" ارزش کل فروش")[0]
        seg = seg.rsplit(")", 1)[-1]
        rows[k] = [None if v == "-" else float(v) for v in re.findall(r"(?<![\w.])(?:\d+(?:\.\d+)?|-)(?![\w.-])", seg)]
    bonds = []
    for i, s in enumerate(symbols):
        b: dict[str, Any] = {"symbol": s.replace(" ", "")}
        for k, v in rows.items():
            if len(v) == len(symbols):
                b[k] = v[i]
        bonds.append(b)
    out: dict[str, Any] = {"bonds": bonds}
    if len(totals) == 2:
        out["total_sold_this_auction"], out["total_sold_all_auctions_this_year"] = float(totals[0]), float(totals[1])
    out["unit"] = "trillion rials (هزار میلیارد ریال)"
    return out


@tool("Government bond auctions")
async def cb_bond_auctions(
    limit: Annotated[int, Field(description="Newest results to read (1-6).", ge=1, le=6)] = 2,
    year: Annotated[int | None, Field(description="Jalali year. Default: current.", ge=1399, le=1500)] = None,
) -> dict[str, Any]:
    """Get the results of the Central Bank's weekly government Islamic securities (sukuk) auctions: sold amounts and yields.

    Per bond symbol (e.g. اراد314): amount offered, orders sent and accepted, yield to maturity (percent), amount
    sold, plus auction and year totals, in trillion rials. Full text: cb_news_item with the `id`.
    """
    items = [
        i
        for i in _news_rows(await _news_page(year))
        if "نتیجه" in i["title"] and "حراج" in _norm(i["title"]) and "اوراق" in i["title"]
    ][:limit]
    arts = await asyncio.gather(*(cb_news_item(i["id"], 8000) for i in items))
    return {
        "auctions": [
            {"id": a["id"], "date": a["date"], "title": a["title"].split(" و تاریخ")[0], **parse_auction(a["text"])}
            for a in arts
        ]
    }


# ---- open market operations ---------------------------------------------------------------------------------------

_REPO_FIELDS = [
    "banks_participating",
    "orders_sent",
    "orders_accepted",
    "repo_days",
    "min_repo_rate_pct",
    "matured",
    "outstanding",
]


def _nums(s: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", s)


def parse_omo(pdf: bytes) -> dict[str, Any]:
    """Pull the repo-auction figures out of a weekly open market operations report (a one-page PDF)."""
    import pypdf

    try:
        text = pypdf.PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
    except Exception as e:
        raise ApiError(f"Could not read the report PDF ({type(e).__name__}). Use its link instead.") from e
    lines = [ln.strip() for ln in text.splitlines()]
    out: dict[str, Any] = {}
    start = next((i for i, ln in enumerate(lines) if ln.startswith("شماره حراج")), None)
    if start is None:
        return out
    out["auction_no"] = int(_nums(lines[start])[0])
    vals = [(_nums(ln) or [None])[-1] for ln in lines[start + 1 : start + 8]]
    if all(v is not None for v in vals):
        out["repo"] = {k: float(v) for k, v in zip(_REPO_FIELDS, vals, strict=True)}
        out["repo"]["banks_participating"] = int(out["repo"]["banks_participating"])
        out["repo"]["repo_days"] = int(out["repo"]["repo_days"])
    out["outright_purchase_bids"] = not any("دريافت نشد" in ln or "دریافت نشد" in ln for ln in lines[:start])
    return out


@tool("Open market operations")
async def cb_open_market(
    limit: Annotated[int, Field(description="Weekly reports to list (1-12).", ge=1, le=12)] = 4,
    year: Annotated[int | None, Field(description="Jalali year. Default: current.", ge=1398, le=1500)] = None,
) -> dict[str, Any]:
    """List the Central Bank's weekly open market operation reports and read the newest one's repo auction figures.

    `latest.repo`: banks taking part, accepted orders (trillion rials), term in days, lowest accepted rate
    (percent), matured and outstanding. Older reports are PDF links. Read from a damaged-text PDF, so only numbers
    are returned; check them against the link. See also cb_policy_rates.
    """
    path = "/category/31094.aspx"
    page = await get(path)
    if year:
        ids = {
            _ascii(t): v
            for v, t in re.findall(r"""<option[^>]*value=["'](\d+)["'][^>]*>\s*(\d{4})\s*</option>""", page)
        }
        if str(year) not in ids:
            raise ApiError(f"No reports for {year}. Available: {', '.join(sorted(ids))}.")
        page = await get(f"/datedlist/{ids[str(year)]}.aspx")
    items = []
    for tr in re.findall(r"<tr[^>]*>.*?</tr>", page, re.S | re.I):
        a = _links(tr, r"/page/\d+\.aspx")
        d = re.search(r"ContentDate[^>]*>(.*?)</span>", tr, re.S | re.I)
        if a and d:
            size = re.search(r"([\d.]+ ?[KM]B)", _line(tr))
            items.append(
                {
                    "date": _ascii(_line(d.group(1))),
                    "title": a[0][1],
                    "url": url_for(a[0][0]),
                    "size": size.group(1) if size else None,
                }
            )
    if not items:
        raise ApiError("cbi.ir changed this page: no open market reports found.")
    out: dict[str, Any] = {"items": items[:limit]}
    try:
        out["latest"] = {"date": items[0]["date"], **parse_omo(await get_file(items[0]["url"]))}
    except ApiError as e:
        out["latest_note"] = str(e)
    return out
