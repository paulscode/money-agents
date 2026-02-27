"""Media Library API endpoints.

Browse, preview, and download generated media files from tool output directories.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from app.api.deps import get_current_user, get_current_admin
from app.core.rate_limit import limiter
from app.models import User
from app.services.media_library_service import (
    MediaFile,
    MediaFileList,
    MediaStats,
    ToolMediaSummary,
    get_media_library_service,
    _classify_file,
    _sanitize_filename,
    TOOL_MEDIA_REGISTRY,
)
from app.services.thumbnail_service import generate_thumbnail

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/tools", response_model=list[ToolMediaSummary])
async def list_tools_with_media(
    current_user: User = Depends(get_current_user),
):
    """List all tools that have output directories, with file counts and sizes."""
    service = get_media_library_service()
    return service.list_tools_with_media()


@router.get("/stats", response_model=MediaStats)
async def get_media_stats(
    current_user: User = Depends(get_current_user),
):
    """Get global media library statistics."""
    service = get_media_library_service()
    return service.get_stats()


@router.get("/{tool_slug}/files", response_model=MediaFileList)
async def list_tool_files(
    tool_slug: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    sort_by: str = Query("modified_at", pattern="^(modified_at|name|size)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    media_type: Optional[str] = Query(None, pattern="^(image|video|audio|document|archive|other)$"),
    current_user: User = Depends(get_current_user),
):
    """List files in a tool's output directory with pagination and filtering."""
    if tool_slug not in TOOL_MEDIA_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_slug}")
    
    service = get_media_library_service()
    return service.list_files(
        tool_slug=tool_slug,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
        media_type=media_type,
    )


@router.get("/{tool_slug}/files/{filename}")
async def get_file(
    tool_slug: str,
    filename: str,
    download: bool = Query(False),
    current_user: User = Depends(get_current_user),
):
    """Download or stream a specific media file.
    
    If ?download=true, sets Content-Disposition to attachment for browser download.
    Otherwise, serves inline for preview/playback.
    """
    if tool_slug not in TOOL_MEDIA_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_slug}")

    try:
        service = get_media_library_service()
        file_path = service.get_file_path(tool_slug, filename)
    except ValueError as e:
        import logging
        logging.getLogger(__name__).error("Media library operation failed: %s", e)
        raise HTTPException(status_code=400, detail="Invalid media request")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")

    _, mime_type = _classify_file(filename)
    
    headers = {}
    if download:
        # SA3-L9: Sanitize filename for Content-Disposition header to prevent
        # header injection via embedded quotes or CRLF sequences.
        import re as _re
        safe_name = _re.sub(r'["\\\r\n]', '_', filename)
        headers["Content-Disposition"] = f'attachment; filename="{safe_name}"'
    
    return FileResponse(
        path=file_path,
        media_type=mime_type,
        filename=filename if download else None,
        headers=headers if headers else None,
    )


@router.get("/{tool_slug}/files/{filename}/thumbnail")
async def get_thumbnail(
    tool_slug: str,
    filename: str,
    current_user: User = Depends(get_current_user),
):
    """Get a thumbnail for a media file.
    
    Returns cached thumbnail if available, otherwise generates on-the-fly.
    Thumbnails are JPEG for images/videos, PNG for audio waveforms.
    """
    if tool_slug not in TOOL_MEDIA_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_slug}")

    service = get_media_library_service()

    # Check cache first
    thumb_path = service.get_thumbnail_path(tool_slug, filename)
    if thumb_path:
        # Determine mime from extension
        media_type = "image/png" if thumb_path.suffix == ".png" else "image/jpeg"
        return FileResponse(path=thumb_path, media_type=media_type)

    # Generate on-the-fly
    try:
        file_path = service.get_file_path(tool_slug, filename)
    except (ValueError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="Source file not found")

    file_media_type, _ = _classify_file(filename)
    if file_media_type not in ("image", "video", "audio"):
        raise HTTPException(status_code=404, detail="No thumbnail available for this file type")

    thumb_data = await generate_thumbnail(file_path, file_media_type)
    if thumb_data is None:
        raise HTTPException(status_code=404, detail="Could not generate thumbnail")

    # Cache it
    service.save_thumbnail(tool_slug, filename, thumb_data)

    # Audio waveforms are PNG, everything else is JPEG
    content_type = "image/png" if file_media_type == "audio" else "image/jpeg"
    return Response(content=thumb_data, media_type=content_type)


@router.post("/{tool_slug}/thumbnails/generate")
@limiter.limit("6/minute")
async def generate_thumbnails_batch(
    request: Request,
    tool_slug: str,
    current_user: User = Depends(get_current_user),
):
    """Trigger batch thumbnail generation for all files in a tool's output.
    
    Returns immediately with the count of files that need thumbnails.
    Thumbnails are generated in the background.
    """
    if tool_slug not in TOOL_MEDIA_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_slug}")

    service = get_media_library_service()
    files_needing = service.get_files_needing_thumbnails(tool_slug)

    if not files_needing:
        return {"status": "ok", "message": "All thumbnails up to date", "count": 0}

    # Generate thumbnails synchronously but for a limited batch
    # (individual thumbnail requests handle the rest on-demand)
    generated = 0
    errors = 0
    MAX_BATCH = 20  # Limit batch size per request

    for fname in files_needing[:MAX_BATCH]:
        try:
            file_path = service.get_file_path(tool_slug, fname)
            media_type, _ = _classify_file(fname)
            thumb_data = await generate_thumbnail(file_path, media_type)
            if thumb_data:
                service.save_thumbnail(tool_slug, fname, thumb_data)
                generated += 1
            else:
                errors += 1
        except Exception as e:
            logger.warning(f"Batch thumbnail generation failed for {fname}: {e}")
            errors += 1

    remaining = max(0, len(files_needing) - MAX_BATCH)
    return {
        "status": "ok",
        "generated": generated,
        "errors": errors,
        "remaining": remaining,
        "total_needed": len(files_needing),
    }


@router.delete("/{tool_slug}/files/{filename}")
async def delete_file(
    tool_slug: str,
    filename: str,
    current_user: User = Depends(get_current_admin),
):
    """Delete a generated media file (admin only).
    
    Also removes the cached thumbnail if present.
    
    NOTE: Tool output directories are mounted read-only in Docker.
    File deletion may fail with a permission error. In that case,
    delete files directly on the host or via the tool's own API.
    """
    if tool_slug not in TOOL_MEDIA_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_slug}")

    service = get_media_library_service()

    try:
        file_path = service.get_file_path(tool_slug, filename)
    except ValueError as e:
        import logging
        logging.getLogger(__name__).error("Media library operation failed: %s", e)
        raise HTTPException(status_code=400, detail="Invalid media request")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")

    # Delete the file
    try:
        file_path.unlink()
    except PermissionError:
        raise HTTPException(
            status_code=403,
            detail="Cannot delete: output directory is mounted read-only. "
                   "Delete files on the host or via the tool's API instead.",
        )
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not delete file: {e}")

    # Remove cached thumbnail if any
    thumb_path = service.get_thumbnail_path(tool_slug, filename)
    if thumb_path:
        try:
            thumb_path.unlink()
        except OSError:
            pass  # Non-critical

    return {"status": "ok", "message": f"Deleted {filename}"}
