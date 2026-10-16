# Multiple Knowledge Bases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Users create, rename and switch between knowledge bases. A test chat is started against a chosen KB. A channel's KB applies to its existing customers too.

**Architecture:**
- `KnowledgeBase` gains an `is_default` flag, so the fallback KB is found by flag rather than by the name "default". It also gains a case-insensitive unique-name constraint, and id-based Qdrant collection names, so a rename never touches Qdrant.
- New create/rename endpoints sit next to the existing list endpoint.
- Upload and crawl take `kb_id`.
- `ensure_contact_conversation` moves an existing customer's conversation to the channel's current KB.
- Frontend:
  - A KB bar on the knowledge page.
  - A KB picker dialog for new test chats.
  - KB badges in the conversation list and the chat header.
  - Small changes on the channels page.

**Tech Stack:** Django 5.0 + DRF (backend, tests via `manage.py test`), Next.js 15 + React 18 + Radix/shadcn UI (frontend, no test runner).

**Spec:** `docs/superpowers/specs/2026-10-16-multi-knowledge-base-design.md`

## Global Constraints

- KB names are trimmed, 1–100 characters, and unique per project **ignoring case**: "products" duplicates "Products".
- New KB collections are named `kb_{project.slug}_{kb.id.hex}`. Existing KBs keep their stored `vector_collection`.
- The default KB is the one with `is_default=True`, at most one per project. It is auto-created as `name="default"` when missing.
- API errors keep the existing shape `{"success": false, "error": {"code", "message"}}`. Codes used here:
  - `missing_project` 400
  - `invalid_name` 400
  - `access_denied` 403
  - `not_found` 404 (KB detail)
  - `kb_not_found` 404 (upload/crawl)
  - `duplicate_name` 409
- Permissions: listing needs project membership (any role). Create, rename, upload and crawl need `role="admin"`.
- Frontend copy, verbatim:
  - "A knowledge base with this name already exists"
  - "No knowledge base — messages are ignored"
  - "Changing this applies to existing customers from their next message"
- localStorage key for the selected KB: `kb:<projectId>`.
- Backend code uses tabs in new and tab-indented code. Match the surrounding indentation in 4-space classes (`DocumentUploadView`, `WebCrawlSubmitView`).
- Run backend tests in the rebuilt `celery` image, one app at a time, because `agents` and `knowledge` clash on the `tests` module name:
  - `docker compose run --rm --no-deps -T celery python manage.py test knowledge`
  - `docker compose run --rm --no-deps -T celery python manage.py test channels_app`

  Postgres must be running (`docker compose up -d postgres`).

## Review Focus

1. **Stale KB in localStorage.** The stored id belongs to a deleted KB or another project. Expected: silently fall back to the default KB. `pickKb` (Task 5) does this; check by hand in Task 8.
2. **Malformed `kb_id` / `project_id`, such as `"not-a-uuid"`.** Expected: 404/403, not a 500 traceback. Tested in Task 2 (`project_id`) and Task 3 (`kb_id`).
3. **Renaming a KB to its own name in different case ("Products" → "products").** Expected: allowed (200), not a 409 against itself. Tested in Task 2.
4. **Non-Latin KB names, such as Bengali "পণ্য তালিকা".** Expected: created fine, with an ASCII-only collection name. Tested in Task 1.
5. **Two requests creating the same KB name at once.** Expected: one 201 and one 409, never a 500. A DB constraint plus `IntegrityError` handling covers it (Task 2). Not unit-tested; the constraint is the guarantee.

---

### Task 1: KnowledgeBase model — default flag, unique names, id-based collections

**Files:**
- Modify: `backend/knowledge/models.py:75-98` (the `KnowledgeBase` class)
- Create: `backend/knowledge/migrations/0012_knowledgebase_default_and_unique_name.py`
- Modify: `backend/channels_app/service/conversation_service.py:11-27` (delete `get_default_knowledge_base`) and `:77-78`
- Modify: `backend/channels_app/api.py:9` (drop the unused import)
- Modify: `backend/channels_app/tasks.py:120` (the reply context bug)
- Modify: `backend/knowledge/views.py:135-140` and `:916-920` (the temporary callers of `get_vector_collection_name`; Task 3 replaces them fully)
- Modify: `backend/knowledge/tests/test_document_upload.py:37-41`
- Create: `backend/knowledge/tests/test_knowledge_base_model.py`
- Create: `backend/channels_app/tests/test_reply_context.py`

**Interfaces:**
- Produces:
  - `KnowledgeBase.is_default: bool`
  - `KnowledgeBase.get_default(project) -> KnowledgeBase` (classmethod; get-or-create)
  - `KnowledgeBase.save()` fills an empty `vector_collection` with `kb_{project.slug}_{id.hex}`
- Removes:
  - `KnowledgeBase.get_vector_collection_name`
  - `conversation_service.get_default_knowledge_base`

- [ ] **Step 1: Write the failing tests**

`backend/knowledge/tests/test_knowledge_base_model.py`:

```python
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import Project
from knowledge.models import KnowledgeBase


class KnowledgeBaseModelTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-kb")

	def test_collection_named_by_id_for_any_name(self):
		kb = KnowledgeBase.objects.create(project=self.project, name="পণ্য তালিকা")
		assert kb.vector_collection == f"kb_p-kb_{kb.id.hex}"
		assert kb.vector_collection.isascii()

	def test_get_default_creates_once_and_survives_rename(self):
		kb = KnowledgeBase.get_default(self.project)
		assert kb.is_default and kb.name == "default"
		kb.name = "Main"
		kb.save()
		assert KnowledgeBase.get_default(self.project).id == kb.id
		assert KnowledgeBase.objects.filter(project=self.project).count() == 1

	def test_names_unique_per_project_ignoring_case(self):
		KnowledgeBase.objects.create(project=self.project, name="Products")
		with self.assertRaises(IntegrityError), transaction.atomic():
			KnowledgeBase.objects.create(project=self.project, name="products")
		other = Project.objects.create(name="Q", slug="q-kb")
		KnowledgeBase.objects.create(project=other, name="Products")  # other project: fine
```

`backend/channels_app/tests/test_reply_context.py`:

```python
from django.test import TestCase

from accounts.models import Project
from channels_app.tasks import build_reply_context
from knowledge.models import KnowledgeBase


class ReplyContextTest(TestCase):
	def test_uses_stored_collection_after_rename(self):
		project = Project.objects.create(name="P", slug="p-ctx")
		kb = KnowledgeBase.objects.create(project=project, name="default", vector_collection="kb_p-ctx_default_legacy")
		kb.name = "Products"
		kb.save()
		context, _ = build_reply_context(project, kb)
		assert context.vector_collection == "kb_p-ctx_default_legacy"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose run --rm --no-deps -T celery python manage.py test knowledge.tests.test_knowledge_base_model`
Expected: errors, because `KnowledgeBase.get_default` does not exist, a KB created without `vector_collection` gets `""`, and no `IntegrityError` is raised.

Run: `docker compose run --rm --no-deps -T celery python manage.py test channels_app.tests.test_reply_context`
Expected: FAIL. `vector_collection` is rebuilt from the name, giving `kb_p-ctx_Products_...` instead of the stored value.

- [ ] **Step 3: Update the model**

