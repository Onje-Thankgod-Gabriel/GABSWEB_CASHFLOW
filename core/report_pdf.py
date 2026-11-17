"""Server-side PDF rendering for the read-only Reports page.

The report data is composed in :mod:`core.reports`; this module only turns
that dictionary into a downloadable PDF document.
"""

from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from xml.sax.saxutils import escape

ZERO = Decimal("0")

INK = colors.HexColor("#12202b")
MUTED = colors.HexColor("#667085")
LINE = colors.HexColor("#e4e7ec")
PAPER = colors.HexColor("#f8fafc")
ACCENT = colors.HexColor("#0f766e")
INCOME = colors.HexColor("#0e9f6e")
EXPENSE = colors.HexColor("#e02424")
SAVINGS = colors.HexColor("#2563eb")
WARNING = colors.HexColor("#b45309")

_FONTS = {}


def _register_fonts():
    if _FONTS:
        return _FONTS
    base = Path(getattr(settings, "BASE_DIR", Path.cwd())) / "static" / "fonts"
    specs = {
        "Poppins": "Poppins-Regular.ttf",
        "Poppins-Medium": "Poppins-Medium.ttf",
        "Poppins-SemiBold": "Poppins-SemiBold.ttf",
        "Poppins-Bold": "Poppins-Bold.ttf",
        "Bricolage": "BricolageGrotesque.ttf",
    }
    _FONTS["body"] = "Helvetica"
    _FONTS["medium"] = "Helvetica-Bold"
    _FONTS["semibold"] = "Helvetica-Bold"
    _FONTS["bold"] = "Helvetica-Bold"
    _FONTS["display"] = "Helvetica-Bold"
    for name, filename in specs.items():
        path = base / filename
        if not path.exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont(name, str(path)))
        except Exception:
            continue
        if name == "Poppins":
            _FONTS["body"] = name
        elif name == "Poppins-Medium":
            _FONTS["medium"] = name
        elif name == "Poppins-SemiBold":
            _FONTS["semibold"] = name
        elif name == "Poppins-Bold":
            _FONTS["bold"] = name
        elif name == "Bricolage":
            _FONTS["display"] = name
    return _FONTS


def _decimal(value):
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(value or 0)
    except (InvalidOperation, TypeError, ValueError):
        return ZERO


def _money(value):
    amount = _decimal(value)
    sign = "-" if amount < ZERO else ""
    return f"{sign}₦{abs(amount):,.2f}"


def _number(value):
    return f"{_decimal(value):,.2f}"


def _percent(value):
    if value is None:
        return "—"
    return f"{_decimal(value):,.1f}%"


def _date(value):
    if not value:
        return "—"
    if isinstance(value, str):
        return value
    return f"{value.day} {value.strftime('%b %Y')}"


def _range_text(report):
    period = report.get("range", {})
    label = period.get("label") or report.get("label") or ""
    comparison = period.get("comparison") or ""
    return label, comparison


