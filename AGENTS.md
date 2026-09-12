# Codex Instructions for Dialogue

## Workspace Safety

This repository is the isolated development worktree.

- Work only inside `/home/ubuntu/dialogue-codex`.
- NEVER modify files in `/home/ubuntu/dialogue`.
- `/home/ubuntu/dialogue` is the live deployed production checkout.
- Do not copy files into the production checkout unless the user explicitly asks.

## Production Services

Do NOT restart, stop, start, or reconfigure any production services unless the user explicitly asks.

This includes:

- `dialogue.service`
- nginx
- PostgreSQL
- Ollama

Do not run `sudo systemctl restart`, `stop`, `start`, or similar commands without explicit permission.

## Database Safety

Do not perform database-changing operations unless explicitly requested.

In particular, do not run:

- `alembic upgrade`
- `alembic downgrade`
- database reset scripts
- DROP / DELETE / TRUNCATE operations
- seed scripts that modify production data

Reading schema/code is allowed.

## Secrets

- Do not read or copy `/home/ubuntu/dialogue/.env` unless explicitly requested.
- Do not commit `.env`, credentials, API keys, passwords, tokens, or secrets.

## Git

Before modifying code:

1. Check `pwd`.
2. Check `git branch --show-current`.
3. Check `git status`.

Expected workspace:

- Path: `/home/ubuntu/dialogue-codex`
- Branch: `codex-work`

After modifying code:

- Show the relevant diff.
- Explain what changed.
- Run appropriate non-destructive checks/tests when possible.
- Do not commit or push unless explicitly requested.

## Editing Style

- Prefer small, focused changes.
- Do not refactor unrelated code.
- Preserve existing application behavior unless the requested task requires changing it.
- Investigate the complete route/service/template flow before modifying behavior.
- Ask before making potentially destructive or production-affecting changes.
