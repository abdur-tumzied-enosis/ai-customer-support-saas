# Agent memory design

How the reply agent gets short-term and long-term memory. This applies the framework in
[How to give your agent memory](https://www.langchain.com/blog/how-to-give-your-agent-memory) (LangChain, June 2026)
and the LangChain v1 memory docs ([short-term](https://docs.langchain.com/oss/python/langchain/short-term-memory),
[long-term](https://docs.langchain.com/oss/python/langchain/long-term-memory)) to our system.

Related backlog: [reply-pipeline-backlog.md](reply-pipeline-backlog.md) (Q5, O1, O2, S2).

---

## Framework in brief

- **Memory** is durable context the agent retrieves across runs to guide behavior. A trace or transcript is only
  *evidence*. It becomes memory once its lesson is turned into retrievable context.
- **Scopes:** short-term (the current task: recent messages, tool results, retrieved docs) and long-term (persists across runs).
- **Types:** semantic (facts), episodic (past experiences and examples), procedural (instructions, rules, workflows).
- **Loop:** capture traces → analyze them → update memory → future runs read the update.
- **Principles:** most traces should *not* become memory, every run must read the latest update (no stale caches),
  and evals should guard any behavior that memory shapes.

## Our constraint: one never-ending conversation per contact

Each external contact has a single `Conversation` (`ensure_contact_conversation`), so there are no sessions.
The `Message` table is the source of truth. Staff replies, handoff messages and batched bursts are written there
outside the agent. So for us:

- **Short-term** = recent messages in the conversation.
- **Long-term** = everything older, plus facts about the customer and lessons about the business.

---

## 1. Short-term memory

**Today:** `build_history` rebuilds the last 10 messages from the database on every reply. Tool results live only inside one `agent.invoke` call.

**Decision: no LangGraph checkpointer and no `SummarizationMiddleware`.**
- A checkpointer (`PostgresSaver` + `thread_id`) would be a second copy of the conversation. It would drift from `Message`
  because staff replies, handoff notes and failed sends bypass the agent, and batching answers several inbound messages at once.
- `SummarizationMiddleware` summarizes inside agent state. We rebuild state on every reply, so it would re-summarize (and pay) every time.

**Work:**
- **ST1. History budgeted by tokens.** Replace the 10-message count with a budget of about 3k tokens of recent messages.
- **ST2. A persisted rolling summary.** See 2b.

## 2. Long-term memory

Every piece of memory has an owner. We are multi-tenant, so scope is a security boundary.

| Type | Scope | Holds | Example |
|---|---|---|---|
| Semantic | contact | Facts about this customer | Name Sarah, order #4471, speaks Bangla, prefers email |
| Episodic | contact | What happened before | Mar 3: mug #4471 arrived cracked → refund promised → staff handled |
| Episodic | business | Good answers to learn from | A staff answer to a question the bot couldn't handle |
| Procedural | business | How the bot behaves | `CustomInstruction` (exists) |
| Procedural | global | Base behavior | `build_system_prompt` (exists) |

### 2a. Contact profile (semantic, per contact)

Stops the bot from asking for details again ("which order?") and personalizes replies with the customer's name and language.

- **Storage:** `CustomerContact.memory` is a JSONField holding a list of
  `{key, value, source_msg_id, updated_at}`. It cascade-deletes with the contact.
- **Write (background):** after a reply is delivered, a Celery task runs a cheap model on the new turn:
  "extract durable facts as JSON, update or overwrite by key." It adds no reply latency.
- **Read:** the whole profile goes into the system prompt. A contact has about 20 facts or fewer, so no vector search is needed.
- **No `remember_fact` agent tool.** The model calls such tools inconsistently, and each call adds an LLM round trip.

### 2b. Episode summaries (episodic, per contact)

The never-ending conversation is split into **episodes**, cut at a gap of more than 12 hours.

- When an episode closes (the next message arrives after the gap, or a beat sweep finds it), summarize it in
  2–3 lines and store it on `Conversation.summary` or in a small `ConversationEpisode` table.
- **Prompt order:** system prompt → profile facts → last 3–5 episode summaries → current episode's recent
  messages (ST1) → the new message.
- Summaries also feed the standalone query rewrite (backlog Q1).
- **Gate:** build this only after confirming how many contacts return across more than one day.

### 2c. Staff answers → suggested knowledge (episodic, per business) ⭐

After a handoff, the staff reply answers exactly the question the bot failed on. Today that answer is lost.

- **Capture:** the handoff reason, the customer question and the staff reply (`sender="staff"`) are already stored.
- **Analyze:** a background task pairs question → staff answer, strips customer-specific details
  (names, order numbers), and drafts a general Q&A.
- **Update:** a **Suggested knowledge** queue in the dashboard. Staff approve, edit or reject each entry. Approved entries
  become a knowledge-base document and are indexed through the existing ingestion pipeline.
- **Human approval is required.** A staff answer may be specific to one customer ("we'll refund *you* 50%"),
  and it carries that customer's PII into answers for others.

### 2d. Instruction proposals (procedural, per business)

`CustomInstruction` is already procedural memory. To close the loop:

- Analyze traces for recurring failures: handoffs with the same reason, "I don't know" answers on the same topic,
  and staff replies that correct the bot.
- Propose instruction edits for staff to approve.
- **Customer messages must never write instructions directly.** That would be prompt injection with persistence.

### 2e. Global prompt (procedural, global)

Every change to `build_system_prompt` runs against the eval set (backlog O2).

---

## 3. The loop, mapped

| Stage | Article | Ours |
|---|---|---|
| Capture | LangSmith traces | Per-reply trace in outbound `meta` (backlog O1), plus `Message` rows |
| Analyze | LangSmith Engine (background) | Celery tasks: fact extraction after each reply, episode summary on close, nightly handoff and failure analysis |
| Update | Context Hub (versioned) | Contact profile and episodes (automatic); suggested knowledge and instruction proposals (staff-approved) |
| Refresh | Runs must read the update | Already true: the prompt is rebuilt from the database on every reply. If we cache the LLM client, never cache the prompt. |

LangSmith is optional. Each stage maps onto a Django model plus a Celery task.

---

## 4. Risks

1. **Memory poisoning.** Customers write the facts. Store them as claims ("customer says order #4471"), tell the prompt
   that profile facts are customer-reported, and never let them override knowledge-base content or instructions.
2. **Cross-tenant leakage.** Every read and write is scoped by `project` and `contact` through FKs, not free-form namespace strings.
3. **Stale facts.** Upsert by key with `updated_at`, and have the extractor overwrite on conflict.
4. **Privacy.** Staff can view and delete a contact's memory in the dashboard, memory is deleted with the contact, and
   sensitive categories (health, payment cards) are never extracted.
5. **Cost.** One cheap-model call per reply (extraction) and one per closed episode, tracked through `track_token_usage`.

---

## 5. Build order

- [x] **M1. Trace capture** (backlog O1). Everything else analyzes these traces.
- [x] **M2. Token-budgeted history** (ST1).
- [x] **M3. Contact profile facts** (2a), including the view and delete UI in the dashboard.
- [x] **M4. Suggested knowledge queue** from staff answers (2c).
- [x] **M5. Episode summaries** (2b), only if the data shows returning customers.
- [x] **M6. Instruction proposals** (2d) and the eval runner (O2; real cases still to collect).

**Not using:** LangGraph checkpointer, `PostgresStore` and `SummarizationMiddleware`. They duplicate state
Django already holds. Revisit `PostgresStore` with an `IndexConfig` only if memory ever needs semantic search across
many items per contact.

---

## As built

| Piece | Where | Runs |
|---|---|---|
| Reply trace (M1) | `agents/rag.py` (`Context.trace`), saved to outbound `meta["trace"]` in `channels_app/reply.py` | Every reply |
| Token-budgeted history (M2) | `channels_app/tasks.py` `build_history` (`HISTORY_TOKEN_BUDGET = 3000`, starts after the last episode) | Every reply |
| Contact facts (M3) | `CustomerContact.memory` / `memory_through`; `channels_app/memory.py` `update_contact_memory` | Celery, after each bot or staff reply (`transaction.on_commit`) |
| Episode summaries (M5) | `ConversationEpisode`; `memory.summarize_idle_episodes` | Beat, every 30 minutes; closes episodes silent for `EPISODE_GAP_HOURS` (12) |
| Prompt section | `memory.memory_prompt` → `build_system_prompt(..., memory)` | Every reply |
| Suggested knowledge (M4) | `MemorySuggestion(kind="knowledge")`; `memory.draft_knowledge_suggestion` | Celery, after each delivered staff reply |
| Instruction proposals (M6) | `MemorySuggestion(kind="instruction")`; `memory.propose_instructions` | Beat, daily; skipped while earlier proposals await review or with fewer than 2 cases |
| Review queue | `knowledge/suggestions.py`: `GET /api/kb/suggestions/`, `POST /api/kb/suggestions/<id>/approve/` or `/reject/`; UI `components/knowledge/SuggestionsQueue.tsx` on the Knowledge page | Staff |
| Memory view and delete | `GET` and `DELETE /api/channels/conversations/<id>/memory[?key=]`; UI `components/chat/MemoryDialog.tsx` | Staff |
| Evals (O2) | `python manage.py run_reply_evals docs/evals/example.json` | By hand, before and after prompt or retrieval changes |

**Settings:** `MEMORY_LLM_MODEL` (default: same model as replies) and `EPISODE_GAP_HOURS` (default 12).

**Deploy:** run `migrate` (`channels_app` 0012–0013, `knowledge` 0011) and restart the worker and beat so they pick up the new tasks and schedule.

**Forgetting:** "Forget everything" clears the facts and replaces every episode with one empty marker at the newest message. Replies and the summarizer both start after that marker, so nothing earlier is shown or summarized again. Deleting a single fact does not edit episode summaries that mention it. Deleted facts are not re-extracted, because `memory_through` stays where it is, and deletes and extraction take the same row lock.

**Model calls:** memory tasks use `method="function_calling"` with a 1024-token cap. In live tests, Gemini's `json_schema` mode looped or garbled string fields in 3 of 4 runs. Schema strings have length limits, so a runaway answer fails validation and saves nothing.

**Known limits:** facts are per contact, while episodes are per conversation. An approved instruction goes project-level when its knowledge base has no KB-level instructions, so in a project with several knowledge bases it applies to all of them.