def _styles():
    _register_fonts()
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "PdfTitle",
            parent=base["Title"],
            fontName=_FONTS["display"],
            fontSize=22,
            leading=26,
            textColor=INK,
            alignment=TA_LEFT,
            spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "PdfSubtitle",
            fontName=_FONTS["body"],
            fontSize=9.5,
            leading=14,
            textColor=MUTED,
            spaceAfter=8,
        ),
        "section": ParagraphStyle(
            "PdfSection",
            fontName=_FONTS["semibold"],
            fontSize=12,
            leading=16,
            textColor=INK,
            spaceBefore=10,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "PdfBody",
            fontName=_FONTS["body"],
            fontSize=8.5,
            leading=12,
            textColor=INK,
        ),
        "small": ParagraphStyle(
            "PdfSmall",
            fontName=_FONTS["body"],
            fontSize=7.5,
            leading=10,
            textColor=MUTED,
        ),
        "cardLabel": ParagraphStyle(
            "PdfCardLabel",
            fontName=_FONTS["medium"],
            fontSize=7.5,
            leading=10,
            textColor=MUTED,
        ),
        "cardValue": ParagraphStyle(
            "PdfCardValue",
            fontName=_FONTS["display"],
            fontSize=13,
            leading=17,
            textColor=INK,
        ),
        "cardValueSmall": ParagraphStyle(
            "PdfCardValueSmall",
            fontName=_FONTS["display"],
            fontSize=11,
            leading=15,
            textColor=INK,
        ),
        "table": ParagraphStyle(
            "PdfTableCell",
            fontName=_FONTS["body"],
            fontSize=7.5,
            leading=10,
            textColor=INK,
        ),
        "tableRight": ParagraphStyle(
            "PdfTableCellRight",
            fontName=_FONTS["body"],
            fontSize=7.5,
            leading=10,
            textColor=INK,
            alignment=TA_RIGHT,
        ),
        "tableHead": ParagraphStyle(
            "PdfTableHead",
            fontName=_FONTS["semibold"],
            fontSize=7,
            leading=9,
            textColor=colors.white,
        ),
        "tableHeadRight": ParagraphStyle(
            "PdfTableHeadRight",
            fontName=_FONTS["semibold"],
            fontSize=7,
            leading=9,
            textColor=colors.white,
            alignment=TA_RIGHT,
        ),
        "insight": ParagraphStyle(
            "PdfInsight",
            fontName=_FONTS["body"],
            fontSize=8,
            leading=12,
            textColor=INK,
            spaceAfter=4,
            leftIndent=8,
            bulletIndent=0,
        ),
    }


def _rich(text):
    """Escape text but keep the naira glyph in a font that contains it."""
    return escape(str(text)).replace(
        "₦", f'<font name="{_FONTS["display"]}">₦</font>'
    )


def _p(text, style):
    return Paragraph(str(text), style)


