"""BUGFIX1 — the SPV statuses an ordinary member may see, in ONE place.

routers/spv.py gates GET /spvs, /spvs/{id} and /spvs/{id}/documents on this
set, and Document Search (services/document_linkage._search_attachments)
applies the same set to SPV attachments, so the two can never drift.
"""
MEMBER_VISIBLE_SPV_STATUSES = ("open", "closing")
