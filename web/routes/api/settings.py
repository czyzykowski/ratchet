"""API: settings endpoints — read-only system configuration inspection."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/settings")


class PromptTemplate(BaseModel):
    category: str
    name: str
    description: str
    source: str
    template: str


def _collect_prompts() -> list[dict[str, str]]:
    from core.compiler import _COMPILE_PROMPT_TEMPLATE
    from core.context_assembler import (
        _COMPLETION_INSTRUCTIONS,
        _CONFLICT_RESOLUTION_INSTRUCTIONS,
        _KNOWLEDGE_PLACEHOLDER,
        _PREAMBLE,
        _QA_HISTORY_HEADER,
    )
    from core.qa_runner import _REVIEW_PROMPT_TEMPLATE
    from core.review_engine import _REVIEW_ENGINE_PROMPT_TEMPLATE
    from orchestrator.sequencer import _QA_FIX_PROMPT_TEMPLATE
    from web.routes.api.architecture_sessions import _ARCHITECTURE_SESSION_TEMPLATE
    from web.routes.api.bootstrap_chat import _BOOTSTRAP_CHAT_TEMPLATE
    from web.routes.api.feature_sessions import _FEATURE_SESSION_TEMPLATE
    from web.routes.api.project_chat_sessions import _PROJECT_CHAT_TEMPLATE
    from web.routes.api.tasks import _SPEC_ROLE_PROMPT

    return [
        {
            "category": "Execution",
            "name": "Implementation Preamble",
            "description": "Instructions and TDD methodology injected into every task prompt.",
            "source": "core/context_assembler.py",
            "template": _PREAMBLE,
        },
        {
            "category": "Execution",
            "name": "Knowledge Placeholder",
            "description": "Placeholder section for knowledge entries injected into task prompts.",
            "source": "core/context_assembler.py",
            "template": _KNOWLEDGE_PLACEHOLDER,
        },
        {
            "category": "Execution",
            "name": "QA History Header",
            "description": "Header for the human input history section in task prompts.",
            "source": "core/context_assembler.py",
            "template": _QA_HISTORY_HEADER,
        },
        {
            "category": "Execution",
            "name": "Completion Instructions",
            "description": "Agent instructions for committing and signalling done or blocked.",
            "source": "core/context_assembler.py",
            "template": _COMPLETION_INSTRUCTIONS,
        },
        {
            "category": "Execution",
            "name": "Conflict Resolution Instructions",
            "description": "Instructions injected when Claude Code is resolving merge conflicts.",
            "source": "core/context_assembler.py",
            "template": _CONFLICT_RESOLUTION_INSTRUCTIONS,
        },
        {
            "category": "Execution",
            "name": "QA Fix Prompt",
            "description": "Prompt sent to Claude when attempting to fix QA failures.",
            "source": "orchestrator/sequencer.py",
            "template": _QA_FIX_PROMPT_TEMPLATE,
        },
        {
            "category": "Compilation",
            "name": "Spec Compilation Prompt",
            "description": "Prompt to compile a high-level spec into a full implementation spec.",
            "source": "core/compiler.py",
            "template": _COMPILE_PROMPT_TEMPLATE,
        },
        {
            "category": "QA",
            "name": "QA Review Prompt",
            "description": "Prompt for reviewing a completed implementation against the spec.",
            "source": "core/qa_runner.py",
            "template": _REVIEW_PROMPT_TEMPLATE,
        },
        {
            "category": "Review Engine",
            "name": "Retrospective Review Prompt",
            "description": "Role and output format for the retrospective review engine.",
            "source": "core/review_engine.py",
            "template": _REVIEW_ENGINE_PROMPT_TEMPLATE,
        },
        {
            "category": "Chat Sessions",
            "name": "Bootstrap Chat System Prompt",
            "description": "System prompt for the project bootstrapping assistant chat session.",
            "source": "web/routes/api/bootstrap_chat.py",
            "template": _BOOTSTRAP_CHAT_TEMPLATE,
        },
        {
            "category": "Chat Sessions",
            "name": "Feature Design System Prompt",
            "description": "System prompt for the feature design assistant chat session.",
            "source": "web/routes/api/feature_sessions.py",
            "template": _FEATURE_SESSION_TEMPLATE,
        },
        {
            "category": "Chat Sessions",
            "name": "Architecture Analysis System Prompt",
            "description": "System prompt for the architecture analysis assistant chat session.",
            "source": "web/routes/api/architecture_sessions.py",
            "template": _ARCHITECTURE_SESSION_TEMPLATE,
        },
        {
            "category": "Chat Sessions",
            "name": "Project Chat System Prompt",
            "description": "System prompt for the free-form project conversation assistant.",
            "source": "web/routes/api/project_chat_sessions.py",
            "template": _PROJECT_CHAT_TEMPLATE,
        },
        {
            "category": "Chat Sessions",
            "name": "Spec Design Prompt",
            "description": "System prompt for the spec design chat used when writing task specs.",
            "source": "web/routes/api/tasks.py",
            "template": _SPEC_ROLE_PROMPT,
        },
    ]


@router.get("/prompts")
async def get_prompts() -> JSONResponse:
    return JSONResponse({"data": _collect_prompts()})