def _table(headers, rows, widths, styles, aligns=None, header=True):
    aligns = aligns or ["LEFT"] * len(headers)
    head_style = styles["tableHead"]
    head_right = styles["tableHeadRight"]
    body = styles["table"]
    body_right = styles["tableRight"]

    data = []
    if header:
        data.append(
            [
                _p(escape(str(head)), head_right if aligns[i] == "RIGHT" else head_style)
                for i, head in enumerate(headers)
            ]
        )
    for row in rows:
        data.append(
            [
                _p(
                    _rich(cell),
                    body_right if aligns[i] == "RIGHT" else body,
                )
                for i, cell in enumerate(row)
            ]
        )

    table = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, LINE),
    ]
    if header:
        commands += [
            ("BACKGROUND", (0, 0), (-1, 0), INK),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, INK),
        ]
        commands.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PAPER]))
    else:
        commands.append(("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, PAPER]))
    table.setStyle(TableStyle(commands))
    return table


def _metric_cards(report, styles):
    current = report.get("current", {})
    cards = [
        ("Income", _money(current.get("income")), INCOME),
        ("Expenses", _money(current.get("expenses")), EXPENSE),
        ("Net cash flow", _money(current.get("net")), ACCENT),
        ("Savings set aside", _money(current.get("savingsContributions")), SAVINGS),
    ]
    cells = []
    for label, value, colour in cards:
        value_style = (
            styles["cardValue"]
            if len(value) <= 15
            else styles["cardValueSmall"]
        )
        inner = Table(
            [[_p(label, styles["cardLabel"])], [_p(_rich(value), value_style)]],
            colWidths=[(A4[0] - 72) / 4 - 8],
        )
        inner.setStyle(
            TableStyle(
                [
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        cells.append(inner)
    table = Table([cells], colWidths=[(A4[0] - 72) / 4] * 4)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#ecfdf3")),
                ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#fef3f2")),
                ("BACKGROUND", (2, 0), (2, 0), colors.HexColor("#ecfdf5")),
                ("BACKGROUND", (3, 0), (3, 0), colors.HexColor("#eff6ff")),
                ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    return table


def _chart(report):
    series = report.get("series") or {}
    buckets = series.get("buckets") or []
    if not buckets:
        return None
    income = [float(_decimal(item.get("income"))) for item in buckets]
    expenses = [float(_decimal(item.get("expenses"))) for item in buckets]
    if not any(income) and not any(expenses):
        return None
    labels = []
    step = max(1, len(buckets) // 8)
    for index, item in enumerate(buckets):
        labels.append(
            str(item.get("label", "")) if index % step == 0 else ""
        )

    width = A4[0] - 72
    drawing = Drawing(width, 170)
    chart = VerticalBarChart()
    chart.x = 4
    chart.y = 26
    chart.width = width - 8
    chart.height = 136
    chart.data = [income, expenses]
    chart.categoryAxis.categoryNames = labels
    chart.categoryAxis.labels.fontName = _FONTS["body"]
    chart.categoryAxis.labels.fontSize = 6
    chart.categoryAxis.labels.fillColor = MUTED
    chart.categoryAxis.strokeColor = LINE
    chart.valueAxis.labels.fontName = _FONTS["body"]
    chart.valueAxis.labels.fontSize = 6
    chart.valueAxis.labels.fillColor = MUTED
    chart.valueAxis.strokeColor = LINE
    chart.valueAxis.gridStrokeColor = LINE
    chart.valueAxis.valueMin = 0
    chart.barSpacing = 1
    chart.groupSpacing = 6
    chart.bars[0].fillColor = INCOME
    chart.bars[1].fillColor = EXPENSE
    chart.bars[0].strokeColor = None
    chart.bars[1].strokeColor = None
    drawing.add(chart)

    drawing.add(Rect(width - 190, 8, 8, 8, fillColor=INCOME, strokeColor=None))
    drawing.add(
        String(
            width - 178,
            10,
            "Income",
            fontName=_FONTS["body"],
            fontSize=7,
            fillColor=INK,
        )
    )
    drawing.add(Rect(width - 120, 8, 8, 8, fillColor=EXPENSE, strokeColor=None))
    drawing.add(
        String(
            width - 108,
            10,
            "Expenses",
            fontName=_FONTS["body"],
            fontSize=7,
            fillColor=INK,
        )
    )
    return drawing


def _summary(report, styles):
    cash = report.get("cash_flow") or {}
    lines = [
        f"Income {_money(cash.get('income'))}",
        f"Expenses {_money(cash.get('expenses'))}",
        f"Net {_money(cash.get('net'))}",
        f"{cash.get('transaction_count', 0)} transactions",
    ]
    return _p(
        " &nbsp;&nbsp;•&nbsp;&nbsp; ".join(_rich(line) for line in lines),
        styles["small"],
    )


def _accounts_section(report, styles):
    accounts = report.get("accounts") or {}
    rows = accounts.get("rows") or []
    story = [_p("Account balances", styles["section"])]
    if not rows:
        story.append(_p("No accounts recorded for this period.", styles["body"]))
        return story
    data = []
    for row in rows:
        data.append(
            [
                row.get("name", "—"),
                row.get("type", "—"),
                _money(row.get("opening")),
                _money(row.get("closing")),
                _money(row.get("movement")),
            ]
        )
    data.append(
        [
            "Total",
            f"{accounts.get('count', len(rows))} accounts",
            _money(accounts.get("total_opening")),
            _money(accounts.get("total_closing")),
            _money(accounts.get("total_movement")),
        ]
    )
    widths = [150, 105, 90, 90, 90]
    table = _table(
        ["Account", "Type", "Opening", "Closing", "Movement"],
        data,
        widths,
        styles,
        aligns=["LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT"],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f2f4f7")),
                ("FONTNAME", (0, -1), (-1, -1), _FONTS["semibold"]),
            ]
        )
    )
    story.append(table)
    return story


def _category_section(report, styles, key, title, colour):
    categories = (report.get("categories") or {}).get(key) or {}
    rows = categories.get("list") or []
    story = [_p(title, styles["section"])]
    if not rows:
        story.append(_p("No activity recorded for this period.", styles["body"]))
        return story
    data = [
        [
            row.get("category", "—"),
            str(row.get("count", 0)),
            _money(row.get("amount")),
            _percent(row.get("percentage")),
        ]
        for row in rows
    ]
    data.append(
        [
            "Total",
            str(sum(int(row.get("count", 0)) for row in rows)),
            _money(categories.get("total")),
            "100.0%",
        ]
    )
    table = _table(
        ["Category", "Count", "Amount", "Share"],
        data,
        [245, 55, 130, 85],
        styles,
        aligns=["LEFT", "RIGHT", "RIGHT", "RIGHT"],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f2f4f7")),
                ("FONTNAME", (0, -1), (-1, -1), _FONTS["semibold"]),
                ("LINEABOVE", (0, 0), (-1, 0), 1.5, colour),
            ]
        )
    )
    story.append(table)
    return story


def _budget_section(report, styles):
    budget = report.get("budget") or {}
    rows = budget.get("rows") or []
    story = [_p("Budget performance", styles["section"])]
    summary_rows = [
        ["Planned", _money(budget.get("total_allocated"))],
        ["Actual spending", _money(budget.get("total_actual"))],
        ["Remaining", _money(budget.get("total_remaining"))],
        ["Unallocated", _money(budget.get("unallocated"))],
    ]
    summary = Table(
        [
            [
                _p(label, styles["cardLabel"]),
                _p(_rich(value), styles["body"]),
            ]
            for label, value in summary_rows
        ],
        colWidths=[90, 145],
    )
    summary.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PAPER),
                ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(summary)
    story.append(Spacer(1, 6))

    if report.get("mode") == "year":
        monthly = budget.get("monthly") or []
        if monthly:
            data = [
                [
                    item.get("month", "—"),
                    _money(item.get("allocated")),
                    _money(item.get("actual")),
                    _money(item.get("remaining")),
                ]
                for item in monthly
            ]
            story.append(
                _table(
                    ["Month", "Planned", "Actual", "Remaining"],
                    data,
                    [120, 135, 135, 125],
                    styles,
                    aligns=["LEFT", "RIGHT", "RIGHT", "RIGHT"],
                )
            )
            story.append(Spacer(1, 6))

    if rows:
        data = [
            [
                row.get("name", "—"),
                row.get("category", "—"),
                _money(row.get("planned")),
                _money(row.get("actual")),
                _money(row.get("remaining")),
                _percent(row.get("usage")),
            ]
            for row in rows
        ]
        story.append(
            _table(
                ["Allocation", "Category", "Planned", "Actual", "Remaining", "Used"],
                data,
                [125, 100, 80, 80, 78, 52],
                styles,
                aligns=["LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT"],
            )
        )
    else:
        story.append(_p("No budget allocations recorded for this period.", styles["body"]))
    return story


def _savings_section(report, styles):
    savings = report.get("savings") or {}
    story = [_p("Savings", styles["section"])]
    summary = [
        ["Set aside", _money(savings.get("contributions"))],
        ["Withdrawn", _money(savings.get("withdrawalTotal"))],
        ["Net for period", _money(savings.get("net"))],
        ["Current balance", _money(savings.get("current_balance"))],
    ]
    table = Table(
        [
            [
                _p(label, styles["cardLabel"]),
                _p(_rich(value), styles["body"]),
            ]
            for label, value in summary
        ],
        colWidths=[110, 150],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eff6ff")),
                ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 6))

    months = savings.get("months") or []
    if months:
        data = [
            [
                item.get("month", "—"),
                _money(item.get("saved")),
                _money(item.get("withdrawn")),
                _money(item.get("net")),
            ]
            for item in months
        ]
        story.append(
            _table(
                ["Month", "Saved", "Withdrawn", "Net"],
                data,
                [120, 135, 135, 125],
                styles,
                aligns=["LEFT", "RIGHT", "RIGHT", "RIGHT"],
            )
        )
    return story


