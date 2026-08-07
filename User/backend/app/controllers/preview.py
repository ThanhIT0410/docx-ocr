"""HTTP routes for PDF page-splitting + preview management. Business logic
lives in app/services/preview_service.py — this module only translates
HTTP <-> that service."""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.schemas import PreviewDto, PreviewPatchRequest
from app.services.preview_service import Preview, PreviewNotFoundError, service

MAX_FILE_BYTES = 50 * 1024 * 1024

router = APIRouter(prefix="/preview", tags=["preview"])


def _to_dto(preview: Preview) -> PreviewDto:
    return PreviewDto(
        previewId=preview.previewId,
        title=preview.title,
        createdAt=preview.createdAt,
        pages=[{"id": p.id, "order": p.order, "source": p.source} for p in preview.pages],
    )


@router.post("", response_model=PreviewDto)
async def create_preview(title: str = Form(...), files: list[UploadFile] = File(...)):
    """Splits PDF pages (PyMuPDF) or stages raw images for the upload wizard's
    step 2. One call = one "đề" (§5) — see stores/upload.ts runPreview()."""
    uploads: list[tuple[str, bytes, str]] = []
    for f in files:
        content = await f.read()
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(413, f"Tệp '{f.filename}' vượt quá 50MB")
        uploads.append((f.filename or "tep", content, f.content_type or ""))

    return _to_dto(service.create(title=title, uploads=uploads))


@router.get("", response_model=list[PreviewDto])
def list_dangling_previews():
    """Recovery hook for §10 "phục vụ trường hợp người dùng tắt ứng dụng giữa
    chừng" — not yet surfaced in the UI; see design report known-gaps."""
    return [_to_dto(p) for p in service.list_all()]


@router.get("/{preview_id}/pages/{page_id}")
def get_page_image(preview_id: str, page_id: str):
    try:
        path = service.page_file(preview_id, page_id)
    except PreviewNotFoundError:
        raise HTTPException(404, "Không tìm thấy trang này")
    return FileResponse(path)


@router.patch("/{preview_id}", response_model=PreviewDto)
def patch_preview(preview_id: str, patch: PreviewPatchRequest):
    try:
        preview = service.patch(
            preview_id,
            title=patch.title,
            page_order=patch.pageOrder,
            delete_page_ids=patch.deletePageIds,
        )
    except PreviewNotFoundError:
        raise HTTPException(404, "Không tìm thấy preview này")
    return _to_dto(preview)


@router.delete("/{preview_id}", status_code=204)
def delete_preview(preview_id: str):
    service.delete(preview_id)
