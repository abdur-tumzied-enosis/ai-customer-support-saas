# Cohere embeddings and reranking via OpenRouter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the self-hosted e5 text embeddings, CLIP photo embeddings and TEI reranker with Cohere models on OpenRouter, store text and photos in one collection per KB, and book provider cost for every embed, image and rerank call.

**Architecture:** One module (`agents/embeddings_api.py`) calls OpenRouter's `/embeddings` and `/rerank` and returns vectors or scores plus the reported usage. `vector_store.py` and `image_search.py` use it in place of TEI and fastembed-CLIP. Photos go into the KB's main collection as dense-only points tagged `kind="image"`; text points never carry that top-level key. Usage is booked through one helper in `usage/utils.py`.

**Tech Stack:** Django, Celery, Qdrant (`qdrant-client`, `langchain-qdrant`), fastembed (BM25 sparse only), requests, OpenRouter API (`cohere/embed-5-fast`, a Cohere rerank model).

**Spec:** `docs/superpowers/specs/2026-11-11-cohere-embeddings-design.md`

**Eval:** `docs/evals/bengali_retrieval.py` (already passed on the new set; re-run after Task 1 to confirm the production client matches).

## Global Constraints

- Embedding model: `cohere/embed-5-fast`, for text and images, through `https://openrouter.ai/api/v1/embeddings`.
- Reranker: a Cohere rerank model through `https://openrouter.ai/api/v1/rerank`; default `cohere/rerank-v3.5`, switched to `cohere/rerank-4-pro` only if the eval says so.
- Auth: `OPENROUTER_API_KEY` from settings (`settings.OPENROUTER_API_KEY`), bearer token.
- Keep fastembed `Qdrant/bm25` (sparse keyword search). Its Dockerfile bake stays.
- Audio is out of scope. `ModelConfig.transcription_model` is not changed.
- Chat LLMs are not changed.
- Tests run in Docker: `docker compose exec django python manage.py test <module> --noinput`. Never a local venv.
- Commits happen only when the user asks for them.

## Review Focus

- **Bengali question, English doc:** a Bengali question must still find the right English chunk (eval gate, Task 1 and Task 5).
- **Photo point in text answers:** a photo point must never be returned by text retrieval, and a text point never by photo search (Task 4).
- **Photo uploaded before any text:** the collection created by a photo must still accept hybrid text points later (Task 4).
- **Old collection dimension:** a KB whose collection still has 512-dim CLIP vectors must fail loudly, not mix vectors (Task 7 reindex step).
- **Embedding API down during ingestion or photo embed:** the job fails or the photo is marked `failed`, with the error recorded; nothing is silently stored with a zero vector (Tasks 1, 6, 7).
- **Empty document list to rerank:** returns the input unchanged, makes no API call (Task 5).
- **Rerank API down:** falls back to original order, as `rerank_docs` does today (Task 5).

---

### Task 1: OpenRouter embedding and rerank client

**Files:**
- Create: `backend/agents/embeddings_api.py`
- Test: `backend/agents/tests/test_embeddings_api.py`

**Interfaces:**
- Consumes: `settings.OPENROUTER_API_KEY`, `agents.image_search.load_image` output (`PIL.Image.Image`).
- Produces:
  - `@dataclass class Embedded: vectors: list[list[float]]; tokens: int; cost_usd: Decimal | None; model: str`
  - `@dataclass class Reranked: scores: list[float]; tokens: int; cost_usd: Decimal | None; model: str`
  - `def embed_texts(texts: list[str], kind: Literal["query", "document"]) -> Embedded`. Sends `input_type` `search_query` or `search_document`.
  - `def embed_image(img: Image.Image) -> Embedded`. Sends one `image_url` part, a data URL of the image re-encoded as JPEG (quality 85).
  - `def rerank_scores(query: str, texts: list[str]) -> Reranked`. Scores come back in `texts` order.
  - Raises `EmbeddingError(Exception)` on any non-200 response or a malformed body.

- [ ] **Step 1: Write the failing tests**

