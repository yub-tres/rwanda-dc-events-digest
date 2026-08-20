"""
DC Events Weekly Digest — Rwanda Embassy Trade & Economic Affairs
---------------------------------------------------------------
Runs every Monday morning. Uses Claude with LIVE web search (not
just training knowledge) to find real upcoming events in the next
7 days from DC think tanks, embassies, trade organizations, and
US government agencies — anything relevant to US-Africa relations,
trade, or economic diplomacy.

Sends one email with the week's events, each with a one-click
"Add to Google Calendar" link. Nothing is saved to Airtable —
this is a fresh list every week, not a growing database.
"""

import os
import json
import logging
import smtplib
import urllib.parse
from datetime import datetime, date, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from anthropic import Anthropic

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── CONFIG ────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
EMAIL_FROM        = os.environ["EMAIL_FROM"]
EMAIL_PASSWORD    = os.environ["EMAIL_PASSWORD"]
EMAIL_TO          = os.environ["EMAIL_TO"]

# ── DISCOVERY PROMPT ──────────────────────────────────────────────────────────
DISCOVERY_PROMPT = """You are a research assistant for the Economic Affairs section of
the Embassy of the Republic of Rwanda in Washington, DC. Use web search to find REAL,
CURRENTLY SCHEDULED public events happening in the Washington DC area in the next 7 days
(from {today} to {week_end}), relevant to trade, economic diplomacy, or US-Africa relations.

Search these types of sources specifically:
- DC think tanks: Brookings Institution, Atlantic Council (especially Africa Center),
  CSIS, Wilson Center (Africa Program)
- Other countries' embassies in DC hosting public events (trade, business, cultural
  events with an economic/diplomatic angle)
- US Chamber of Commerce, Corporate Council on Africa, BCIU (Business Council for
  International Understanding), or similar trade/business organizations
- US government public events: State Department, USAID, Department of Commerce
  international trade events

RULES:
1. Only include REAL events you actually found via search, with a real source URL
2. Only include events happening between {today} and {week_end}
3. Do not invent or guess at events — if you're not confident it's real and
   currently scheduled, leave it out
4. Prioritize events relevant to trade, Africa, or economic diplomacy specifically
   over generic unrelated events, even at the listed organizations
5. If you find fewer than 3 genuinely real events, that's fine — report only
   what you actually verified

For each event, note the exact date and time if available (Eastern Time), and
whether it's in-person (with address) or virtual.

Respond with ONLY a valid JSON array, no other text before or after:

[
  {{
    "title": "Exact event name",
    "organization": "Hosting organization",
    "date": "YYYY-MM-DD",
    "start_time": "HH:MM" or null if unknown,
    "end_time": "HH:MM" or null if unknown,
    "location": "Address, or 'Virtual' if online",
    "description": "1-2 sentence description of the event",
    "relevance": "1 sentence on why this matters for Rwanda/US-Africa economic relations",
    "source_url": "Direct URL to the event page"
  }}
]"""


def generate_calendar_link(event: dict) -> str:
    """Builds a Google Calendar 'add event' link — no API needed, just a URL."""
    title = event.get("title", "Event")
    location = event.get("location", "")
    details = f"{event.get('description', '')}\n\nSource: {event.get('source_url', '')}"

    date_str = event.get("date", "")
    start_time = event.get("start_time")
    end_time = event.get("end_time")

    try:
        if start_time:
            start_dt = datetime.strptime(f"{date_str} {start_time}", "%Y-%m-%d %H:%M")
            end_dt = (datetime.strptime(f"{date_str} {end_time}", "%Y-%m-%d %H:%M")
                      if end_time else start_dt + timedelta(hours=1))
            dates_param = f"{start_dt.strftime('%Y%m%dT%H%M%S')}/{end_dt.strftime('%Y%m%dT%H%M%S')}"
        else:
            # All-day event fallback — no time given
            start_dt = datetime.strptime(date_str, "%Y-%m-%d")
            end_dt = start_dt + timedelta(days=1)
            dates_param = f"{start_dt.strftime('%Y%m%d')}/{end_dt.strftime('%Y%m%d')}"
    except (ValueError, TypeError):
        return ""  # if dates are malformed, skip the calendar link rather than break

    params = {
        "action": "TEMPLATE",
        "text": title,
        "dates": dates_param,
        "details": details,
        "location": location,
    }
    return "https://calendar.google.com/calendar/render?" + urllib.parse.urlencode(params)


