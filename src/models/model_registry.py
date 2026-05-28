"""File-based model registry for Dixon-Coles versions."""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.models.calibrator import ProbabilityCalibrator
from src.models.dixon_coles import DixonColesModel


@dataclass(frozen=True)
class ModelVersion:
    model_id: str
    league: str
    model_path: Path
    meta_path: Path
    created_at_utc: str
    status: str
    n_matches: int
    dataset_hash: str
    brier_score: float | None = None
    log_loss: float | None = None


class ModelRegistry:
    """Save, load, list, and promote model versions."""

    def __init__(self, root: Path = Path("data/models")) -> None:
        self.root = root

    def save(
        self,
        model: DixonColesModel,
        league: str,
        metrics: dict[str, Any] | None = None,
        status: str = "candidate",
        calibrator: ProbabilityCalibrator | None = None,
    ) -> str:
        params = model.params
        if params is None:
            raise ValueError("Cannot save an unfitted model")
        self.root.mkdir(parents=True, exist_ok=True)
        created = datetime.now(timezone.utc)
        short_hash = params.dataset_hash.split(":", 1)[-1][:8]
        model_id = self._unique_model_id(f"dc_{league}_{created:%Y%m%d}_{short_hash}")
        model.model_id = model_id
        model_path = self.root / f"{model_id}.pkl"
        meta_path = self.root / f"{model_id}.meta.json"
        with model_path.open("wb") as fh:
            pickle.dump(model, fh)
        calibration_path = None
        if calibrator is not None:
            calibration_path = self.root / f"calibration_{model_id}.pkl"
            with calibration_path.open("wb") as fh:
                pickle.dump(calibrator, fh)
        meta = {
            "model_id": model_id,
            "league": league,
            "trained_on": {
                "start": params.trained_on_dates[0].isoformat(),
                "end": params.trained_on_dates[1].isoformat(),
            },
            "n_matches": params.n_matches,
            "dataset_hash": params.dataset_hash,
            "status": status,
            "created_at_utc": created.isoformat(),
            "model_path": str(model_path),
            "calibration_path": str(calibration_path) if calibration_path else None,
            **(metrics or {}),
        }
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        return model_id

    def load_latest(self, league: str, production_only: bool = True) -> DixonColesModel:
        versions = self.list_versions(league)
        if production_only:
            versions = [item for item in versions if item.status == "production"]
        if not versions:
            raise FileNotFoundError(f"No model versions found for league={league}")
        latest = max(versions, key=lambda item: datetime.fromisoformat(item.created_at_utc))
        with latest.model_path.open("rb") as fh:
            model = pickle.load(fh)
        if not isinstance(model, DixonColesModel):
            raise TypeError(f"Registry object is not DixonColesModel: {latest.model_path}")
        model.model_id = latest.model_id
        return model

    def load_calibrator(self, model_id: str) -> ProbabilityCalibrator | None:
        meta_path = self._meta_path_for(model_id)
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
        calibration_path = raw.get("calibration_path")
        if not calibration_path:
            return None
        path = Path(str(calibration_path))
        if not path.exists():
            return None
        with path.open("rb") as fh:
            calibrator = pickle.load(fh)
        if not isinstance(calibrator, ProbabilityCalibrator):
            raise TypeError(f"Registry object is not ProbabilityCalibrator: {path}")
        return calibrator

    def load_latest_with_calibrator(
        self, league: str, production_only: bool = True
    ) -> tuple[DixonColesModel, ProbabilityCalibrator | None]:
        model = self.load_latest(league, production_only=production_only)
        if model.model_id is None:
            return model, None
        return model, self.load_calibrator(model.model_id)

    def list_versions(self, league: str) -> list[ModelVersion]:
        versions: list[ModelVersion] = []
        for meta_path in sorted(self.root.glob(f"dc_{league}_*.meta.json")):
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
            model_path = Path(
                str(raw.get("model_path") or meta_path.with_suffix("").with_suffix(".pkl"))
            )
            versions.append(
                ModelVersion(
                    model_id=str(raw["model_id"]),
                    league=str(raw["league"]),
                    model_path=model_path,
                    meta_path=meta_path,
                    created_at_utc=str(raw["created_at_utc"]),
                    status=str(raw.get("status", "candidate")),
                    n_matches=int(raw["n_matches"]),
                    dataset_hash=str(raw["dataset_hash"]),
                    brier_score=_optional_float(raw.get("brier_score")),
                    log_loss=_optional_float(raw.get("log_loss")),
                )
            )
        return versions

    def promote(self, model_id: str) -> None:
        target = self._meta_path_for(model_id)
        raw = json.loads(target.read_text(encoding="utf-8"))
        league = str(raw["league"])
        for version in self.list_versions(league):
            meta = json.loads(version.meta_path.read_text(encoding="utf-8"))
            meta["status"] = "production" if version.model_id == model_id else "archived"
            version.meta_path.write_text(
                json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
            )

    def _meta_path_for(self, model_id: str) -> Path:
        path = self.root / f"{model_id}.meta.json"
        if not path.exists():
            raise FileNotFoundError(f"Unknown model_id={model_id}")
        return path

    def _unique_model_id(self, base: str) -> str:
        candidate = base
        counter = 2
        while (self.root / f"{candidate}.meta.json").exists() or (
            self.root / f"{candidate}.pkl"
        ).exists():
            candidate = f"{base}_{counter}"
            counter += 1
        return candidate


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