In `backend/knowledge/models.py`, add `from django.db.models.functions import Lower` below `from django.db import models`. Then replace the whole `KnowledgeBase` class (lines 75-98, from `class KnowledgeBase` through the end of `get_vector_collection_name`) with:

```python
class KnowledgeBase(models.Model):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="knowledge_bases")
	name = models.CharField(max_length=255)
	vector_collection = models.CharField(max_length=255, unique=True)
	# Fallback for uploads, crawls and test chats that name no KB. A flag, not the name "default", so it survives a rename.
	is_default = models.BooleanField(default=False)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(Lower("name"), "project", name="kb_unique_name_per_project"),
			models.UniqueConstraint(fields=["project"], condition=models.Q(is_default=True), name="kb_one_default_per_project"),
		]

	def __str__(self) -> str:
		return f"{self.project.name} - {self.name}"

	def save(self, *args, **kwargs):
		# Named by id, not name: a rename never touches Qdrant, and any name (spaces, Bengali) gives a valid collection name
		if not self.vector_collection:
			self.vector_collection = f"kb_{self.project.slug}_{self.id.hex}"
		super().save(*args, **kwargs)

	@classmethod
	def get_default(cls, project) -> "KnowledgeBase":
		kb, _ = cls.objects.get_or_create(project=project, is_default=True, defaults={"name": "default"})
		return kb
```

- [ ] **Step 4: Write the migration**

`backend/knowledge/migrations/0012_knowledgebase_default_and_unique_name.py`:

```python
import django.db.models.functions.text
from django.db import migrations, models


def mark_defaults(apps, schema_editor):
	# Until now the default KB was found by its name
	apps.get_model("knowledge", "KnowledgeBase").objects.filter(name="default").update(is_default=True)


class Migration(migrations.Migration):
	dependencies = [
		("knowledge", "0011_memory_suggestion"),
	]

	operations = [
		migrations.AddField(
			model_name="knowledgebase",
			name="is_default",
			field=models.BooleanField(default=False),
		),
		migrations.RunPython(mark_defaults, migrations.RunPython.noop),
		migrations.AddConstraint(
			model_name="knowledgebase",
			constraint=models.UniqueConstraint(
				django.db.models.functions.text.Lower("name"), "project", name="kb_unique_name_per_project"
			),
		),
		migrations.AddConstraint(
			model_name="knowledgebase",
			constraint=models.UniqueConstraint(
				condition=models.Q(("is_default", True)), fields=("project",), name="kb_one_default_per_project"
			),
		),
	]
```

Then verify that the model and the migration agree:

Run: `docker compose run --rm --no-deps -T celery python manage.py makemigrations knowledge --check --dry-run`
Expected: `No changes detected`. If Django proposes a migration, adjust `0012` to match it rather than adding a second migration.

- [ ] **Step 5: Switch callers to the new model API**

`backend/channels_app/service/conversation_service.py`:
- Delete the whole `get_default_knowledge_base` function (lines 11-27).
- In `create_conversation`, replace

```python
	if knowledge_base is None:
		knowledge_base = get_default_knowledge_base(project)
```

with

```python
	if knowledge_base is None:
		knowledge_base = KnowledgeBase.get_default(project)
```

`backend/channels_app/api.py:9`: change

```python
from .service.conversation_service import create_conversation, get_default_knowledge_base
```

to

```python
from .service.conversation_service import create_conversation
```

`backend/channels_app/tasks.py:120`: change

```python
		vector_collection=KnowledgeBase.get_vector_collection_name(project, knowledge_base.name),
```

to

```python
		vector_collection=knowledge_base.vector_collection,
```

`backend/knowledge/views.py`: in both `KnowledgeBase.objects.get_or_create(...)` calls (upload at ~line 136 and crawl at ~line 916), delete the `defaults={'vector_collection': ...}` argument line, because `save()` now fills the collection. Task 3 replaces these calls entirely; this step only keeps the code importable.

`backend/knowledge/tests/test_document_upload.py:37-41`: replace

```python
		self.kb = KnowledgeBase.objects.create(
			project=self.project,
			name="default",
			vector_collection=KnowledgeBase.get_vector_collection_name(self.project, "default")
		)
```

with

```python
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", is_default=True)
```

Confirm nothing else references the removed names:

Run: `grep -rn "get_vector_collection_name\|get_default_knowledge_base" backend --include=*.py`
Expected: no output, except possibly the `docs/` plan files, which are outside `backend`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `docker compose run --rm --no-deps -T celery python manage.py test knowledge`
Expected: all pass, including the 3 new model tests.

Run: `docker compose run --rm --no-deps -T celery python manage.py test channels_app`
Expected: all pass, including `test_uses_stored_collection_after_rename`.

- [ ] **Step 7: Commit**

```bash
git add backend/knowledge/models.py backend/knowledge/migrations/0012_knowledgebase_default_and_unique_name.py \
  backend/channels_app/service/conversation_service.py backend/channels_app/api.py backend/channels_app/tasks.py \
  backend/knowledge/views.py backend/knowledge/tests/test_document_upload.py \
  backend/knowledge/tests/test_knowledge_base_model.py backend/channels_app/tests/test_reply_context.py
git commit -m "feat(kb): default flag, unique names and id-based collections for knowledge bases

Replies now search the KB's stored collection instead of one rebuilt from its name.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Knowledge base list / create / rename API

**Files:**
- Modify: `backend/knowledge/views.py`
  - Imports at lines 1-20
  - Replace `KnowledgeBaseListView` at lines 36-65
- Modify: `backend/knowledge/urls.py`
- Create: `backend/knowledge/tests/test_knowledge_bases.py`

**Interfaces:**
- Consumes: `KnowledgeBase.get_default(project)` and auto `vector_collection` (Task 1).
- Produces:
  - `GET /api/kb/knowledge-bases/?project_id=` → `data.knowledge_bases: [{id, name, is_default, document_count, created_at}]` and `data.count`
  - `POST /api/kb/knowledge-bases/` with `{project_id, name}` → 201, `data.knowledge_base`
  - `PATCH /api/kb/knowledge-bases/<uuid>/` with `{name}` → 200, `data.knowledge_base`
  - Helper `_project_user(user, project_id, admin=False) -> ProjectUser | None` in `knowledge/views.py`. Task 3 reuses it.

- [ ] **Step 1: Write the failing tests**

`backend/knowledge/tests/test_knowledge_bases.py`:

```python
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from knowledge.models import Document, KnowledgeBase

URL = "/api/kb/knowledge-bases/"


