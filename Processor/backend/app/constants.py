"""Shared literals — status values, storage prefixes, header names.

Kept in one place because several modules (repos, services, controllers)
need to agree on the exact same strings.
"""

# exams.status values. `failed` is new — added by
# migrations/0001_processor_worker_columns.sql on top of the User side's
# original ('pending', 'processing', 'finished'). See DESIGN_REPORT.md
# §4.2 "Trạng thái failed" for the reasoning and the open question this
# raises for the User-side UI.
STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_FINISHED = "finished"
STATUS_FAILED = "failed"

ALL_STATUSES = (STATUS_PENDING, STATUS_PROCESSING, STATUS_FINISHED, STATUS_FAILED)

# Storage key convention (DESIGN_REPORT.md §4.1 "Quy ước Storage key").
# Pending objects keep the exact convention the User frontend already
# uploads to (`{examId}/{pageId}.{ext}` — see User/supabase/storage.sql),
# so nothing on the User side has to change. The Processor only introduces
# a new prefix for objects it has claimed.
PROCESSING_PREFIX = "processing"

API_KEY_HEADER = "X-API-Key"
ADMIN_API_KEY_HEADER = "X-Admin-API-Key"
