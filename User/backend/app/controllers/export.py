"""HTTP route for result export. Business logic lives in
app/services/export_service.py — this module only translates HTTP <-> that
service."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Response

from app.schemas import ExportRequest
from app.services.export_service import UnsupportedPageAspectRatioError, create_export_service
from app.services.layout_reconstructor_v2 import LayoutReconstructionError

router = APIRouter(tags=["export"])


@router.post("/export")
def export_exam(payload: ExportRequest) -> Response:
    """§9.2: Nuxt has already fetched the exam's ocr_text + signed image URLs
    from Supabase and forwards them here — this process never touches
    Supabase directly (no key is ever given to it, by design)."""
    try:
        result = create_export_service(payload.title, payload.mode).export(payload.pages)
    except UnsupportedPageAspectRatioError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LayoutReconstructionError as exc:
        # Model file missing/corrupt or a prediction error — a deployment/
        # environment problem, not something retrying the same request fixes.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    # ExportService guarantees an ASCII filename today, but
    # Content-Disposition is latin-1-only — add the RFC 5987 `filename*`
    # form too so this keeps working if a future pipeline emits unicode
    # names, instead of crashing the same way the plain title did here.
    disposition = f"attachment; filename=\"{result.filename}\"; filename*=UTF-8''{quote(result.filename)}"
    return Response(
        content=result.content,
        media_type=result.media_type,
        headers={"Content-Disposition": disposition},
    )