Mock `requests.post`. Assert:
- `embed_texts(["a", "b"], "query")` posts `{"model": "cohere/embed-5-fast", "input": ["a", "b"], "input_type": "search_query"}` and returns vectors sorted by `index`, with `tokens` from `usage.prompt_tokens` and `cost_usd` from `usage.cost` as `Decimal`.
- `embed_texts([...], "document")` sends `input_type` `search_document`.
- `embed_image` posts one input whose `content` is `[{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}]`.
- `rerank_scores` returns scores in input order when the response `results` are out of order (`index` 1 before 0).
- A 500 response raises `EmbeddingError`.
- An empty `texts` list returns an empty `Embedded` with no request.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose exec django python manage.py test agents.tests.test_embeddings_api --noinput`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement `backend/agents/embeddings_api.py`**

Two POST helpers, one per endpoint. `embed_texts` and `embed_image` share a private `_post_embeddings(content_input, kind)`. Cost is `Decimal(str(usage["cost"]))` when present, else `None`. Do not estimate cost here: the caller decides fallback (Task 3).

- [ ] **Step 4: Run the tests to verify they pass**

Run the same command. Expected: PASS.

- [ ] **Step 5: Re-run the retrieval eval against this client**

Run the standalone eval script (it has its own HTTP client, so this checks the API behaviour, not the production module): `OPENROUTER_API_KEY=<key> python docs/evals/bengali_retrieval.py cohere/embed-5-fast`. Expected: `top-1 8/8`. Then add a one-line check in `test_embeddings_api` that `embed_texts` returns the same number of vectors as inputs for the eval's 8 questions (mocked, no network). Record the eval result in the spec's Gate section.

---

### Task 2: Pricing rows for embedding and rerank models

**Files:**
- Modify: `backend/agents/models.py` (`AIModel.KIND_CHOICES`, new field)
- Create: `backend/agents/migrations/0003_embedding_pricing.py`
- Test: `backend/agents/tests/test_model_config.py` (add cases)

**Interfaces:**
- Consumes: none.
- Produces:
  - `AIModel.KIND_CHOICES` gains `("embedding", "Embedding")` and `("rerank", "Rerank")`.
  - `AIModel.image_price_per_mtok = DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)` — USD per 1M image tokens.

- [ ] **Step 1: Write the failing test**

A test that `AIModel.objects.create(kind="embedding", provider="openrouter", name="cohere/embed-5-fast", input_price_per_mtok=Decimal("0.08"), image_price_per_mtok=Decimal("0.40"))` saves and reads back both prices.

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose exec django python manage.py test agents.tests.test_model_config --noinput`
Expected: FAIL, unknown kind or field.

- [ ] **Step 3: Add the field and kinds, then generate the migration**

Run: `docker compose exec django python manage.py makemigrations agents -n embedding_pricing`. Add a data step in the same migration that creates one row for `cohere/embed-5-fast` (input `0.08`, image `0.40`, provider `openrouter`, kind `embedding`) with `get_or_create`. Do not seed a rerank price: rerank cost comes from the provider-reported `cost` (Task 3); a missing price is recorded as `unknown`, never `0`.

- [ ] **Step 4: Run the test to verify it passes**

Run the same command. Expected: PASS.

---

### Task 3: Book provider cost for embed, image and rerank calls

**Files:**
- Modify: `backend/usage/models.py` (`KIND_CHOICES` gains `("rerank", "Rerank")`)
- Create: `backend/usage/migrations/0008_usageevent_rerank_kind.py` (generated)
- Modify: `backend/usage/utils.py` (`record_image_usage` signature, new `record_model_usage`)
- Test: `backend/usage/tests/test_embed_usage.py`

**Interfaces:**
- Consumes: `agents.embeddings_api.Embedded` / `Reranked` (Task 1), `agents.models.AIModel` (Task 2).
- Produces:
  - `def record_model_usage(project_id: str, kind: Literal["embed", "image", "rerank"], *, tokens: int, cost_usd: Decimal | None, model: str, image_tokens: int = 0, conversation_id: str | None = None, message_id: str | None = None, **metadata) -> None`. Never raises.
  - `record_image_usage` keeps its name and metadata `op`, but takes `tokens` and `cost_usd` (both required). Its hard-coded `$0` is removed. Photo index and search calls pass the values from Task 1's `Embedded`.
- Pricing rule (same as `llm_pricing`): if `cost_usd` is not `None`, store it with `cost_source="provider_reported"`. Otherwise compute `tokens × input_price_per_mtok / 1e6` (plus `image_tokens × image_price_per_mtok / 1e6` for images) from the matching `AIModel` row and store `cost_source="token_calculated"`. If there is no row or no price, store `cost_usd=None` and `cost_source="unknown"`.

