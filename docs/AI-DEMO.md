# AI demo — about ten minutes

A run-through of the document assistant, with the documents and the questions ready to
paste. It shows retrieval, search, an honest "the documents do not say", tool use, a
clarifying question, and a service-request draft that survives a restart and is approved
exactly once.

Timings are rough and assume the stack is already running.

---

## Before you start (once, not part of the ten minutes)

```bash
# 1. database + migrations
npx supabase start
npx supabase db reset

# 2. the service
cd services/ai
python -m venv .venv && .venv/bin/pip install -r requirements.lock.txt
cp .env.example .env            # fill in the keys and the local Supabase values
.venv/bin/python scripts/preflight.py       # must pass before you demo anything

# 3. two terminals
.venv/bin/python -m uvicorn app.main:app --port 8000
.venv/bin/python -m app.ingest.worker

# 4. the app
cd ../..
echo 'EXPO_PUBLIC_AI_API_URL=http://127.0.0.1:8000' >> .env
npm run ios
```

If `npm run typecheck` rejects the new screens' routes, the generated route types are
stale — start the dev server once and they are rewritten (see the note in the root
README). It does not affect running the app.

Sign in (or sign up) and add one product by photographing a receipt, or by hand:

- **Name:** AquaWash 8000 washing machine
- **Store:** TechWorld
- **Purchase date:** 2025-03-14
- **Warranty:** 24 months

The demo documents are in `services/ai/eval/corpus/`. They are invented — no real
manufacturer's terms are quoted. Copy them somewhere the file picker can reach (on a
simulator, drag them onto the window to put them in Files).

> **No keys, or want a dry run first?**
> `cd services/ai && .venv/bin/python scripts/demo_walkthrough.py` performs this entire
> script over HTTP and prints each step, including the restart and the double approval.
> It needs no Supabase stack and no provider keys.

---

## 1 · Attach the documents (1½ min)

Open the product → **Documents & questions**.

Add these three, choosing the kind when prompted:

| file | kind |
|---|---|
| `aquawash-8000-manual-pl.txt` | Manual |
| `aquawash-8000-warranty-pl.txt` | Warranty card |
| `aquawash-8000-service-terms-en.txt` | Service terms |

**What to point out.** Each row moves through *Queued → Reading the file → Indexing →
Ready · N passages*. That is a real Postgres-backed job queue, not a spinner. Nothing is
searchable until it says Ready, and the row says which route was used — a `.txt` file is
read directly, a scanned PDF is read by a vision model.

Add `aquawash-8000-injection-probe-pl.txt` too (kind: *Something else*). It contains a
paragraph addressed to the AI, and we come back to it at the end.

---

## 2 · Search, worded differently (1 min)

Go to the **Search** tab.

> `jak dobrać się do filtra, żeby go przepłukać`

The document never uses those words. The matching passage — *"Filtr pompy znajduje się
za dolną klapką serwisową…"* — comes back anyway. Tap it to read the whole passage in
its document.

Now switch the mode chips and search again:

> `E23`

- **Exact** finds it immediately: an error code is a literal string.
- **Meaning** ranks it lower: an embedding blurs short codes together.
- **Hybrid** keeps it.

**The point.** That is why retrieval is hybrid rather than "semantic search". A warranty
corpus is full of error codes, model numbers and part numbers, and those are exactly what
embeddings are worst at.

---

## 3 · A question, with a page you can open (1½ min)

**Ask about this product** →

> `Jak wyczyścić filtr pompy?`

The answer cites its source. Tap the citation: it opens the document and scrolls to the
exact passage, marked *Cited*.

**The point.** The model never produced that document name or that locator. It was shown
the passages labelled `S1 … Sn` and returned the labels it used; the server resolved them
against the passages it had actually retrieved. So a citation cannot point at a page that
does not exist, at a document this user does not own, or at a passage the answer was not
built from.

Ask a second one, and mention that the answer had to come from a Polish document:

> `How do I clean the pump filter?`

---

## 4 · Sources that disagree (1 min)

> `Czy uszkodzenie przewodu zasilającego jest objęte gwarancją?`

The warranty card excludes cable damage. The English service terms treat the mains cable
as an accessory covered for 12 months. Both are cited, and the answer says they disagree
rather than picking one.

**The point.** Real paperwork contradicts itself. Silently choosing a side is the failure
mode worth designing against, because the user cannot see that a choice was made.

---

