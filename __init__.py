"""MORPH — a native Hermes Agent plugin.

``register(ctx)`` is the whole integration surface: Hermes calls it once, after the
plugin is enabled, and MORPH registers its tools, its lifecycle hooks, an in-session
slash command and a ``hermes morph`` CLI subcommand.  The dashboard half lives in
``dashboard/`` and is discovered from ``dashboard/manifest.json``.

Design notes that matter when reading this file:

* The engine lives in the ``morph`` package beside this file, not in it, so the same
  code serves the Hermes tools, the dashboard API, the CLI and the tests.  The plugin
  root goes on ``sys.path`` once so that one package object is shared by all of them —
  two copies would mean two SQLite connections believing they hold the write lock.
* Nothing here applies a change to a connected application. The write path requires
  operator authority, which only the authenticated dashboard (or a person at a TTY)
  can hold.
* If anything in here raises, Hermes disables MORPH and keeps running; registration
  is therefore ordered so that the read-only tools are in place before the extras.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from morph import cli as morph_cli  # noqa: E402
from morph import config, engine, hostllm, schemas, store, tools  # noqa: E402
from morph.actions import Principal, expire_overdue  # noqa: E402
from morph.errors import MorphError  # noqa: E402
from morph.util import clean_text  # noqa: E402

__all__ = ["register", "PLUGIN_ROOT"]

logger = logging.getLogger("morph")
TOOLSET = "morph"


# ──────────────────────────────────── hooks ────────────────────────────────────

def _on_session_start(**kwargs: object) -> None:
    """Start each session with a clean slate: expire proposals nobody decided, and
    drop the cached capability graph so the first read sees current reality."""
    del kwargs
    try:
        expired = expire_overdue()
        engine.invalidate()
        if expired:
            logger.info("MORPH expired %d proposal(s) that were never decided", expired)
    except Exception as exc:  # noqa: BLE001 - a hook must never break a session
        logger.debug("MORPH session-start housekeeping skipped: %s", exc)


def _pre_tool_call(**kwargs: object) -> object:
    """Refuse any tool in the ``morph_`` namespace that MORPH did not register.

    MORPH's security story rests on there being no approval tool.  If another plugin
    (or a future version of this one, misconfigured) introduced ``morph_approve``,
    the agent would call it in good faith.  This hook makes the namespace closed, so
    the guarantee does not depend on nobody ever adding such a tool.
    """
    name = str(kwargs.get("tool_name") or "")
    if name.startswith("morph_") and name not in tools.TOOL_NAMES:
        logger.warning("MORPH blocked an unregistered tool in its namespace: %s", name)
        return {"action": "block",
                "message": (f"{name} is not a MORPH tool. MORPH registers exactly: "
                            f"{', '.join(tools.TOOL_NAMES)}. Actions are approved by a person in "
                            f"the MORPH dashboard, never by a tool call.")}
    return None


def _post_tool_call(**kwargs: object) -> None:
    """Record the agent's MORPH tool use in MORPH's own audit log.

    The proposal and approval paths audit themselves; this covers the read path, so
    the log answers "what did the agent look at before it proposed that?".
    """
    name = str(kwargs.get("tool_name") or "")
    if name not in tools.TOOL_NAMES or name in ("morph_propose_action",):
        return
    try:
        result = kwargs.get("result")
        ok = True
        if isinstance(result, str) and result.startswith("{"):
            try:
                ok = bool(json.loads(result).get("ok", True))
            except (TypeError, ValueError):
                ok = True
        with store.write() as connection:
            store.append_audit(connection, actor="agent", authority="agent", role="agent",
                               kind="tool_call", subject=name,
                               payload={"ok": ok,
                                        "duration_ms": kwargs.get("duration_ms"),
                                        "args": _safe_args(kwargs.get("args"))})
    except Exception as exc:  # noqa: BLE001 - observability must not break the turn
        logger.debug("MORPH could not audit %s: %s", name, exc)


def _safe_args(args: object) -> dict[str, object]:
    if not isinstance(args, dict):
        return {}
    out: dict[str, object] = {}
    for key, value in list(args.items())[:8]:
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[str(key)] = clean_text(value, 120) if isinstance(value, str) else value
        else:
            out[str(key)] = f"<{type(value).__name__}>"
    return out


# ───────────────────────────── in-session command ──────────────────────────────

def _slash_command(raw: str) -> str:
    """``/morph`` — status, or ``/morph goal <text>`` to recompose the workspace."""
    argv = (raw or "").strip()
    try:
        if argv.startswith("goal "):
            result = engine.plan_workspace(Principal.agent(), argv[5:].strip(),
                                           llm=hostllm.current())
            saved = result["workspace"]
            lines = [f"MORPH recomposed the workspace (revision {saved['revision']}, "
                     f"{result['plan']['mode']}): {saved['title']}"]
            for section in (saved.get("schema") or {}).get("sections") or []:
                lines.append(f"  · {section['title']}: "
                             + ", ".join(b["title"] for b in section["blocks"]))
            if result["context_carried"]:
                lines.append("  carried over: " + "; ".join(result["context_carried"]))
            return "\n".join(lines)
        if argv in ("", "status"):
            return morph_cli.status_text()
        if argv in ("pending", "proposals"):
            return morph_cli.proposals_text()
        return "Usage: /morph [status | pending | goal <what you want to do>]"
    except MorphError as exc:
        return f"MORPH: {exc.message}"
    except Exception as exc:  # noqa: BLE001 - a command must answer
        return f"MORPH: {type(exc).__name__}: {exc}"


# ─────────────────────────────── registration ──────────────────────────────────

def register(ctx) -> None:  # noqa: ANN001 - Hermes passes its PluginContext
    """Entry point Hermes calls once after MORPH is enabled."""
    # Configuration may come from the Hermes profile; bind it before anything reads it.
    try:
        config.bind_host_config(lambda key, default=None: ctx.get_config(f"morph.{key}", default))
    except Exception as exc:  # noqa: BLE001 - a host without get_config is still usable
        logger.debug("MORPH is using file configuration only: %s", exc)

    # The host-owned model façade. MORPH holds no credentials and picks no model;
    # without this the planner runs deterministically.
    try:
        hostllm.bind(ctx.llm)
    except Exception as exc:  # noqa: BLE001
        logger.info("MORPH has no host model access; planning will be deterministic (%s)", exc)

    handlers = tools.build_handlers()
    for name, schema in schemas.TOOLS.items():
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handlers[name])

    for hook, callback in (("on_session_start", _on_session_start),
                           ("pre_tool_call", _pre_tool_call),
                           ("post_tool_call", _post_tool_call)):
        try:
            ctx.register_hook(hook, callback)
        except Exception as exc:  # noqa: BLE001 - an older host may not offer every hook
            logger.info("MORPH could not register the %s hook: %s", hook, exc)

    for register_extra in (_register_command, _register_cli, _register_prompt_section):
        try:
            register_extra(ctx)
        except Exception as exc:  # noqa: BLE001 - optional surfaces never block the tools
            logger.info("MORPH skipped an optional surface (%s): %s",
                        register_extra.__name__, exc)

    logger.info("MORPH registered %d tools; approval stays with the dashboard operator",
                len(schemas.TOOLS))


def _register_command(ctx) -> None:  # noqa: ANN001
    ctx.register_command(name="morph", handler=_slash_command,
                         description="Show or recompose the MORPH workspace",
                         args_hint="[status | pending | goal <what you want to do>]")


def _register_cli(ctx) -> None:  # noqa: ANN001
    ctx.register_cli_command(name="morph", help="Inspect and drive MORPH from the terminal",
                             setup_fn=morph_cli.setup_parser, handler_fn=morph_cli.run)


def _register_prompt_section(ctx) -> None:  # noqa: ANN001
    ctx.register_system_prompt_section(id="morph", content=schemas.SYSTEM_PROMPT_SECTION)
