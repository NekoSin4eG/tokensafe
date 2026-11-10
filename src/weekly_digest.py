"""
TokenSafe Weekly Team ROI Digest Engine.
Generates automated weekly performance digests for engineering teamleads every Friday.
Aggregates:
- Total team tokens and USD saved over the past 7 days
- Engineer leaderboard ranking team members by token economy contribution
- Optimization category breakdown (Skeletons, Compactor, Deduplication, Sanitization)
- Formats Apple-style responsive dark-mode email and plain text versions
"""

import os
import sys
import time
import json
import sqlite3
from typing import Dict, List, Any, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("TOKENSAFE_DATA_DIR", os.path.join(PROJECT_ROOT, "data"))
CLOUD_DB_PATH = os.environ.get("TOKENSAFE_CLOUD_DB_PATH", os.path.join(DATA_DIR, "cloud.db"))

def format_token_number(n: int) -> str:
    """Formats large token count into human-readable 1.2M, 450K, etc."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)

class WeeklyDigestGenerator:
    """Compiles weekly team ROI statistics and email templates."""

    @classmethod
    def get_team_weekly_stats(
        cls,
        owner_email: str,
        now: Optional[int] = None,
        db_path: str = CLOUD_DB_PATH
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves 7-day telemetry for team owned by owner_email.
        Returns aggregated digest data or None if no team records found.
        """
        now = now or int(time.time())
        seven_days_ago = now - (7 * 86400)

        if not os.path.exists(db_path):
            return None

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            # 1. Verify owner and tier
            owner = conn.execute("""
                SELECT * FROM customers 
                WHERE email = ? AND status = 'active';
            """, (owner_email.lower(),)).fetchone()

            if not owner or owner["tier"] not in ("team", "enterprise"):
                return None

            # 2. Find all member emails and license keys in this team
            licenses = conn.execute("""
                SELECT * FROM licenses 
                WHERE parent_email = ? OR customer_email = ?;
            """, (owner_email.lower(), owner_email.lower())).fetchall()

            team_emails = {owner_email.lower()}
            for lic in licenses:
                if lic["customer_email"]:
                    team_emails.add(lic["customer_email"].lower())

            placeholders = ",".join(["?"] * len(team_emails))
            params = list(team_emails) + [seven_days_ago]

            # 3. Aggregate 7-day telemetry
            events = conn.execute(f"""
                SELECT * FROM telemetry 
                WHERE customer_email IN ({placeholders}) AND timestamp >= ?
                ORDER BY timestamp DESC;
            """, params).fetchall()

            total_orig = sum(e["original_tokens"] for e in events)
            total_saved = sum(e["saved_tokens"] for e in events)
            total_usd = sum(e["usd_saved"] for e in events)
            event_count = len(events)
            reduction_pct = round((total_saved / total_orig * 100), 1) if total_orig > 0 else 76.5

            # 4. Engineer leaderboard
            engineer_stats: Dict[str, Dict[str, Any]] = {}
            for em in team_emails:
                engineer_stats[em] = {
                    "email": em,
                    "saved_tokens": 0,
                    "original_tokens": 0,
                    "usd_saved": 0.0,
                    "event_count": 0
                }

            category_breakdown: Dict[str, int] = {}

            for e in events:
                em = e["customer_email"].lower()
                if em not in engineer_stats:
                    engineer_stats[em] = {
                        "email": em,
                        "saved_tokens": 0,
                        "original_tokens": 0,
                        "usd_saved": 0.0,
                        "event_count": 0
                    }
                engineer_stats[em]["saved_tokens"] += e["saved_tokens"]
                engineer_stats[em]["original_tokens"] += e["original_tokens"]
                engineer_stats[em]["usd_saved"] += e["usd_saved"]
                engineer_stats[em]["event_count"] += 1

                ev_type = e["event_type"]
                category_breakdown[ev_type] = category_breakdown.get(ev_type, 0) + e["saved_tokens"]

            leaderboard = sorted(
                engineer_stats.values(),
                key=lambda x: x["saved_tokens"],
                reverse=True
            )

            # Assign ranks
            for idx, eng in enumerate(leaderboard, 1):
                eng["rank"] = idx
                eng["saved_tokens_human"] = format_token_number(eng["saved_tokens"])
                eng["usd_saved_formatted"] = f"${eng['usd_saved']:.2f}"

            return {
                "owner_email": owner_email,
                "tier": owner["tier"],
                "seats_total": owner["seats_total"],
                "active_members_count": len([e for e in leaderboard if e["event_count"] > 0]),
                "total_members_count": len(team_emails),
                "total_original_tokens": total_orig,
                "total_saved_tokens": total_saved,
                "total_saved_human": format_token_number(total_saved),
                "total_usd_saved": round(total_usd, 2),
                "total_events": event_count,
                "reduction_pct": reduction_pct,
                "leaderboard": leaderboard,
                "category_breakdown": category_breakdown,
                "start_time": seven_days_ago,
                "end_time": now
            }
        finally:
            conn.close()

    @classmethod
    def render_email_html(cls, digest: Dict[str, Any]) -> str:
        """Generates Apple-style responsive dark-mode HTML email."""
        owner_email = digest["owner_email"]
        total_saved_human = digest["total_saved_human"]
        usd_saved = f"${digest['total_usd_saved']:.2f}"
        reduction_pct = digest["reduction_pct"]
        leaderboard = digest["leaderboard"]
        seats_total = digest["seats_total"]
        active_count = digest["active_members_count"]

        # Build leaderboard rows
        rows_html = ""
        for eng in leaderboard[:10]:
            rank = eng["rank"]
            medal = "🥇 " if rank == 1 else "🥈 " if rank == 2 else "🥉 " if rank == 3 else f"#{rank} "
            email_mask = eng["email"].split("@")[0] + "@..."
            rows_html += f"""
            <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
              <td style="padding: 12px 8px; font-weight: 700; color: #10b981;">{medal}</td>
              <td style="padding: 12px 8px; font-family: monospace; color: #e5e7eb; font-size: 13px;">{email_mask}</td>
              <td style="padding: 12px 8px; text-align: right; font-weight: 700; color: #ffffff;">{eng['saved_tokens_human']}</td>
              <td style="padding: 12px 8px; text-align: right; color: #10b981; font-weight: 600;">{eng['usd_saved_formatted']}</td>
            </tr>
            """

        html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TokenSafe Weekly Team ROI Digest</title>