- [ ] **Step 1: Write the failing tests**

- Reported cost is stored as given, with `cost_source="provider_reported"`.
- With no reported cost and an `AIModel` row, cost is `tokens × input price / 1e6`, `cost_source="token_calculated"`.
- With no reported cost and no row, `cost_usd is None`, `cost_source="unknown"`.
- `record_image_usage(..., tokens=1200, cost_usd=Decimal("0.00048"))` stores both; the old `$0` is gone.

- [ ] **Step 2: Run them to verify they fail**

Run: `docker compose exec django python manage.py test usage.tests.test_embed_usage --noinput`
Expected: FAIL.

- [ ] **Step 3: Implement the helper and the rerank kind**

Add the kind, run `makemigrations usage -n usageevent_rerank_kind`, then implement `record_model_usage`. Rewrite `record_image_usage` to call it with `kind="image"` and `metadata={"op": op, ...}`. Keep the existing `atomic` wrapper and the never-raise contract.

- [ ] **Step 4: Run the tests to verify they pass**

Run the same command. Expected: PASS.

---

### Task 4: One collection per KB, photos tagged with `kind`

**Files:**
- Modify: `backend/agents/image_search.py` (`upsert_image`, `search_products`, `delete_images`)
- Modify: `backend/agents/vector_store.py` (`_create_collection` is reused, `get_retriever` / `_store` take a filter)
- Modify: `backend/knowledge/models.py:104` (remove the `image_collection` property; callers at lines 298 and 332 use `vector_collection`)
- Modify: `backend/knowledge/tasks.py:794,803` (use `kb.vector_collection`)
- Modify: `backend/channels_app/image_reply.py:30` (use `kb.vector_collection`)
- Modify: `backend/accounts/views.py:403` (remove the `image_collection` delete)
- Test: `backend/agents/tests/test_image_search.py` (update), `backend/agents/tests/test_hybrid_search.py` (add)

**Interfaces:**
- Consumes: `vector_store._create_collection(client, name)` (creates a hybrid-capable collection, sized from `embed_texts`).
- Produces:
  - `upsert_image(collection: str, image_id: str, vector: list[float], product_id: str) -> None`. Creates the collection with `_create_collection` if missing, so the collection is always hybrid-capable. Payload: `{"kind": "image", "product_id": ..., "image_id": ...}` at the top level.
  - `search_products(collection: str, vector: list[float]) -> list[tuple[str, float]]`. Adds `query_filter` `must: kind == "image"`.
  - Text retrieval (`get_retriever`, `_store`) adds `search_kwargs["filter"]` = `must_not: kind == "image"`. Text points have no top-level `kind`, so they pass.

- [ ] **Step 1: Write the failing tests**

- Upsert a photo into an empty collection name, then `add_texts_to_collection` into the same name: both succeed, and the collection reports sparse vectors (hybrid). This is the case that breaks if `upsert_image` creates a dense-only collection.
- A text retrieval over a collection holding one text and one photo point never returns the photo.
- `search_products` over the same collection never returns a text point.

- [ ] **Step 2: Run them to verify they fail**

Run: `docker compose exec django python manage.py test agents.tests.test_image_search agents.tests.test_hybrid_search --noinput`
Expected: FAIL.

- [ ] **Step 3: Implement**

Use `models.FieldCondition(key="kind", match=models.MatchValue(value="image"))` inside `models.Filter(must=[...])` or `models.Filter(must_not=[...])`. Remove the `_images` collection everywhere listed above.

- [ ] **Step 4: Run the tests to verify they pass**

Run the same command. Expected: PASS.

---

### Task 5: Cohere reranking in `rerank_docs`

**Files:**
- Modify: `backend/agents/vector_store.py:72-88` (replace `_tei_rerank_scores`), `:103-117` (`rerank_docs`)
- Modify: `backend/core/settings.py:178-186` (remove `RERANKER_URL`; re-tune `RERANK_MIN_SCORE`)
- Test: `backend/agents/tests/test_search.py` (update rerank cases)

**Interfaces:**
- Consumes: `agents.embeddings_api.rerank_scores` (Task 1), `usage.utils.record_model_usage` (Task 3).
- Produces: `rerank_docs(query: str, docs: list[Document], top_k: int = 4, min_score: float | None = None, project_id: str | None = None) -> list[Document]`. Same behaviour as today. Books a `rerank` usage event when `project_id` is given.