class KnowledgeBaseApiTest(TestCase):
	def setUp(self):
		User = get_user_model()
		self.admin = User.objects.create_user(username="admin@x.com", email="admin@x.com", password="pw-12345678")
		self.member = User.objects.create_user(username="member@x.com", email="member@x.com", password="pw-12345678")
		self.outsider = User.objects.create_user(username="out@x.com", email="out@x.com", password="pw-12345678")
		self.project = Project.objects.create(name="P", slug="p-kbapi")
		ProjectUser.objects.create(project=self.project, user=self.admin, role="admin")
		ProjectUser.objects.create(project=self.project, user=self.member, role="member")
		self.client = APIClient()

	def as_user(self, user):
		self.client.force_authenticate(user=user)
		return self.client

	def create(self, name, user=None):
		return self.as_user(user or self.admin).post(URL, {"project_id": str(self.project.id), "name": name}, format="json")

	def rename(self, kb, name, user=None):
		return self.as_user(user or self.admin).patch(f"{URL}{kb.id}/", {"name": name}, format="json")

	def test_list_puts_default_first_with_top_level_document_counts(self):
		kb = KnowledgeBase.objects.create(project=self.project, name="Alpha")
		parent = Document.objects.create(kb=kb, source_type="url")
		Document.objects.create(kb=kb, source_type="url", parent_crawl_id=parent)  # crawled child page: not counted
		res = self.as_user(self.member).get(URL, {"project_id": str(self.project.id)})
		assert res.status_code == 200
		rows = res.data["data"]["knowledge_bases"]
		assert [(r["name"], r["is_default"], r["document_count"]) for r in rows] == [("default", True, 0), ("Alpha", False, 1)]

	def test_list_denies_outsider_and_malformed_project(self):
		assert self.as_user(self.outsider).get(URL, {"project_id": str(self.project.id)}).status_code == 403
		assert self.as_user(self.member).get(URL, {"project_id": "not-a-uuid"}).status_code == 403
		assert self.as_user(self.member).get(URL).status_code == 400

	def test_create_trims_name_and_names_collection_by_id(self):
		res = self.create("  Products ")
		assert res.status_code == 201
		kb = KnowledgeBase.objects.get(id=res.data["data"]["knowledge_base"]["id"])
		assert kb.name == "Products" and not kb.is_default
		assert kb.vector_collection == f"kb_p-kbapi_{kb.id.hex}"
		assert KnowledgeBase.objects.filter(project=self.project, is_default=True).exists()

	def test_create_rejects_duplicate_ignoring_case(self):
		assert self.create("Products").status_code == 201
		res = self.create("products")
		assert res.status_code == 409 and res.data["error"]["code"] == "duplicate_name"

	def test_create_rejects_blank_and_long_names(self):
		assert self.create("   ").status_code == 400
		assert self.create("x" * 101).status_code == 400

	def test_create_requires_admin(self):
		assert self.create("Products", user=self.member).status_code == 403
		assert self.create("Products", user=self.outsider).status_code == 403

	def test_rename_default_keeps_it_default(self):
		kb = KnowledgeBase.get_default(self.project)
		res = self.rename(kb, "Main")
		assert res.status_code == 200 and res.data["data"]["knowledge_base"]["name"] == "Main"
		assert KnowledgeBase.get_default(self.project).id == kb.id

	def test_rename_to_own_name_in_other_case_is_allowed(self):
		kb = KnowledgeBase.objects.create(project=self.project, name="Products")
		assert self.rename(kb, "products").status_code == 200

	def test_rename_rejects_duplicate_non_admin_and_unknown(self):
		KnowledgeBase.objects.create(project=self.project, name="Products")
		kb = KnowledgeBase.objects.create(project=self.project, name="FAQ")
		assert self.rename(kb, "PRODUCTS").status_code == 409
		assert self.rename(kb, "New", user=self.member).status_code == 403
		res = self.as_user(self.admin).patch(f"{URL}00000000-0000-0000-0000-000000000000/", {"name": "x"}, format="json")
		assert res.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose run --rm --no-deps -T celery python manage.py test knowledge.tests.test_knowledge_bases`
Expected: failures:
- POST returns 405.
- The PATCH URL returns 404 for all cases.
- The list lacks `is_default` and `document_count`.
- The outsider gets 200.

- [ ] **Step 3: Implement the views**

In `backend/knowledge/views.py`, add to the imports:

```python
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from accounts.models import ProjectUser
```

Replace the whole `KnowledgeBaseListView` class (lines 36-65) with:

```python
def _project_user(user, project_id, admin: bool = False):
	"""The user's membership in project_id (admin role if `admin`), or None — also for a malformed id."""
	try:
		qs = ProjectUser.objects.filter(project_id=project_id, user=user)
		if admin:
			qs = qs.filter(role="admin")
		return qs.select_related("project").first()
	except (ValidationError, ValueError):
		return None


def _kb_error(code: str, message: str, http_status: int) -> Response:
	return Response({"success": False, "error": {"code": code, "message": message}}, status=http_status)


def _kb_dict(kb: KnowledgeBase, document_count: int) -> dict:
	return {
		"id": str(kb.id),
		"name": kb.name,
		"is_default": kb.is_default,
		"document_count": document_count,
		"created_at": kb.created_at.isoformat(),
	}


def _top_level_doc_count(kb: KnowledgeBase) -> int:
	return kb.documents.filter(parent_crawl_id__isnull=True).count()


def _clean_kb_name(project, raw, exclude_id=None):
	"""(name, None) when usable, else (None, error response)."""
	name = (raw or "").strip()
	if not 1 <= len(name) <= 100:
		return None, _kb_error("invalid_name", "Name must be 1-100 characters", status.HTTP_400_BAD_REQUEST)
	if KnowledgeBase.objects.filter(project=project, name__iexact=name).exclude(id=exclude_id).exists():
		return None, _kb_error("duplicate_name", "A knowledge base with this name already exists", status.HTTP_409_CONFLICT)
	return name, None


class KnowledgeBaseListView(APIView):
	"""List a project's knowledge bases (default first) or create one."""
	permission_classes = [IsAuthenticated]

	def get(self, request):
		project_id = request.query_params.get('project_id')
		if not project_id:
			return _kb_error("missing_project", "project_id is required", status.HTTP_400_BAD_REQUEST)
		project_user = _project_user(request.user, project_id)
		if not project_user:
			return _kb_error("access_denied", "No access to this project", status.HTTP_403_FORBIDDEN)
		project = project_user.project
		# Created up front: every project shows its default, and a KB the user names "default" can't collide with it later
		KnowledgeBase.get_default(project)
		kbs = KnowledgeBase.objects.filter(project=project).annotate(
			doc_count=Count("documents", filter=Q(documents__parent_crawl_id__isnull=True))
		).order_by("-is_default", "name")
		kb_data = [_kb_dict(kb, kb.doc_count) for kb in kbs]
		return Response({"success": True, "data": {"knowledge_bases": kb_data, "count": len(kb_data)}}, status=status.HTTP_200_OK)

	def post(self, request):
		project_user = _project_user(request.user, request.data.get("project_id"), admin=True)
		if not project_user:
			return _kb_error("access_denied", "No admin access to this project", status.HTTP_403_FORBIDDEN)
		project = project_user.project
		KnowledgeBase.get_default(project)
		name, error = _clean_kb_name(project, request.data.get("name"))
		if error:
			return error
		kb = KnowledgeBase(project=project, name=name)
		try:
			with transaction.atomic():
				kb.save()
		except IntegrityError:  # same name created concurrently
			return _kb_error("duplicate_name", "A knowledge base with this name already exists", status.HTTP_409_CONFLICT)
		return Response({"success": True, "data": {"knowledge_base": _kb_dict(kb, 0)}}, status=status.HTTP_201_CREATED)


