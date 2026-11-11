# Cohere embeddings and reranking via OpenRouter — design

Date: 2026-11-11. Status: approved in conversation. Eval gate passed on the new 8-case set (see Gate): cohere/embed-5-fast 8/8 top-1, e5 7/8, bge-m3 8/8, qwen3-embedding-8b 8/8. The original eval's cases were never saved, so this is a new set and a weak signal.

## Intent

Remove the self-hosted embedding stack and use hosted Cohere models through OpenRouter. One embedding model covers text and product photos, so a text question and a photo can share one vector space. Embedding and rerank costs show up in the usage dashboard.

## Goals

- Replace the e5 text embeddings (TEI container), the CLIP photo embeddings (fastembed in the backend) and the cross-encoder reranker (TEI container) with Cohere models on OpenRouter.
- Use `cohere/embed-5-fast` for text and photos.
- Use a Cohere rerank model for reranking.
- Store one collection per KB that holds both document chunks and product photos.
- Record real provider cost for every embedding and rerank call, so the dashboard can show it.

## Non-goals

- Audio. Audio is transcribed to text later, through the existing `ModelConfig.transcription_model` slot, which stays untouched.
- Sparse BM25 keyword search. It stays local (fastembed `Qdrant/bm25`).
- Changes to chat LLMs.

## Anti-goals

- Bengali→English retrieval gets worse than the current e5 baseline (7/8 top-1 on the old eval).
- Embedding failures lose data. A failed embed fails its ingestion job or photo, as it does today when TEI is down.

## Gate

Before cutover, the retrieval eval (`docs/evals/bengali_retrieval.py`) must show `cohere/embed-5-fast` at least as good as `intfloat/multilingual-e5-large` on the Bengali→English cases. If it isn't, stop and come back.

Result (2026-11-11, 8 cases, top-1 / top-3): cohere/embed-5-fast 8/8 / 8/8; intfloat/multilingual-e5-large 7/8 / 8/8; baai/bge-m3 8/8 / 8/8; qwen/qwen3-embedding-8b 8/8 / 8/8. The set is small and its topics are distinct, so it can't separate the top models. Grow it before relying on small gaps.

## Approaches considered

- **A. Cohere for text, photos and rerank (chosen).** One vector space, one provider, one billing source. Cost: reindex everything and retune the rerank threshold.
- **B. Keep e5 on TEI for text, Cohere only for photos.** Smaller change, but two vector spaces and the text→photo search is lost.
- **C. Gemini Embedding 2 for everything.** Covers audio, but costs about 10–20x more on text and was not picked.

## Design

### 1. Embedding client

- A new module `agents/embeddings_api.py` with:
  - `OpenRouterEmbeddings(Embeddings)` (langchain) for text. `embed_documents` sends `input_type: "search_document"`; `embed_query` sends `input_type: "search_query"`. Cohere's input types replace e5's `query:`/`passage:` prefixes, so `TEIEmbeddings` and its prefixes are removed.
  - `embed_image(img)` sends one image as a base64 `image_url` content part, using the existing `load_image` output, re-encoded as WebP or JPEG.
- Both call `POST https://openrouter.ai/api/v1/embeddings` with model `cohere/embed-5-fast` and the backend's `OPENROUTER_API_KEY`.
- Both return the vector and the usage object. Callers record the usage (see section 5).

### 2. Reranker

- `rerank_docs` keeps its signature and its fallback (original order on error).
- `_tei_rerank_scores` is replaced by a call to `POST https://openrouter.ai/api/v1/rerank` with a Cohere rerank model (`cohere/rerank-v3.5` by default; the eval decides between it and `cohere/rerank-4-pro`).
- Cohere scores are relevance scores (0–1), not logits. `RERANK_MIN_SCORE` (`core/settings.py:186`, currently -4.5) is re-tuned on the eval set. The old comment about mmarco logits is replaced.

### 3. Collections

- One collection per KB, `kb.vector_collection`, holds document chunks and product photos.
- Vector size comes from the model's output (`_create_collection` already probes it), so it adapts automatically.
- Document chunks are hybrid (dense + BM25 sparse), as today.
- Product photos are dense-only points with payload `{"kind": "image", "product_id", "image_id"}`. Document chunks get `{"kind": "text"}`.
- Every text retrieval filters `kind != "image"`, so photo points never reach text answers.
- Every photo search filters `kind == "image"`.
- The separate `{kb}_images` collection (`knowledge/models.py:104`) is dropped.
- `search_products` and `upsert_image` keep their behaviour and take the merged collection name.

### 4. Reindexing and rollout

- The old collections are not compatible. Rollout: delete each KB's collections, rebuild the stack, run `reindex_vectors` for documents, and re-queue `embed_product_image` for every product image.
- `reindex_vectors` already drops and rebuilds collections. It needs the `kind` payload added and the `_images` drop.

### 5. Cost tracking

- `AIModel.kind` gets a new choice `embedding`. Text embedding models use `input_price_per_mtok`. Add `image_price_per_mtok` (USD per 1M image tokens).
- Each embedding or rerank call books the provider-reported `usage.cost` on its `UsageEvent`, with `cost_source="provider_reported"` and the real token count. Text events use kind `embed`, photo events use kind `image`.
- If the reported cost is missing, fall back to tokens × the `AIModel` price, as `llm_pricing` does for chat.
- `record_image_usage` books the real cost instead of `$0`.
- Document ingestion (`knowledge/tasks.py:297`) books the real cost instead of `$0` and estimated tokens.
- `analytics/views.py` adds cost by kind to the dashboard payload.

### 6. Infrastructure

- Remove the `embeddings` and `reranker` services from `docker-compose.prod.yml` and `docker-compose.yml`.
- Remove `EMBEDDINGS_URL`, `RERANKER_URL` and `IMAGE_EMBEDDING_MODEL` from settings and `.env.example`.
- Remove the Dockerfile bake of the e5, CLIP and reranker models. Keep the `Qdrant/bm25` bake.

### 7. Errors

- Embedding API failure during ingestion: the job fails, as it does today when TEI is down.
- Embedding API failure during photo embed: the photo is marked `failed`, as today.
- Rerank failure: original order, as today.

## Calls I made (not discussed)

- `input_type` values `search_document` / `search_query` for Cohere. Verified in the eval; if OpenRouter ignores them, the eval shows it.
- Sparse BM25 stays local.
- Photos and text share the KB collection, distinguished by a `kind` payload.

## Unverified (checked during the eval or the first real call)

- Vector size of `cohere/embed-5-fast`.
- Whether OpenRouter's embeddings response returns `usage.cost` for Cohere.
- Whether `input_type` is honoured for Cohere on OpenRouter.
- How image tokens are counted for billing.
- The Bengali→English score against the eval gate.
- The Cohere rerank threshold.

## Left to the builder

- Exact function names and file layout beyond those above.
- Test structure, beyond: photo points never appear in text retrieval, text points never appear in photo search, and cost is booked on every embedding and rerank call.
