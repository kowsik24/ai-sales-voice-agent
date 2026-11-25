"""Lead qualification (BANT-style) and lightweight sentiment.

The score is deliberately rule-based and explainable: a sales manager can see exactly
why a lead is Hot/Warm/Cold. The LLM extracts the facts; Python decides the score.
"""
import re

POSITIVE = {"great", "good", "nice", "interested", "love", "perfect", "yes", "sure", "awesome", "excellent",
            "happy", "like", "wonderful", "definitely", "okay", "ok", "thanks", "thank"}
NEGATIVE = {"expensive", "costly", "not", "bad", "busy", "later", "worried", "problem", "never",
            "annoying", "stop", "waste", "too", "high", "angry", "disappointed", "don't", "dont"}


def lexicon_sentiment(text: str) -> float:
    words = re.findall(r"[a-z']+", text.lower())
    if not words:
        return 0.0
    pos = sum(w in POSITIVE for w in words)
    neg = sum(w in NEGATIVE for w in words)
    if pos == neg == 0:
        return 0.0
    return round((pos - neg) / (pos + neg), 2)


def sentiment_label(score: float) -> str:
    return "positive" if score > 0.2 else "negative" if score < -0.2 else "neutral"


def score_lead(lead: dict, has_appointment: bool, sentiment_avg: float):
    """Returns (score 0-100, category, breakdown list) so the UI can explain the score."""
    breakdown = []

    def add(points, reason):
        breakdown.append({"points": points, "reason": reason})

    if lead.get("budget"):
        add(20, "Budget shared")
    if lead.get("need"):
        add(15, "Requirement identified")
    months = lead.get("timeline_months")
    if months is not None:
        if months <= 3:
            add(25, "Buying within 3 months")
        elif months <= 6:
            add(15, "Buying within 6 months")
        else:
            add(5, "Long-term buyer")
    elif lead.get("timeline"):
        add(10, "Timeline mentioned")
    authority = str(lead.get("authority") or "").lower()
    if authority and not re.search(r"\b(not|no|unknown|someone else)\b", authority):
        add(10, "Decision maker involved")
    if lead.get("phone") or lead.get("email"):
        add(10, "Contact details captured")
    if has_appointment:
        add(20, "Site visit booked")
    if sentiment_avg > 0.2:
        add(10, "Positive sentiment")
    elif sentiment_avg < -0.2:
        add(-10, "Negative sentiment")

    score = max(0, min(100, sum(b["points"] for b in breakdown)))
    category = "Hot" if score >= 70 else "Warm" if score >= 40 else "Cold"
    return score, category, breakdown
