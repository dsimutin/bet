#!/usr/bin/env python3
"""Pre-deployment health check script.

Validates system configuration, dependencies, and readiness for deployment.
Run this before deploying to Render: python scripts/health_check.py
"""

from __future__ import annotations

import sys
import argparse
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)

_log = logging.getLogger(__name__)


def check_environment() -> bool:
    """Check critical environment variables."""
    from src.infrastructure.startup_validation import validate_startup, log_startup_validation

    _log.info("\n" + "=" * 70)
    _log.info("1. ENVIRONMENT VARIABLES")
    _log.info("=" * 70)

    result = validate_startup()
    log_startup_validation(result)

    if not result["critical_ok"]:
        return False

    return True


def check_dependencies() -> bool:
    """Check required Python dependencies."""
    _log.info("\n" + "=" * 70)
    _log.info("2. DEPENDENCIES")
    _log.info("=" * 70)

    required = [
        ("pandas", "data manipulation"),
        ("tenacity", "API retry logic"),
        ("pydantic", "data validation"),
        ("requests", "HTTP requests"),
        ("fastapi", "web framework"),
    ]

    all_ok = True
    for module_name, description in required:
        try:
            __import__(module_name)
            _log.info(f"✓ {module_name:20s} ({description})")
        except ImportError:
            _log.error(f"✗ {module_name:20s} NOT INSTALLED — run: pip install -e .")
            all_ok = False

    return all_ok


def check_data_files() -> bool:
    """Check that required data files exist."""
    _log.info("\n" + "=" * 70)
    _log.info("3. DATA FILES")
    _log.info("=" * 70)

    required_dirs = [
        ("data/models", "trained models"),
        ("data/staging", "staging directory"),
        ("data/core", "core ledger"),
    ]

    all_ok = True
    for path, description in required_dirs:
        p = Path(path)
        if p.exists():
            _log.info(f"✓ {path:30s} ({description})")
        else:
            _log.warning(f"⚠️  {path:30s} missing ({description})")
            # Don't fail, might be created on first run

    # Check ledger specifically
    ledger = Path("data/core/paper_signal_ledger.json")
    if ledger.exists():
        try:
            import json

            data = json.loads(ledger.read_text())
            entries = len(data.get("entries", {}))
            _log.info(f"   → Ledger has {entries} signals")
        except Exception as exc:
            _log.error(f"   → Ledger corrupted: {exc}")
            all_ok = False

    return all_ok


def check_models() -> bool:
    """Check that trained models exist."""
    _log.info("\n" + "=" * 70)
    _log.info("4. TRAINED MODELS")
    _log.info("=" * 70)

    import pickle
    from pathlib import Path

    model_dir = Path("data/models")
    if not model_dir.exists():
        _log.warning(f"✓ Model directory will be created on first training")
        return True

    models = list(model_dir.glob("*.pkl"))
    if not models:
        _log.warning("⚠️  No models found in data/models/")
        _log.info("   Models will be trained on first run by run_trainer.py")
        return True

    for model_file in sorted(models)[:5]:  # Show first 5
        try:
            with open(model_file, "rb") as f:
                pickle.load(f)
            _log.info(f"✓ {model_file.name}")
        except Exception as exc:
            _log.error(f"✗ {model_file.name} corrupted: {exc}")
            return False

    if len(models) > 5:
        _log.info(f"   ... and {len(models) - 5} more")

    return True


