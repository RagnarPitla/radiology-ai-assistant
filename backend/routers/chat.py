"""
Agentic chat router (prefix /api/chat).

Exposes the local tool-using assistant. All tools run on the machine.
"""
from __future__ import annotations

from fastapi import APIRouter

from backend.schemas import ChatRequest, ChatResponse
from backend.services import agent

router = APIRouter()


@router.post("", response_model=ChatResponse)
@router.post("/", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    return agent.run_agent(req)
