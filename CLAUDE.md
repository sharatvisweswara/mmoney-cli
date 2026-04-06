# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv run mmoney --help              # Run the CLI
uv run pytest tests/              # Run all tests
uv run pytest tests/test_cli.py::TestAuthCommands::test_name -v  # Run a single test
./scripts/setup-dev.sh lint       # Run all linters (ruff, vulture, pyright)
./scripts/setup-dev.sh test       # Run tests with summary
```

Individual linters:
```bash
uv run ruff check .
uv run ruff format --check .
uv run vulture mmoney_cli --min-confidence 80
uv run pyright mmoney_cli/
```

Pre-commit hooks run ruff format + detect-secrets on commit, and pytest on push.

## Architecture

All CLI logic lives in a single file: `mmoney_cli/cli.py`. It is structured top-to-bottom:

1. **Enums**: `ExitCode` (0–6) and `ErrorCode` (machine-readable strings like `AUTH_REQUIRED`)
2. **Config utilities**: read/write `~/.mmoney/` directory for device IDs, session pickle fallback
3. **Keychain helpers**: OS keychain via `keyring`, fallback to pickle
4. **Output formatting**: `output_data()` handles `--format json|jsonl|csv|text`; `_extract_records()` unwraps nested API responses; `_flatten_dict()` for CSV/text
5. **`run_async()`**: wraps all `async` Monarch Money API calls
6. **`@require_mutations` decorator**: blocks write operations unless `--allow-mutations` flag is passed
7. **Command groups**: `auth`, `config`, `accounts`, `holdings`, `transactions`, `categories`, `tags`, `budgets`, `cashflow`, `recurring`, `institutions`, `subscription`

Data model: `Institution → Credential → Account → Transaction → Category/Merchant/Tags` and `Account → Holding` for investments.

Error output is always structured JSON:
```json
{"error": {"code": "AUTH_REQUIRED", "message": "...", "details": "..."}}
```

Exit codes: 0 success, 1 general, 2 auth, 3 not found, 4 validation, 5 API error, 6 mutation blocked.

## Authentication

The CLI supports four methods (in preference order for automation):
1. Email + password + TOTP secret (`--mfa-secret`)
2. Email + password + one-time MFA code
3. Device UUID (`--device-id`)
4. Session token from browser network tab

Credentials are stored in the OS keychain; `~/.mmoney/session.pickle` is the fallback.

## Testing

Tests use Click's `CliRunner` and `unittest.mock`. Fixtures live in `tests/conftest.py`. Two test files: `test_cli.py` (happy paths) and `test_cli_errors.py` (error/edge cases).