class KnowledgeBaseDetailView(APIView):
	"""Rename a knowledge base. Only the label changes; its collection, documents and conversations stay."""
	permission_classes = [IsAuthenticated]

	def patch(self, request, kb_id):
		kb = KnowledgeBase.objects.filter(id=kb_id).select_related("project").first()
		if not kb:
			return _kb_error("not_found", "Knowledge base not found", status.HTTP_404_NOT_FOUND)
		if not _project_user(request.user, kb.project_id, admin=True):
			return _kb_error("access_denied", "No admin access to this project", status.HTTP_403_FORBIDDEN)
		name, error = _clean_kb_name(kb.project, request.data.get("name"), exclude_id=kb.id)
		if error:
			return error
		kb.name = name
		try:
			with transaction.atomic():
				kb.save(update_fields=["name"])
		except IntegrityError:
			return _kb_error("duplicate_name", "A knowledge base with this name already exists", status.HTTP_409_CONFLICT)
		return Response({"success": True, "data": {"knowledge_base": _kb_dict(kb, _top_level_doc_count(kb))}}, status=status.HTTP_200_OK)
```

In `backend/knowledge/urls.py`:
- Add `KnowledgeBaseDetailView` to the `from .views import (...)` list, next to `KnowledgeBaseListView`.
- Add below the list route:

```python
	path("knowledge-bases/<uuid:kb_id>/", KnowledgeBaseDetailView.as_view(), name="knowledge-base-detail"),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose run --rm --no-deps -T celery python manage.py test knowledge`
Expected: all pass, including 9 new API tests.

- [ ] **Step 5: Commit**

```bash
git add backend/knowledge/views.py backend/knowledge/urls.py backend/knowledge/tests/test_knowledge_bases.py
git commit -m "feat(kb): create and rename knowledge bases; list checks project access

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Upload and crawl target a KB by id

**Files:**
- Modify: `backend/knowledge/views.py`
  - `DocumentUploadView.post` KB block, ~lines 134-140 at the time of writing; search for `# Get or create knowledge base`
  - `WebCrawlSubmitView.post`: the `kb_name = ...` line and the KB block
- Modify: `backend/knowledge/tests/test_document_upload.py`
- Create: `backend/knowledge/tests/test_crawl_view.py`

**Interfaces:**
- Consumes: `_project_user` is not needed here (both views already check admin access). Uses `KnowledgeBase.get_default` (Task 1).
- Produces: helper `_resolve_kb(project, kb_id) -> KnowledgeBase | None`. Upload and crawl accept a `kb_id` form/JSON field and no longer read `kb_name`.

- [ ] **Step 1: Write the failing tests**

In `backend/knowledge/tests/test_document_upload.py`, replace every request-data line `"kb_name": "default"` with `"kb_id": str(self.kb.id)`:

Run: `sed -i 's/"kb_name": "default"/"kb_id": str(self.kb.id)/' backend/knowledge/tests/test_document_upload.py`

Then replace the whole `test_upload_creates_knowledge_base` method (decorator through `self.assertIsNotNone(kb)`) with:

```python
	@patch('knowledge.views.run_ingestion_job.delay')
	def test_upload_goes_to_chosen_kb(self, mock_task):
		other = KnowledgeBase.objects.create(project=self.project, name="Products")
		data = {"project_id": str(self.project.id), "kb_id": str(other.id), "file": self.create_test_file()}
		response = self.client.post("/api/kb/documents/upload/", data, format="multipart")
		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.assertEqual(Document.objects.get().kb_id, other.id)

	@patch('knowledge.views.run_ingestion_job.delay')
	def test_upload_without_kb_id_uses_default(self, mock_task):
		data = {"project_id": str(self.project.id), "file": self.create_test_file()}
		response = self.client.post("/api/kb/documents/upload/", data, format="multipart")
		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.assertEqual(Document.objects.get().kb_id, self.kb.id)

	@patch('knowledge.views.run_ingestion_job.delay')
	def test_upload_rejects_foreign_or_malformed_kb_id(self, mock_task):
		foreign = KnowledgeBase.objects.create(project=Project.objects.create(name="Other", slug="other-proj"), name="x")
		for kb_id in (str(foreign.id), "not-a-uuid"):
			data = {"project_id": str(self.project.id), "kb_id": kb_id, "file": self.create_test_file()}
			response = self.client.post("/api/kb/documents/upload/", data, format="multipart")
			self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
		self.assertFalse(Document.objects.exists())
```

Confirm no `kb_name` request fields remain in the tests:

Run: `grep -n "kb_name" backend/knowledge/tests/test_document_upload.py`
Expected: no output.

`backend/knowledge/tests/test_crawl_view.py`:

```python
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from knowledge.models import Document, KnowledgeBase


@patch("knowledge.views.crawl_website.delay")
class CrawlKbTest(TestCase):
	def setUp(self):
		user = get_user_model().objects.create_user(username="a@x.com", email="a@x.com", password="pw-12345678")
		self.project = Project.objects.create(name="P", slug="p-crawl")
		ProjectUser.objects.create(project=self.project, user=user, role="admin")
		self.client = APIClient()
		self.client.force_authenticate(user=user)

	def crawl(self, **extra):
		data = {"url": "https://example.com", "depth": 1, "project_id": str(self.project.id), **extra}
		return self.client.post("/api/kb/crawl/", data, format="json")

	def test_crawl_goes_to_chosen_kb(self, _delay):
		kb = KnowledgeBase.objects.create(project=self.project, name="Products")
		assert self.crawl(kb_id=str(kb.id)).status_code == 201
		assert Document.objects.get().kb_id == kb.id

	def test_crawl_without_kb_id_uses_default(self, _delay):
		assert self.crawl().status_code == 201
		assert Document.objects.get().kb.is_default

	def test_crawl_rejects_unknown_kb(self, _delay):
		assert self.crawl(kb_id="not-a-uuid").status_code == 404
		assert not Document.objects.exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose run --rm --no-deps -T celery python manage.py test knowledge.tests.test_document_upload knowledge.tests.test_crawl_view`
Expected:
- `test_upload_goes_to_chosen_kb` / `test_crawl_goes_to_chosen_kb` fail, because `kb_id` is ignored and the document lands in the "default" KB.
- The 404 tests fail with 201.

- [ ] **Step 3: Implement**

In `backend/knowledge/views.py`, add next to the other helpers from Task 2:

```python
def _resolve_kb(project, kb_id):
	"""The project's KB `kb_id`, or its default KB when kb_id is empty; None if kb_id isn't one of the project's KBs."""
	if not kb_id:
		return KnowledgeBase.get_default(project)
	try:
		return KnowledgeBase.objects.filter(id=kb_id, project=project).first()
	except (ValidationError, ValueError):
		return None
```

In `DocumentUploadView.post` (4-space indented), replace

```python
            # Get or create knowledge base
            kb_name = request.data.get('kb_name', 'default')
            kb, created = KnowledgeBase.objects.get_or_create(
                project=project,
                name=kb_name,
            )
```

with

```python
            kb = _resolve_kb(project, request.data.get('kb_id'))
            if kb is None:
                return Response(
                    {"success": False, "error": {"code": "kb_not_found", "message": "Knowledge base not found"}},
                    status=status.HTTP_404_NOT_FOUND
                )
```

The exact text may differ slightly after Task 1 removed the `defaults=` line. Replace the whole `kb_name = ...` through the closing `)` of `get_or_create`.

