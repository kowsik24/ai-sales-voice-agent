"""Appointment (site visit) scheduling: slot generation, validation, booking."""
from datetime import datetime, timedelta

from . import db
from .config import BOOKING_DAYS_AHEAD, BUSINESS_HOURS


def available_slots(limit=30):
    taken = db.booked_slots()
    now = datetime.now()
    slots = []
    for d in range(1, BOOKING_DAYS_AHEAD + 1):           # from tomorrow
        day = (now + timedelta(days=d)).date()
        if day.weekday() == 1:                              # office closed on Tuesdays (demo rule)
            continue
        for h in BUSINESS_HOURS:
            s = datetime(day.year, day.month, day.day, h).strftime("%Y-%m-%d %H:%M")
            if s not in taken:
                slots.append(s)
    return slots[:limit]


def pretty(slot: str) -> str:
    dt = datetime.strptime(slot, "%Y-%m-%d %H:%M")
    return dt.strftime("%A %d %b, %I:%M %p").replace(" 0", " ")


def normalise(slot: str):
    """Accept a few formats the LLM might produce and return 'YYYY-MM-DD HH:MM' or None."""
    if not slot:
        return None
    slot = slot.strip().replace("T", " ")[:16]
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H"):
        try:
            return datetime.strptime(slot, fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return None


def book(lead_id, call_id, slot: str, kind="Site visit"):
    """Returns (ok, slot_or_alternatives)."""
    slot = normalise(slot)
    free = available_slots(limit=100)
    if slot and slot in free:
        if db.create_appointment(lead_id, call_id, slot, kind):
            return True, slot
    return False, free[:3]