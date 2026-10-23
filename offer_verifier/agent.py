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

def call_asi1(system: str, user: str, max_tokens: int = 800) -> str:
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

AGENT_NAME = "Offer Verifier"
FREE_MAIL = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com",
             "rediffmail.com", "proton.me", "protonmail.com"}

SYSTEM = """You are the Offer Verifier in a scam-investigation team.
You get a job/internship message and, possibly, OFFICIAL SOURCE TEXT supplied by the user.
Tasks:
1. Extract the claims (company, role, stipend/salary, fees, contact channel, process).
2. If official source text is supplied, compare each claim against it and say
   matches / contradicts / not mentioned.
3. If NO official source is supplied, you cannot verify anything: mark every claim
   'unverified'. Never use outside knowledge to assert what a company does or doesn't do.
4. Flag internal inconsistencies (e.g. high pay but upfront fee, free-mail sender for a
   large company, no interview, fee called 'refundable').
Do not declare the offer definitely fake.
Reply with ONLY this JSON:
{"risk_signal":"low|medium|high|unclear","confidence":0.0-1.0,
"summary":"2-3 sentences","evidence":["..."],"limitations":"..."}"""

agent = Agent(
    name="offer_verifier",
    seed=os.getenv("AGENT_SEED"),
    port=int(os.getenv("AGENT_PORT", "8003")),
    mailbox=True,
)

@agent.on_event("startup")
async def started(ctx: Context):
    ctx.logger.info(f"{AGENT_NAME} address: {agent.address}")

@agent.on_message(InvestigationRequest)
async def handle(ctx: Context, sender: str, msg: InvestigationRequest):
    ctx.logger.info(f"[{msg.investigation_id}] verifying offer claims")
    await asyncio.sleep(4)
    has_source = bool(msg.official_source.strip())
    emails = re.findall(r"[\w.+-]+@([\w-]+\.[\w.-]+)", msg.message_text)
    hints = [f"Sender/contact email uses free-mail domain: {d}"
             for d in emails if d.lower() in FREE_MAIL]
    prompt = (
        f"MESSAGE:\n{msg.message_text}\n\n"
        f"OFFICIAL SOURCE TEXT:\n{msg.official_source if has_source else 'NONE SUPPLIED'}\n\n"
        f"HEURISTIC HINTS: {hints or 'none'}"
    )
    limitation = ("Compared against user-supplied official text only."
                  if has_source else
                  "No official source was supplied, so company/offer claims are UNVERIFIED.")
    try:
        raw = await asyncio.to_thread(call_asi1, SYSTEM, prompt)
        d = parse_json(raw) or {}
        finding = Finding(
            investigation_id=msg.investigation_id,
            agent_name=AGENT_NAME,
            risk_signal=str(d.get("risk_signal", "unclear")),
            confidence=float(d.get("confidence", 0.3)),
            summary=str(d.get("summary", "Could not parse model output.")),
            evidence=[str(x) for x in d.get("evidence", [])] + hints,
            limitations=limitation + " " + str(d.get("limitations", "")),
        )
    except Exception as e:
        ctx.logger.error(f"error: {e}")
        finding = Finding(
            investigation_id=msg.investigation_id,
            agent_name=AGENT_NAME,
            risk_signal="unclear",
            confidence=0.0,
            summary="Offer verification failed.",
            evidence=[],
            limitations=f"Error: {e}",
        )
    await ctx.send(sender, finding)

if __name__ == "__main__":
    agent.run()