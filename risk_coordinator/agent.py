import os, re, json, time, asyncio, random
from datetime import datetime, timezone
from typing import List, Dict
from uuid import uuid4
import requests
from dotenv import load_dotenv
from uagents import Agent, Context, Model, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    TextContent,
    chat_protocol_spec,
)

load_dotenv()

# ---------- Shared message models (identical in every agent) ----------
class InvestigationRequest(Model):
    investigation_id: str
    message_text: str
    official_source: str
    urls: List[str]

class Finding(Model):
    investigation_id: str
    agent_name: str
    risk_signal: str
    confidence: float
    summary: str
    evidence: List[str]
    limitations: str

class ReviewRequest(Model):
    investigation_id: str
    message_text: str
    findings_json: str

class Challenge(Model):
    investigation_id: str
    agent_name: str
    innocent_explanations: List[str]
    weak_points: List[str]
    missing_evidence: List[str]
    adjustment: str
    summary: str

# ---------- ASI:One (with 429 retry) ----------
def call_asi1(system: str, user: str, max_tokens: int = 1400) -> str:
    for attempt in range(4):
        r = requests.post(
            "https://api.asi1.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {os.getenv('ASI1_API_KEY')}",
                "Content-Type": "application/json",
            },
            json={
                "model": os.getenv("ASI1_MODEL", "asi1-mini"),
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.2,
                "max_tokens": max_tokens,
            },
            timeout=60,
        )
        if r.status_code == 429:
            try:
                wait = float(r.headers.get("Retry-After", ""))
            except ValueError:
                wait = 4 * (attempt + 1)
            time.sleep(min(wait, 10) + random.random() * 2)
            continue
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    raise RuntimeError("ASI1 rate limit (429) persisted after 4 tries")

REPORT_SYSTEM = """You are the Risk Coordinator of ScamSwarm. You receive the original
message, findings from three investigators, and a Devil's Advocate challenge.
Write a clear report for an ordinary user, in this structure:

**Risk assessment:** Low / Medium / High / Inconclusive, with one sentence why.
**Warning signs found:** bullets, each tagged with the agent that found it.
**What the Devil's Advocate challenged:** the innocent explanations and weak points,
and whether they change the assessment.
**What could NOT be verified:** be explicit (e.g. links not checked against live
reputation data, offer claims unverified without an official source).
**Recommended safe next steps:** concrete steps. For India include: do not pay any fee,
do not share OTP/UPI PIN/documents, verify through the company's official website or
careers page typed manually, and if money was lost call 1930 or report at cybercrime.gov.in.

Rules: never state the message is definitely a scam unless the evidence is overwhelming
(e.g. an upfront fee demanded for a job) - even then say 'strong indicators of fraud'.
Never say a link or company has been verified unless an agent actually did so.
If a finding is missing or an agent failed, say so. Keep it under 350 words."""

FOLLOWUP_SYSTEM = """You are the Risk Coordinator of ScamSwarm answering a follow-up question
about a completed investigation. Use ONLY the case data provided. If the user shares new facts
(e.g. they already paid, or they got a reply), adjust the advice accordingly. Never claim
anything was verified that the case data says was unverified. If the user needs a new check
(a new message or link), tell them to paste it as a new message. Keep it under 150 words."""

# ---------- Config ----------
def clean_addr(name: str) -> str:
    v = (os.getenv(name) or "").strip().split()
    return v[0] if v else ""

PATTERN = clean_addr("PATTERN_AGENT_ADDRESS")
LINK = clean_addr("LINK_AGENT_ADDRESS")
OFFER = clean_addr("OFFER_AGENT_ADDRESS")
DEVIL = clean_addr("DEVIL_AGENT_ADDRESS")

URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>\"')]+|\b[\w-]+(?:\.[\w-]+)+/[^\s<>\"')]*", re.I)

PENDING: Dict[str, dict] = {}   # running investigations
CASES: Dict[str, dict] = {}     # last finished case per user (for follow-ups)

agent = Agent(
    name="scamswarm_risk_coordinator",
    seed=os.getenv("AGENT_SEED"),
    port=int(os.getenv("AGENT_PORT", "8005")),
    mailbox=True,
    publish_agent_details=True,
)

chat_proto = Protocol(spec=chat_protocol_spec)

