import os, re, json, asyncio, ipaddress, time, random
from typing import List
from urllib.parse import urlparse
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

AGENT_NAME = "Link Investigator"

SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "cutt.ly", "rb.gy",
              "is.gd", "shorturl.at", "tiny.cc", "ow.ly", "rebrand.ly"}
RISKY_TLDS = {"xyz", "top", "click", "work", "shop", "live", "icu", "buzz",
              "zip", "mov", "gq", "tk", "ml", "cf", "ga", "rest", "cyou"}
BAIT_WORDS = ["login", "verify", "secure", "kyc", "pay", "upi", "refund",
              "offer", "career", "intern", "job", "reward", "claim", "update"]
BRANDS = ["amazon", "google", "microsoft", "paytm", "phonepe", "gpay", "sbi",
          "hdfc", "icici", "tcs", "infosys", "wipro", "flipkart", "naukri",
          "linkedin", "whatsapp", "instagram"]

def check_url(url: str) -> dict:
    flags = []
    p = urlparse(url if "://" in url else "http://" + url)
    host = (p.hostname or "").lower()
    if p.scheme == "http":
        flags.append("Uses plain HTTP (no encryption)")
    try:
        ipaddress.ip_address(host)
        flags.append("Host is a raw IP address")
    except ValueError:
        pass
    if "@" in url.split("//", 1)[-1].split("/")[0]:
        flags.append("Contains '@' in the authority part (can disguise the real host)")
    if host in SHORTENERS:
        flags.append("URL shortener hides the real destination")
    if "xn--" in host:
        flags.append("Punycode domain (possible lookalike characters)")
    labels = host.split(".")
    tld = labels[-1] if labels else ""
    if tld in RISKY_TLDS:
        flags.append(f"TLD '.{tld}' is frequently abused (not proof of fraud)")
    if len(labels) > 4:
        flags.append("Unusually many subdomains")
    if host.count("-") >= 2:
        flags.append("Multiple hyphens in the domain")
    registered = ".".join(labels[-2:]) if len(labels) >= 2 else host
    for b in BRANDS:
        if b in host and not registered.startswith(b + "."):
            flags.append(f"Mentions brand '{b}' but registered domain is '{registered}'")
            break
    hits = [w for w in BAIT_WORDS if w in host or w in p.path.lower()]
    if hits:
        flags.append("Bait-style words in URL: " + ", ".join(hits[:5]))
    return {"url": url, "host": host, "registered_domain": registered, "flags": flags}

SYSTEM = """You are the Link Investigator in a scam-investigation team.
You receive results of LOCAL heuristic URL checks. No live reputation database,
WHOIS or page-content lookup was performed, and you must not claim otherwise.
Heuristic flags are signals, not proof. Never call a link 'safe' or 'verified'.
Reply with ONLY this JSON:
{"risk_signal":"low|medium|high|unclear","confidence":0.0-1.0,
"summary":"2-3 sentences","evidence":["..."],"limitations":"..."}"""

agent = Agent(
    name="link_investigator",
    seed=os.getenv("AGENT_SEED"),
    port=int(os.getenv("AGENT_PORT", "8002")),
    mailbox=True,
)

@agent.on_event("startup")
async def started(ctx: Context):
    ctx.logger.info(f"{AGENT_NAME} address: {agent.address}")

@agent.on_message(InvestigationRequest)
async def handle(ctx: Context, sender: str, msg: InvestigationRequest):
    ctx.logger.info(f"[{msg.investigation_id}] checking {len(msg.urls)} URL(s)")
    limitation = ("Local heuristics only. No live reputation, WHOIS or page-content "
                  "check was done, so these links are UNVERIFIED.")
    if not msg.urls:
        await ctx.send(sender, Finding(
            investigation_id=msg.investigation_id,
            agent_name=AGENT_NAME,
            risk_signal="unclear",
            confidence=0.2,
            summary="No URLs were found in the message, so nothing was investigated.",
            evidence=[],
            limitations="No links supplied.",
        ))
        return
    try:
        await asyncio.sleep(2)
        results = [check_url(u) for u in msg.urls[:5]]
        raw = await asyncio.to_thread(call_asi1, SYSTEM, json.dumps(results))
        d = parse_json(raw) or {}
        evidence = [str(x) for x in d.get("evidence", [])]
        if not evidence:
            evidence = [f"{r['url']}: {f}" for r in results for f in r["flags"]]
        finding = Finding(
            investigation_id=msg.investigation_id,
            agent_name=AGENT_NAME,
            risk_signal=str(d.get("risk_signal", "unclear")),
            confidence=float(d.get("confidence", 0.3)),
            summary=str(d.get("summary", "Heuristic checks completed.")),
            evidence=evidence,
            limitations=limitation,
        )
    except Exception as e:
        ctx.logger.error(f"error: {e}")
        finding = Finding(
            investigation_id=msg.investigation_id,
            agent_name=AGENT_NAME,
            risk_signal="unclear",
            confidence=0.0,
            summary="Link analysis failed.",
            evidence=[],
            limitations=f"Error: {e}",
        )
    await ctx.send(sender, finding)

if __name__ == "__main__":
    agent.run()