def _goals_section(report, styles):
    goals = report.get("goals") or {}
    rows = goals.get("rows") or []
    story = [_p("Goals", styles["section"])]
    if not rows:
        story.append(_p("No goals recorded for this period.", styles["body"]))
        return story
    data = [
        [
            row.get("name", "—"),
            _money(row.get("target")),
            _money(row.get("assigned")),
            _money(row.get("reserved")),
            _percent(row.get("progress")),
            (row.get("status") or "—").title(),
        ]
        for row in rows
    ]
    story.append(
        _table(
            ["Goal", "Target", "Assigned", "Reserved", "Progress", "Status"],
            data,
            [135, 90, 90, 90, 60, 75],
            styles,
            aligns=["LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "LEFT"],
        )
    )
    return story


def _transfers_section(report, styles):
    transfers = report.get("transfers") or {}
    rows = transfers.get("rows") or []
    if not rows:
        return []
    story = [_p("Transfers", styles["section"])]
    data = [
        [
            _date(row.get("date")),
            row.get("from", "—"),
            row.get("to", "—"),
            _money(row.get("amount")),
            row.get("description") or "—",
        ]
        for row in rows
    ]
    story.append(
        _table(
            ["Date", "From", "To", "Amount", "Description"],
            data,
            [62, 95, 95, 85, 150],
            styles,
            aligns=["LEFT", "LEFT", "LEFT", "RIGHT", "LEFT"],
        )
    )
    return story


def _insights_section(report, styles):
    insights = report.get("insights") or []
    if not insights:
        return []
    story = [_p("Insights", styles["section"])]
    for item in insights:
        title = escape(str(item.get("title", "")))
        text = str(item.get("text", ""))
        text = (
            text.replace("<strong>", "")
            .replace("</strong>", "")
            .replace("&nbsp;", " ")
        )
        text = _rich(text)
        story.append(
            _p(f"<b>{title}.</b> {text}", styles["insight"])
        )
    return story


def _page_decorator(report):
    label = str(report.get("label", ""))

    def draw(canvas, doc):
        canvas.saveState()
        width, height = A4
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(36, height - 38, width - 36, height - 38)
        canvas.setFont(_FONTS["semibold"], 8)
        canvas.setFillColor(INK)
        canvas.drawString(36, height - 31, "GABSWEB Cashflow")
        canvas.setFont(_FONTS["body"], 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(width - 36, height - 31, label)
        canvas.line(36, 34, width - 36, 34)
        canvas.setFont(_FONTS["body"], 7)
        canvas.drawString(36, 24, f"Generated {_date(date.today())}")
        canvas.drawRightString(width - 36, 24, f"Page {doc.page}")
        canvas.restoreState()

    return draw


def build_report_pdf(report):
    """Return the report as PDF bytes."""
    styles = _styles()
    buffer = BytesIO()
    label = str(report.get("label") or "Financial Report")
    title = f"{'Yearly' if report.get('mode') == 'year' else 'Monthly'} financial report"
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=54,
        bottomMargin=48,
        title=f"Gabsweb {label} report",
        author="Gabsweb Cashflow",
        subject=title,
    )

    range_label, comparison = _range_text(report)
    story = [
        _p(title, styles["title"]),
        _p(
            escape(label)
            + (f" &nbsp;•&nbsp; {escape(comparison)}" if comparison else ""),
            styles["subtitle"],
        ),
        _metric_cards(report, styles),
        Spacer(1, 8),
        _summary(report, styles),
    ]

    drawing = _chart(report)
    if drawing is not None:
        chart_table = Table([[drawing]], colWidths=[A4[0] - 72])
        chart_table.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(_p("Income and expense trend", styles["section"]))
        story.append(chart_table)

    sections = [
        _accounts_section(report, styles),
        _category_section(report, styles, "income", "Income by category", INCOME),
        _category_section(report, styles, "expense", "Expenses by category", EXPENSE),
        _budget_section(report, styles),
        _savings_section(report, styles),
        _goals_section(report, styles),
        _transfers_section(report, styles),
        _insights_section(report, styles),
    ]
    for section in sections:
        if not section:
            continue
        story.append(PageBreak() if False else Spacer(1, 4))
        story.extend(section)

    decorator = _page_decorator(report)
    document.build(story, onFirstPage=decorator, onLaterPages=decorator)
    return buffer.getvalue()