- [ ] **Step 1: Write the failing tests**

- `rerank_docs("q", [])` returns `[]` and makes no call.
- Scores decide the order, and `min_score` drops low-scoring docs.
- A raised `EmbeddingError` returns `docs[:top_k]` in original order.

- [ ] **Step 2: Run them to verify they fail**

Run: `docker compose exec django python manage.py test agents.tests.test_search --noinput`
Expected: FAIL.

- [ ] **Step 3: Implement and re-tune**

Replace the TEI call with `rerank_scores(query, texts)`. Re-tune `RERANK_MIN_SCORE`: score the eval docs with the rerank model, take the lowest relevant score and the highest unrelated score, and set the threshold between them. Write the chosen value and the two numbers in the comment at `settings.py:182`.

- [ ] **Step 4: Run the tests to verify they pass**

Run the same command. Expected: PASS.

---

### Task 6: Cohere text embeddings in place of TEI

**Files:**
- Modify: `backend/agents/vector_store.py:32-69` (replace `get_default_embeddings`, remove `TEIEmbeddings`, `TEI_BATCH`, `TEI_TIMEOUT` if unused)
- Modify: `backend/agents/vector_store.py:165-176` (`add_texts_to_collection` gains `usage: list | None = None`, appended with the `Embedded` result)
- Modify: `backend/knowledge/tasks.py:297-310` (book the real cost instead of `$0` and `estimate_tokens`)
- Test: `backend/agents/tests/test_tei.py` (delete), `backend/agents/tests/test_embeddings_api.py` (extend)

**Interfaces:**
- Consumes: `embed_texts` (Task 1), `record_model_usage` (Task 3).
- Produces:
  - `class OpenRouterEmbeddings(Embeddings)` (langchain) with `embed_documents(texts) -> list[list[float]]` (kind `document`) and `embed_query(text) -> list[float]` (kind `query`). Each call records its `Embedded` on `self.last_usage`.
  - `get_default_embeddings() -> OpenRouterEmbeddings` (no `EMBEDDINGS_URL` check; the OpenRouter key check replaces it).

- [ ] **Step 1: Write the failing tests**

- `OpenRouterEmbeddings().embed_query("x")` calls `embed_texts(["x"], "query")` and returns the first vector.
- `embed_documents` calls `embed_texts(texts, "document")`.
- `add_texts_to_collection(name, ["a"], usage=acc)` appends one `Embedded` to `acc`.

- [ ] **Step 2: Run them to verify they fail**

Run: `docker compose exec django python manage.py test agents.tests.test_embeddings_api --noinput`
Expected: FAIL.

- [ ] **Step 3: Implement and remove TEI**

Delete `TEIEmbeddings` and the e5 prefix comment. Ingestion (`knowledge/tasks.py:297`): pass `usage=acc`, then call `record_model_usage(project_id, "embed", tokens=sum(e.tokens for e in acc), cost_usd=..., model=...)`. Keep the surrounding `try/except` that logs and does not fail ingestion for a usage error.

- [ ] **Step 4: Run the tests to verify they pass**

Run the same command. Expected: PASS.

---

### Task 7: Photo embedding through Cohere; remove CLIP and the model servers

**Files:**
- Modify: `backend/agents/image_search.py:73-90` (remove `_get_model` and `_model`; `embed_image` moves to `embeddings_api`)
- Modify: `backend/knowledge/tasks.py:793-794` (use `embed_image` from `embeddings_api`, book cost, handle failure)
- Modify: `backend/channels_app/image_reply.py:30` (book cost for the search)
- Modify: `backend/core/settings.py:176` (remove `IMAGE_EMBEDDING_MODEL`), `:180` (remove `EMBEDDINGS_URL`)
- Modify: `.env.example:52-54,73-74`, `docker-compose.yml` and `docker-compose.prod.yml` (remove the `embeddings` and `reranker` services and their `depends_on` entries), `backend/Dockerfile` (remove the e5, CLIP and reranker bakes; keep `Qdrant/bm25`)
- Test: `backend/agents/tests/test_image_search.py` (update)

