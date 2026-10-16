# Multiple knowledge bases — design

## Goal

A project owner keeps separate sets of knowledge (e.g. "Products", "Support policies") and can test the bot against any one of them.

Success: upload a document to KB "Products", start a test chat on "Products", and the bot answers only from "Products" documents. The selected KB is always visible, so the user knows which knowledge they are editing or testing.

## Current state

- `KnowledgeBase` (project FK, `name`, `vector_collection`) already exists. Each KB has its own Qdrant collection, plus `{collection}_web` for crawled pages.
- Upload and crawl accept `kb_name` and `get_or_create` the KB by name. That is the only way a KB gets created today.
- `GET /knowledge-bases/` lists a project's KBs. It does **not** check that the user belongs to the project.
- `DocumentListView` already filters by `kb_id`.
- Conversations:
  - `POST /conversations/` accepts `knowledge_base_id`; when omitted, `get_default_knowledge_base` looks up the KB named `"default"`.
  - `Conversation.knowledge_base` is `PROTECT`.
- Channels already pick a KB on the channels page (`ChannelCredential.knowledge_base`, `SET_NULL`).
- **Bug:** `build_reply_context` (`channels_app/tasks.py`) rebuilds the collection name from `knowledge_base.name` via `get_vector_collection_name` instead of reading `knowledge_base.vector_collection`. Renaming a KB would make replies search a missing collection.
- Frontend:
  - The knowledge page hardcodes `"default"` for upload and crawl, and lists documents from all KBs together.
  - "New test chat" never sends a KB.

## Scope

In:
- Create a KB
- Rename a KB
- Switch the KB on the knowledge page
- Pick the KB for a new test chat
- Show the KB name on the conversation list and in the chat header
- Changing a channel's KB applies to its existing customers too, not only new ones
- Flag a channel that has no KB

Out (later):
- Delete a KB
- Switch the KB mid-chat
- KB-specific custom instructions in the UI (the backend already supports them)
- A global KB switcher
- KB filters on the conversations and analytics pages

## Backend

### Model (`knowledge/models.py`) + migration `0012`

- Add `is_default = BooleanField(default=False)`.
- Data migration: set `is_default=True` on every KB named `"default"`.
- Constraints:
  - `UniqueConstraint(Lower(name), project)`: KB names are unique per project, ignoring case ("products" duplicates "Products").
  - `UniqueConstraint(project, condition=Q(is_default=True))`: at most one default per project.
- Collection naming for new KBs: `kb_{project.slug}_{kb.id.hex}`. The name is not part of it, so spaces or Bengali names cannot produce an invalid collection name, and a rename never touches Qdrant. Existing KBs keep their stored `vector_collection`.
- Replace `get_vector_collection_name(project, kb_name)` with `new_collection_name(project, kb_id)`, and update its callers and tests.

If existing data has duplicate `(project, name)` pairs, the migration fails. They are unlikely, because creation was always `get_or_create` by name. Check before deploying with:

```sql
select project_id, lower(name), count(*) from knowledge_knowledgebase group by 1, 2 having count(*) > 1;
```

### Default KB (`channels_app/service/conversation_service.py`)

`get_default_knowledge_base(project)` looks up by `is_default=True`, not by name. When none exists, it creates `name="default", is_default=True`.

`GET /knowledge-bases/` calls it first. Every project therefore has its default before the user can create other KBs, and a user-created KB named "default" cannot collide with an auto-created one.

### Channel KB applies to existing customers (`channels_app/service/conversation_service.py`)

Today each external contact has one conversation, and that conversation keeps the KB it was created with. After a channel's KB changes, existing customers are still answered from the old KB.

`ensure_contact_conversation(contact, knowledge_base)` now also handles an existing conversation: when its `knowledge_base_id` differs from the channel's KB, it updates the conversation and saves it with `update_fields=["knowledge_base"]`. The switch therefore happens on the customer's next message.

Callers:
- `facebook_messing.py` (also handles Instagram)
- `tasks.py`
- `whatsapp_messaging.py`, which currently skips the call when `contact.conversation` exists. It must always call `ensure_contact_conversation`.

Test chats have no contact and are unaffected.

### Reply context fix (`channels_app/tasks.py`)

`build_reply_context` uses `knowledge_base.vector_collection`.

### API (`knowledge/views.py`, `knowledge/urls.py`)

Every endpoint returns errors in the existing `{"success": false, "error": {code, message}}` shape.

