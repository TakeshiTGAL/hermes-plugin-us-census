"""US Census ACS estimates for Hermes Agent."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SKILL = Path(__file__).resolve().parent / "skills" / "us-census" / "SKILL.md"
SKILL_DESCRIPTION = (
    "US Census population, household income, median age, and housing for a place, "
    "with the ACS vintage year, variable id, and margin of error. "
    "Before web_search, use tool_search and tool_call. "
    "Report only returned figures."
)


if __package__:
    from . import tools


def register(ctx) -> None:
    from . import schemas
    from .cli import handle, setup_parser

    for schema in schemas.ALL_SCHEMAS:
        ctx.register_tool(
            name=schema["name"],
            toolset="us-census",
            schema=schema,
            handler=tools.HANDLERS[schema["name"]],
        )
    ctx.register_cli_command(
        name="us-census",
        help="Schedule a model-free note when a new ACS vintage is listed",
        setup_fn=setup_parser,
        handler_fn=handle,
        description="Save one no-agent local cron job. Does not register a slash command.",
    )
    try:
        ctx.register_skill("us-census", _SKILL, description=SKILL_DESCRIPTION)
    except Exception:
        logger.warning("us-census skill was not registered", exc_info=True)
