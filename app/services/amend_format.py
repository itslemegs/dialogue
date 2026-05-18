# app/services/amend_format.py
from app.services.amend_ai import AmendGen


def _one_line(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _quote(text: str) -> str:
    text = _one_line(text)
    return f'"{text}"'


def amend_gen_to_body_markdown(gen: AmendGen) -> str:
    lines: list[str] = []

    for op in gen.operations:
        action = op.action
        clause_type = op.clause_type
        target = _one_line(op.target)
        content = _one_line(op.content)

        if not target:
            continue

        if action == "ADD":
            if not content:
                continue
            lines.append(f"ADD the following {clause_type} {target}")
            lines.append(_quote(content))
            lines.append("")

        elif action == "REPLACE":
            if not content:
                continue
            lines.append(f"REPLACE in the {clause_type} {target}")
            lines.append(_quote(content))
            lines.append("")

        elif action == "REMOVE":
            if content:
                lines.append(f"REMOVE in the {clause_type} {target}")
                lines.append(_quote(content))
                lines.append("")
            else:
                lines.append(f"REMOVE the whole {clause_type} {target}")
                lines.append("")

    return "\n".join(lines).strip()