In `WebCrawlSubmitView.post`:
- Delete the line `kb_name = request.data.get('kb_name', 'default')`.
- Replace the `# Get or create knowledge base` block (`kb, created = KnowledgeBase.objects.get_or_create(...)`) with the same `kb = _resolve_kb(project, request.data.get('kb_id'))` block shown above.

Confirm `kb_name` is gone from request handling:

Run: `grep -n "kb_name" backend/knowledge/views.py`
Expected: no output.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose run --rm --no-deps -T celery python manage.py test knowledge`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/knowledge/views.py backend/knowledge/tests/test_document_upload.py backend/knowledge/tests/test_crawl_view.py
git commit -m "feat(kb): upload and crawl into a chosen knowledge base by id

Missing kb_id uses the default KB; an unknown or foreign kb_id is a 404, so a typo no longer creates a KB.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Existing customers follow the channel's KB

**Files:**
- Modify: `backend/channels_app/service/conversation_service.py` (`ensure_contact_conversation`, ~line 109 at the time of writing)
- Modify: `backend/channels_app/service/facebook_messing.py:~131-134` (the `if conversation is None:` block)
- Modify: `backend/channels_app/service/whatsapp_messaging.py:~40-42`
- Modify: `backend/channels_app/tests/test_facebook_conversation.py`
- Modify: `backend/channels_app/tests/test_whatsapp.py`

**Interfaces:**
- Produces: `ensure_contact_conversation(contact, knowledge_base, name="") -> Conversation`. Same signature as before, but it now also sets an existing conversation's `knowledge_base` to the one passed in.

- [ ] **Step 1: Write the failing tests**

Append to the `FacebookConversationTest` class in `backend/channels_app/tests/test_facebook_conversation.py`:

```python
	@mock.patch("channels_app.service.facebook_messing.get_user_name", return_value="Ann Lee")
	def test_existing_customer_follows_channel_kb_change(self, _get_name, schedule):
		handle_facebook_message(payload("m1"))
		new_kb = KnowledgeBase.objects.create(project=self.project, name="kb2", vector_collection="vc-fb2")
		ChannelCredential.objects.filter(project=self.project).update(knowledge_base=new_kb)
		handle_facebook_message(payload("m2"))
		contact = CustomerContact.objects.get(external_id="psid-1")
		assert contact.conversation.knowledge_base_id == new_kb.id
		assert Message.objects.get(external_message_id="m2").conversation_id == contact.conversation_id
```

In `backend/channels_app/tests/test_whatsapp.py`:
- Add `Conversation` to the `from channels_app.models import ...` line.
- Add `from channels_app.service.conversation_service import ensure_contact_conversation`.
- Append this method to `WhatsAppInboundTest`:

```python
	def test_existing_customer_follows_channel_kb_change(self, schedule):
		handle_whatsapp_message(payload(text("wamid.1")))
		new_kb = KnowledgeBase.objects.create(project=self.project, name="kb2", vector_collection="vc-wa2")
		ChannelCredential.objects.filter(id=self.cred.id).update(knowledge_base=new_kb)
		handle_whatsapp_message(payload(text("wamid.2")))
		assert CustomerContact.objects.get(external_id="8801700000000").conversation.knowledge_base_id == new_kb.id
```

and add this new class at the end of the file:

```python
class ContactConversationKbTest(WhatsAppBase):
	def test_same_kb_leaves_conversation_unsaved(self):
		contact = CustomerContact.objects.create(project=self.project, platform="whatsapp", external_id="x")
		conversation = ensure_contact_conversation(contact, self.kb)
		with mock.patch.object(Conversation, "save") as save:
			assert ensure_contact_conversation(contact, self.kb).id == conversation.id
		save.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose run --rm --no-deps -T celery python manage.py test channels_app.tests.test_facebook_conversation channels_app.tests.test_whatsapp`
Expected: both `test_existing_customer_follows_channel_kb_change` tests FAIL (the conversation still has the old KB). `test_same_kb_leaves_conversation_unsaved` passes already.

- [ ] **Step 3: Implement**

Replace `ensure_contact_conversation` in `backend/channels_app/service/conversation_service.py` with:

```python
def ensure_contact_conversation(contact: CustomerContact, knowledge_base: KnowledgeBase, name: str = "") -> Conversation:
	"""The single conversation of an external-platform contact, created on first use; it follows the channel's current KB."""
	with transaction.atomic():
		contact = CustomerContact.objects.select_for_update().get(pk=contact.pk)  # two webhooks at once must not create two
		conversation = contact.conversation
		if conversation is None:
			return create_conversation(project=contact.project, knowledge_base=knowledge_base, name=name, contact=contact)
		if conversation.knowledge_base_id != knowledge_base.id:  # the channel's KB was changed since this conversation began
			conversation.knowledge_base = knowledge_base
			conversation.save(update_fields=["knowledge_base"])
		return conversation
```

In `backend/channels_app/service/facebook_messing.py`, replace

```python
			if conversation is None:
				conversation = ensure_contact_conversation(
					contact, knowledge_base, name=contact.display_name or default_name
				)
```

with

```python
			conversation = ensure_contact_conversation(
				contact, knowledge_base, name=contact.display_name or default_name
			)
```

In `backend/channels_app/service/whatsapp_messaging.py`, replace

```python
	conversation = contact.conversation or ensure_contact_conversation(
		contact, cred.knowledge_base, name=contact.display_name or "WhatsApp user"
	)
```

with

```python
	conversation = ensure_contact_conversation(
		contact, cred.knowledge_base, name=contact.display_name or "WhatsApp user"
	)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose run --rm --no-deps -T celery python manage.py test channels_app`
Expected: all pass. That includes `test_instagram.py`, which also goes through `handle_facebook_message`.

- [ ] **Step 5: Commit**

```bash
git add backend/channels_app/service/conversation_service.py backend/channels_app/service/facebook_messing.py \
  backend/channels_app/service/whatsapp_messaging.py backend/channels_app/tests/test_facebook_conversation.py \
  backend/channels_app/tests/test_whatsapp.py
git commit -m "feat(channels): existing customers follow the channel's knowledge base

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Frontend — KB API client, selection helpers, knowledge page bar

**Files:**
- Modify: `frontend/lib/api.ts`
  - `documentApi.upload` ~line 174
  - `documentApi.crawl` ~line 268
  - `knowledgeBaseApi` ~line 431
- Create: `frontend/lib/knowledgeBase.ts`
- Create: `frontend/components/knowledge/KnowledgeBaseBar.tsx`
- Modify: `frontend/app/(dashboard)/knowledge/page.tsx`

**Interfaces:**
- Consumes: the Task 2 and Task 3 endpoints.
- Produces, used by Tasks 6-7:
  - `type KnowledgeBase = { id: string; name: string; is_default: boolean; document_count: number; created_at: string }`, exported from `lib/api.ts`
  - `knowledgeBaseApi.list(projectId)`, `.create(projectId, name)`, `.rename(kbId, name)`
  - `getStoredKbId(projectId): string | null`
  - `storeKbId(projectId, kbId): void`
  - `pickKb(kbs, storedId): KnowledgeBase | undefined`
  - `kbOptionLabel(kb): string`

  All four helpers live in `lib/knowledgeBase.ts`.

- [ ] **Step 1: API client**

In `frontend/lib/api.ts`, replace the whole `knowledgeBaseApi` block (from `// Knowledge Base API functions` to its closing `};`) with:

