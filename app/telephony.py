"""Optional: real phone calls through Twilio (same agent brain as the web demo).

Setup: buy/verify a Twilio number -> Voice webhook = https://<ngrok-url>/twilio/voice (POST).
Twilio does STT via <Gather input="speech"> and TTS via <Say>, so no extra keys are needed.
For production low-latency calls, replace this with Twilio Media Streams (WebSocket audio)
+ streaming STT (Deepgram) + streaming TTS (see docs/ARCHITECTURE.md).
"""
from xml.sax.saxutils import escape

from fastapi import APIRouter, Form
from fastapi.responses import Response

from . import agent, db

router = APIRouter(prefix="/twilio", tags=["telephony"])
TWILIO_LANG = {"en": ("en-IN", "Polly.Aditi"), "hi": ("hi-IN", "Polly.Aditi")}


def _twiml(say: str, language="en", hangup=False):
    lang, voice = TWILIO_LANG.get(language, TWILIO_LANG["en"])
    say_xml = f'<Say voice="{voice}" language="{lang}">{escape(say)}</Say>'
    if hangup:
        body = f"{say_xml}<Hangup/>"
    else:
        body = (f'<Gather input="speech" action="/twilio/gather" method="POST" speechTimeout="auto" '
                f'language="{lang}">{say_xml}</Gather><Redirect method="POST">/twilio/gather</Redirect>')
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>', media_type="application/xml")


@router.post("/voice")
def incoming_call(CallSid: str = Form(...), From: str = Form(None)):
    started = agent.start_call("en", phone=From, channel="phone", external_id=CallSid)
    return _twiml(started["greeting"])


@router.post("/gather")
def gather(CallSid: str = Form(...), SpeechResult: str = Form(None)):
    call = db.get_call_by_external(CallSid)
    if not call:
        return _twiml("Sorry, something went wrong. Goodbye.", hangup=True)
    if not SpeechResult:
        return _twiml("Sorry, I didn't catch that. Could you repeat?", call["language"])
    result = agent.handle_turn(call["id"], SpeechResult)
    if result["end_call"]:
        agent.end_call(call["id"])
        return _twiml(result["reply"], call["language"], hangup=True)
    return _twiml(result["reply"], call["language"])