# ---------- Chat helpers ----------
async def say(ctx: Context, to: str, text: str, session=None, end: bool = False):
    """Send a chat message to the user, inside the ORIGINAL chat session."""
    if session is not None:
        ctx._session = session
    content = [TextContent(type="text", text=text)]
    if end:
        content.append(EndSessionContent(type="end-session"))
    try:
        await ctx.send(to, ChatMessage(
            timestamp=datetime.now(timezone.utc), msg_id=uuid4(), content=content))
    except Exception as e:
        ctx.logger.error(f"failed to send chat message: {e}")

def fmt_finding(f: dict) -> str:
    icon = {"high": "🔴", "medium": "🟠", "low": "🟢"}.get(f["risk_signal"], "⚪")
    ev = "\n".join(f"- {e}" for e in f["evidence"][:3])
    return (f"{icon} **{f['agent_name']}**: {f['risk_signal']} "
            f"(confidence {f['confidence']:.0%})\n{f['summary']}\n{ev}\n"
            f"_Limits: {f['limitations']}_")

def fmt_challenge(c: dict) -> str:
    arrow = {"lower": "⬇️ suggests LOWER risk", "raise": "⬆️ suggests HIGHER risk"}.get(
        c["adjustment"], "➡️ keeps the assessment")
    ex = "\n".join(f"- {e}" for e in c["innocent_explanations"][:3])
    wp = "\n".join(f"- {e}" for e in c["weak_points"][:3])
    return (f"😈 **Devil's Advocate** {arrow}\n{c['summary']}\n"
            f"Innocent explanations:\n{ex}\nWeak points:\n{wp}")

async def send_logged(ctx: Context, name: str, dest: str, msg, inv_id: str):
    try:
        status = await ctx.send(dest, msg)
        ctx.logger.info(f"[{inv_id}] -> {name}: {status}")
    except Exception as e:
        ctx.logger.error(f"[{inv_id}] -> {name} FAILED: {e}")

# ---------- Orchestration ----------
async def send_review(ctx: Context, inv_id: str):
    inv = PENDING.get(inv_id)
    if not inv or inv["review_sent"]:
        return
    inv["review_sent"] = True
    await send_logged(ctx, "Devil's Advocate", DEVIL, ReviewRequest(
        investigation_id=inv_id,
        message_text=inv["message"],
        findings_json=json.dumps(inv["findings"], ensure_ascii=False),
    ), inv_id)