```ts
export type KnowledgeBase = {
	id: string;
	name: string;
	is_default: boolean;
	document_count: number;
	created_at: string;
};

// Knowledge Base API functions
export const knowledgeBaseApi = {
	list: async (projectId: string) =>
		apiFetch<{ knowledge_bases: KnowledgeBase[]; count: number }>(`/api/kb/knowledge-bases/?project_id=${projectId}`),

	create: async (projectId: string, name: string) =>
		apiFetch<{ knowledge_base: KnowledgeBase }>("/api/kb/knowledge-bases/", {
			method: "POST",
			json: { project_id: projectId, name },
		}),

	rename: async (kbId: string, name: string) =>
		apiFetch<{ knowledge_base: KnowledgeBase }>(`/api/kb/knowledge-bases/${kbId}/`, {
			method: "PATCH",
			json: { name },
		}),
};
```

In `documentApi.upload`:
- Change the signature to `upload: async (file: File, projectId: string, kbId: string) => {`.
- Change `formData.append("kb_name", kbName);` to `formData.append("kb_id", kbId);`.

In `documentApi.crawl`:
- Change the signature to `crawl: async (url: string, depth: number, projectId: string, kbId: string, searchQuery?: string) => {`.
- In its `json`, change `kb_name: kbName,` to `kb_id: kbId,`.

- [ ] **Step 2: Selection helpers**

`frontend/lib/knowledgeBase.ts`:

```ts
import type { KnowledgeBase } from "@/lib/api";

// The KB last picked on the knowledge page, preselected for new test chats. Per browser, per project.
const storageKey = (projectId: string) => `kb:${projectId}`;

export function getStoredKbId(projectId: string): string | null {
	try {
		return localStorage.getItem(storageKey(projectId));
	} catch {
		return null; // storage blocked (private mode): fall back to the default KB
	}
}

export function storeKbId(projectId: string, kbId: string): void {
	try {
		localStorage.setItem(storageKey(projectId), kbId);
	} catch {
		// storage blocked: the selection just isn't remembered
	}
}

/** The stored KB if it still exists in this project, else the default one. */
export function pickKb(kbs: KnowledgeBase[], storedId: string | null): KnowledgeBase | undefined {
	return kbs.find((kb) => kb.id === storedId) ?? kbs.find((kb) => kb.is_default) ?? kbs[0];
}

/** "Products · 12 docs · default" */
export function kbOptionLabel(kb: KnowledgeBase): string {
	const docs = `${kb.document_count} doc${kb.document_count === 1 ? "" : "s"}`;
	return `${kb.name} · ${docs}${kb.is_default ? " · default" : ""}`;
}
```

- [ ] **Step 3: KB bar component**

`frontend/components/knowledge/KnowledgeBaseBar.tsx`:

```tsx
"use client";

import { useState } from "react";
import { Database, Pencil, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { knowledgeBaseApi, ApiError, type KnowledgeBase } from "@/lib/api";
import { kbOptionLabel } from "@/lib/knowledgeBase";
import { formatError } from "@/lib/errors";

type Props = {
	projectId: string;
	knowledgeBases: KnowledgeBase[];
	selected: KnowledgeBase | undefined;
	onSelect: (kb: KnowledgeBase) => void;
	/** After a create or rename; the parent refetches the list and selects `kb`. */
	onSaved: (kb: KnowledgeBase) => void;
};

export default function KnowledgeBaseBar({ projectId, knowledgeBases, selected, onSelect, onSaved }: Props) {
	const [mode, setMode] = useState<"create" | "rename" | null>(null);
	const [name, setName] = useState("");
	const [saving, setSaving] = useState(false);
	const [error, setError] = useState<string | null>(null);

	function open(next: "create" | "rename") {
		setMode(next);
		setName(next === "rename" ? selected?.name ?? "" : "");
		setError(null);
	}

	async function save() {
		if (!name.trim() || saving) return;
		setSaving(true);
		setError(null);
		try {
			const res = mode === "rename" && selected
				? await knowledgeBaseApi.rename(selected.id, name)
				: await knowledgeBaseApi.create(projectId, name);
			setMode(null);
			onSaved(res.data.knowledge_base);
		} catch (err) {
			setError(err instanceof ApiError && err.code === "duplicate_name"
				? "A knowledge base with this name already exists"
				: formatError(err));
		} finally {
			setSaving(false);
		}
	}

	return (
		<div className="flex flex-wrap items-center gap-2 rounded-lg border bg-card p-3">
			<Database className="h-5 w-5 text-primary" />
			<span className="text-sm font-medium">Knowledge base:</span>
			<Select
				value={selected?.id}
				onValueChange={(id: string) => {
					const kb = knowledgeBases.find((k) => k.id === id);
					if (kb) onSelect(kb);
				}}
			>
				<SelectTrigger className="w-72">
					<SelectValue placeholder="Loading..." />
				</SelectTrigger>
				<SelectContent>
					{knowledgeBases.map((kb) => (
						<SelectItem key={kb.id} value={kb.id}>
							{kbOptionLabel(kb)}
						</SelectItem>
					))}
				</SelectContent>
			</Select>
			<Button variant="ghost" size="icon" onClick={() => open("rename")} disabled={!selected} title="Rename knowledge base">
				<Pencil className="h-4 w-4" />
			</Button>
			<Button variant="outline" onClick={() => open("create")}>
				<Plus className="mr-2 h-4 w-4" />
				New knowledge base
			</Button>

			<Dialog open={mode !== null} onOpenChange={(isOpen) => !isOpen && setMode(null)}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>{mode === "rename" ? "Rename knowledge base" : "New knowledge base"}</DialogTitle>
					</DialogHeader>
					<div className="space-y-2">
						<Label htmlFor="kb-name">Name</Label>
						<Input
							id="kb-name"
							value={name}
							maxLength={100}
							autoFocus
							onChange={(e) => setName(e.target.value)}
							onKeyDown={(e) => e.key === "Enter" && save()}
						/>
						{error && <p className="text-sm text-destructive">{error}</p>}
					</div>
					<DialogFooter>
						<Button variant="outline" onClick={() => setMode(null)}>Cancel</Button>
						<Button onClick={save} disabled={saving || !name.trim()}>
							{saving ? "Saving..." : mode === "rename" ? "Rename" : "Create"}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</div>
	);
}
```

- [ ] **Step 4: Wire it into the knowledge page**

In `frontend/app/(dashboard)/knowledge/page.tsx`:

**Imports.** Change the `@/lib/api` import to also bring in `knowledgeBaseApi` and `type KnowledgeBase`, and add:

```tsx
import KnowledgeBaseBar from "@/components/knowledge/KnowledgeBaseBar";
import { getStoredKbId, pickKb, storeKbId } from "@/lib/knowledgeBase";
```

**State.** Next to the other `useState` calls:

```tsx
	const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
	const [selectedKb, setSelectedKb] = useState<KnowledgeBase | undefined>();
	const selectedKbId = selectedKb?.id;
```

**Fetching.**
- Place `fetchKnowledgeBases` directly above `fetchDocuments`.
- Replace `fetchDocuments` so documents are scoped to the selected KB:

