# 🕵️ ScamSwarm

**A team of AI agents that investigates suspicious messages before you lose money.**

People receive fake job offers, internship messages, payment requests, shopping links and UPI scams every day. A single AI guessing "scam or not" is easy to fool and hard to trust. ScamSwarm has **five agents investigate from different angles, challenge each other, and report what they could and could not verify.**

## How it works

```
User ──► Risk Coordinator ──┬──► Pattern Detective ──┐
        (chat on ASI:One)   ├──► Link Investigator ──┼──► findings
                            └──► Offer Verifier ─────┘       │
                                                             ▼
                              final report ◄── Risk Coordinator ◄── Devil's Advocate
```

| Agent | Role |
|---|---|
| **Pattern Detective** | Finds suspicious wording: urgency, impersonation, fee and payment requests, off-platform contact |
| **Link Investigator** | Runs local heuristic checks on URLs: lookalike brands, risky TLDs, shorteners, plain HTTP, bait words |
| **Offer Verifier** | Compares job or internship claims against official source text you supply, and flags inconsistencies |
| **Devil's Advocate** | Challenges the other agents: innocent explanations, weak reasoning, missing evidence |
| **Risk Coordinator** | Orchestrates the team, writes the final report, answers follow-up questions |

## Honest by design

- It **never declares a message definitely fraudulent** without overwhelming evidence. It says "strong indicators of fraud".
- The Link Investigator uses **local heuristics only**. No live reputation, WHOIS or page-content lookup is done, and every report says the links are **unverified**.
- The Offer Verifier checks claims **only against official text you provide**. Without it, every claim is marked unverified.
- Missing or failed agents are reported in the final report instead of being hidden.

## Tech stack

- [uAgents](https://github.com/fetchai/uAgents): agent framework and messaging
- [Agentverse](https://agentverse.ai): mailbox connection and agent hosting
- [ASI:One](https://asi1.ai): LLM reasoning and chat interface

## Project structure

```
scamswarm/
├── requirements.txt
├── README.md
├── LICENSE
├── .gitignore
├── pattern_detective/    agent.py  .env
├── link_investigator/    agent.py  .env
├── offer_verifier/       agent.py  .env
├── devils_advocate/      agent.py  .env
└── risk_coordinator/     agent.py  .env
```

## Setup

**Requirements:** Python 3.10+ and an ASI:One API key.

```bash
pip install -r requirements.txt
```

Create a `.env` in each agent folder:

```
AGENT_SEED=<a different long random phrase for each agent>
AGENT_PORT=<8001 to 8005, unique per agent>
ASI1_API_KEY=<your ASI:One API key>
ASI1_MODEL=asi1-mini
```

The Risk Coordinator's `.env` also needs the four helper addresses:

```
PATTERN_AGENT_ADDRESS=agent1q...
LINK_AGENT_ADDRESS=agent1q...
OFFER_AGENT_ADDRESS=agent1q...
DEVIL_AGENT_ADDRESS=agent1q...
```

> ⚠️ Never commit `.env` files or share your seeds and API key.

## Running

Start the four helpers first (one terminal each), then the coordinator last:

```bash
cd pattern_detective && python agent.py
cd link_investigator && python agent.py
cd offer_verifier && python agent.py
cd devils_advocate && python agent.py
cd risk_coordinator && python agent.py
```

1. Each agent prints its address and an **Agent Inspector** link. Open it and choose **Connect → Mailbox**.
2. Copy each helper's printed address into the coordinator's `.env`, then start the coordinator.
3. Open the Risk Coordinator on Agentverse (**Chat with Agent**) or on ASI:One.

## Usage

Paste a suspicious message. Optionally add `OFFICIAL:` followed by text from the real company's website, so the Offer Verifier has something to compare against.

```
Congratulations! You are selected for a Data Analyst Internship at TechNova Solutions.
Stipend: ₹40,000 per month. No interview needed.
Pay a one-time registration fee of ₹999 (refundable) to confirm your seat within 2 hours:
http://technova-careers.xyz/pay-now
Contact HR on WhatsApp: +91 98XXXXXX01 or hr.technova@gmail.com
OFFICIAL: TechNova's careers page lists no open internships and states that it never charges candidates any fee.
```

You get the three investigator findings and the Devil's Advocate challenge in one update, followed by the final report. Afterwards you can ask follow-up questions such as *"What if I already paid the ₹999?"*. To start a new case with a short message, begin it with `new:`.

## Limitations

- Link checks are heuristic, not a reputation lookup.
- Results come from a language model and can be wrong. Treat them as guidance, not proof.
- One challenge round only; investigators don't respond to the Devil's Advocate.
- The chat and API services are rate limited, so the agents retry and the coordinator sends only a few chat messages per case.
- Not a substitute for official channels. If you lost money in India, call **1930** or report at **cybercrime.gov.in**.

## Roadmap

- Live URL reputation and domain-age lookups
- A second round where investigators respond to the Devil's Advocate
- Support for screenshots and voice notes

## License

MIT. See [LICENSE](LICENSE).

## Disclaimer

ScamSwarm provides automated, informational analysis only. It cannot guarantee that a message is safe or fraudulent. Verify offers through official websites and contact details you find yourself.

Built by [Your Name](https://github.com/YOUR-USERNAME).