async def finalize(ctx: Context, inv_id: str):
    inv = PENDING.get(inv_id)
    if not inv or inv["done"]:
        return
    inv["done"] = True
    payload = {
        "original_message": inv["message"],
        "official_source_supplied": bool(inv["official"].strip()),
        "findings": inv["findings"],
        "devils_advocate": inv["challenge"] or "No challenge received (agent timed out).",
        "agents_missing": [n for n in ("Pattern Detective", "Link Investigator", "Offer Verifier")
                           if n not in inv["findings"]],
    }
    try:
        report = await asyncio.to_thread(
            call_asi1, REPORT_SYSTEM, json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        report = (f"I couldn't generate the final report ({e}).\n\n"
                  "Raw findings:\n" + json.dumps(inv["findings"], indent=2, ensure_ascii=False))
    report += "\n\n💬 You can now ask me follow-up questions about this case, or paste a new message to investigate."
    await say(ctx, inv["user"], "📋 **Final report**\n\n" + report,
              session=inv["session"], end=True)
    CASES[inv["user"]] = {
        "message": inv["message"], "findings": inv["findings"],
        "challenge": inv["challenge"], "report": report,
    }
    PENDING.pop(inv_id, None)

# ---------- Chat entry point ----------
@chat_proto.on_message(ChatMessage)
async def handle_chat(ctx: Context, sender: str, msg: ChatMessage):
    ctx.logger.info(f"chat received from {sender[:16]}...")
    try:
        await _process_chat(ctx, sender, msg)
    except Exception as e:
        ctx.logger.exception("handler crashed")
        await say(ctx, sender, f"⚠️ Coordinator error: {e}", end=True)

async def _process_chat(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(
        timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id))
    text = "".join(c.text for c in msg.content if isinstance(c, TextContent)).strip()
    if not text:
        return
    session = ctx.session

    # Split out optional OFFICIAL: text (works even if everything is on one line)
    parts = re.split(r"(?i)\bOFFICIAL:", text, maxsplit=1)
    message_text = parts[0].strip()
    official = parts[1].strip() if len(parts) > 1 else ""
    for prefix in ("new:", "check:"):
        if message_text.lower().startswith(prefix):
            message_text = message_text[len(prefix):].strip()

    prev = CASES.get(sender)
    is_new = (
        not prev
        or bool(URL_RE.search(message_text))
        or len(message_text) > 200
        or text.lower().startswith(("new:", "check:"))
    )

    # ----- Follow-up question about the previous case -----
    if not is_new:
        try:
            answer = await asyncio.to_thread(
                call_asi1, FOLLOWUP_SYSTEM,
                json.dumps({"case": prev, "user_question": message_text}, ensure_ascii=False),
                600)
        except Exception as e:
            answer = f"Sorry, I couldn't answer that right now ({e})."
        await say(ctx, sender, answer, session=session, end=True)
        return

    # ----- New investigation -----
    missing = [n for n, a in (("PATTERN", PATTERN), ("LINK", LINK),
                              ("OFFER", OFFER), ("DEVIL", DEVIL)) if not a.startswith("agent1")]
    if missing:
        await say(ctx, sender,
                  f"⚠️ Setup problem: address for {', '.join(missing)} is missing or invalid "
                  f"in the coordinator's .env.", session=session, end=True)
        return

    urls = list(dict.fromkeys(URL_RE.findall(message_text)))
    inv_id = uuid4().hex[:8]
    PENDING[inv_id] = {
        "user": sender, "session": session, "message": message_text, "official": official,
        "findings": {}, "challenge": None, "review_sent": False,
        "done": False, "created": time.time(),
    }
    await say(ctx, sender,
              f"🔎 Investigation `{inv_id}` started. Three investigators are working "
              f"separately, then the Devil's Advocate will challenge them. "
              f"Results will arrive in about a minute.", session=session)

    req = InvestigationRequest(
        investigation_id=inv_id, message_text=message_text,
        official_source=official, urls=urls)
    for name, addr in (("Pattern Detective", PATTERN), ("Link Investigator", LINK),
                       ("Offer Verifier", OFFER)):
        await send_logged(ctx, name, addr, req, inv_id)

@chat_proto.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass

# ---------- Helper replies ----------
@agent.on_message(Finding)
async def on_finding(ctx: Context, sender: str, msg: Finding):
    inv = PENDING.get(msg.investigation_id)
    if not inv or inv["done"]:
        return
    inv["findings"][msg.agent_name] = msg.dict()
    ctx.logger.info(f"[{msg.investigation_id}] finding from {msg.agent_name}")
    if len(inv["findings"]) >= 3:
        await send_review(ctx, msg.investigation_id)

@agent.on_message(Challenge)
async def on_challenge(ctx: Context, sender: str, msg: Challenge):
    inv = PENDING.get(msg.investigation_id)
    if not inv or inv["done"]:
        return
    c = msg.dict()
    inv["challenge"] = c
    ctx.logger.info(f"[{msg.investigation_id}] challenge received")
    combined = "\n\n".join(fmt_finding(f) for f in inv["findings"].values())
    combined += "\n\n" + fmt_challenge(c)
    await say(ctx, inv["user"], combined, session=inv["session"])
    await asyncio.sleep(3)
    await finalize(ctx, msg.investigation_id)

@agent.on_interval(period=10.0)
async def watchdog(ctx: Context):
    now = time.time()
    for inv_id, inv in list(PENDING.items()):
        if inv["done"]:
            continue
        age = now - inv["created"]
        if age > 60 and not inv["review_sent"] and inv["findings"]:
            await send_review(ctx, inv_id)
        if age > 120:
            await finalize(ctx, inv_id)

agent.include(chat_proto, publish_manifest=True)

@agent.on_event("startup")
async def started(ctx: Context):
    ctx.logger.info(f"Risk Coordinator address: {agent.address}")
    for n, a in (("PATTERN", PATTERN), ("LINK", LINK), ("OFFER", OFFER), ("DEVIL", DEVIL)):
        ctx.logger.info(f"{n} address loaded: {a[:14]}... "
                        f"{'OK' if a.startswith('agent1') else 'INVALID/MISSING'}")

if __name__ == "__main__":
    agent.run()