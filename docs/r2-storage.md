# R2 object storage

Every uploaded or received file (catalog photos, knowledge documents, chat photos, voice notes) lives in a
**private** Cloudflare R2 bucket. Browsers upload straight to R2 with presigned PUT URLs and read through
short-lived presigned GET URLs that Django hands out after its permission checks. File bytes never pass
through the Django web process.

| Environment | Bucket | Token scope |
|---|---|---|
| Local dev | `local` | `local` only |
| Production | `main` | `main` only |

## One-time setup (Cloudflare dashboard → R2)

1. Create the buckets `local` and `main`. Keep public access **off**.
2. **Manage R2 API Tokens → Create API token**, once per bucket:
   - Permission: *Object Read & Write*
   - Specify bucket: only that bucket
   - Put the Access Key ID and Secret Access Key into that environment's env (below). Dev machines only ever get
     the `local` token.
3. On **each** bucket → *Settings*:
   - **CORS policy**:

     ```json
     [
       {
         "AllowedOrigins": ["http://localhost:3000", "http://localhost:8251", "https://app.example.com", "https://api.example.com"],
         "AllowedMethods": ["PUT", "GET"],
         "AllowedHeaders": ["Content-Type", "x-amz-checksum-sha256"],
         "MaxAgeSeconds": 3600
       }
     ]
     ```

     Origins are the dashboard (frontend) and the backend, because the chat widget iframe is served from the backend.
     Replace the example domains with the real ones. `local` only needs the localhost origins, and `main` only the
     production ones.
   - **Object lifecycle rule**: delete objects with prefix `tmp/` after 1 day. Uploads land under `tmp/` and the
     worker moves them out once processed, so this clears uploads that were started but never confirmed.

## Environment

```
R2_ACCOUNT_ID=<Cloudflare account id>
R2_ACCESS_KEY_ID=<token access key>
R2_SECRET_ACCESS_KEY=<token secret>
R2_BUCKET=local        # main in production
```

The app refuses to start when any of these is missing, and refuses `R2_BUCKET=main` while `DEBUG=true`. Tests use
in-memory storage and need none of them.

## Key layout

| Prefix | What |
|---|---|
| `tmp/` | browser uploads waiting for confirm/processing (lifecycle-deleted after 1 day) |
| `products/<project>/<kb>/` | catalog photos: `<id>.webp` (≤1600 px) + `<id>_thumb.webp` (≤320 px) |
| `documents/<project>/<kb>/` | knowledge documents |
| `chat_media/<project>/` | photos and voice notes from customers (all channels) |
