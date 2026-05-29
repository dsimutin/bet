"""Persistent Disk adapter for Render.com deployments.

On Render, models and ledger live on a mounted disk (/data by default).
This module wraps path resolution so the rest of the codebase never
hard-codes local paths like 'data/models/'.

Usage:
    from src.infrastructure.render_storage import RenderStorage
    storage = RenderStorage()
    model_path = storage.model_path("dc_EPL_20240529")
    ledger_path = storage.ledger_path
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


class RenderStorage:
    """Path resolver that adapts local dev paths to Render Persistent Disk paths."""

    def __init__(
        self,
        data_dir: str | Path | None = None,
    ) -> None:
        # DATA_DIR env var set in render.yaml; falls back to local 'data/'
        root = data_dir or os.environ.get("DATA_DIR", "data")
        self._root = Path(root)

    # ── Core directories ────────────────────────────────────────────

    @property
    def root(self) -> Path:
        return self._ensure(self._root)

    @property
    def model_dir(self) -> Path:
        d = Path(os.environ.get("MODEL_DIR", self._root / "models"))
        return self._ensure(d)

    @property
    def staging_dir(self) -> Path:
        d = Path(os.environ.get("STAGING_DIR", self._root / "staging"))
        return self._ensure(d)

    @property
    def reports_dir(self) -> Path:
        d = Path(os.environ.get("REPORTS_DIR", self._root / "reports"))
        return self._ensure(d)

    @property
    def core_dir(self) -> Path:
        return self._ensure(self._root / "core")

    # ── File paths ──────────────────────────────────────────────────

    @property
    def ledger_path(self) -> Path:
        p = os.environ.get("LEDGER_PATH", str(self._root / "core" / "paper_signal_ledger.json"))
        return Path(p)

    def model_pkl(self, model_id: str) -> Path:
        return self.model_dir / f"{model_id}.pkl"

    def model_meta(self, model_id: str) -> Path:
        return self.model_dir / f"{model_id}.meta.json"

    def drift_report(self, date_str: str = "") -> Path:
        name = f"drift_report_{date_str}.json" if date_str else "drift_report.json"
        return self.reports_dir / name

    def settlement_report(self, date_str: str = "") -> Path:
        name = f"settlement_{date_str}.json" if date_str else "settlement_report.json"
        return self.reports_dir / name

    def staging_csv(self, league: str, filename: str) -> Path:
        d = self._ensure(self.staging_dir / league)
        return d / filename

    # ── Disk health ─────────────────────────────────────────────────

    def disk_usage_mb(self) -> dict[str, float]:
        """Return disk usage in MB for key directories."""
        def _mb(p: Path) -> float:
            if not p.exists():
                return 0.0
            total = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            return round(total / 1_048_576, 2)

        return {
            "models_mb": _mb(self.model_dir),
            "staging_mb": _mb(self.staging_dir),
            "reports_mb": _mb(self.reports_dir),
            "total_mb": _mb(self._root),
        }

    def is_healthy(self) -> tuple[bool, str]:
        """Check disk is mounted and writable."""
        try:
            test = self._root / ".render_healthcheck"
            test.write_text("ok", encoding="utf-8")
            test.unlink()
            return True, "disk writable"
        except Exception as e:
            return False, f"disk error: {e}"

    # ── Migration helpers ────────────────────────────────────────────

    def import_file(self, src: Path, dest_relative: str) -> Path:
        """Copy a file into the managed disk tree."""
        dest = self._root / dest_relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return dest

    # ── Internal ────────────────────────────────────────────────────

    @staticmethod
    def _ensure(path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path


# Module-level singleton — import and use directly
storage = RenderStorage()
