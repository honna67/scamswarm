import os, re, json, asyncio, time, random
from typing import List
import requests
from dotenv import load_dotenv
from uagents import Agent, Context, Model

load_dotenv()

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

def call_asi1(system: str, user: str, max_tokens: int = 900) -> str:
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
                "temperature": 0.3,
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

AGENT_NAME = "Devil's Advocate"

SYSTEM = """You are the Devil's Advocate in a scam-investigation team.
You receive the original message and the findings of three other agents.
Your job is to CHALLENGE them honestly:
- Give plausible innocent explanations for each suspicious signal.
- Point out weak, circular or over-confident reasoning in the findings.
- List evidence that is missing and would change the conclusion.
- Also call out if the others are being too lenient and ignoring a strong signal.
Do not argue for the sake of it: if the evidence is strong (e.g. upfront fee for a job),
say so and recommend 'keep' or 'raise'.
Reply with ONLY this JSON:
{"innocent_explanations":["..."],"weak_points":["..."],"missing_evidence":["..."],
"adjustment":"lower|keep|raise","summary":"2-3 sentences"}"""

agent = Agent(
    name="devils_advocate",
    seed=os.getenv("AGENT_SEED"),
    port=int(os.getenv("AGENT_PORT", "8004")),
    mailbox=True,
)

@agent.on_event("startup")
async def started(ctx: Context):
    ctx.logger.info(f"{AGENT_NAME} address: {agent.address}")

@agent.on_message(ReviewRequest)
async def handle(ctx: Context, sender: str, msg: ReviewRequest):
    ctx.logger.info(f"[{msg.investigation_id}] challenging findings")
    prompt = f"ORIGINAL MESSAGE:\n{msg.message_text}\n\nFINDINGS:\n{msg.findings_json}"
    try:
        raw = await asyncio.to_thread(call_asi1, SYSTEM, prompt)
        d = parse_json(raw) or {}
        adj = str(d.get("adjustment", "keep")).lower()
        if adj not in ("lower", "keep", "raise"):
            adj = "keep"
        reply = Challenge(
            investigation_id=msg.investigation_id,
            agent_name=AGENT_NAME,
            innocent_explanations=[str(x) for x in d.get("innocent_explanations", [])],
            weak_points=[str(x) for x in d.get("weak_points", [])],
            missing_evidence=[str(x) for x in d.get("missing_evidence", [])],
            adjustment=adj,
            summary=str(d.get("summary", "Could not parse model output.")),
        )
    except Exception as e:
        ctx.logger.error(f"error: {e}")
        reply = Challenge(
            investigation_id=msg.investigation_id,
            agent_name=AGENT_NAME,
            innocent_explanations=[],
            weak_points=[],
            missing_evidence=[],
            adjustment="keep",
            summary=f"Challenge step failed: {e}",
        )
    await ctx.send(sender, reply)

if __name__ == "__main__":
    agent.run()