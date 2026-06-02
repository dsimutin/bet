"""Validate critical environment variables on startup.

Prevents silent failures where missing configuration causes no signals to generate.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

_log = logging.getLogger(__name__)


class StartupValidationError(Exception):
    """Raised when critical environment variables are missing or invalid."""

    pass


def validate_startup() -> dict[str, Any]:
    """Validate all critical and recommended environment variables.

    Returns:
        dict with keys: critical_ok, critical_issues, warnings
    """
    issues: list[str] = []
    warnings: list[str] = []

    # ─────────────────────────────────────────────────────────────────────
    # CRITICAL: THE_ODDS_API_KEY (blocks signal generation)
    # ─────────────────────────────────────────────────────────────────────
    odds_key = os.environ.get("THE_ODDS_API_KEY", "").strip()
    if not odds_key:
        issues.append(
            "🔴 THE_ODDS_API_KEY not set — signal generation will return 0 candidates. "
            "Register at https://the-odds-api.com and set the environment variable."
        )
    elif len(odds_key) < 20:
        issues.append(
            f"⚠️  THE_ODDS_API_KEY looks too short ({len(odds_key)} chars, expect 32+). "
            "Check if value was copied correctly."
        )

    # ─────────────────────────────────────────────────────────────────────
    # RECOMMENDED: TELEGRAM (affects alert delivery)
    # ─────────────────────────────────────────────────────────────────────
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not telegram_token and not telegram_chat:
        warnings.append(
            "⚠️  Telegram not configured (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID). "
            "System will run in dry-run mode (logs to stdout only). To enable alerts, "
            "get bot token from @BotFather and chat ID from @userinfobot on Telegram."
        )
    elif telegram_token and not telegram_chat:
        warnings.append(
            "⚠️  TELEGRAM_BOT_TOKEN set but TELEGRAM_CHAT_ID missing. "
            "Alerts will not be delivered. Get chat ID from @userinfobot."
        )
    elif not telegram_token and telegram_chat:
        warnings.append(
            "⚠️  TELEGRAM_CHAT_ID set but TELEGRAM_BOT_TOKEN missing. "
            "Alerts will not be delivered. Get bot token from @BotFather."
        )

    # ─────────────────────────────────────────────────────────────────────
    # CONFIGURATION: Check reasonable defaults
    # ─────────────────────────────────────────────────────────────────────
    leagues = os.environ.get("LEAGUES", "").strip()
    if not leagues:
        warnings.append(
            "ℹ️  LEAGUES not set, using defaults (EPL, Bundesliga, LaLiga, Serie A, Ligue 1)"
        )

    active_mode = os.environ.get("ACTIVE_MODE", "true").lower()
    if active_mode not in ("true", "1", "yes", "on"):
        warnings.append(
            f"ℹ️  ACTIVE_MODE={active_mode} (automatic scans disabled). "
            "Set ACTIVE_MODE=true for 07:00 and 15:00 UTC scans."
        )

    # ─────────────────────────────────────────────────────────────────────
    # EXOTIC LEAGUES: Warn if extended leagues would exceed quota
    # ─────────────────────────────────────────────────────────────────────
    exotic = os.environ.get("EXOTIC_LEAGUES", "").strip()
    if exotic:
        num_exotic = len([x for x in exotic.split(",") if x.strip()])
        extra_quota = num_exotic * 30  # 30 calls/month per league
        warnings.append(
            f"ℹ️  {num_exotic} exotic leagues enabled = +{extra_quota} API calls/month. "
            f"(Free tier limit: 500/month, default uses ~417/month, buffer: 83)"
        )

    # ─────────────────────────────────────────────────────────────────────
    # CACHE: Verify optimization settings
    # ─────────────────────────────────────────────────────────────────────
    cache_ttl_raw = os.environ.get("ODDS_CACHE_TTL_SECONDS", "28800")
    try:
        cache_ttl = int(cache_ttl_raw)
        if cache_ttl < 300:
            warnings.append(
                f"⚠️  ODDS_CACHE_TTL_SECONDS={cache_ttl}s is below minimum (300s). "
                "Will be enforced to 300s. Current setting wastes API quota."
            )
        elif cache_ttl < 28800:
            warnings.append(
                f"⚠️  ODDS_CACHE_TTL_SECONDS={cache_ttl}s is below optimal (28800s = 8h). "
                "With 07:00 and 15:00 UTC scans, recommend 28800s to share cache."
            )
    except ValueError:
        issues.append(
            f"❌ ODDS_CACHE_TTL_SECONDS='{cache_ttl_raw}' is not a valid integer."
        )

    # ─────────────────────────────────────────────────────────────────────
    # STORAGE: Verify paths exist
    # ─────────────────────────────────────────────────────────────────────
    import pathlib

    for path_env, friendly_name in [
        ("MODEL_DIR", "model directory"),
        ("STAGING_DIR", "staging directory"),
        ("LEDGER_PATH", "ledger file"),
    ]:
        path_raw = os.environ.get(path_env, "").strip() or _get_default(path_env)
        path = pathlib.Path(path_raw)
        if path_env == "LEDGER_PATH":
            parent = path.parent
            if not parent.exists():
                warnings.append(
                    f"⚠️  {friendly_name} parent directory missing: {parent} "
                    "(will be created on first signal)"
                )
        else:
            if not path.exists():
                warnings.append(
                    f"⚠️  {friendly_name} missing: {path} "
                    "(will cause issues if models/data needed)"
                )

    return {
        "critical_ok": len(issues) == 0,
        "critical_issues": issues,
        "warnings": warnings,
        "ready_for_deployment": len(issues) == 0,
    }


def _get_default(env_name: str) -> str:
    defaults = {
        "MODEL_DIR": "data/models",
        "STAGING_DIR": "data/staging",
        "LEDGER_PATH": "data/core/paper_signal_ledger.json",
    }
    return defaults.get(env_name, "")


def log_startup_validation(result: dict[str, Any]) -> None:
    """Log validation results in a user-friendly format."""
    if result["critical_issues"]:
        _log.error("=" * 70)
        _log.error("STARTUP VALIDATION FAILED — Critical issues found:")
        _log.error("=" * 70)
        for issue in result["critical_issues"]:
            _log.error(issue)
        _log.error("=" * 70)

    if result["warnings"]:
        _log.warning("Startup warnings:")
        for warning in result["warnings"]:
            _log.warning(warning)

    if result["critical_ok"]:
        _log.info("✓ All critical environment variables validated")

    if result["warnings"]:
        _log.info(f"({len(result['warnings'])} warnings — see above)")


def raise_if_invalid(result: dict[str, Any]) -> None:
    """Raise exception if critical issues found."""
    if not result["critical_ok"]:
        msg = "\n".join(result["critical_issues"])
        raise StartupValidationError(f"\n{msg}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = validate_startup()
    log_startup_validation(result)
    sys.exit(0 if result["critical_ok"] else 1)
