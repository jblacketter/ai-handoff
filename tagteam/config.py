"""
Centralized configuration handling for Tagteam.

This module provides a single source of truth for reading and validating
tagteam.yaml configuration files.
"""

from pathlib import Path

# PyYAML is optional
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def _indent(line: str) -> int:
    """Return leading whitespace width for fallback YAML parsing."""
    return len(line) - len(line.lstrip(" \t"))


def _parse_simple_value(raw: str) -> str:
    """Parse a simple scalar value from the fallback YAML parser."""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


# Headless providers known to `tagteam.headless` (Phase 31). Kept here so
# `validate_config` can reject unknown providers without importing the
# adapter module.
HEADLESS_PROVIDERS = ("claude", "codex")


def _read_config_fallback(content: str) -> dict | None:
    """Parse the simple tagteam.yaml shape without PyYAML.

    This intentionally supports only the configuration shape TagTeam writes:
    ``agents -> lead/reviewer -> name/command/model_patterns/headless``
    (``headless`` being a mapping of ``provider``/``executable``/``args``).
    """
    if content.strip() == "{}":
        return {}

    lines = content.splitlines()
    agents_index = None
    agents_indent = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "agents:":
            agents_index = i
            agents_indent = _indent(line)
            break
        return None

    if agents_index is None:
        return None

    agents: dict[str, dict] = {}
    i = agents_index + 1
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        line_indent = _indent(line)
        if line_indent <= agents_indent:
            break

        role = stripped[:-1] if stripped.endswith(":") else None
        if role not in {"lead", "reviewer"}:
            i += 1
            continue

        role_indent = line_indent
        role_data: dict[str, str | list[str]] = {}
        i += 1
        while i < len(lines):
            sub_line = lines[i]
            sub = sub_line.strip()
            if not sub or sub.startswith("#"):
                i += 1
                continue

            sub_indent = _indent(sub_line)
            if sub_indent <= role_indent:
                break

            if sub.startswith("name:"):
                role_data["name"] = _parse_simple_value(sub.split(":", 1)[1])
                i += 1
                continue
            if sub.startswith("command:"):
                role_data["command"] = _parse_simple_value(sub.split(":", 1)[1])
                i += 1
                continue
            if sub == "model_patterns:":
                patterns: list[str] = []
                pattern_indent = sub_indent
                i += 1
                while i < len(lines):
                    item_line = lines[i]
                    item = item_line.strip()
                    if not item or item.startswith("#"):
                        i += 1
                        continue
                    item_indent = _indent(item_line)
                    if item_indent <= pattern_indent:
                        break
                    if item.startswith("- "):
                        patterns.append(_parse_simple_value(item[2:]))
                    i += 1
                role_data["model_patterns"] = patterns
                continue
            if sub == "headless:":
                headless: dict = {}
                headless_indent = sub_indent
                i += 1
                while i < len(lines):
                    h_line = lines[i]
                    h = h_line.strip()
                    if not h or h.startswith("#"):
                        i += 1
                        continue
                    h_indent = _indent(h_line)
                    if h_indent <= headless_indent:
                        break
                    if h.startswith("provider:"):
                        headless["provider"] = _parse_simple_value(h.split(":", 1)[1])
                        i += 1
                        continue
                    if h.startswith("executable:"):
                        headless["executable"] = _parse_simple_value(h.split(":", 1)[1])
                        i += 1
                        continue
                    if h.startswith("args:"):
                        rest = h.split(":", 1)[1].strip()
                        args_list: list[str] = []
                        if rest.startswith("[") and rest.endswith("]"):
                            inner = rest[1:-1].strip()
                            if inner:
                                args_list = [_parse_simple_value(x) for x in inner.split(",")]
                            headless["args"] = args_list
                            i += 1
                            continue
                        args_indent = h_indent
                        i += 1
                        while i < len(lines):
                            a_line = lines[i]
                            a = a_line.strip()
                            if not a or a.startswith("#"):
                                i += 1
                                continue
                            if _indent(a_line) <= args_indent:
                                break
                            if a.startswith("- "):
                                args_list.append(_parse_simple_value(a[2:]))
                            i += 1
                        headless["args"] = args_list
                        continue
                    i += 1
                role_data["headless"] = headless
                continue

            i += 1

        agents[role] = role_data

    return {"agents": agents} if agents else None


