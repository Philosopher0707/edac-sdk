"""FastAPI router for approval gate endpoints (HITL)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, status

from edac.human.approval import ApprovalGate, ApprovalManager

router = APIRouter()


@router.get("/approvals/gates")
async def list_gates(request: Request) -> List[Dict[str, Any]]:
    """List all approval gates with their approval state."""
    app = request.app
    mgr: Optional[ApprovalManager] = getattr(app.state, "approval_manager", None)
    if mgr is None:
        return []
    return [
        {
            "trigger_on": g.trigger_on,
            "prompt": g.prompt,
            "approved": g.is_approved(),
            "approvals": list(g.approvals) if g.approvals else [],
            "required_approvals": g.required_approvers,
            "timeout_seconds": g.timeout_seconds,
        }
        for g in mgr.gates
    ]


@router.post("/approvals/gates")
async def create_gate(body: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """Create a new approval gate."""
    app = request.app
    mgr: Optional[ApprovalManager] = getattr(app.state, "approval_manager", None)
    if mgr is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Approval manager not enabled",
        )
    gate = ApprovalGate(
        trigger_on=body["trigger_on"],
        prompt=body.get("prompt", "Approval required"),
        timeout_seconds=body.get("timeout_seconds", 300.0),
        required_approvers=body.get("required_approvers", 1),
    )
    mgr.add_gate(gate)
    return {
        "trigger_on": gate.trigger_on,
        "prompt": gate.prompt,
        "approved": gate.is_approved(),
    }


@router.post("/approvals/gates/{trigger_on}/approve")
async def approve_gate(trigger_on: str, body: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """Approve an approval gate by trigger pattern."""
    app = request.app
    mgr: Optional[ApprovalManager] = getattr(app.state, "approval_manager", None)
    if mgr is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Approval manager not enabled",
        )
    approver: str = body.get("approver", "anonymous")

    # Find gate by exact trigger_on pattern
    gate = next((g for g in mgr.gates if g.trigger_on == trigger_on), None)
    if gate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Gate '{trigger_on}' not found",
        )

    # Check if already approved before adding (idempotent)
    already = gate.is_approved()
    approved = gate.approve(approver)

    return {
        "trigger_on": trigger_on,
        "approver": approver,
        "approved": approved,
        "remaining": max(0, gate.required_approvers - len(gate.approvals)),
    }