```tsx
	const fetchKnowledgeBases = useCallback(async (selectId?: string) => {
		if (!currentProjectId) return;
		try {
			const response = await knowledgeBaseApi.list(currentProjectId);
			const kbs = response.data.knowledge_bases;
			setKnowledgeBases(kbs);
			const kb = pickKb(kbs, selectId ?? getStoredKbId(currentProjectId));
			setSelectedKb(kb);
			if (kb) storeKbId(currentProjectId, kb.id);
		} catch (error) {
			setAlert({ type: "error", message: formatError(error) });
		}
	}, [currentProjectId]);

	const fetchDocuments = useCallback(async () => {
		if (!currentProjectId || !selectedKbId) return;

		setLoading(true);
		try {
			const response = await documentApi.list(currentProjectId, selectedKbId);
			setDocuments(response.data.documents);
		} catch (error) {
			setAlert({ type: "error", message: formatError(error) });
		} finally {
			setLoading(false);
		}
	}, [currentProjectId, selectedKbId]);
```

**Effect.** In the mount `useEffect`:
- Call `fetchKnowledgeBases();` instead of `fetchDocuments();`.
- Add `fetchKnowledgeBases` to its dependency array.
- Add a second effect so documents load whenever the selection changes:

```tsx
	useEffect(() => {
		fetchDocuments();
	}, [fetchDocuments]);
```

**Selecting a KB.**

```tsx
	function handleSelectKb(kb: KnowledgeBase) {
		setSelectedKb(kb);
		if (currentProjectId) storeKbId(currentProjectId, kb.id);
	}
```

**Upload.** In `handleFileUpload`:
- After the `!currentProjectId` guard, add:

```tsx
		if (!selectedKb) return;
```

- Change `await documentApi.upload(file, currentProjectId);` to `await documentApi.upload(file, currentProjectId, selectedKb.id);`.
- Replace the success alert and refresh with:

```tsx
			const what = files.length === 1 ? files[0].name : `${files.length} files`;
			setAlert({ type: "success", message: `Uploaded ${what} to ${selectedKb.name}` });

			// Refresh documents and the per-KB document counts
			await Promise.all([fetchDocuments(), fetchKnowledgeBases()]);
```

**Crawl.** In the crawl submit handler:
- Add the same `if (!selectedKb) return;` guard next to its `currentProjectId` guard.
- Change the call to `await documentApi.crawl(crawlUrl, depth, currentProjectId, selectedKb.id, crawlSearchQuery.trim() || undefined);`.
- Change the alert to `` `Web crawl started for ${selectedKb.name}` ``.
- Change its refresh to `await Promise.all([fetchDocuments(), fetchKnowledgeBases()]);`.

**Delete document.** In `handleConfirmDeleteDocument`, after `setAlert({ type: "success", message: "Document deleted successfully" });`, add `fetchKnowledgeBases();` so the count updates.

**Suggestions.** In the `SuggestionsQueue` `onApproved` callback, add `fetchKnowledgeBases();` (an approved FAQ adds a document).

**Empty state.** In `renderDocumentTable`, replace `<p>No documents found</p>` with:

```tsx
					<p>No documents in <span className="font-medium">{selectedKb?.name ?? "this knowledge base"}</span> yet</p>
```

**Bar placement.** Insert directly before `{alert && (`:

```tsx
			{currentProjectId && (
				<KnowledgeBaseBar
					projectId={currentProjectId}
					knowledgeBases={knowledgeBases}
					selected={selectedKb}
					onSelect={handleSelectKb}
					onSaved={(kb) => fetchKnowledgeBases(kb.id)}
				/>
			)}
```

**Instructions card.** Instructions stay project-wide. Change the Custom Instructions `CardDescription` text to:

```
Define how your agent should behave, act, and take actions with customers. These apply to every knowledge base.
```

- [ ] **Step 5: Type-check and lint**

Run: `cd frontend && npx tsc --noEmit && npm run lint`
Expected: no errors.
- If `node_modules` is missing locally, run `npm install` first.
- Callers of `documentApi.upload`/`crawl` elsewhere would surface here as type errors. Fix them by passing a KB id.

- [ ] **Step 6: Commit**

```bash
git add frontend/lib/api.ts frontend/lib/knowledgeBase.ts frontend/components/knowledge/KnowledgeBaseBar.tsx "frontend/app/(dashboard)/knowledge/page.tsx"
git commit -m "feat(ui): knowledge base switcher with create and rename on the knowledge page

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Frontend — pick the KB for a new test chat; show KB on conversations

**Files:**
- Modify: `frontend/app/(dashboard)/conversations/page.tsx`
- Modify: `frontend/app/(dashboard)/conversations/[id]/page.tsx:~286-291` (header)

**Interfaces:**
- Consumes:
  - `knowledgeBaseApi.list` and `type KnowledgeBase` from `@/lib/api`
  - `getStoredKbId`, `pickKb`, `kbOptionLabel` from `@/lib/knowledgeBase` (Task 5)
  - `conversationApi.create({ project_slug, knowledge_base_id })` (exists)

- [ ] **Step 1: New test chat dialog**

In `conversations/page.tsx`, add the imports:

```tsx
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { getStoredKbId, kbOptionLabel, pickKb } from "@/lib/knowledgeBase";
```

Change the `@/lib/api` import to:

```tsx
import { conversationApi, knowledgeBaseApi, ApiError, type ConversationListItem, type KnowledgeBase } from "@/lib/api";
```

Add state:

```tsx
	const [newChatOpen, setNewChatOpen] = useState(false);
	const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
	const [newChatKbId, setNewChatKbId] = useState("");
```

Replace `handleCreateConversation` with:

```tsx
	async function openNewChat() {
		if (!currentProject) return;
		setError(null);
		try {
			const projectId = String(currentProject.id);
			const response = await knowledgeBaseApi.list(projectId);
			setKnowledgeBases(response.data.knowledge_bases);
			setNewChatKbId(pickKb(response.data.knowledge_bases, getStoredKbId(projectId))?.id ?? "");
			setNewChatOpen(true);
		} catch (err) {
			setError(formatError(err));
		}
	}

	async function handleCreateConversation() {
		if (!currentProject || !newChatKbId) return;

		setCreating(true);
		setError(null);
		try {
			const response = await conversationApi.create({
				project_slug: currentProject.slug,
				knowledge_base_id: newChatKbId,
			});
			router.push(`/conversations/${response.data.conversation.id}`);
		} catch (err) {
			setError(formatError(err));
			setCreating(false);
		}
	}
```

Replace the header button

```tsx
					<Button onClick={handleCreateConversation} disabled={creating}>
						{creating ? "Creating..." : "New Conversation"}
					</Button>
```

with

```tsx
					<Button onClick={openNewChat} disabled={creating}>
						New test chat
					</Button>
