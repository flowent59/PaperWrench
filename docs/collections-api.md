# Static collections API (M12)

`/api/v1/collections` supports GET list and POST create; `/{id}` supports
GET, PUT and DELETE. Create accepts `{name, description?, document_ids?}`;
PUT accepts `{name, description?}`. Names are unique. Only static collections
are exposed. `POST /{id}/documents` and `DELETE /{id}/documents` accept
`{document_ids: number[]}`. Inputs contain at most 500 positive IDs. Add is
idempotent for existing members; removal is idempotent for absent members.

`GET /{id}/documents?page=1&page_size=25` returns `{items, page, page_size,
total, page_count}` with a maximum page size of 100. Each item contains
`document_id`, `available`, and `document` in the normalized Explorer list
shape when readable. A deleted or inaccessible ID remains a member with
`available=false` and `document=null`; its metadata is never returned. New
inaccessible IDs are rejected before any membership is saved, with the same
generic error as missing IDs. A page makes at most `page_size` Paperless
single-document reads. SQLite holds no title, OCR, or other document mirror.

No collection endpoint mutates a Paperless document. Collection membership
survives a frontend reload and can be removed even when the Paperless document
is no longer accessible.
