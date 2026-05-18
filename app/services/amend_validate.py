import re
from dataclasses import dataclass
from typing import List, Literal, Optional


Action = Literal["REMOVE", "ADD", "REPLACE"]
ClauseType = Literal["preambular", "operative"]


@dataclass
class ParsedOp:
    action: Action
    clause_type: ClauseType
    target: str
    content: Optional[str]  # None means not provided (e.g., REMOVE whole target)


# Matches the three formats your builder generates:
# 1) REMOVE the whole <type> <target>
# 2) REMOVE in the <type> <target>  "text"
# 3) ADD the following <type> <target-or-clause>  "text"
# 4) REPLACE in the <type> <target>  "text"
OP_RE = re.compile(
    r"""
    ^(?P<action>REMOVE|ADD|REPLACE)\s+
    (?:
        # REMOVE whole
        the\ whole\s+(?P<ct_whole>preambular|operative)\s+(?P<target_whole>.+)
        |
        # REMOVE/REPLACE in the <type> <target>
        in\ the\s+(?P<ct_in>preambular|operative)\s+(?P<target_in>.+)
        |
        # ADD the following <type> <target>
        the\ following\s+(?P<ct_add>preambular|operative)\s+(?P<target_add>.+)
    )
    $""",
    re.IGNORECASE | re.VERBOSE,
)

# Matches the indented quoted line:  "..."
QUOTE_RE = re.compile(r'^\s*"(?P<q>.*)"\s*$', re.DOTALL)


def parse_ops(body_markdown: str) -> List[ParsedOp]:
    """
    Parses the markdown built by your JS builder into ParsedOp objects.
    Tolerant: ignores empty lines and unknown lines, but requires at least 1 valid op.
    """
    lines = [ln.rstrip() for ln in body_markdown.splitlines()]

    ops: List[ParsedOp] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line:
            continue

        m = OP_RE.match(line)
        if not m:
            raise ValueError(
                f"Invalid amendment operation line: {line}. "
                "Use ADD / REMOVE / REPLACE operations."
            )

        action = m.group("action").upper()
        ct = (m.group("ct_whole") or m.group("ct_in") or m.group("ct_add") or "").lower()
        target = (m.group("target_whole") or m.group("target_in") or m.group("target_add") or "").strip()

        # Optional quoted content may be on the next non-empty line
        content = None
        j = i
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines):
            qm = QUOTE_RE.match(lines[j])
            if qm:
                content = qm.group("q").strip()
                i = j + 1  # consume the quote line

        if ct not in ("preambular", "operative"):
            continue

        ops.append(ParsedOp(action=action, clause_type=ct, target=target, content=content))

    return ops


def validate_ops(body_markdown: str) -> None:
    ops = parse_ops(body_markdown)

    if not ops:
        raise ValueError("No valid operations found. Use ADD / REMOVE / REPLACE operations.")

    for idx, op in enumerate(ops, start=1):
        if not op.target:
            raise ValueError(f"Operation {idx}: target is required (e.g., operative 4(a) or preambular starting with “Noting”).")

        if op.action in ("ADD", "REPLACE"):
            if op.content is None or not op.content.strip():
                raise ValueError(f"Operation {idx}: {op.action} requires quoted text content.")

        if op.action == "REMOVE":
            # REMOVE is valid either as "whole target" (no content) or "remove specific phrase" (with content)
            # but must not provide empty quotes
            if op.content is not None and not op.content.strip():
                raise ValueError(f"Operation {idx}: REMOVE has empty quoted content; either omit quotes to remove the whole target or provide the phrase to remove.")