import os, re, json, asyncio, time, random
from typing import List
import requests
from dotenv import load_dotenv
from uagents import Agent, Context, Model

load_dotenv()

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

def call_asi1(system: str, user: str, max_tokens: int = 700) -> str:
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

def parse_json(text: str):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None

AGENT_NAME = "Pattern Detective"

SYSTEM = """You are the Pattern Detective in a scam-investigation team.
Analyse ONLY the wording of the message. Look for: urgency or pressure tactics,
impersonation of companies/officials, requests for money/fees/OTP/UPI PIN/personal
documents, too-good-to-be-true pay, vague roles, poor grammar patterns, and
off-platform contact requests (WhatsApp/Telegram).
Rules: do not declare the message definitely fraudulent. You only see text, so say
what you could not check. Quote short phrases from the message as evidence.
Reply with ONLY this JSON:
{"risk_signal":"low|medium|high|unclear","confidence":0.0-1.0,
"summary":"2-3 sentences","evidence":["..."],"limitations":"..."}"""

agent = Agent(
    name="pattern_detective",
    seed=os.getenv("AGENT_SEED"),
    port=int(os.getenv("AGENT_PORT", "8001")),
    mailbox=True,
)

@agent.on_event("startup")
async def started(ctx: Context):
    ctx.logger.info(f"{AGENT_NAME} address: {agent.address}")

@agent.on_message(InvestigationRequest)
async def handle(ctx: Context, sender: str, msg: InvestigationRequest):
    ctx.logger.info(f"[{msg.investigation_id}] analysing wording")
    try:
        raw = await asyncio.to_thread(call_asi1, SYSTEM, msg.message_text)
        d = parse_json(raw) or {}
        finding = Finding(
            investigation_id=msg.investigation_id,
            agent_name=AGENT_NAME,
            risk_signal=str(d.get("risk_signal", "unclear")),
            confidence=float(d.get("confidence", 0.3)),
            summary=str(d.get("summary", "Could not parse model output.")),
            evidence=[str(x) for x in d.get("evidence", [])],
            limitations=str(d.get("limitations", "Text-only analysis.")),
        )
    except Exception as e:
        ctx.logger.error(f"error: {e}")
        finding = Finding(
            investigation_id=msg.investigation_id,
            agent_name=AGENT_NAME,
            risk_signal="unclear",
            confidence=0.0,
            summary="Analysis failed.",
            evidence=[],
            limitations=f"Error: {e}",
        )
    await ctx.send(sender, finding)

if __name__ == "__main__":
    agent.run()