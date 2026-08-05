"""
Export tracked requests as an .ics file, so statutory deadlines show up in
whatever calendar app the user already checks, not just this tracker screen.
"""
from datetime import datetime, timedelta

from icalendar import Calendar, Event


def build_ics(requests: list[dict]) -> bytes:
    """Build an .ics calendar with one all-day event per open deadline.

    Completed requests are skipped — there's nothing left to follow up on.
    """
    cal = Calendar()
    cal.add("prodid", "-//Non-Pursuit//non-pursuit.local//EN")
    cal.add("version", "2.0")

    for r in requests:
        if r["status"] == "Complete":
            continue

        deadline = datetime.strptime(r["deadline"], "%Y-%m-%d").date()
        event = Event()
        event.add("summary", f"Follow up: {r['broker_name']} deletion request")
        event.add("dtstart", deadline)
        event.add("dtend", deadline + timedelta(days=1))
        event.add(
            "description",
            f"{r['channel']} request sent {r['date_sent']} — status: {r['status']}",
        )
        event.add("uid", f"non-pursuit-request-{r['id']}@non-pursuit.local")
        cal.add_component(event)

    return cal.to_ical()
