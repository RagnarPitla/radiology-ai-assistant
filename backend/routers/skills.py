"""Generated skills and agents router."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.schemas import SkillsListResponse
from backend.services import skills_service

router = APIRouter()


@router.get("/", response_model=SkillsListResponse)
def list_skills() -> SkillsListResponse:
    return SkillsListResponse(skills=skills_service.list_skills())


@router.get("/{slug}")
def get_skill(slug: str) -> dict:
    try:
        return skills_service.get_skill(slug)
    except KeyError:
        raise HTTPException(status_code=404, detail="Skill not found")


@router.post("/regenerate")
def regenerate() -> dict[str, int]:
    return {"regenerated": skills_service.regenerate_all()}