```

and add the dialog just before the closing `</div>` of the returned root element:

```tsx
			<Dialog open={newChatOpen} onOpenChange={setNewChatOpen}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>New test chat</DialogTitle>
						<DialogDescription>The bot answers only from the knowledge base you pick.</DialogDescription>
					</DialogHeader>
					<div className="space-y-2">
						<Label htmlFor="new-chat-kb">Knowledge base</Label>
						<Select value={newChatKbId || undefined} onValueChange={setNewChatKbId}>
							<SelectTrigger id="new-chat-kb">
								<SelectValue placeholder="Select a knowledge base" />
							</SelectTrigger>
							<SelectContent>
								{knowledgeBases.map((kb) => (
									<SelectItem key={kb.id} value={kb.id}>
										{kbOptionLabel(kb)}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					</div>
					<DialogFooter>
						<Button variant="outline" onClick={() => setNewChatOpen(false)}>Cancel</Button>
						<Button onClick={handleCreateConversation} disabled={creating || !newChatKbId}>
							{creating ? "Creating..." : "Start chat"}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
```

Change the empty-list text `No conversations yet. Create one to get started!` to `No conversations yet. Start a test chat to try your knowledge base.`

- [ ] **Step 2: KB badge in the list**

In the conversation row:
- Add after the channel `<Badge variant="outline">…</Badge>`:

```tsx
											<Badge variant="secondary">{conv.knowledge_base_name}</Badge>
```

- Change the meta line `{conv.message_count} messages • {conv.knowledge_base_name}` to `{conv.message_count} messages`. The KB is now in the badge.

- [ ] **Step 3: KB badge in the chat header**

In `conversations/[id]/page.tsx`:
- Add `import { Badge } from "@/components/ui/badge";`.
- Replace

```tsx
								<p className="text-xs text-muted-foreground">
									{conversation?.channel === "facebook" ? "Facebook" : "Test chat"}
									{conversation?.is_paused ? " · bot paused" : ""}
								</p>
```

with the following. It is a `div`, not a `p`, because `Badge` renders a `div` and a `div` inside a `p` is invalid HTML:

```tsx
								<div className="flex items-center gap-2 text-xs text-muted-foreground">
									<span>
										{conversation?.channel === "facebook" ? "Facebook" : "Test chat"}
										{conversation?.is_paused ? " · bot paused" : ""}
									</span>
									{conversation?.knowledge_base_name && (
										<Badge variant="secondary" className="text-xs">{conversation.knowledge_base_name}</Badge>
									)}
								</div>
```

- [ ] **Step 4: Type-check and lint**

Run: `cd frontend && npx tsc --noEmit && npm run lint`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add "frontend/app/(dashboard)/conversations/page.tsx" "frontend/app/(dashboard)/conversations/[id]/page.tsx"
git commit -m "feat(ui): choose the knowledge base for a new test chat; show KB on conversations

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Frontend — channels page KB polish

**Files:**
- Modify: `frontend/app/(dashboard)/channels/page.tsx`

**Interfaces:**
- Consumes: `type KnowledgeBase` from `@/lib/api`, and `kbOptionLabel` from `@/lib/knowledgeBase` (Task 5).

- [ ] **Step 1: Use the shared KB type and labels**

- Delete the local `type KnowledgeBase = { id: string; name: string; created_at: string; };` (~lines 60-64).
- Change the api import to `import { channelApi, platformApi, knowledgeBaseApi, type ChannelCredential, type KnowledgeBase, type Platform } from "@/lib/api";`.
- Add `import { kbOptionLabel } from "@/lib/knowledgeBase";`.

- [ ] **Step 2: Dropdown labels, edit hint, default preselect**

Change `renderKnowledgeBaseSelect` to take an `isEdit` flag:
- Its signature becomes `const renderKnowledgeBaseSelect = (id: string, isEdit: boolean = false) => (`.
- Inside, replace `{kb.name}` in the `SelectItem` with `{kbOptionLabel(kb)}`.
- Replace the hint paragraph's contents with:

```tsx
			<p className="text-xs text-muted-foreground">
				{knowledgeBases.length === 0
					? "No knowledge bases yet. Create one first; the AI answers from it."
					: isEdit
						? "Changing this applies to existing customers from their next message"
						: "The AI answers this channel's messages from this knowledge base"}
			</p>
```

Change the edit dialog call `{renderKnowledgeBaseSelect("edit-kb-select")}` to `{renderKnowledgeBaseSelect("edit-kb-select", true)}`.

Change `openAddDialog` to preselect the default KB:

```tsx
	const openAddDialog = () => {
		resetForm();
		const defaultKb = knowledgeBases.find((kb) => kb.is_default);
		if (defaultKb) setFormData((prev) => ({ ...prev, knowledge_base_id: defaultKb.id }));
		setAddDialogOpen(true);
	};
```

- [ ] **Step 3: Flag channels without a KB**

In the channel card's badge row, right after

```tsx
									{!channel.is_active && (
										<Badge variant="outline">Inactive</Badge>
									)}
```

add

```tsx
									{!channel.knowledge_base && (
										<Badge variant="destructive">
											<AlertCircle className="mr-1 h-3 w-3" />
											No knowledge base — messages are ignored
										</Badge>
									)}
```

`AlertCircle` is already imported.

- [ ] **Step 4: Type-check and lint**

Run: `cd frontend && npx tsc --noEmit && npm run lint`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add "frontend/app/(dashboard)/channels/page.tsx"
git commit -m "feat(ui): channels show KB doc counts, default KB preselected, warn when a channel has no KB

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: End-to-end check

No code changes unless a check fails. If one does, fix it in the task that owns the code and re-run that task's tests.

- [ ] **Step 1: Check for duplicate KB names, then migrate the dev DB**

Run:

```bash
docker compose exec -T postgres psql -U postgres -d auto_magic_pro -c "select project_id, lower(name), count(*) from knowledge_knowledgebase group by 1, 2 having count(*) > 1;"
```

Expected: 0 rows. If there are any, rename the duplicates before migrating.

Run: `docker compose run --rm --no-deps -T celery python manage.py migrate knowledge`
Expected: `Applying knowledge.0012_knowledgebase_default_and_unique_name... OK`

- [ ] **Step 2: Full backend suites**

Run:
- `docker compose run --rm --no-deps -T celery python manage.py test knowledge`
- `docker compose run --rm --no-deps -T celery python manage.py test channels_app`
- `docker compose run --rm --no-deps -T celery python manage.py test agents`

Expected: all OK.

- [ ] **Step 3: Manual UI check** (`docker compose up -d`, frontend on :3000)

1. **Knowledge page:**
   1. The bar shows `default · N docs · default`.
   2. Create "Products"; it becomes selected and the list is empty ("No documents in Products yet").
   3. Upload a TXT file; the alert says "Uploaded <file> to Products", and the dropdown count becomes 1.
2. **Rename:** rename "Products" to "products" (case-only change) and it's accepted. Create another KB and try renaming it to "PRODUCTS"; you get "A knowledge base with this name already exists".
3. **Persistence:** reload the page and the same KB is still selected. In devtools, set `localStorage["kb:<projectId>"]` to a garbage id and reload; it falls back to the default KB (Review Focus 1).
4. **New test chat:**
   1. On Conversations, "New test chat" preselects the KB from the knowledge page.
   2. Start the chat and ask about the uploaded file; the answer uses it.
   3. The list and the header show the KB badge.
5. **Isolation:** start a test chat on `default` and ask the same question. Expected: no answer from the Products file.
6. **Channels:**
   1. The dropdown shows doc counts and the default tag.
   2. "Add channel" preselects the default KB.
   3. The edit dialog shows the "Changing this applies to existing customers…" hint.

- [ ] **Step 4: Mark the spec done**

No commit is needed unless fixes were made. Report the results to the user.
