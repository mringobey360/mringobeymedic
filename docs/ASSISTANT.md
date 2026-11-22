# Help Assistant (floating chat widget)

A "Help" button (bottom-right of every page) opens a chat that answers in Kiswahili or English.
It runs in one of two modes, chosen automatically — **no code change needed to switch**:

| Mode | When active | How it answers | Cost |
|---|---|---|---|
| **Offline (default)** | `ANTHROPIC_API_KEY` is empty/not set | Matches the question against a curated Kiswahili/English keyword FAQ in `assistant/offline.py` and returns a pre-written answer. Pricing, this user's subscription, and a visitor's signup status are still generated from live database values. | **Free — no API calls at all.** |
| **AI (optional upgrade)** | `ANTHROPIC_API_KEY` is set (and `ASSISTANT_ENABLED=True`) | Sends the question to the Claude API with the full knowledge base in `assistant/knowledge.py` as context, so it can handle phrasing the offline FAQ doesn't recognise. | Paid, per Anthropic's usage pricing (Claude Haiku 4.5 by default — inexpensive). |

The widget itself, and everyone who sees it, is unchanged either way:

| Who sees it | Assistant | What it helps with |
|---|---|---|
| Visitors (not logged in) | **Public** | Signup steps, prices, how to pay, "I paid but I'm not approved yet", forgot password. On a customer's own private `/jiunge/...` link it also knows that one registration's status. |
| Shop owners & employees | **Staff** | How to use POS, products/batches (including the "Inaisha Muda" expiry toggle), debts, reports, employees, branches, subscription, chat. |
| Platform superadmin | — (hidden) | — |

## What it does NOT do
- It does not read or change pharmacy data (stock, sales, customers). It only explains how to do things.
- It cannot confirm payments, reset passwords or edit accounts.
- It refuses medical advice (dosing, diagnosis, interactions) — in both modes.
- Conversations are kept only in the browser tab (sessionStorage) and are **not stored on the server**.

## Setup — free (offline) mode, default, nothing to pay
Nothing to do. As long as `ASSISTANT_ENABLED=True` (the default) and `ANTHROPIC_API_KEY` is left empty, the Help
button appears and answers using the local FAQ in `assistant/offline.py` — zero external cost.

To turn the whole widget off instead: set `ASSISTANT_ENABLED=False`.

## Setup — optional AI upgrade (paid)
Only do this if you want smarter, free-form answers and are OK with the (small) per-message cost:
1. Create an API key at https://console.anthropic.com/ and add billing there.
2. Add to `.env` (local) or Railway **Variables** (production):

```
ANTHROPIC_API_KEY=sk-ant-...
ASSISTANT_ENABLED=True
ASSISTANT_MODEL=claude-haiku-4-5-20251001   # fast & cheap; use claude-sonnet-5 for smarter answers
```
3. Restart the server. No migration is needed (the assistant adds no database tables) and no new package is needed.
4. To go back to the free offline mode at any time, just remove/blank `ANTHROPIC_API_KEY` and restart.

## Cost protection when AI mode is on (env variables, defaults shown)
```
ASSISTANT_PUBLIC_PER_10_MIN=12        # per visitor IP
ASSISTANT_PUBLIC_PER_DAY=60           # per visitor IP
ASSISTANT_PUBLIC_GLOBAL_PER_DAY=1000  # all visitors together
ASSISTANT_STAFF_PER_HOUR=40           # per logged-in user
ASSISTANT_STAFF_PER_DAY=300           # per logged-in user
ASSISTANT_MAX_TOKENS=600              # max length of each answer
```
These rate limits apply in both modes (cheap protection against spam even for the free offline mode), but they
only matter for cost when AI mode is on. Counters use Django's cache — with several gunicorn workers on the
default in-memory cache each worker counts separately, so limits are approximate; use a shared cache (e.g. Redis)
for exact limits. Also set a monthly spend limit in the Anthropic console if you enable AI mode.

## Keeping answers accurate
- **Offline mode**: edit `assistant/offline.py` — add/adjust entries in `STAFF_FAQ` / `PUBLIC_FAQ` (keywords +
  answer). Keep it in sync with what the system actually does.
- **AI mode**: edit `assistant/knowledge.py` (`PUBLIC_KNOWLEDGE`, `STAFF_KNOWLEDGE`).
- Either way, prices and payment details are read live from **Payment Settings** and the active SMS packages, so
  they never go stale.

## Files
- `assistant/offline.py` — free, no-API-key FAQ engine (default brain)
- `assistant/knowledge.py` — system prompts / knowledge base for the optional AI mode
- `assistant/client.py` — Anthropic API call (standard library only), used only when AI mode is on
- `assistant/views.py` — `/assistant/api/public/` and `/assistant/api/staff/` (validation, rate limits, mode switch)
- `templates/assistant/widget.html` — the chat widget (included from `base.html`)
- `/assistant/` is exempt from the subscription lock so an owner with an expired plan can still ask how to renew.

## Customer care (v20.21)
The assistant now behaves like a customer-care officer, in both offline and AI mode:

| Behaviour | How it works |
|---|---|
| **Checks in when a customer goes quiet** | If the chat is open and the customer writes nothing for `ASSISTANT_IDLE_SECONDS` (default 60) the assistant says it is still there and asks if they found what they needed. If they stay quiet for twice as long it shares the Technical Support numbers. It stops after two check-ins, never interrupts someone who is typing, and never nags after the customer said "no, thanks". Visitors who never open the chat get one small "Habari! Nikusaidie?" bubble above the Help button (once per visit). Check-ins are shown in the chat only; they are not sent to the server or stored. |
| **Asks "anything else?"** | Every answer ends with a short follow-up question (wording rotates). The widget shows two quick replies under it: "Nina swali lingine" / "Hapana, asante". Saying no (or "asante", "sina swali", "that's all", "bye") gets a warm goodbye with the support numbers instead of another question. |
| **Gives Technical Support numbers** | `+255610117824` and `+255678828519` are always shown (tap-to-call) at the bottom of the chat, given when someone asks for support / a person, added automatically to any answer that says "contact support", and included when an answer cannot be found. Phone numbers inside chat messages are tap-to-call links. |

Change the numbers without touching code: set `SUPPORT_PHONES=+255...,+255...` (comma-separated) in `.env` / Railway Variables.
Change the check-in delay with `ASSISTANT_IDLE_SECONDS`.

Where the code lives: `templates/assistant/widget.html` (check-ins, quick replies, teaser, tap-to-call),
`assistant/offline.py` (follow-up, goodbye, support answer), `assistant/knowledge.py` (customer-care rules and the
TECHNICAL SUPPORT TEAM block given to the AI), `assistant/support.py` (reads `SUPPORT_PHONES`).

> **Rate limits and Redis (v20.22):** when `REDIS_URL` is set, the assistant's rate-limit counters are shared by all
> gunicorn workers, so the limits above become exact instead of approximate. If the cache is ever unavailable the
> assistant keeps working (limits are simply not enforced until it is back).
### Production hardening

- `templates/assistant/widget.html` explicitly loads Django's `static` template tag library because included templates do not inherit `{% load static %}` from their parent.
- Signed-out authentication screens no longer expose an `Install App` control.
