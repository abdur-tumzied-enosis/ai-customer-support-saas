# Staff Photos and Customer Photo Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let staff upload a photo from their computer in the dashboard composer and deliver it to the customer on that conversation's channel, and show customer photos as images in the chat.

**Architecture:** The browser PUTs the photo straight to R2 through the existing presign flow; the send endpoint receives only the R2 key, writes a Meta-acceptable JPEG variant, then the existing `deliver()` hands Meta a short-lived presigned URL. `_send_parts` gains a second part type (an image) so its per-part retry bookkeeping is reused unchanged. One auth-checked photo endpoint redirects to a presigned URL for display.

**Tech Stack:** Django + DRF, Celery, Pillow, Cloudflare R2 (django-storages S3 backend), Next.js (App Router) + TypeScript + Tailwind.

**Spec:** `docs/superpowers/specs/2026-11-13-staff-and-customer-photos-design.md`

## Global Constraints

- Photos only: JPEG, PNG, WebP, max 5 MB (`channels_app.media.PHOTO_ERROR`, `agents.image_search.MAX_IMAGE_BYTES`).
- **Outbound photos must be JPEG.** Meta's Send API documents JPEG/PNG/GIF for `attachment.payload.url` and not WebP, while `media_store._store` writes WebP. The staff-photo path therefore writes its own JPEG variant; the inbound path is not changed.
- Photo bytes never pass through the API server. Uploads are presigned browser PUTs; the send endpoint takes only the R2 key.
- Presigned GET URLs expire in 300 seconds (`core.storage.GET_EXPIRES`), and Meta fetches the image at send time. Presign inside the send closure, never when the parts list is built.
- Facebook, Instagram and WhatsApp only accept free-form messages within 24 hours of the customer's last inbound message. `self` and `widget` conversations have no window.
- No new database field: attachments live in the existing `Message.attachment_info` JSON (`[{type, path}]`).
- No product-catalog picker; staff uploads a file from their computer only.
- Tests run in Docker: `docker compose exec django python manage.py test <dotted.path> --noinput`.
- Backend code in this repo is tab-indented; follow the file you are editing.

## Review Focus

- A customer photo whose R2 storage failed has no `path`: the chat must still show "📷 Photo" rather than a broken image.
- A customer who sent several photos must have all of them shown, not only the first.
- A staff photo sent with no text must produce exactly one image part, not an empty text part.
- A retry after the image part already landed must resume at the text part and never resend the image.
- A message from another project must not be readable through the photo endpoint.
- A send whose outcome is *unknown* must not delete the stored photo — the customer may already have it.

---

### Task 1: Outbound messages carry attachments; the message dict exposes photo URLs

**Files:**
- Modify: `backend/channels_app/service/message_service.py` (`create_outbound_message`, ~line 122)
- Modify: `backend/channels_app/media.py` (add `photo_keys` near `has_photo`, line 37)
- Modify: `backend/channels_app/api.py` (`_message_dict`, line 225)
- Test: `backend/channels_app/tests/test_staff_photo.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `create_outbound_message(..., attachment_info: list | None = None) -> Message` — sets `has_attachments=bool(attachment_info)` and `attachment_info=attachment_info or []`.
  - `media.photo_keys(msg: Message) -> list[str]` — the stored `path` of every image attachment in order, skipping non-dicts, non-photos, and entries with no string `path`.
  - `_message_dict(msg, credits=())` now includes `"photo_urls": list[str]` — `f"/api/channels/messages/{msg.id}/photo?index={i}"` for each entry of `photo_keys(msg)` (index is the position in that list), else `[]`.

- [ ] **Step 1: Write the failing test**

```python
from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Project, ProjectUser
from channels_app.api import _message_dict
from channels_app.models import CustomerContact, Message
from channels_app.service.conversation_service import create_conversation, ensure_contact_conversation
from channels_app.service.message_service import create_outbound_message
from knowledge.models import KnowledgeBase


class OutboundPhotoTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-staff-photo", image_search_allowed=True)
		self.kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-staff-photo")
		self.contact = CustomerContact.objects.create(project=self.project, platform="whatsapp", external_id="w1")
		self.conv = ensure_contact_conversation(self.contact, self.kb)

	def message(self, info, **extra):
		return Message.objects.create(
			project=self.project, contact=self.contact, conversation=self.conv, direction="in", text="",
			has_attachments=bool(info), attachment_info=info, **extra,
		)

	def test_outbound_message_records_attachment(self):
		msg = create_outbound_message(
			project=self.project, contact=self.contact, conversation=self.conv, text="here",
			sender="staff", attachment_info=[{"type": "image", "path": "chat_media/p/x.jpg"}],
		)
		msg.refresh_from_db()
		assert msg.has_attachments is True
		assert msg.attachment_info == [{"type": "image", "path": "chat_media/p/x.jpg"}]

	def test_outbound_message_without_attachment_is_unchanged(self):
		msg = create_outbound_message(project=self.project, contact=self.contact, conversation=self.conv, text="hi")
		assert msg.has_attachments is False and msg.attachment_info == []

	def test_photo_urls_lists_every_stored_photo(self):
		msg = self.message([
			{"type": "image", "path": "chat_media/p/a.webp"},
			{"type": "audio", "path": "chat_media/p/v.ogg"},
			{"type": "image", "path": "chat_media/p/b.webp"},
		])
		assert _message_dict(msg)["photo_urls"] == [
			f"/api/channels/messages/{msg.id}/photo?index=0",
			f"/api/channels/messages/{msg.id}/photo?index=1",
		]

	def test_photo_urls_empty_without_a_stored_photo(self):
		unstored = self.message([{"type": "image", "payload": {"url": "https://scontent.xx.fbcdn.net/x"}}])
		d = _message_dict(unstored)
		assert d["has_photo"] is True and d["photo_urls"] == []
		plain = self.message([])
		assert _message_dict(plain)["has_photo"] is False and _message_dict(plain)["photo_urls"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec django python manage.py test channels_app.tests.test_staff_photo --noinput`
Expected: FAIL — `TypeError: create_outbound_message() got an unexpected keyword argument 'attachment_info'` and `KeyError: 'photo_urls'`.

- [ ] **Step 3: Implement**

- `media.py`: `def photo_keys(msg: Message) -> list[str]:` — return `[a["path"] for a in msg.attachment_info or [] if isinstance(a, dict) and is_photo(a) and isinstance(a.get("path"), str) and a["path"]]`.
- `message_service.py`: add `attachment_info: list | None = None` to the signature and docstring; in `create_kwargs` replace the hardcoded `"has_attachments": False, "attachment_info": []` with `"has_attachments": bool(attachment_info), "attachment_info": attachment_info or []`.
- `api.py`: import `photo_keys`; in `_message_dict` add
  `"photo_urls": [f"/api/channels/messages/{msg.id}/photo?index={i}" for i, _ in enumerate(photo_keys(msg))]`.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec django python manage.py test channels_app.tests.test_staff_photo --noinput`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/channels_app/service/message_service.py backend/channels_app/media.py backend/channels_app/api.py backend/channels_app/tests/test_staff_photo.py
git commit -m "feat(channels): outbound messages carry photo attachments"
```

---

### Task 2: A JPEG send variant and the outbound store

**Files:**
- Modify: `backend/agents/image_search.py` (add `_jpeg` beside `_webp`, line 51; `make_send_variant` beside `make_variants`, line 59)
- Modify: `backend/channels_app/media_store.py` (add `store_outbound_photo`; `_store` and the task are unchanged)
- Test: `backend/channels_app/tests/test_staff_photo.py` (append)

**Interfaces:**
- Consumes: `agents.image_search.load_image`, `DISPLAY_EDGE`, `read_stored`, `thumb_key`, `delete_keys`.
- Produces:
  - `agents.image_search.make_send_variant(data: bytes) -> bytes` — a JPEG (quality 85, longest edge `DISPLAY_EDGE`, upright, metadata-free); raises `NotAnImage`.
  - `channels_app.media_store.store_outbound_photo(conversation: Conversation, att: dict) -> str` — writes `chat_media/<project slug>/<uuid>.jpg` and its `_thumb.webp`, deletes the `tmp/` upload, returns the display key. Raises `MediaError` / `NotAnImage`.

- [ ] **Step 1: Write the failing test**

```python
from channels_app import media_store
from agents.image_search import make_send_variant
from channels_app.tests.test_widget import png_bytes
from core.tests.utils import fresh_storage, stored_files


@fresh_storage
class OutboundStoreTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-staff-photo", image_search_allowed=True)
		self.kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-staff-photo")
		self.contact = CustomerContact.objects.create(project=self.project, platform="whatsapp", external_id="w1")
		self.conv = ensure_contact_conversation(self.contact, self.kb)
		self.tmp = f"tmp/chat/p-staff-photo/{self.conv.id}/abc.jpg"

	def upload(self, data=None):
		default_storage.save(self.tmp, ContentFile(png_bytes() if data is None else data))
		return {"type": "image", "path": self.tmp}

	def test_send_variant_is_jpeg(self):
		data = make_send_variant(png_bytes(600))
		assert data[:3] == b"\xff\xd8\xff"  # JPEG magic

	def test_store_outbound_photo_writes_jpeg_and_thumb(self):
		key = media_store.store_outbound_photo(self.conv, self.upload())
		assert key.startswith("chat_media/p-staff-photo/") and key.endswith(".jpg")
		files = stored_files("chat_media/p-staff-photo/")
		assert key in files and f"{key[:-4]}_thumb.webp" in files
		assert not stored_files(f"tmp/chat/p-staff-photo/{self.conv.id}/")  # temp copy deleted

	def test_store_outbound_photo_rejects_non_image(self):
		with self.assertRaises(Exception):
			media_store.store_outbound_photo(self.conv, self.upload(b"nope, not an image"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec django python manage.py test channels_app.tests.test_staff_photo.OutboundStoreTest --noinput`
Expected: FAIL — `ImportError: cannot import name 'make_send_variant'`.

- [ ] **Step 3: Implement**

- `image_search.py`: `_jpeg(img, edge)` mirrors `_webp` but `img.save(buf, "JPEG", quality=JPEG_QUALITY)` (add `JPEG_QUALITY = 85` beside `WEBP_QUALITY`); `make_send_variant(data)` = `_jpeg(load_image(data), DISPLAY_EDGE)`.
- `media_store.store_outbound_photo(conversation, att)`: `data = read_stored(att["path"])` → `display = make_send_variant(data)` → `name = default_storage.save(f"chat_media/{conversation.project.slug}/{uuid4().hex}.jpg", ContentFile(display))` → `default_storage.save(thumb_key(name), ContentFile(make_variants(data)[1]))` → `delete_keys(att["path"])` → `return name`. Import `make_send_variant` alongside the existing `make_variants` import, and `Conversation` for the type hint.
  (Deliberately a second, small function rather than a flag on `_store`: the inbound path writes WebP and deletes nothing else, and the two have different callers, different validations and different formats.)

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec django python manage.py test channels_app.tests.test_staff_photo.OutboundStoreTest --noinput`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/agents/image_search.py backend/channels_app/media_store.py backend/channels_app/tests/test_staff_photo.py
git commit -m "feat(channels): store outbound photos as meta-acceptable jpeg"
```

---

### Task 3: Photo endpoint for the chat

**Files:**
- Modify: `backend/channels_app/api.py` (add `MessagePhotoView` after `MessageStatusView`, ~line 645)
- Modify: `backend/channels_app/urls_api.py:41` (add the route beside `messages/<uuid:message_id>/status`)
- Test: `backend/channels_app/tests/test_staff_photo.py` (append)

**Interfaces:**
- Consumes: `media.photo_keys(msg) -> list[str]` (Task 1), `core.storage.presign_get(key) -> str`, `core.storage.thumb_key(name) -> str`, `media.UPLOAD_PREFIXES`.
- Produces: `GET /api/channels/messages/<uuid>/photo?index=<n>[&size=thumb]` → 302 to a presigned R2 URL; 404 when that index has no readable stored photo or the caller cannot see the project.

- [ ] **Step 1: Write the failing test**

```python
@fresh_storage
class MessagePhotoViewTest(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user(username="pp@x.com", email="pp@x.com", password="pw123456")
		self.project = Project.objects.create(name="P", slug="p-photo-view", image_search_allowed=True)
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-photo-view")
		self.contact = CustomerContact.objects.create(project=self.project, platform="whatsapp", external_id="w1")
		self.conv = ensure_contact_conversation(self.contact, self.kb)
		self.client = APIClient()
		self.client.force_authenticate(self.user)

	def photo_message(self, info, conv=None):
		conv = conv or self.conv
		return Message.objects.create(
			project=conv.project, contact=conv.contact, conversation=conv, direction="in", text="",
			has_attachments=True, attachment_info=info,
		)

	def get(self, msg, **params):
		query = "".join(f"&{k}={v}" for k, v in params.items())
		return self.client.get(f"/api/channels/messages/{msg.id}/photo?index=0{query}")

	def test_redirects_to_presigned_url(self):
		msg = self.photo_message([{"type": "image", "path": "chat_media/p-photo-view/x.jpg"}])
		with mock.patch("channels_app.api.presign_get", return_value="https://r2.test/x.jpg") as presign:
			r = self.get(msg)
		assert r.status_code == 302 and r["Location"] == "https://r2.test/x.jpg"
		presign.assert_called_once_with("chat_media/p-photo-view/x.jpg")

	def test_thumb_variant_is_presigned(self):
		msg = self.photo_message([{"type": "image", "path": "chat_media/p-photo-view/x.jpg"}])
		with mock.patch("channels_app.api.presign_get", return_value="https://r2.test/t.webp") as presign:
			assert self.get(msg, size="thumb").status_code == 302
		presign.assert_called_once_with("chat_media/p-photo-view/x_thumb.webp")

	def test_second_index_addresses_the_second_photo(self):
		msg = self.photo_message([
			{"type": "image", "path": "chat_media/p-photo-view/a.jpg"},
			{"type": "image", "path": "chat_media/p-photo-view/b.jpg"},
		])
		with mock.patch("channels_app.api.presign_get", return_value="https://r2.test/b.jpg") as presign:
			r = self.client.get(f"/api/channels/messages/{msg.id}/photo?index=1")
		assert r.status_code == 302
		presign.assert_called_once_with("chat_media/p-photo-view/b.jpg")

	def test_404_for_missing_index_or_unstored_photo(self):
		msg = self.photo_message([
			{"type": "image", "path": "chat_media/p-photo-view/a.jpg"},
			{"type": "image", "payload": {"url": "https://scontent.xx.fbcdn.net/x"}},
		])
		assert self.client.get(f"/api/channels/messages/{msg.id}/photo?index=1").status_code == 404
		assert self.client.get(f"/api/channels/messages/{msg.id}/photo?index=5").status_code == 404
		assert self.client.get(f"/api/channels/messages/{msg.id}/photo").status_code == 404  # no index

	def test_404_for_another_project(self):
		other = Project.objects.create(name="O", slug="o-photo-view")
		okb = KnowledgeBase.objects.create(project=other, name="o", vector_collection="vc-o-view")
		ocontact = CustomerContact.objects.create(project=other, platform="whatsapp", external_id="w9")
		msg = self.photo_message([{"type": "image", "path": "chat_media/o-photo-view/x.jpg"}], conv=ensure_contact_conversation(ocontact, okb))
		assert self.get(msg).status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec django python manage.py test channels_app.tests.test_staff_photo.MessagePhotoViewTest --noinput`
Expected: FAIL — every request 404s because the route does not exist.

- [ ] **Step 3: Implement**

`MessagePhotoView(APIView)`, `permission_classes = [IsAuthenticated]`, `get(self, request, message_id)`:
1. `Message.objects.filter(id=message_id).select_related("project").first()`; if none → `_error("not_found", "Message not found", 404)`.
2. `ProjectUser.objects.filter(project=message.project, user=request.user).exists()`; if not → the same 404 (another project's message must be indistinguishable from a missing one, matching `_test_photo_conversation`).
3. `index = request.query_params.get("index")`; must parse as an int ≥ 0 → else 404. `keys = photo_keys(message)`; out of range → 404.
4. `key = keys[index]`; if not `key.startswith(UPLOAD_PREFIXES)` → 404 (never presign an arbitrary key).
5. `if request.query_params.get("size") == "thumb": key = thumb_key(key)`.
6. `return HttpResponseRedirect(presign_get(key))`. Import `HttpResponseRedirect`, `presign_get`, `thumb_key`, `UPLOAD_PREFIXES`, `photo_keys` at the top of `api.py`.

Route in `urls_api.py`: `path("messages/<uuid:message_id>/photo", MessagePhotoView.as_view(), name="message-photo"),`

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec django python manage.py test channels_app.tests.test_staff_photo.MessagePhotoViewTest --noinput`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/channels_app/api.py backend/channels_app/urls_api.py backend/channels_app/tests/test_staff_photo.py
git commit -m "feat(channels): serve chat photos through a presigned redirect"
```

---

### Task 4: Delivery sends image parts

**Files:**
- Modify: `backend/channels_app/facebook_client.py` (add `send_image_message` after `send_text_message`; `send_whatsapp_image` after `send_whatsapp_text`)
- Modify: `backend/channels_app/reply.py:61-120` (`_send_facebook`, `_send_whatsapp`, `_send_parts`)
- Modify: `backend/channels_app/tasks.py:8` (extend the `facebook_client` import)
- Test: `backend/channels_app/tests/test_staff_photo.py` (append)

**Interfaces:**
- Consumes: `reply._send_parts(out_msg, parts, send, channel)` with `parts: list[str | dict]`, where `{"image": <r2 key>}` is a photo.
- Produces:
  - `facebook_client.send_image_message(page_access_token: str, recipient_psid: str, image_url: str) -> dict`
  - `facebook_client.send_whatsapp_image(access_token: str, phone_number_id: str, to: str, image_url: str, callback_data: str = "") -> dict`
  - `reply._facebook_send(conversation, out_msg) -> callable` and `reply._whatsapp_send(conversation, out_msg) -> callable`, each returning the `send(part, ref) -> dict` closure `_send_parts` consumes.
  - Both senders use the text senders' return contract: `{"message_id"}`, `{"error": ...}`, `{"error": ..., "unknown": True}`, or `{}`.

- [ ] **Step 1: Write the failing test**

```python
from channels_app.reply import _facebook_send, _send_parts, _whatsapp_send


class DeliverImagePartTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-deliver-img")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-deliver-img")
		self.contact = CustomerContact.objects.create(project=self.project, platform="facebook", external_id="psid")
		self.conv = ensure_contact_conversation(self.contact, self.kb)
		self.image = mock.patch("channels_app.tasks.send_image_message", return_value={"message_id": "img1"}).start()
		self.text = mock.patch("channels_app.tasks.send_text_message", return_value={"message_id": "txt1"}).start()
		cred = mock.Mock()
		cred.get_credentials.return_value.page_access_token = "tok"
		mock.patch("channels_app.tasks.get_facebook_credential", return_value=cred).start()
		self.addCleanup(mock.patch.stopall)
		self.msg = Message.objects.create(
			project=self.project, contact=self.contact, conversation=self.conv, direction="out",
			text="hello", status="pending", attachment_info=[{"type": "image", "path": "chat_media/p/a.jpg"}],
		)

	def send_parts(self, parts, conv=None):
		conv = conv or self.conv
		with mock.patch("channels_app.reply.presign_get", return_value="https://r2.test/a.jpg"):
			return _send_parts(self.msg, parts, _facebook_send(conv, self.msg), conv.channel)

	def test_image_part_reaches_send_image_message(self):
		assert self.send_parts([{"image": "chat_media/p/a.jpg"}]) is True
		assert [c.kwargs["image_url"] for c in self.image.call_args_list] == ["https://r2.test/a.jpg"]
		assert self.msg.meta["parts_sent"] == 1

	def test_image_then_text_sends_both(self):
		assert self.send_parts([{"image": "chat_media/p/a.jpg"}, "hello"]) is True
		assert [c.kwargs["text"] for c in self.text.call_args_list] == ["hello"]
		assert self.msg.meta["parts_sent"] == 2

	def test_resume_after_the_image_part_skips_it(self):
		Message.objects.filter(id=self.msg.id).update(meta={"parts_sent": 1, "parts_total": 2})
		self.msg.refresh_from_db()
		assert self.send_parts([{"image": "chat_media/p/a.jpg"}, "hello"]) is True
		self.image.assert_not_called()  # the photo already landed
		assert self.text.call_count == 1

	def test_whatsapp_image_part_uses_image_link(self):
		wa = mock.patch("channels_app.tasks.send_whatsapp_image", return_value={"message_id": "w1"}).start()
		creds = mock.Mock(access_token="tok", phone_number_id="123")
		cred = mock.Mock()
		cred.get_credentials.return_value = creds
		mock.patch("channels_app.tasks.get_whatsapp_credential", return_value=cred).start()
		self.conv.channel = "whatsapp"
		with mock.patch("channels_app.reply.presign_get", return_value="https://r2.test/a.jpg"):
			assert _send_parts(self.msg, [{"image": "chat_media/p/a.jpg"}], _whatsapp_send(self.conv, self.msg), "whatsapp") is True
		assert [c.kwargs["image_url"] for c in wa.call_args_list] == ["https://r2.test/a.jpg"]

	def test_unknown_image_send_holds_the_message(self):
		self.image.return_value = {"unknown": True}
		assert self.send_parts([{"image": "chat_media/p/a.jpg"}]) is False
		assert self.msg.meta["inflight"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec django python manage.py test channels_app.tests.test_staff_photo.DeliverImagePartTest --noinput`
Expected: FAIL — `ImportError: cannot import name '_facebook_send'`.

- [ ] **Step 3: Implement**

- `facebook_client.send_image_message`: POST to `FACEBOOK_API_URL` with `{"recipient": {"id": recipient_psid}, "message": {"attachment": {"type": "image", "payload": {"url": image_url}}}}`. Mirror `send_text_message`'s error mapping exactly (200 with no `error` → return the body; a 5xx or a `never_sent`-false request exception → `{"unknown": True}`; anything else definite → `{}`). `never_sent` is already imported in that module. Skip the text sanitizing — there is no text.
- `facebook_client.send_whatsapp_image`: payload `{"messaging_product": "whatsapp", "to": to, "type": "image", "image": {"link": image_url}}` plus `biz_opaque_callback_data` when given; mirror `send_whatsapp_text`'s error mapping.
- `reply.py`: import `presign_get` from `core.storage`. Split the closure out of each sender:
  - `_facebook_send(conversation, out_msg)`: look up the credential (as today, `tasks.get_facebook_credential(out_msg, conversation.channel)`), then return a closure that sends one part — a dict → `tasks.send_image_message(page_access_token=..., recipient_psid=conversation.contact.external_id, image_url=presign_get(part["image"]))`; a str → the existing `tasks.send_text_message(...)`.
  - `_whatsapp_send(conversation, out_msg)`: same shape with `tasks.send_whatsapp_image` / `tasks.send_whatsapp_text`.
  - `_send_facebook` / `_send_whatsapp` become `_send_parts(out_msg, parts, _facebook_send(conversation, out_msg), conversation.channel)` (and the WhatsApp equivalent). The `reference_msg` parameter disappears; `deliver()`'s `reference_msg or out_msg` call already passes `out_msg` here, and `SENDERS` lambdas for `self`/`widget` keep their three-argument signature.
  - `_send_parts` signature becomes `parts: list`; its docstring says a part is text or `{"image": <key>}`. No other change: `len(parts)`, `parts_sent` and `inflight` keep working.
  - Presign inside the closure so every retry gets a fresh 300s URL.
- `tasks.py` line 8: extend the `from .facebook_client import ...` to include `send_image_message` and `send_whatsapp_image`, keeping `channels_app.tasks.*` the single patch point.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec django python manage.py test channels_app.tests.test_staff_photo.DeliverImagePartTest channels_app.tests.test_reply_pipeline --noinput`
Expected: PASS, including the existing pipeline suite (proves the bot's text path is unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/channels_app/facebook_client.py backend/channels_app/reply.py backend/channels_app/tasks.py backend/channels_app/tests/test_staff_photo.py
git commit -m "feat(channels): deliver image parts to facebook, instagram and whatsapp"
```

---

### Task 5: Staff photo presign and send endpoints

**Files:**
- Modify: `backend/channels_app/api.py` (`_test_photo_prefix` rename; extract the 24h window check from `ConversationStaffReplyView.post`, line 990; add two views after it)
- Modify: `backend/channels_app/urls_api.py`
- Test: `backend/channels_app/tests/test_staff_photo.py` (append)

**Interfaces:**
- Consumes: `media.presign_chat_photo`, `media.is_own_upload`, `media.uploaded_photo_ok`, `media_store.store_outbound_photo` (Task 2), `create_outbound_message` with `attachment_info` (Task 1), `reply.deliver` (Task 4).
- Produces:
  - `_chat_photo_prefix(conversation) -> str` — `f"tmp/chat/{conversation.project.slug}/{conversation.id}/"`.
  - `_window_error(conversation, last_in) -> Response | None` — the 24-hour refusal shared by the text and photo staff replies.
  - `POST /api/channels/conversations/<uuid>/staff-reply/photo/presign` `{content_type, size}` → `{key, url, headers}`.
  - `POST /api/channels/conversations/<uuid>/staff-reply/photo` `{key, text?}` → `201 {message}`.

- [ ] **Step 1: Write the failing test**

```python
from datetime import timedelta
from django.utils import timezone
from core.tests.utils import fresh_storage, stored_files


@fresh_storage
class StaffPhotoApiTest(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user(username="sp@x.com", email="sp@x.com", password="pw123456")
		self.project = Project.objects.create(name="P", slug="p-staff-api", image_search_allowed=True)
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-staff-api")
		self.contact = CustomerContact.objects.create(project=self.project, platform="facebook", external_id="psid")
		self.conv = ensure_contact_conversation(self.contact, self.kb)
		self.client = APIClient()
		self.client.force_authenticate(self.user)
		mock.patch("channels_app.media.presign_put", side_effect=fake_presign).start()
		self.deliver = mock.patch("channels_app.api.deliver", return_value=True).start()
		self.addCleanup(mock.patch.stopall)

	def url(self, presign=False, conv=None):
		return f"/api/channels/conversations/{(conv or self.conv).id}/staff-reply/photo" + ("/presign" if presign else "")

	def uploaded_key(self, conv=None, data=None):
		key = self.client.post(self.url(presign=True, conv=conv), {"content_type": "image/jpeg", "size": 100}, format="json").json()["data"]["key"]
		default_storage.save(key, ContentFile(png_bytes() if data is None else data))
		return key

	def test_presign_issues_conversation_scoped_key(self):
		r = self.client.post(self.url(presign=True), {"content_type": "image/jpeg", "size": 100}, format="json")
		assert r.status_code == 200, r.content
		assert r.json()["data"]["key"].startswith(f"tmp/chat/p-staff-api/{self.conv.id}/")

	def test_presign_rejects_bad_type_and_size(self):
		for body in ({"content_type": "image/gif", "size": 100}, {"content_type": "image/jpeg", "size": 6 * 1024 * 1024}):
			r = self.client.post(self.url(presign=True), body, format="json")
			assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_image"

	def test_send_creates_a_stored_jpeg_outbound_message(self):
		r = self.client.post(self.url(), {"key": self.uploaded_key(), "text": "this one?"}, format="json")
		assert r.status_code == 201, r.content
		data = r.json()["data"]["message"]
		assert data["photo_urls"] == [f"/api/channels/messages/{data['id']}/photo?index=0"]
		assert data["text"] == "this one?"
		msg = Message.objects.get(id=data["id"])
		assert msg.sender == "staff" and msg.direction == "out"
		assert msg.attachment_info[0]["path"].startswith("chat_media/p-staff-api/") and msg.attachment_info[0]["path"].endswith(".jpg")
		self.deliver.assert_called_once()

	def test_send_without_text_is_allowed(self):
		r = self.client.post(self.url(), {"key": self.uploaded_key()}, format="json")
		assert r.status_code == 201 and r.json()["data"]["message"]["text"] == ""

	def test_send_rejects_foreign_key(self):
		other = ensure_contact_conversation(
			CustomerContact.objects.create(project=self.project, platform="facebook", external_id="psid2"), self.kb)
		r = self.client.post(self.url(conv=other), {"key": self.uploaded_key()}, format="json")
		assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_upload"
		self.deliver.assert_not_called()

	def test_send_rejects_non_image_body_and_leaves_no_message(self):
		r = self.client.post(self.url(), {"key": self.uploaded_key(data=b"nope")}, format="json")
		assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_image"
		assert not Message.objects.filter(conversation=self.conv, direction="out").exists()
		assert not stored_files("chat_media/p-staff-api/")

	def test_send_outside_24h_window_refused(self):
		old = Message.objects.create(project=self.project, contact=self.contact, conversation=self.conv, direction="in", text="hi")
		Message.objects.filter(id=old.id).update(created_at=timezone.now() - timedelta(hours=25))
		r = self.client.post(self.url(), {"key": self.uploaded_key()}, format="json")
		assert r.status_code == 400 and r.json()["error"]["code"] == "outside_messaging_window"
		self.deliver.assert_not_called()

	def test_send_without_any_inbound_message_refused(self):
		r = self.client.post(self.url(), {"key": self.uploaded_key()}, format="json")
		assert r.status_code == 400 and r.json()["error"]["code"] == "outside_messaging_window"
		self.deliver.assert_not_called()

	def test_definite_failure_marks_failed_and_deletes_the_photo(self):
		self.deliver.return_value = False
		r = self.client.post(self.url(), {"key": self.uploaded_key(), "text": "x"}, format="json")
		assert r.status_code == 502 and r.json()["error"]["code"] == "send_failed"
		msg = Message.objects.get(conversation=self.conv, direction="out")
		assert msg.status == "failed"
		assert not stored_files("chat_media/p-staff-api/")

	def test_unknown_outcome_keeps_the_photo(self):
		def deliver_and_leave_inflight(conversation, msg, reference_msg):
			msg.meta = {**msg.meta, "inflight": 0}
			msg.save(update_fields=["meta"])
			return False
		self.deliver.side_effect = deliver_and_leave_inflight
		r = self.client.post(self.url(), {"key": self.uploaded_key()}, format="json")
		assert r.status_code == 502 and r.json()["error"]["code"] == "send_unknown"
		msg = Message.objects.get(conversation=self.conv, direction="out")
		assert msg.status == "unknown"
		assert stored_files("chat_media/p-staff-api/")  # the customer may already have it

	def test_test_conversation_can_send_staff_photo(self):
		ui = create_conversation(project=self.project, knowledge_base=self.kb, user_id=str(self.user.id))
		key = self.uploaded_key(conv=ui)
		assert self.client.post(self.url(conv=ui), {"key": key}, format="json").status_code == 201
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec django python manage.py test channels_app.tests.test_staff_photo.StaffPhotoApiTest --noinput`
Expected: FAIL — 404 for the missing routes.

- [ ] **Step 3: Implement**

- Rename `_test_photo_prefix` to `_chat_photo_prefix` and update its two existing call sites in `ConversationPhotoPresignView` / `ConversationPhotoView`.
- Extract the window check: `def _window_error(conversation, last_in):` returns the existing `outside_messaging_window` response for `conversation.channel in ("facebook", "instagram", "whatsapp")` when `last_in` is None or older than 24h, else `None`. Call it from `ConversationStaffReplyView.post` in place of the inline block (behavior unchanged).
- `ConversationStaffPhotoPresignView.post`: `_get_conversation` → 404; `presign_chat_photo(_chat_photo_prefix(conversation), content_type, size)` → 400 `invalid_image` when `None`; else 200 with the upload dict.
- `ConversationStaffPhotoView.post`: `_get_conversation` → 404; `last_in`; `_window_error` → return it; `is_own_upload` → 400 `invalid_upload`; `uploaded_photo_ok` → 400 `invalid_image`; `att = {"type": "image", "path": key}`; `key = store_outbound_photo(conversation, att)` inside a `try` that maps `MediaError`/`NotAnImage` to the same 400 `invalid_image`; create the message with `text=(request.data.get("text") or "").strip()`, `attachment_info=[{**att, "path": key}]`, `sender="staff"`, `meta=answered_marker(last_in)`; then the delivery branch:
  - `deliver(...)` True → `note_staff_activity`, `emit_event("message.staff_reply", ...)` with the same shape as the text view, `schedule_memory_update`, `transaction.on_commit(draft_knowledge_suggestion)`, return 201 with `_message_dict(msg)`.
  - False and `"inflight" in msg.meta` → `status = "unknown"`, save, `_error("send_unknown", "<the photo may or may not have been delivered>", 502)`, keep the objects.
  - False otherwise → `status = "failed"`, save, delete the display key and `thumb_key(key)`, `_error("send_failed", "The message could not be delivered to the customer", 502)`.
- Imports to add: `store_outbound_photo` from `.media_store`; `delete_keys`, `thumb_key` from `core.storage`; `MediaError` from `.media`; `NotAnImage` from `agents.image_search`. `answered_marker`, `schedule_memory_update`, `note_staff_activity`, `emit_event` are already imported in `api.py` — verify, add any that are missing.
- Routes in `urls_api.py`, above the plain `staff-reply` line: `path("conversations/<uuid:conversation_id>/staff-reply/photo/presign", ...)` then `path("conversations/<uuid:conversation_id>/staff-reply/photo", ...)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec django python manage.py test channels_app.tests.test_staff_photo channels_app.tests.test_test_chat_photo channels_app.tests.test_handoff_api --noinput`
Expected: PASS — the new suite plus the untouched test-chat and staff-reply suites.

- [ ] **Step 5: Commit**

```bash
git add backend/channels_app/api.py backend/channels_app/urls_api.py backend/channels_app/tests/test_staff_photo.py
git commit -m "feat(channels): staff can send a photo in a conversation"
```

---

### Task 6: Show photos in the chat, and keep them current

**Files:**
- Modify: `frontend/lib/api.ts` (`ConversationMessage`, ~line 656; add the two calls beside `staffReply`, line 774)
- Modify: `frontend/components/chat/MessageList.tsx` (`ChatMessage`, line 6; bubble, line 59-72)
- Modify: `frontend/app/(dashboard)/conversations/[id]/page.tsx` (`toChatMessage` ~line 97; the 5s polling effect ~line 54; `refreshPhotoMatches` ~line 278)

**Interfaces:**
- Consumes: `photo_urls` from `_message_dict` (Task 1); `POST .../staff-reply/photo/presign` and `POST .../staff-reply/photo` (Task 5).
- Produces:
  - `ConversationMessage.photo_urls?: string[]`
  - `ChatMessage.photoUrls?: string[]`
  - `conversationApi.staffPhotoPresign(conversationId: string, body: { content_type: string; size: number }) => Promise<ApiSuccessResponse<PresignedUpload & { key: string }>>`
  - `conversationApi.staffPhotoSend(conversationId: string, body: { key: string; text?: string }) => Promise<ApiSuccessResponse<{ message: ConversationMessage }>>`
- No frontend test runner is configured in this repo; the check is `npx tsc --noEmit`.

- [ ] **Step 1: Add the API surface**

In `lib/api.ts`: add `photo_urls?: string[];` to `ConversationMessage`, and the two methods next to `staffReply` (plain `apiFetch` with `method: "POST"`, mirroring `sendPhoto`/`staffReply`).

- [ ] **Step 2: Render every photo**

`MessageList.tsx`: add `photoUrls?: string[]` to `ChatMessage`. Replace the `📷 Photo` line with:

```tsx
{m.photoUrls?.length ? (
	/* customer photos are stored asynchronously, so a photo can arrive after the message does;
	   the API sends a list because one message may carry several photos */
	<div className="flex flex-wrap gap-2">
		{m.photoUrls.map((url) => (
			<a key={url} href={withApiBase(url)} target="_blank" rel="noreferrer" className="block">
				{/* eslint-disable-next-line @next/next/no-img-element */}
				<img
					src={`${withApiBase(url)}&size=thumb`}
					alt="Photo"
					loading="lazy"
					className="max-h-60 rounded-lg object-cover"
				/>
			</a>
		))}
	</div>
) : (
	m.hasPhoto && <span className="block">📷 Photo</span>
)}
```

Import `API_BASE_URL` from `@/lib/api` and add a local `const withApiBase = (path: string) => `${API_BASE_URL}${path}`;` — the same prefix `ProductTable.tsx` uses.

`page.tsx`: in `toChatMessage`, add `photoUrls: msg.photo_urls`.

- [ ] **Step 3: Merge refreshed photos in the poll**

The customer photo is stored by a Celery task *after* the message row exists, so the first response has
`has_photo: true` and no `photo_urls`. Today's poll only appends unseen messages, so the photo would appear only on a
reload.

In the 5s polling effect, build a map of the fresh messages by id and merge the refreshed fields into every message
already on screen (in addition to appending the new ones):

```tsx
setMessages((prev) => {
	const byId = new Map(latestRaw.map((m) => [m.id, m]));
	const merged = prev.map((m) => {
		const fresh = byId.get(m.id);
		// photo_urls/credits/photo_matches are written after the row exists, so refresh them in place
		return fresh ? { ...m, photoUrls: fresh.photo_urls, credits: fresh.credits } : m;
	});
	const known = new Set(prev.map((m) => m.id));
	const added = latest.filter((m) => !known.has(m.id));
	return added.length || merged.some((m, i) => m !== prev[i]) ? [...merged, ...added] : prev;
});
```

Keep a `latestRaw` alongside `latest` (the mapped `ChatMessage[]`) so the merge reads the API shape. Apply the same
`photoUrls` merge in `refreshPhotoMatches`, so the test-chat path (which has no poll) also picks its photo up.

- [ ] **Step 4: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/api.ts frontend/components/chat/MessageList.tsx "frontend/app/(dashboard)/conversations/[id]/page.tsx"
git commit -m "feat(chat): show and refresh photos in the conversation thread"
```

---

### Task 7: Staff photo button in the composer

**Files:**
- Modify: `frontend/components/chat/MessageComposer.tsx` (photo gate at line 160; add the `onSendStaffPhoto` prop)
- Modify: `frontend/app/(dashboard)/conversations/[id]/page.tsx` (wire the handler)

**Interfaces:**
- Consumes: `conversationApi.staffPhotoPresign` / `staffPhotoSend` (Task 6), `downscaleImage` / `putToR2` from `lib/upload.ts`.
- Produces: the `MessageComposer` prop `onSendStaffPhoto?: (file: File, text: string) => Promise<void>`; the button is hidden when it is absent.

- [ ] **Step 1: Composer**

- Add the optional `onSendStaffPhoto` prop.
- Show the button when `(canSimulate && mode === "ai" && onSendPhoto) || (mode === "staff" && onSendStaffPhoto)`; test-chat behavior is unchanged (it still requires image search on).
- `onPhotoPicked`: in staff mode `await run(() => onSendStaffPhoto(file, text.trim()))` and clear the text on success; otherwise keep the existing `onSendPhoto(file, text.trim())` call.
- `aria-label` and the `sr-only` text become "Send a photo", fitting both modes.

- [ ] **Step 2: Wire the handler**

In `page.tsx`:

```tsx
async function handleSendStaffPhoto(file: File, text: string) {
	const photo = await downscaleImage(file);
	const presigned = await conversationApi.staffPhotoPresign(conversationId, { content_type: photo.type, size: photo.size });
	await putToR2(presigned.data, photo);
	const res = await conversationApi.staffPhotoSend(conversationId, { key: presigned.data.key, text });
	setMessages((prev) => [...prev, toChatMessage(res.data.message)]);
	await fetchConversation();
}
```

Pass `onSendStaffPhoto={handleSendStaffPhoto}` to `MessageComposer`; import `downscaleImage` and `putToR2`. Errors are surfaced by the composer's existing `run()` helper, so the handler does not catch.

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/chat/MessageComposer.tsx "frontend/app/(dashboard)/conversations/[id]/page.tsx"
git commit -m "feat(chat): staff can send a photo from the composer"
```

---

### Task 8: Full verification

**Files:** none.

- [ ] **Step 1: Run the affected backend suites**

Run: `docker compose exec django python manage.py test channels_app knowledge agents --noinput`
Expected: PASS. `knowledge` and `agents` are included because `image_search.py` and `media.py` are shared surface; report any pre-existing unrelated failure rather than hiding it.

- [ ] **Step 2: Run the frontend build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Report**

State what passed and what was skipped. Notably: no real Meta send was exercised, so the image format and the
Instagram URL form are unproven against the live API until someone sends a photo on each channel.
