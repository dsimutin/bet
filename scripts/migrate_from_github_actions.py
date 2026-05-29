"""Миграция данных из GitHub Actions артефактов на Render Persistent Disk.

Что переносит:
  1. data/core/paper_signal_ledger.json  → /data/core/
  2. data/models/*.pkl + *.meta.json     → /data/models/
  3. data/staging/**/*.csv               → /data/staging/
  4. data/reports/                       → /data/reports/

Использование:
    # Локально (перед деплоем на Render):
    python scripts/migrate_from_github_actions.py \
        --source ./data \
        --dest /data \
        --dry-run

    # Реальная миграция:
    python scripts/migrate_from_github_actions.py \
        --source ./data \
        --dest /data
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


MIGRATION_MANIFEST = "migration_manifest.json"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Migrate data to Render Persistent Disk.")
    p.add_argument("--source", type=Path, default=Path("data"),
                   help="Source data directory (local repo, default: ./data)")
    p.add_argument("--dest", type=Path, default=Path("/data"),
                   help="Destination path (Render disk mountPath, default: /data)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would be copied without doing it")
    p.add_argument("--skip-models", action="store_true",
                   help="Skip copying model .pkl files (large, re-train instead)")
    p.add_argument("--skip-staging", action="store_true",
                   help="Skip copying staging CSVs (re-download instead)")
    return p.parse_args()


def _copy(src: Path, dst: Path, dry_run: bool) -> bool:
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dry_run:
        size_kb = round(src.stat().st_size / 1024, 1)
        print(f"  [DRY] {src} → {dst}  ({size_kb} KB)")
        return True
    shutil.copy2(src, dst)
    size_kb = round(src.stat().st_size / 1024, 1)
    print(f"  [OK]  {src} → {dst}  ({size_kb} KB)")
    return True


def migrate_ledger(source: Path, dest: Path, dry_run: bool) -> dict:
    src = source / "core" / "paper_signal_ledger.json"
    dst = dest / "core" / "paper_signal_ledger.json"
    if not src.exists():
        return {"ledger": "not_found"}

    # Validate JSON before copying
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
        n_entries = len(data.get("entries", {}))
    except json.JSONDecodeError as e:
        print(f"[ERROR] Ledger JSON invalid: {e}", file=sys.stderr)
        return {"ledger": "invalid_json"}

    copied = _copy(src, dst, dry_run)
    return {"ledger": "copied" if copied else "skip", "entries": n_entries}


def migrate_models(source: Path, dest: Path, dry_run: bool, skip: bool) -> dict:
    src_dir = source / "models"
    dst_dir = dest / "models"
    if not src_dir.exists():
        return {"models": "dir_not_found"}

    if skip:
        print("  [SKIP] --skip-models set — models will be retrained on Render")
        return {"models": "skipped"}

    copied = 0
    skipped = 0
    for f in sorted(src_dir.glob("*")):
        if f.suffix in (".pkl", ".json"):
            dst = dst_dir / f.name
            if _copy(f, dst, dry_run):
                copied += 1
            else:
                skipped += 1
    return {"models": "copied", "files_copied": copied, "files_skipped": skipped}


def migrate_staging(source: Path, dest: Path, dry_run: bool, skip: bool) -> dict:
    src_dir = source / "staging"
    if not src_dir.exists():
        return {"staging": "dir_not_found"}

    if skip:
        print("  [SKIP] --skip-staging set — data will be re-downloaded on Render")
        return {"staging": "skipped"}

    copied = 0
    for f in sorted(src_dir.rglob("*.csv")):
        rel = f.relative_to(src_dir)
        dst = dest / "staging" / rel
        if _copy(f, dst, dry_run):
            copied += 1
    for f in sorted(src_dir.rglob("*.parquet")):
        rel = f.relative_to(src_dir)
        dst = dest / "staging" / rel
        if _copy(f, dst, dry_run):
            copied += 1
    return {"staging": "copied", "files_copied": copied}


def migrate_reports(source: Path, dest: Path, dry_run: bool) -> dict:
    src_dir = source / "reports"
    if not src_dir.exists():
        return {"reports": "dir_not_found"}

    copied = 0
    for f in sorted(src_dir.glob("*.json")):
        dst = dest / "reports" / f.name
        if _copy(f, dst, dry_run):
            copied += 1
    for f in sorted(src_dir.glob("*.md")):
        dst = dest / "reports" / f.name
        if _copy(f, dst, dry_run):
            copied += 1
    return {"reports": "copied", "files_copied": copied}


def write_manifest(dest: Path, results: dict, dry_run: bool) -> None:
    manifest = {
        "migrated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "results": results,
    }
    if dry_run:
        print(f"\n[DRY] Would write manifest → {dest / MIGRATION_MANIFEST}")
        print(json.dumps(manifest, indent=2))
        return

    out = dest / MIGRATION_MANIFEST
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[OK]  Manifest → {out}")


def main() -> None:
    args = parse_args()

    print("=" * 60)
    print(f"  Migration: {args.source} → {args.dest}")
    print(f"  Dry run: {args.dry_run}")
    print("=" * 60)

    if not args.source.exists():
        print(f"[ERROR] Source directory not found: {args.source}", file=sys.stderr)
        sys.exit(1)

    if not args.dry_run and not args.dest.exists():
        print(f"[WARN] Destination {args.dest} does not exist — creating...")
        args.dest.mkdir(parents=True, exist_ok=True)

    results: dict = {}

    print("\n── Ledger ──────────────────────────────")
    results["ledger"] = migrate_ledger(args.source, args.dest, args.dry_run)

    print("\n── Models ──────────────────────────────")
    results["models"] = migrate_models(args.source, args.dest, args.dry_run, args.skip_models)

    print("\n── Staging data ────────────────────────")
    results["staging"] = migrate_staging(args.source, args.dest, args.dry_run, args.skip_staging)

    print("\n── Reports ─────────────────────────────")
    results["reports"] = migrate_reports(args.source, args.dest, args.dry_run)

    write_manifest(args.dest, results, args.dry_run)

    print("\n" + "=" * 60)
    if args.dry_run:
        print("  DRY RUN complete. Run without --dry-run to apply.")
    else:
        print("  Migration complete. Verify on Render: GET /health/all")
    print("=" * 60)


if __name__ == "__main__":
    main()
