"""FastAPI router for modality dispatch endpoints."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, status

from edac.event.schema import ModalityType
from edac.modality.base import ModalityContent
from edac.modality.dispatcher import ModalityDispatcher

router = APIRouter()


def _get_dispatcher(request: Request) -> ModalityDispatcher:
    dispatcher = getattr(request.app.state, "modality_dispatcher", None)
    if dispatcher is None:
        runtime = getattr(request.app.state, "ctx_runtime", None)
        if runtime is not None:
            dispatcher = getattr(runtime, "modality_dispatcher", None)
    if dispatcher is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Modality dispatcher not enabled",
        )
    return dispatcher


@router.get("/modality")
async def list_modalities(request: Request) -> Dict[str, Any]:
    """List all supported modality types."""
    dispatcher = _get_dispatcher(request)
    return {"types": [m.value for m in dispatcher.supported_types()]}


@router.post("/modality/detect")
async def detect_modality(body: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """Detect the modality of raw data (with optional filename hint)."""
    dispatcher = _get_dispatcher(request)
    data = body.get("data", "")
    filename = body.get("filename")
    modality = dispatcher.detect(data, filename=filename)
    return {"modality": modality.value, "filename": filename}


@router.post("/modality/process")
async def process_modality(body: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """Auto-detect and encode raw data into a ModalityContent."""
    dispatcher = _get_dispatcher(request)
    data = body.get("data", "")
    filename = body.get("filename")
    content: ModalityContent = dispatcher.process(data, filename=filename)
    return {
        "modality": content.modality.value,
        "data": content.data,
        "metadata": content.metadata,
        "mime_type": content.mime_type,
    }


@router.post("/modality/validate")
async def validate_modality(body: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """Validate a ModalityContent payload."""
    dispatcher = _get_dispatcher(request)
    content = ModalityContent(
        modality=ModalityType(body.get("modality", "text")),
        data=body.get("data"),
        metadata=body.get("metadata", {}),
        mime_type=body.get("mime_type"),
    )
    valid = dispatcher.validate(content)
    return {"valid": valid, "modality": content.modality.value}


@router.get("/modality/stats")
async def modality_stats(request: Request) -> Dict[str, Any]:
    """Return dispatch counters and supported types."""
    dispatcher = _get_dispatcher(request)
    return dispatcher.stats()