def find_events(client: Anthropic, today: date, week_end: date) -> list:
    log.info("Asking Claude to search for upcoming DC events...")
    prompt = DISCOVERY_PROMPT.format(today=today.isoformat(), week_end=week_end.isoformat())

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}],
            messages=[{"role": "user", "content": prompt}]
        )

        # Response contains multiple block types (search calls, search results, text).
        # We only want the final text block(s) for parsing.
        text_blocks = [b.text for b in message.content if b.type == "text"]
        text = "\n".join(text_blocks).strip()

        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.split("```")[0].strip()

        events = json.loads(text)
        log.info(f"Found {len(events)} event(s)")
        return events

    except json.JSONDecodeError as e:
        log.error(f"JSON parse error: {e}")
        return []
    except Exception as e:
        log.error(f"Claude API error: {e}")
        return []


def send_email(events: list, today: date, week_end: date):
    count = len(events)
    subject = f"DC Events This Week — {count} relevant event{'s' if count != 1 else ''} ({today.strftime('%b %d')}–{week_end.strftime('%b %d')})"

    def event_block(e):
        cal_link = generate_calendar_link(e)
        time_str = e.get("start_time") or "Time TBD"
        cal_button = (
            f'<a href="{cal_link}" style="display:inline-block; margin-top:8px; '
            f'padding:6px 14px; background:#1a4d8f; color:#fff; text-decoration:none; '
            f'border-radius:5px; font-size:13px;">+ Add to Calendar</a>'
            if cal_link else ""
        )
        return f"""
        <li style="margin-bottom:22px; padding-bottom:18px; border-bottom:1px solid #eee;">
          <div style="font-size:17px; font-weight:600;">{e.get('title','Untitled Event')}</div>
          <div style="color:#666; font-size:14px; margin-top:2px;">
            {e.get('organization','')} &middot; {e.get('date','')} at {time_str} &middot; {e.get('location','')}
          </div>
          <div style="font-size:14px; margin-top:8px;">{e.get('description','')}</div>
          <div style="font-size:13px; color:#1a4d8f; margin-top:6px; font-style:italic;">
            Why it matters: {e.get('relevance','')}
          </div>
          <div style="margin-top:4px;">
            <a href="{e.get('source_url','')}" style="font-size:13px; color:#888;">Source →</a>
          </div>
          {cal_button}
        </li>"""

    events_html = "".join(event_block(e) for e in events) if events else \
        '<li style="color:#666;">No verified relevant events found for this week.</li>'

    body_html = f"""\
<html>
<head>
<meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=EB+Garamond:wght@400;600&display=swap" rel="stylesheet">
</head>
<body style="margin:0; padding:0; background-color:#f7f5f0;">
  <div style="font-family:'EB Garamond', Georgia, 'Times New Roman', serif; max-width:600px; margin:0 auto; padding:32px 24px; background-color:#ffffff; color:#222;">

    <h1 style="font-size:22px; font-weight:600; margin-bottom:4px;">
      DC Events This Week
    </h1>
    <p style="color:#888; font-size:14px; margin-top:0;">
      {today.strftime('%B %d')} – {week_end.strftime('%B %d, %Y')} &middot; Trade &amp; Economic Affairs
    </p>

    <ul style="list-style:none; padding:0; margin:24px 0 0;">
      {events_html}
    </ul>

    <p style="font-size:13px; color:#aaa; margin-top:28px; padding-top:16px; border-top:1px solid #eee;">
      Rwanda Embassy — Economic Affairs<br>
      Events found via live web search; always verify details before attending.
    </p>

  </div>
</body>
</html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO
        msg["Subject"] = subject
        msg.attach(MIMEText(body_html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        log.info(f"Email sent to {EMAIL_TO}")
    except Exception as e:
        log.error(f"Email error: {e}")


def run_digest():
    log.info("=" * 60)
    log.info("DC EVENTS WEEKLY DIGEST — STARTING")
    log.info("=" * 60)

    today = date.today()
    week_end = today + timedelta(days=7)

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    events = find_events(client, today, week_end)
    send_email(events, today, week_end)

    log.info("=" * 60)
    log.info(f"DIGEST COMPLETE — {len(events)} event(s) sent")
    log.info("=" * 60)


if __name__ == "__main__":
    run_digest()