def read_config(config_path: Path | str) -> dict | None:
    """Read and parse tagteam.yaml.

    Args:
        config_path: Path to config file

    Returns:
        Parsed config dict, or None if file doesn't exist or is invalid
    """
    path = Path(config_path)
    if not path.exists():
        return None

    try:
        content = path.read_text(encoding="utf-8")
        if HAS_YAML:
            result = yaml.safe_load(content)
            # Only return if it's a dict (not [], "foo", or other valid YAML)
            return result if isinstance(result, dict) else None

        return _read_config_fallback(content)
    except Exception:
        pass
    return None


def validate_config(config: dict) -> list[str]:
    """Validate tagteam.yaml structure.

    Args:
        config: Parsed config dict

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    if not isinstance(config, dict):
        return ["Config must be a YAML mapping"]

    agents = config.get("agents")
    if not isinstance(agents, dict):
        errors.append("Missing 'agents' section")
        return errors

    # Validate lead
    lead = agents.get("lead")
    if not isinstance(lead, dict) or not lead.get("name"):
        errors.append("Missing or invalid 'agents.lead.name'")

    # Validate reviewer
    reviewer = agents.get("reviewer")
    if not isinstance(reviewer, dict) or not reviewer.get("name"):
        errors.append("Missing or invalid 'agents.reviewer.name'")

    # Validate command if present
    for role in ["lead", "reviewer"]:
        agent = agents.get(role, {})
        if isinstance(agent, dict):
            command = agent.get("command")
            if command is not None and not isinstance(command, str):
                errors.append(f"'agents.{role}.command' must be a string")
            elif isinstance(command, str) and not command.strip():
                errors.append(f"'agents.{role}.command' is empty")

    # Validate model_patterns if present
    all_patterns: list[tuple[str, list[str]]] = []
    for role in ["lead", "reviewer"]:
        agent = agents.get(role, {})
        if not isinstance(agent, dict):
            continue
        patterns = agent.get("model_patterns")
        if patterns is not None:
            if not isinstance(patterns, list):
                errors.append(f"'agents.{role}.model_patterns' must be a list")
            elif not all(isinstance(p, str) and p for p in patterns):
                errors.append(f"'agents.{role}.model_patterns' must contain non-empty strings")
            else:
                all_patterns.append((role, [p.lower() for p in patterns]))

    # Validate headless block if present (Phase 31). Structural checks only;
    # option-level validation of `args` lives in `tagteam.headless.build_argv`.
    for role in ["lead", "reviewer"]:
        agent = agents.get(role, {})
        if not isinstance(agent, dict):
            continue
        headless = agent.get("headless")
        if headless is None:
            continue
        if not isinstance(headless, dict):
            errors.append(f"'agents.{role}.headless' must be a mapping")
            continue
        provider = headless.get("provider")
        if provider is not None and provider not in HEADLESS_PROVIDERS:
            errors.append(
                f"'agents.{role}.headless.provider' must be one of: "
                f"{', '.join(HEADLESS_PROVIDERS)} (got {provider!r})"
            )
        executable = headless.get("executable")
        if executable is not None and (
                not isinstance(executable, str) or not executable.strip()):
            errors.append(f"'agents.{role}.headless.executable' must be a non-empty string")
        args = headless.get("args")
        if args is not None:
            if not isinstance(args, list):
                errors.append(
                    f"'agents.{role}.headless.args' must be a list of strings "
                    f"(got {type(args).__name__}); shell strings are never tokenized"
                )
            elif not all(isinstance(a, str) for a in args):
                errors.append(f"'agents.{role}.headless.args' must contain only strings")
        unknown = set(headless) - {"provider", "executable", "args"}
        if unknown:
            errors.append(
                f"'agents.{role}.headless' has unknown keys: {sorted(unknown)}"
            )

    # Check for pattern overlap (error, not warning)
    if len(all_patterns) == 2:
        role1, patterns1 = all_patterns[0]
        role2, patterns2 = all_patterns[1]
        for p1 in patterns1:
            for p2 in patterns2:
                if p1 in p2 or p2 in p1:
                    errors.append(
                        f"Pattern overlap: '{p1}' ({role1}) and '{p2}' ({role2}) "
                        f"could match the same model identifier"
                    )

    return errors


def get_launch_commands(config: dict) -> tuple[str, str]:
    """Extract launch commands for lead and reviewer agents.

    Uses the optional 'command' field from each agent config,
    falling back to the lowercase agent name.

    Args:
        config: Parsed config dict

    Returns:
        (lead_command, reviewer_command) tuple
    """
    agents = config.get("agents", {})
    lead = agents.get("lead", {}) if isinstance(agents.get("lead"), dict) else {}
    reviewer = agents.get("reviewer", {}) if isinstance(agents.get("reviewer"), dict) else {}

    lead_cmd = lead.get("command") or (lead.get("name") or "claude").lower()
    reviewer_cmd = reviewer.get("command") or (reviewer.get("name") or "codex").lower()

    return lead_cmd, reviewer_cmd


def get_agent_names(config: dict) -> tuple[str | None, str | None]:
    """Extract lead and reviewer names from config.

    Args:
        config: Parsed config dict

    Returns:
        (lead_name, reviewer_name) tuple, with None for missing values
    """
    agents = config.get("agents", {})
    lead = agents.get("lead", {})
    reviewer = agents.get("reviewer", {})

    lead_name = lead.get("name") if isinstance(lead, dict) else None
    reviewer_name = reviewer.get("name") if isinstance(reviewer, dict) else None

    return lead_name, reviewer_name


def infer_headless_provider(config: dict, role: str) -> str | None:
    """Pick the headless provider for a role.

    Order: explicit ``agents.<role>.headless.provider`` → basename of the
    first whitespace token of the interactive ``command`` (inference only;
    the command string is never executed or tokenized for headless) →
    the agent ``name`` lowercased. Returns None if nothing matches a
    known provider.
    """
    agents = config.get("agents", {}) if isinstance(config, dict) else {}
    agent = agents.get(role, {}) if isinstance(agents, dict) else {}
    if not isinstance(agent, dict):
        return None
    headless = agent.get("headless") or {}
    explicit = headless.get("provider") if isinstance(headless, dict) else None
    if explicit in HEADLESS_PROVIDERS:
        return explicit
    candidates: list[str] = []
    command = agent.get("command")
    if isinstance(command, str) and command.strip():
        first = command.strip().split()[0]
        candidates.append(Path(first).name.lower())
    name = agent.get("name")
    if isinstance(name, str) and name.strip():
        candidates.append(name.strip().lower())
    for cand in candidates:
        for prov in HEADLESS_PROVIDERS:
            if cand == prov or cand.startswith(prov):
                return prov
    return None


def get_headless_spec(config: dict, role: str) -> dict:
    """Return ``{"provider", "executable", "args"}`` for a role.

    ``provider`` may be None (uninferable — the caller reports it);
    ``executable`` is the configured string or None (caller resolves the
    provider name via ``shutil.which``); ``args`` is always a list.
    """
    agents = config.get("agents", {}) if isinstance(config, dict) else {}
    agent = agents.get(role, {}) if isinstance(agents, dict) else {}
    headless = agent.get("headless") if isinstance(agent, dict) else None
    headless = headless if isinstance(headless, dict) else {}
    args = headless.get("args")
    return {
        "provider": infer_headless_provider(config, role),
        "executable": headless.get("executable") or None,
        "args": list(args) if isinstance(args, list) else [],
    }