def check_api_quota() -> bool:
    """Check API quota budget."""
    _log.info("\n" + "=" * 70)
    _log.info("5. API QUOTA BUDGET")
    _log.info("=" * 70)

    import os

    cache_ttl = int(os.environ.get("ODDS_CACHE_TTL_SECONDS", "28800"))
    exotic = os.environ.get("EXOTIC_LEAGUES", "").strip()

    estimated_calls = 390  # base scans
    if exotic:
        num_exotic = len([x for x in exotic.split(",") if x.strip()])
        estimated_calls += num_exotic * 30

    estimated_calls += 27  # admin overhead

    _log.info(f"Cache TTL:          {cache_ttl}s ({cache_ttl/3600:.1f}h)")
    _log.info(
        f"Exotic leagues:     {len([x for x in exotic.split(',') if x.strip()]) if exotic else 'default (6)'}"
    )
    _log.info(f"Est. calls/month:   {estimated_calls} / 500")
    _log.info(
        f"Buffer:             {500 - estimated_calls} calls ({100*(500-estimated_calls)/500:.0f}%)"
    )

    if estimated_calls > 480:
        _log.error(f"✗ Exceeds safe quota (480/500). Disable extended features.")
        return False

    _log.info(f"✓ Quota fits safely within free tier")
    return True


def check_tests(*, collect_only: bool = False) -> bool:
    """Check that tests pass."""
    _log.info("\n" + "=" * 70)
    _log.info("6. TEST SUITE")
    _log.info("=" * 70)

    import subprocess

    cmd = ["python", "-m", "pytest", "-q"]
    timeout = 300
    if collect_only:
        _log.warning("COLLECTION ONLY — NOT A DEPLOYMENT GATE")
        cmd = ["python", "-m", "pytest", "--co", "-q"]
        timeout = 30
    _log.info("Running %s...", " ".join(cmd))
    try:
        child_env = os.environ.copy()
        for secret_name in (
            "THE_ODDS_API_KEY",
            "ODDS_API_IO_KEY",
            "API_FOOTBALL_KEY",
            "TELEGRAM_BOT_TOKEN",
        ):
            child_env.pop(secret_name, None)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=child_env,
        )
        if result.returncode == 0:
            if collect_only:
                test_count = len([line for line in result.stdout.split("\n") if "::" in line])
                _log.info(f"✓ {test_count} tests can be collected")
            else:
                _log.info("✓ pytest suite passed")
        else:
            _log.error("✗ Test command failed:")
            _log.error(result.stdout)
            _log.error(result.stderr)
            return False
    except subprocess.TimeoutExpired:
        _log.error("✗ Test collection timed out")
        return False

    return True


def main(argv: list[str] | None = None) -> int:
    """Run all health checks."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--collect-only", action="store_true")
    args = parser.parse_args(argv)
    _log.info("\n")
    _log.info("╔════════════════════════════════════════════════════════════════╗")
    _log.info("║             PRE-DEPLOYMENT HEALTH CHECK                        ║")
    _log.info("╚════════════════════════════════════════════════════════════════╝")

    checks = [
        ("Environment Variables", check_environment),
        ("Dependencies", check_dependencies),
        ("Data Files", check_data_files),
        ("Trained Models", check_models),
        ("API Quota Budget", check_api_quota),
        ("Test Suite", lambda: check_tests(collect_only=args.collect_only)),
    ]

    results = []
    for name, check_fn in checks:
        try:
            ok = check_fn()
            results.append((name, ok))
        except Exception as exc:
            _log.error(f"\n✗ {name} check failed with exception: {exc}")
            results.append((name, False))

    # Summary
    _log.info("\n" + "=" * 70)
    _log.info("SUMMARY")
    _log.info("=" * 70)

    all_ok = True
    for name, ok in results:
        status = "✓ PASS" if ok else "✗ FAIL"
        _log.info(f"{status:10s} {name}")
        if not ok:
            all_ok = False

    _log.info("=" * 70)

    if all_ok:
        if args.collect_only:
            _log.warning("\nCOLLECTION ONLY — NOT A DEPLOYMENT GATE\n")
        else:
            _log.info("\n✅ All checks passed. System is ready for deployment.\n")
        _log.info("Next steps:")
        _log.info("  1. Commit and push the feature branch")
        _log.info("  2. Open a PR into all-the-best")
        _log.info("  3. Set required secrets in Render and GitHub Actions")
        _log.info("  4. Deploy from the reviewed production branch")
        _log.info("  5. Monitor logs at 07:00 UTC for signal generation\n")
        return 0
    else:
        _log.error("\n❌ Some checks failed. Fix issues before deploying.\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