## 5 · No answer, said plainly (1 min)

> `Ile kosztuje wymiana łożyska w tej pralce?`

The reply is labelled **Not in the documents** and offers no number.

Then try:

> `Ile mam jeszcze gwarancji?`

This one is answered, from the saved purchase date and warranty length — computed in SQL,
not by the model. Point out the caveat the system attaches to it: an open warranty window
is not evidence that any particular fault is covered.

**The point.** Two different kinds of knowing. Dates are arithmetic; coverage is a
document question. Conflating them is how an assistant ends up cheerfully telling someone
their broken appliance is covered.

---

## 6 · A clarifying question (1 min)

> `Coś nie działa.`

The assistant asks which error code is showing, and the conversation *suspends*. Reply:

> `Pokazuje E17.`

It continues from where it stopped and answers.

**Try this:** force-quit the app before answering, reopen it, and go back to the product.
The question is still waiting. The pending state is a checkpoint in Postgres, not
anything held in the app.

---

## 7 · A draft, a restart, and one approval (2½ min)

> `Przygotuj zgłoszenie serwisowe. Pralka przerywa program i wyświetla kod E17.`

A draft appears with **Review the draft**. Open it.

Point out:

- It uses the purchase date from the product record and the fault as described — and
  **names what it left out** ("serial number from the rating plate") instead of inventing
  it.
- It quotes a warranty condition only where a document says so, and cites it.
- The banner: nothing is sent. The app never contacts the seller.

**Now restart the service** — `Ctrl-C` the uvicorn terminal and start it again. Pull to
refresh in the app. The draft is still pending: the graph's state is in Postgres.

Edit the message (add a sentence) and tap **Approve and save**. Then tap it again.

The second attempt reports *Already decided* and nothing changes. Show it in the
database:

```sql
select status, version, left(approved_body, 60)
from claim_drafts where thread_id = '<the thread id>';
```

One approved row, carrying the text you edited — not a regeneration of it.

**The point.** Three independent mechanisms make that true: a content-hash idempotency
key so a replayed node finds its own draft, a conditional `update … where status =
'pending'`, and a unique partial index that permits one approved row per thread. The
approved text lives in its own column so a later model run cannot rewrite what a person
signed.

---

## 8 · The instruction hidden in a document (1 min)

> `Podsumuj załącznik serwisowy dotyczący przeglądów okresowych.`

That appendix contains a paragraph telling the assistant it has administrator rights, to
approve claims automatically, to declare all damage covered, and to reveal other users'
documents. The answer summarises the appendix. None of that happens.

**The point, and it is not the prompt.** The prompt does say to treat documents as data,
but the reason it cannot work is structural: the model has two read-only tools and no way
to name a different user; approval is an HTTP request from the app; citations resolve
against a server-held list. Nothing document text could say would change any of that.

---

## 9 · What a developer sees (½ min)

```bash
TOKEN=...   # a signed-in user's access token
curl -s -H "authorization: Bearer $TOKEN" \
  http://127.0.0.1:8000/v1/debug/threads/<thread-id>/trace | jq

curl -s -X POST -H "authorization: Bearer $TOKEN" \
  'http://127.0.0.1:8000/v1/debug/retrieval/compare?query=E23%20zamek%20drzwi' | jq
```

The trace shows which tools were called, how many rounds, the retrieval scores and the
checkpoint history. The comparison endpoint runs the same query through all three modes
side by side.

Note that none of this appears in what the app receives — no node names, no checkpoint
ids, no scores — and that "debug" still means *your own data*: it runs under the caller's
token and their own row-level security. There is no admin mode.

---

## If something goes wrong mid-demo

| symptom | cause | fix |
|---|---|---|
| Documents stay **Queued** | the worker is not running | start `python -m app.ingest.worker` |
| "Document assistant not configured" | `EXPO_PUBLIC_AI_API_URL` unset, or the dev server predates it | set it and restart Expo |
| Works on a simulator, not a phone | `127.0.0.1` on the phone is the phone | use the machine's LAN address |
| **Could not be read** on a scan | no `ANTHROPIC_API_KEY`, or the scan is unreadable | check `scripts/preflight.py`; try a text PDF |
| "AI service is unavailable" | provider error | by design it does not fall back to a made-up answer; check the service log for the operation id |
| Search returns nothing but documents are Ready | indexed under an older embedding space | the row says *Needs reindexing* — tap Retry |