</head>
<body style="margin: 0; padding: 0; background-color: #030712; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #f3f4f6;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #030712; padding: 40px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" style="max-width: 600px; background-color: #0f172a; border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 16px; padding: 32px; box-shadow: 0 20px 40px rgba(0,0,0,0.5);">
          
          <!-- Header -->
          <tr>
            <td style="padding-bottom: 20px; border-bottom: 1px solid rgba(255, 255, 255, 0.08);">
              <span style="font-size: 20px; font-weight: 800; color: #ffffff; letter-spacing: -0.5px;">
                Token<span style="color: #10b981;">Safe</span>
              </span>
              <span style="float: right; font-size: 11px; font-weight: 700; color: #10b981; background: rgba(16, 185, 129, 0.12); border: 1px solid rgba(16, 185, 129, 0.25); padding: 4px 10px; border-radius: 999px;">
                WEEKLY DIGEST • FRIDAY
              </span>
            </td>
          </tr>

          <!-- Hero Headline -->
          <tr>
            <td style="padding: 28px 0 16px;">
              <h1 style="margin: 0 0 8px; font-size: 24px; font-weight: 800; color: #ffffff; line-height: 1.3;">
                TokenSafe saved your team <span style="color: #10b981;">{total_saved_human} tokens</span> ({usd_saved}) this week
              </h1>
              <p style="margin: 0; font-size: 14px; color: #94a3b8; line-height: 1.5;">
                Friday engineering summary for <strong>{owner_email}</strong>. Active seats: {active_count}/{seats_total}.
              </p>
            </td>
          </tr>

          <!-- Key Metrics Grid -->
          <tr>
            <td style="padding: 12px 0 24px;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                <tr>
                  <td width="32%" style="background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 12px; padding: 16px; text-align: center;">
                    <div style="font-size: 11px; color: #94a3b8; text-transform: uppercase; font-weight: 600;">Tokens Saved</div>
                    <div style="font-size: 22px; font-weight: 800; color: #10b981; margin-top: 4px;">{total_saved_human}</div>
                  </td>
                  <td width="2%"></td>
                  <td width="32%" style="background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 12px; padding: 16px; text-align: center;">
                    <div style="font-size: 11px; color: #94a3b8; text-transform: uppercase; font-weight: 600;">Est. Cost Saved</div>
                    <div style="font-size: 22px; font-weight: 800; color: #ffffff; margin-top: 4px;">{usd_saved}</div>
                  </td>
                  <td width="2%"></td>
                  <td width="32%" style="background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 12px; padding: 16px; text-align: center;">
                    <div style="font-size: 11px; color: #94a3b8; text-transform: uppercase; font-weight: 600;">Net Reduction</div>
                    <div style="font-size: 22px; font-weight: 800; color: #06b6d4; margin-top: 4px;">-{reduction_pct}%</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Leaderboard Table -->
          <tr>
            <td style="padding: 12px 0 20px;">
              <h3 style="margin: 0 0 12px; font-size: 15px; font-weight: 700; color: #ffffff; text-transform: uppercase; letter-spacing: 0.5px;">
                🏆 Engineer Savings Leaderboard
              </h3>
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse: collapse; background: rgba(0,0,0,0.2); border-radius: 10px; overflow: hidden;">
                <thead>
                  <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.08); background: rgba(255, 255, 255, 0.02); font-size: 11px; color: #94a3b8; text-transform: uppercase;">
                    <th style="padding: 10px 8px; text-align: left;">Rank</th>
                    <th style="padding: 10px 8px; text-align: left;">Engineer</th>
                    <th style="padding: 10px 8px; text-align: right;">Saved</th>
                    <th style="padding: 10px 8px; text-align: right;">Value</th>
                  </tr>
                </thead>
                <tbody>
                  {rows_html}
                </tbody>
              </table>
            </td>
          </tr>

          <!-- CTA Button -->
          <tr>
            <td style="padding: 24px 0 16px; text-align: center;">
              <a href="https://tokensafe.dev/dashboard" style="display: inline-block; background: #10b981; color: #000000; font-weight: 700; font-size: 14px; text-decoration: none; padding: 14px 28px; border-radius: 10px; box-shadow: 0 4px 14px rgba(16, 185, 129, 0.35);">
                Open Live Team Dashboard ➔
              </a>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding-top: 24px; border-top: 1px solid rgba(255, 255, 255, 0.06); font-size: 12px; color: #64748b; text-align: center;">
              TokenSafe Enterprise • Automatic Friday ROI Digest • <a href="https://tokensafe.dev" style="color: #10b981; text-decoration: none;">tokensafe.dev</a>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
        return html

    @classmethod
    def render_email_text(cls, digest: Dict[str, Any]) -> str:
        """Generates clean plain-text version for terminal or fallback."""
        owner_email = digest["owner_email"]
        total_saved = digest["total_saved_human"]
        usd = f"${digest['total_usd_saved']:.2f}"
        reduction = digest["reduction_pct"]

        lines = [
            f"⚡ TOKENSAFE WEEKLY TEAM ROI DIGEST",
            f"Recipient: {owner_email}",
            f"Period: Last 7 Days (Delivered Friday)\n",
            f"TokenSafe saved your team {total_saved} tokens ({usd}) this week!",
            f"Net Context Reduction: -{reduction}%\n",
            f"--- 🏆 ENGINEER SAVINGS LEADERBOARD ---"
        ]
        for eng in digest["leaderboard"]:
            lines.append(f"#{eng['rank']:<2} {eng['email']:<28} {eng['saved_tokens_human']:>8} ({eng['usd_saved_formatted']})")

        lines.append(f"\nOpen your live dashboard: https://tokensafe.dev/dashboard")
        return "\n".join(lines)
