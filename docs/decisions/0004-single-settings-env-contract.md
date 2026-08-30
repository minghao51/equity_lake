# ADR-0004: Single `Settings` with `extra="forbid"`, raw API keys at client seams

**Status:** Accepted
**Recorded:** 2026-08-29 (backfilled)

## Context

Configuration arrived from YAML, `.env`, and environment variables at once.
Silently unknown keys hid typos; declaring provider API keys in typed
settings spread secret handling across config models.

## Decision

- One `Settings(BaseSettings)` with `YamlConfigSettingsSource`,
  `env_prefix="EQUITY_"`, `env_nested_delimiter="__"`, and
  `extra="forbid"`. Priority: init > env vars > .env > YAML.
- Because of `extra="forbid"`, any `EQUITY_<GROUP>__*` env var requires a
  matching nested `BaseModel` field or Settings raises at load; the model and
  the `.env.example` entry are added in the same change.
- SDK/API keys (`FRED_API_KEY`, `FINNHUB_API_KEY`, `DEEPSEEK_API_KEY`,
  `WANDB_API_KEY`, …) stay raw/unprefixed and are read via `os.getenv` at the
  client seam, never declared in `Settings`.

## Consequences

- Misconfigured environments fail loudly at load instead of silently
  defaulting.
- Config surface is enumerable from one class; `.env.example` stays the
  human contract.
- Secrets remain confined to client seams, keeping dotenvx the only secrets
  channel.
