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
import html as _html
import time
import json
import sqlite3
from contextlib import contextmanager
from typing import Dict, List, Any, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("TOKENSAFE_DATA_DIR", os.path.join(PROJECT_ROOT, "data"))
CLOUD_DB_PATH = os.environ.get("TOKENSAFE_CLOUD_DB_PATH", os.path.join(DATA_DIR, "cloud.db"))


@contextmanager
def _open_digest_db(db_path: str):
    """Yields BunnyDB/cloud connection for default DB path, or local sqlite3 for custom paths."""
    if db_path == CLOUD_DB_PATH:
        try:
            from src.cloud.db import get_db_connection
            from src.cloud.config import BUNNY_DB_URL, BUNNY_DB_TOKEN, CLOUD_DB_PATH as CLOUD_CFG_DB_PATH

            if (BUNNY_DB_URL and BUNNY_DB_TOKEN) or os.path.exists(CLOUD_CFG_DB_PATH):
                with get_db_connection() as conn:
                    yield conn
                return
            yield None
            return
        except ImportError:
            pass

    if not os.path.exists(db_path):
        yield None
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

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
        db_path: str = CLOUD_DB_PATH,
        allow_any_tier: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves 7-day telemetry for team owned by owner_email.
        Supports both BunnyDB (cloud) and local SQLite databases.
        Returns aggregated digest data or None if no eligible records found.
        """
        clean_email = (owner_email or "").strip().lower()
        if not clean_email or "@" not in clean_email:
            return None

        now = now or int(time.time())
        seven_days_ago = now - (7 * 86400)

        with _open_digest_db(db_path) as conn:
            if conn is None:
                return None

            # 1. Verify owner and active team/enterprise entitlement across customers & organizations
            owner = conn.execute(
                "SELECT * FROM customers WHERE email = ?;",
                (clean_email,),
            ).fetchone()

            orgs = []
            try:
                orgs = conn.execute(
                    "SELECT * FROM organizations WHERE owner_email = ? AND status = 'active' ORDER BY is_primary DESC, created_at ASC;",
                    (clean_email,),
                ).fetchall()
            except sqlite3.OperationalError:
                orgs = []

            cust_active = bool(owner and str(owner["status"] or "").lower() == "active")
            cust_tier = str(owner["tier"] or "").lower() if owner else ""
            team_orgs = [o for o in orgs if str(o["tier"] or "").lower() in ("team", "enterprise")]

            if team_orgs:
                effective_tier = str(team_orgs[0]["tier"] or "team").lower()
                effective_seats = sum(int(o["seats_total"] or 1) for o in team_orgs)
            elif cust_active and cust_tier in ("team", "enterprise"):
                effective_tier = cust_tier
                effective_seats = int(owner["seats_total"] or 1)
            elif allow_any_tier and (cust_active or orgs):
                if orgs:
                    effective_tier = str(orgs[0]["tier"] or cust_tier or "solo").lower()
                    effective_seats = int(orgs[0]["seats_total"] or 1)
                else:
                    effective_tier = cust_tier or "solo"
                    effective_seats = int(owner["seats_total"] or 1)
            else:
                return None

            # 2. Find all member emails and license keys in this team / owned organizations
            licenses = conn.execute(
                "SELECT * FROM licenses WHERE parent_email = ? OR customer_email = ?;",
                (clean_email, clean_email),
            ).fetchall()

            team_emails = {clean_email}
            for lic in licenses:
                if lic["customer_email"]:
                    team_emails.add(str(lic["customer_email"]).strip().lower())

            org_ids = [o["id"] for o in (team_orgs if team_orgs else orgs) if o["id"]]
            if org_ids:
                org_placeholders = ",".join(["?"] * len(org_ids))
                org_lics = conn.execute(
                    f"SELECT customer_email FROM licenses WHERE org_id IN ({org_placeholders});",
                    org_ids,
                ).fetchall()
                for lic in org_lics:
                    if lic["customer_email"]:
                        team_emails.add(str(lic["customer_email"]).strip().lower())

            placeholders = ",".join(["?"] * len(team_emails))
            params = list(team_emails) + [seven_days_ago]

            # 3. Aggregate 7-day telemetry
            events = conn.execute(
                f"""
                SELECT * FROM telemetry
                WHERE customer_email IN ({placeholders}) AND timestamp >= ?
                ORDER BY timestamp DESC;
                """,
                params,
            ).fetchall()

            total_orig = sum(int(e["original_tokens"] or 0) for e in events)
            total_saved = sum(int(e["saved_tokens"] or 0) for e in events)
            total_usd = sum(float(e["usd_saved"] or 0.0) for e in events)
            event_count = len(events)
            reduction_pct = round((total_saved / total_orig * 100), 1) if total_orig > 0 else 0.0

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
                em = str(e["customer_email"] or clean_email).strip().lower()
                if em not in engineer_stats:
                    engineer_stats[em] = {
                        "email": em,
                        "saved_tokens": 0,
                        "original_tokens": 0,
                        "usd_saved": 0.0,
                        "event_count": 0
                    }
                saved_toks = int(e["saved_tokens"] or 0)
                orig_toks = int(e["original_tokens"] or 0)
                usd_val = float(e["usd_saved"] or 0.0)
                engineer_stats[em]["saved_tokens"] += saved_toks
                engineer_stats[em]["original_tokens"] += orig_toks
                engineer_stats[em]["usd_saved"] += usd_val
                engineer_stats[em]["event_count"] += 1

                ev_type = str(e["event_type"] or "PROMPT")
                category_breakdown[ev_type] = category_breakdown.get(ev_type, 0) + saved_toks

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
                "owner_email": clean_email,
                "tier": effective_tier,
                "seats_total": effective_seats,
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

    @classmethod
    def render_email_html(cls, digest: Dict[str, Any]) -> str:
        """Generates Apple-style responsive dark-mode HTML email."""
        owner_email = _html.escape(str(digest["owner_email"]))
        total_saved_human = _html.escape(str(digest["total_saved_human"]))
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
            email_mask = _html.escape(eng["email"].split("@")[0] + "@...")
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
