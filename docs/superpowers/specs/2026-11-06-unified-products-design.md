# One Products section (merge "Product Catalog" and "Product photos")

Date: 2026-11-06. Status: proposal, not built.

## Problem

The Products page (`/knowledge/products`) shows two cards for the same thing:

| Card | Component | What it does |
|---|---|---|
| Product Catalog | `ProductsSection` + `ProductTable` + `ProductEditor` | Price, SKU, product ID, variants, custom fields, status (needs info / active / hidden), import sources, review of found products |
| Product photos | `ProductCatalog` | Its own "Add product" form (name, description, photos), its own list of the same products, add/delete photos, edit name and description |

What goes wrong for a user:

1. **The same product appears twice.** Once as a table row, once as a card below it. Two lists, two fetches, kept in sync by `onProductsChange`.
2. **Two "add product" paths.** The photos card creates a product with only a name, description and photos. It lands as "needs info", and the user must find it again in the catalog to add a price. The catalog has no add button at all.
3. **Two edit paths.** The photos card edits name and description inline. The catalog editor edits everything except photos. Neither edits the whole product.
4. **Photos are invisible in the catalog.** The table has no thumbnail. The editor has no photos.
5. **The photos card disappears** when image search is off (`imageSearchOn`). Then there is no way to add a photo at all, and no hint that photos exist.
6. **Names don't match the job.** "Product Catalog" and "Product photos" read as two features. Photos are just one field of a product.

## Goal

One section, **Products**. A product is one thing with a name, price, details, variants and photos. One list, one add flow, one editor.

Non-goals: no backend change to storage, image indexing, import sources or review. No change to what the agent sees.

## Design

### Page layout

```
Products                                   [ + Add product ]
Prices, details and photos the assistant uses to answer customers and take orders

[ Products | Sources | Review (3) ]

[ All | Needs info | Active | Hidden ]

 Photo   Name              Price    SKU      Status
 [img]   Red kurta         ৳1,200   RK-01    Active
 [img]   Blue panjabi      —        —        Needs info
 [ + ]   Black shawl       ৳900     BS-02    Active      (no photo yet: shows "Add photo")
```

- One card, one title: **Products**. The "Product photos" card is removed.
- The table gets a **thumbnail column** (first ready photo, else a muted placeholder). A row with a photo still processing shows a spinner on the thumbnail.
- **Add product** button in the header (admins only). It opens the same editor, empty.

### One editor for everything

`ProductEditor` gains a **Photos** block, placed under the basic fields and above variants:

```
Photos
[img ✕] [img ✕] [img ⟳ indexing] [img ! failed ✕] [ + Add photos ]
Photos let the assistant recognise this product when a customer sends a picture.
```

- Each photo shows its status (uploading, indexing, ready, failed) as a small badge only when it is not ready. Ready is the normal state and needs no label.
- Delete photo uses the existing `productApi.removeImage`. Add uses `productApi.addImages`.
- Create mode (Add product): same form, name required, everything else optional. On save we create the product with its photos, then patch price, SKU and the rest (see "API" below).
- The editor keeps its "add a SKU or product ID" warning.

### Image search switched off

Today the whole photos card is hidden. New behaviour: **photos are always manageable**, and when `imageSearchOn === false` the Photos block shows one line: "Image search is off, so the assistant won't match customer pictures yet. Turn it on in Channels." (link). Reason: hiding the control hides the feature. This is an open question, see below.

### Behaviour kept as is

- Tabs All / Needs info / Active / Hidden, the needs-info banner and inline price edit.
- Sources and Review views, including paging in `CandidateReview`.
- Admin-only editing. Non-admins see thumbnails and the table only.
- Delete product: confirm dialog, same text ("Delete X and its photos?"), moved into the editor and a row action.

### Polling

`ProductCatalog` polls every 3 s while any photo is `pending`. Move that into `ProductsSection`: if any product has a pending photo, re-run `load()` every 3 s. One list, one poll. `reportProducts` already feeds the side nav counts, so `onProductsChange` goes away.

## Changes

| File | Change |
|---|---|
| `components/knowledge/sections/ProductsSection.tsx` | Remove the second card and `ProductCatalog` use. Add header "Add product" button, create-mode editor, pending-photo poll. |
| `components/knowledge/catalog/ProductTable.tsx` | Add thumbnail column. |
| `components/knowledge/catalog/ProductEditor.tsx` | Add `PhotosBlock`. Support create mode (no `product`). |
| `components/knowledge/ProductCatalog.tsx` | **Delete.** Its create, edit, delete and photo logic moves into the editor. |
| `components/knowledge/KnowledgeSectionNav.tsx` | Keep label "Product Catalog" → rename "Products". Stats text stays "N products · M photos". |
| `components/orders/RequirementsDialog.tsx` | Link label "Open Product Catalog" → "Open Products". |
| `app/(dashboard)/analytics/page.tsx` | "Customer photos matched against the catalog" → "…against your products". Optional wording. |

Route stays `/knowledge/products`, so no links break.

## API

All needed calls exist: `productApi.list`, `create(name, description, files)`, `patch`, `addImages`, `removeImage`, `remove`.

The only gap: `create` takes just name, description and files. Create mode therefore does two calls (create, then `patch` for price, SKU, variants). If the second call fails, the product exists as "needs info" and the editor stays open with the error, so nothing is lost. A backend change to accept all fields on create would be cleaner but is not required.

## Rollout

Single change, no flag, no data migration. Order of work:

1. Photos block in the editor (edit mode only). Table thumbnail column. Poll moved.
2. Create mode and header "Add product".
3. Remove the second card, delete `ProductCatalog.tsx`, rename labels.

Steps 1–2 can ship before 3, so there is never a moment where photos can't be managed.

## Risks

- **Editor gets longer.** Mitigation: photos block is one row of thumbnails. Variants and custom fields stay below it.
- **Photo upload inside a form that may not be saved.** Edit mode uploads immediately (as today). Create mode holds files locally and uploads on save, so Cancel leaves nothing behind.
- **Large catalogs.** The table loads one thumbnail per row (`thumb_url`, already a small image). Use `loading="lazy"`.
- **`CandidateReview` shows imported images too.** Unaffected. Those are source URLs, not uploaded photos.

## Open questions

1. When image search is off, should photos still be uploadable (recommended), or stay hidden as today?
2. Rename the left-nav item to "Products"? Recommended, since "Catalog" is the word that split it in two.
3. Should create also accept price and SKU on the backend (one call), or is the two-call create acceptable?
