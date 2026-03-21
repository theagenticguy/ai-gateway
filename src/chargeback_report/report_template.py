"""HTML template renderer for monthly chargeback reports.

Produces clean, printable HTML with inline CSS for email compatibility.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chargeback_report.models import ReportData, TeamUsageSummary

# -- Shared inline style fragments -------------------------------------------

_CELL = "padding: 10px 14px; border-bottom: 1px solid #e0e0e0"
_CELL_R = f"{_CELL}; text-align: right"
_TH_BASE = (
    "padding: 12px 14px; font-size: 11px; text-transform: uppercase;"
    " letter-spacing: 1px; color: #888; border-bottom: 2px solid #e0e0e0"
)
_TH_L = f"{_TH_BASE}; text-align: left"
_TH_R = f"{_TH_BASE}; text-align: right"
_FT = "padding: 12px 14px; border-top: 2px solid #1a1a2e"
_FT_R = f"{_FT}; text-align: right"
_STAT = "font-size: 28px; font-weight: 700; color: #1a1a2e"
_LABEL = "font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-top: 4px"


def _format_usd(amount: Decimal) -> str:
    """Format a Decimal as USD currency string."""
    return f"${amount:,.2f}"


def _format_tokens(count: int) -> str:
    """Format token count with comma separators."""
    return f"{count:,}"


def _format_pct(value: Decimal | None) -> str:
    """Format percentage or return N/A."""
    if value is None:
        return "N/A"
    return f"{value:.1f}%"


def _budget_color(pct: Decimal | None) -> str:
    """Return CSS color based on budget utilization percentage."""
    if pct is None:
        return "#888888"
    if pct >= Decimal(100):
        return "#dc3545"
    if pct >= Decimal(80):
        return "#fd7e14"
    return "#28a745"


def _render_team_row(team: TeamUsageSummary) -> str:
    """Render a single team row in the report table."""
    bpct = _format_pct(team.budget_utilization_pct)
    bcol = _budget_color(team.budget_utilization_pct)
    cost = _format_usd(team.total_cost_usd)
    inp = _format_tokens(team.input_tokens)
    out = _format_tokens(team.output_tokens)
    cached = _format_tokens(team.cached_tokens)
    bstyle = f"{_CELL_R}; color: {bcol}; font-weight: 600"
    return (
        f"        <tr>\n"
        f'          <td style="{_CELL}; font-weight: 500;">'
        f"{team.team}</td>\n"
        f'          <td style="{_CELL_R};">{cost}</td>\n'
        f'          <td style="{_CELL_R};">{inp}</td>\n'
        f'          <td style="{_CELL_R};">{out}</td>\n'
        f'          <td style="{_CELL_R};">{cached}</td>\n'
        f'          <td style="{bstyle};">{bpct}</td>\n'
        f'          <td style="{_CELL};">'
        f"{team.top_model}</td>\n"
        f"        </tr>"
    )


def _render_mom_section(report: ReportData) -> str:
    """Render month-over-month comparison if data is available."""
    mom_pct = report.month_over_month_change_pct
    if mom_pct is None:
        return ""

    if mom_pct > 0:
        arrow, color, direction = "&#9650;", "#dc3545", "increase"
    elif mom_pct < 0:
        arrow, color, direction = "&#9660;", "#28a745", "decrease"
    else:
        arrow, color, direction = "&#9644;", "#888888", "no change"

    prev = _format_usd(report.previous_month_cost_usd or Decimal(0))
    outer = (
        "background: #f8f9fa; border-radius: 8px;"
        f" padding: 16px 20px; margin-bottom: 24px;"
        f" border-left: 4px solid {color}"
    )
    return (
        f'\n    <div style="{outer};">\n'
        f'      <span style="font-size: 14px; color: #555;">'
        f"Month-over-Month:</span>\n"
        f'      <span style="font-size: 18px; font-weight: 600;'
        f' color: {color}; margin-left: 8px;">'
        f"{arrow} {abs(mom_pct):.1f}%</span>\n"
        f'      <span style="font-size: 14px; color: #888;'
        f' margin-left: 4px;">'
        f"({direction} from {prev})</span>\n"
        f"    </div>"
    )


def render_html(report_data: ReportData) -> str:
    """Render chargeback report as HTML."""
    sorted_teams = sorted(
        report_data.teams,
        key=lambda t: t.total_cost_usd,
        reverse=True,
    )
    team_rows = "\n".join(_render_team_row(t) for t in sorted_teams)
    mom_section = _render_mom_section(report_data)

    total_cost = _format_usd(report_data.total_cost_usd)
    total_tok = _format_tokens(report_data.total_tokens)
    gen_ts = report_data.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    body_style = (
        "margin: 0; padding: 0; background: #f4f4f7;"
        " font-family: -apple-system, BlinkMacSystemFont,"
        " 'Segoe UI', Roboto, 'Helvetica Neue', Arial,"
        " sans-serif; color: #1a1a1a;"
        " font-size: 14px; line-height: 1.5"
    )
    hdr_style = (
        "background: linear-gradient(135deg, #1a1a2e 0%,"
        " #16213e 100%); color: #ffffff; padding: 32px;"
        " border-radius: 12px 12px 0 0"
    )
    card_style = "background: #ffffff; padding: 24px 32px; border-bottom: 1px solid #e0e0e0"
    td_border = "text-align: center; padding: 12px"
    td_bl = f"{td_border}; border-left: 1px solid #e0e0e0"

    # Footer totals
    ft_inp = _format_tokens(
        sum(t.input_tokens for t in report_data.teams),
    )
    ft_out = _format_tokens(
        sum(t.output_tokens for t in report_data.teams),
    )
    ft_cached = _format_tokens(
        sum(t.cached_tokens for t in report_data.teams),
    )

    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport"'
        ' content="width=device-width, initial-scale=1.0">\n'
        "  <title>AI Gateway Chargeback Report"
        f" - {report_data.month}</title>\n"
        "</head>\n"
        f'<body style="{body_style};">\n'
        '  <div style="max-width: 960px;'
        ' margin: 0 auto; padding: 32px 16px;">\n\n'
        "    <!-- Header -->\n"
        f'    <div style="{hdr_style};">\n'
        '      <h1 style="margin: 0 0 8px 0;'
        ' font-size: 24px; font-weight: 700;">'
        "AI Gateway Chargeback Report</h1>\n"
        '      <p style="margin: 0;'
        ' font-size: 16px; opacity: 0.85;">'
        f"{report_data.month}</p>\n"
        '      <p style="margin: 4px 0 0 0;'
        ' font-size: 12px; opacity: 0.6;">'
        f"Generated {gen_ts}</p>\n"
        "    </div>\n\n"
        "    <!-- Summary Cards -->\n"
        f'    <div style="{card_style};">\n'
        '      <table role="presentation"'
        ' style="width: 100%; border-collapse: collapse;">\n'
        "        <tr>\n"
        f'          <td style="{td_border};">\n'
        f'            <div style="{_STAT};">'
        f"{total_cost}</div>\n"
        f'            <div style="{_LABEL};">'
        "Total Cost</div>\n"
        "          </td>\n"
        f'          <td style="{td_bl};">\n'
        f'            <div style="{_STAT};">'
        f"{total_tok}</div>\n"
        f'            <div style="{_LABEL};">'
        "Total Tokens</div>\n"
        "          </td>\n"
        f'          <td style="{td_bl};">\n'
        f'            <div style="{_STAT};">'
        f"{report_data.team_count}</div>\n"
        f'            <div style="{_LABEL};">'
        "Teams</div>\n"
        "          </td>\n"
        f'          <td style="{td_bl};">\n'
        f'            <div style="{_STAT};">'
        f"{report_data.total_requests:,}</div>\n"
        f'            <div style="{_LABEL};">'
        "Requests</div>\n"
        "          </td>\n"
        "        </tr>\n"
        "      </table>\n"
        "    </div>\n\n"
        f"    {mom_section}\n\n"
        "    <!-- Team Breakdown Table -->\n"
        '    <div style="background: #ffffff;'
        ' border-radius: 0 0 12px 12px; overflow: hidden;">\n'
        '      <table role="presentation"'
        ' style="width: 100%; border-collapse: collapse;">\n'
        "        <thead>\n"
        '          <tr style="background: #f8f9fa;">\n'
        f'            <th style="{_TH_L};">Team</th>\n'
        f'            <th style="{_TH_R};">Total Cost</th>\n'
        f'            <th style="{_TH_R};">'
        "Input Tokens</th>\n"
        f'            <th style="{_TH_R};">'
        "Output Tokens</th>\n"
        f'            <th style="{_TH_R};">'
        "Cached Tokens</th>\n"
        f'            <th style="{_TH_R};">'
        "Budget Used</th>\n"
        f'            <th style="{_TH_L};">Top Model</th>\n'
        "          </tr>\n"
        "        </thead>\n"
        "        <tbody>\n"
        f"{team_rows}\n"
        "        </tbody>\n"
        "        <tfoot>\n"
        '          <tr style="background: #f8f9fa;'
        ' font-weight: 700;">\n'
        f'            <td style="{_FT};">TOTAL</td>\n'
        f'            <td style="{_FT_R};">'
        f"{total_cost}</td>\n"
        f'            <td style="{_FT_R};">'
        f"{ft_inp}</td>\n"
        f'            <td style="{_FT_R};">'
        f"{ft_out}</td>\n"
        f'            <td style="{_FT_R};">'
        f"{ft_cached}</td>\n"
        f'            <td style="{_FT_R};">-</td>\n'
        f'            <td style="{_FT};">-</td>\n'
        "          </tr>\n"
        "        </tfoot>\n"
        "      </table>\n"
        "    </div>\n\n"
        "    <!-- Footer -->\n"
        '    <div style="text-align: center;'
        ' padding: 24px 0; font-size: 12px; color: #888;">\n'
        "      AI Gateway Chargeback Report"
        " | Auto-generated on the 1st of each month\n"
        "    </div>\n\n"
        "  </div>\n"
        "</body>\n"
        "</html>"
    )