**`GET /knowledge-bases/?project_id=`**
- Returns 403 unless the user belongs to the project. This is the security fix.
- Returns `{id, name, is_default, document_count, created_at}` for each KB.
- `document_count` counts top-level documents only (`parent_crawl_id IS NULL`), so crawled child pages are not counted.
- Sort order: default first, then by name.

**`POST /knowledge-bases/`** with `{project_id, name}`
- Admin role required (same as upload), else 403.
- The name is trimmed and must be 1–100 characters, else 400.
- A duplicate name in the project returns 409.
- Returns 201 with the KB dict.

**`PATCH /knowledge-bases/<uuid>/`** with `{name}`
- Admin of the KB's project required, else 403. A KB that does not exist returns 404.
- Same name rules as create.
- Changes only `name`. The collection, documents and conversations are untouched.

**Upload (`POST /documents/upload/`) and crawl (`POST /crawl/`)**
- Accept `kb_id`.
- If given, it must belong to the project, else 404. If omitted, the default KB is used.
- `kb_name` is removed, so a typo can no longer silently create a KB.

## Frontend

### `lib/api.ts`

- `knowledgeBaseApi.create(projectId, name)` and `knowledgeBaseApi.rename(kbId, name)`
- `documentApi.list(projectId, kbId)`
- `documentApi.upload(file, projectId, kbId)` and `documentApi.crawl(..., kbId, ...)`, replacing `kbName`
- KB type gains `is_default` and `document_count`

### Knowledge page (`app/(dashboard)/knowledge/page.tsx`)

- Top bar: **Knowledge base: [Products · 12 docs ▾] [✏️] [+ New knowledge base]**.
  - The default KB shows a small "default" tag in the dropdown.
- The selection is stored in localStorage under `kb:<projectId>`. It falls back to the default KB when the stored id is missing or stale.
- Documents, upload and crawl use the selected KB.
- Feedback names the KB:
  - "Uploaded *file.pdf* to **Products**"
  - Empty state: "No documents in **Products** yet"
- "New knowledge base" dialog: a name input. On success, the new KB is selected.
- Rename dialog: prefilled with the current name. A 409 shows "A knowledge base with this name already exists".
- The instructions tab is unchanged and stays project-level.

### Conversations

- **New test chat** (`conversations/page.tsx`):
  - The button opens a small dialog with a KB dropdown, defaulting to the stored `kb:<projectId>` selection or else the default KB.
  - Create sends `knowledge_base_id`.
- **List:** each conversation's KB name shows as a badge (it is plain text today).
- **Chat header** (`conversations/[id]/page.tsx`): shows a KB name badge next to "Test chat" / the channel label.

### Channels page (`app/(dashboard)/channels/page.tsx`)

The add and edit dialogs already have a required KB dropdown, and the channel card already shows the KB name. Changes:

- Dropdown items read `Products · 12 docs`, and the default KB carries a "default" tag.
- Adding a channel preselects the default KB.
- A channel card with no KB shows a red badge: "No knowledge base — messages are ignored".
- Edit dialog hint: "Changing this applies to existing customers from their next message".

## Testing

Backend (`knowledge/tests`, `channels_app/tests`):

- **KB list**
  - An outsider gets 403.
  - Returns `is_default` and `document_count`, and puts the default first.
  - Creates the default KB when the project has none.
- **KB create**
  - Succeeds.
  - A duplicate name gets 409.
  - A blank name or one over 100 characters gets 400.
  - A non-admin gets 403.
  - The new KB's `vector_collection` follows the uuid naming.
- **KB rename**
  - Succeeds.
  - A duplicate name gets 409.
  - A non-admin gets 403.
  - After renaming the default KB, `get_default_knowledge_base` returns the same KB and does not create a new one.
- **Upload and crawl**
  - With `kb_id`, the document lands in that KB.
  - Another project's `kb_id` gets 404.
  - With no `kb_id`, the default KB is used.
  - Existing tests are moved from `kb_name` to `kb_id`.
- **Reply context:** `build_reply_context` uses the stored `vector_collection` after a rename.
- **Channel KB change**
  - An existing contact's conversation switches to the channel's new KB on its next message, for both the Facebook and WhatsApp paths.
  - The conversation is not changed when the KB is the same.

Frontend: there is no test setup in `frontend/package.json`, so check by hand:

1. Create a KB.
2. Rename it.
3. Upload to it.
4. Start a test chat on it.
5. Confirm the answer comes from that KB's docs, and that the KB badge shows in the list and the header.