**Interfaces:**
- Consumes: `embeddings_api.embed_image` (Task 1), `record_image_usage` (Task 3), `upsert_image` (Task 4).
- Produces: none new. `knowledge.tasks.embed_product_image(image_id)` keeps its signature.

- [ ] **Step 1: Write the failing tests**

- `embed_product_image` with a mocked `embed_image` returning `Embedded(vectors=[v], tokens=1200, cost_usd=Decimal("0.00048"))` books one `image` usage event with those values and calls `upsert_image` with `kb.vector_collection`.
- With `embed_image` raising `EmbeddingError`, the image row is `status="failed"` and no usage event is booked.

- [ ] **Step 2: Run them to verify they fail**

Run: `docker compose exec django python manage.py test agents.tests.test_image_search knowledge.tests.test_image_fetch --noinput`
Expected: FAIL.

- [ ] **Step 3: Implement and remove**

Delete the CLIP code and the settings and compose entries listed above. Run `docker compose config` after editing compose to confirm the services are gone and no `depends_on` references remain.

- [ ] **Step 4: Run the tests to verify they pass**

Run the same command. Expected: PASS. Then run `docker compose config --quiet` (exit 0).

---

### Task 8: Reindex on rollout; refuse old-dimension collections

**Files:**
- Modify: `backend/knowledge/management/commands/reindex_vectors.py` (drop `_images` handling if any; re-queue photos)
- Test: `backend/knowledge/tests/test_reindex_vectors.py` (extend)

**Interfaces:**
- Consumes: `embed_product_image` (Task 7), `delete_collection` (existing).
- Produces: unchanged command interface `manage.py reindex_vectors`. New behaviour: after rebuilding each KB's text collection, call `embed_product_image.delay(str(image.id))` for every `ProductImage` of that KB with status `ready` or `pending`.

- [ ] **Step 1: Write the failing test**

Create a KB with one text document and one product image. Run the command with `embed_product_image.delay` patched. Assert: the text collection exists and is hybrid, and `delay` was called once with the image id.

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose exec django python manage.py test knowledge.tests.test_reindex_vectors --noinput`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add the re-queue loop. Before the rebuild, check each existing KB collection's vector size against `len(embed_texts(["probe"], "query").vectors[0])`. If they differ, delete the collection (the command already drops collections, so this is a log line plus the normal rebuild).

- [ ] **Step 4: Run the test to verify it passes**

Run the same command. Expected: PASS.

---

### Task 9: Cost in the dashboard

**Files:**
- Modify: `backend/analytics/views.py` (add `cost_by_kind` next to `tokens_by_kind`, around line 218)
- Modify: `backend/agents/rag.py` and `backend/channels_app/tasks.py:104` only if `project_id` is not already on the reply `Context` (decision for the implementer: thread `project_id` into `rerank_docs` and `embed_query` calls on the reply path)
- Test: `backend/analytics/tests/` (add a case, or the nearest existing analytics test)

**Interfaces:**
- Consumes: `UsageEvent.cost_usd`, `UsageEvent.kind`.
- Produces: the analytics response gains `cost_by_kind: {kind: "decimal string"}` (sum of `cost_usd` per kind, nulls excluded).

- [ ] **Step 1: Write the failing test**

Two events, `embed` at `0.01` and `rerank` at `0.02`, plus one `image` with `cost_usd=None`. Assert `cost_by_kind == {"embed": "0.01", "rerank": "0.02"}`.

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose exec django python manage.py test analytics --noinput`
Expected: FAIL.

- [ ] **Step 3: Implement**

Use `.values("kind").annotate(total=Sum("cost_usd"))` with `cost_usd__isnull=False`. Convert to strings for JSON.

- [ ] **Step 4: Run the test to verify it passes**

Run the same command. Expected: PASS.

The frontend card that displays `cost_by_kind` is not in this plan: the dashboard component has not been identified yet.

---

## Final verification

- [ ] Run the full backend suite: `docker compose exec django python manage.py test --noinput`. Expected: no new failures. Failures already present before this work are reported, not hidden.
- [ ] Run the eval again: `OPENROUTER_API_KEY=<key> python docs/evals/bengali_retrieval.py`. Expected: `cohere/embed-5-fast` at least 7/8 top-1.
- [ ] Rollout (manual, not in a test): delete the Qdrant volume or each KB's collections, rebuild images, run `manage.py reindex_vectors`, then check one photo search and one Bengali question end to end.
