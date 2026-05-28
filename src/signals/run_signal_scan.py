"""
Модуль ежедневного сканирования сигналов (исключительно бумажная торговля).

Назначение:
    Сканирует текущие котировки, сравнивает с эталонными «честными» курсами,
    генерирует торговые сигналы при наличии достаточного преимущества (edge)
    и фиксирует их в бумажном журнале (PaperLedger).

ВАЖНО:
    Модуль работает исключительно в режиме бумажной торговли (status='paper').
    Реальные финансовые транзакции не выполняются ни при каких условиях.
    Все сигналы предназначены только для исследовательских целей.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field, field_validator

from src.ingest.active_sports_resolver import ActiveSportsResolver, ActiveSportsReport

# ---------------------------------------------------------------------------
# Вспомогательная функция генерации идентификатора сигнала
# ---------------------------------------------------------------------------


def _make_signal_id(counter: int) -> str:
    """
    Генерирует уникальный идентификатор сигнала в формате sig_{YYYYMMDD}_{counter}.

    Параметры
    ----------
    counter : int
        Порядковый номер сигнала за день (6 цифр, дополняется нулями).

    Возвращает
    ----------
    str
        Идентификатор вида 'sig_20260101_000001'.
    """
    date_str = datetime.now(tz=timezone.utc).strftime("%Y%m%d")
    return f"sig_{date_str}_{counter:06d}"


# ---------------------------------------------------------------------------
# Pydantic-модель сигнала
# ---------------------------------------------------------------------------


class SignalRecord(BaseModel):
    """
    Запись торгового сигнала в бумажном режиме.

    Поля соответствуют JSON-спецификации системы сигналов.
    Статус всегда равен 'paper' — реальные транзакции не производятся.
    """

    signal_id: str = Field(
        ...,
        description="Уникальный идентификатор сигнала (формат: sig_{YYYYMMDD}_{6digit_counter})",
    )
    strategy_id: str = Field(..., description="Идентификатор стратегии, генерирующей сигнал")
    normalized_event_id: str = Field(
        ...,
        description="Нормализованный идентификатор события (единый формат для всех источников)",
    )
    bookmaker: str = Field(..., description="Название букмекера")
    market_key: str = Field(..., description="Ключ рынка (например, 'h2h', 'totals')")
    selection: str = Field(..., description="Название исхода (например, 'home_win', 'over_2.5')")
    entry_odds: float = Field(..., gt=1.0, description="Текущий курс букмекера (десятичный)")
    reference_fair_odds: float = Field(
        ..., gt=1.0, description="Эталонный «честный» курс без маржи"
    )
    edge_pct: float = Field(
        ...,
        description="Преимущество в %: (entry_odds / reference_fair_odds - 1) * 100",
    )
    clv_proxy_expected: Literal["positive", "neutral", "negative", "unknown"] = Field(
        "unknown",
        description="Ожидаемое направление CLV (оценка до закрытия линии)",
    )
    status: Literal["paper"] = Field(
        "paper",
        description="Статус сигнала: всегда 'paper' (бумажная торговля)",
    )
    confidence: Literal["high", "medium", "low"] = Field(
        ...,
        description="Уровень уверенности в сигнале: high/medium/low",
    )
    explain: list[str] = Field(
        default_factory=list,
        description="Список факторов, обосновывающих сигнал (читаемые строки)",
    )
    timestamp_utc: datetime = Field(
        ...,
        description="Временная метка генерации сигнала (UTC)",
    )
    risk_flags: list[str] = Field(
        default_factory=list,
        description="Список флагов риска (например, 'lineup_missing', 'low_source_quality')",
    )

    @field_validator("timestamp_utc", mode="before")
    @classmethod
    def ensure_utc(cls, v: datetime | str) -> datetime:
        """Нормализует временную метку в UTC."""
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    @field_validator("status")
    @classmethod
    def must_be_paper(cls, v: str) -> str:
        """Гарантирует, что статус сигнала всегда равен 'paper'."""
        if v != "paper":
            raise ValueError("status должен быть 'paper'. Реальная торговля запрещена.")
        return v


# ---------------------------------------------------------------------------
# Фильтр рисков
# ---------------------------------------------------------------------------


@dataclass
class RiskFilter:
    """
    Набор параметров фильтрации рисков для торговых сигналов.

    Сигнал пропускается только при выполнении всех условий.
    При нарушении любого условия сигнал блокируется с соответствующим флагом.
    """

    min_edge_pct: float = 2.0
    """Минимальный edge в % для допуска сигнала"""

    min_source_quality_score: float = 0.4
    """Минимальный балл качества источника данных о составе"""

    max_book_margin_pct: float = 5.0
    """Максимально допустимая маржа букмекера в %"""

    no_bet_if_lineup_missing: bool = True
    """Блокировать сигнал, если состав команды неизвестен"""

    no_bet_if_data_quality_below: float = 0.4
    """Блокировать сигнал при качестве данных ниже порога"""


# ---------------------------------------------------------------------------
# Основной сканер сигналов
# ---------------------------------------------------------------------------


class SignalScanner:
    """
    Ежедневный сканер торговых сигналов.

    Алгоритм работы:
        1. Загружает текущие котировки и эталонные «честные» курсы.
        2. Для каждого исхода вычисляет edge = (odds / fair_odds - 1) * 100.
        3. Применяет фильтры рисков (edge, качество данных, наличие состава).
        4. Генерирует SignalRecord для прошедших фильтрацию кандидатов.
        5. Сохраняет сигналы в JSON и записывает в PaperLedger.

    Параметры конфигурации задаются в YAML/JSON файле, путь — config_path.
    """

    def __init__(self, config_path: Path) -> None:
        """
        Инициализирует сканер из конфигурационного файла.

        Параметры
        ----------
        config_path : Path
            Путь к JSON или YAML конфигурационному файлу.
            Ожидаемые ключи: strategy_id, risk_filter (опционально).
        """
        self.config_path = config_path
        self._config: dict[str, Any] = {}
        self.risk_filter = RiskFilter()
        self._signal_counter: int = 0

        if config_path.exists():
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            self._config = raw

            # Настройка фильтра рисков из конфигурации
            rf_cfg = raw.get("risk_filter", {})
            self.risk_filter = RiskFilter(
                min_edge_pct=rf_cfg.get("min_edge_pct", 2.0),
                min_source_quality_score=rf_cfg.get("min_source_quality_score", 0.4),
                max_book_margin_pct=rf_cfg.get("max_book_margin_pct", 5.0),
                no_bet_if_lineup_missing=rf_cfg.get("no_bet_if_lineup_missing", True),
                no_bet_if_data_quality_below=rf_cfg.get("no_bet_if_data_quality_below", 0.4),
            )

    @property
    def strategy_id(self) -> str:
        """Идентификатор стратегии из конфигурации."""
        return self._config.get("strategy_id", "default_strategy")

    def load_current_odds(self, data_dir: Path) -> pd.DataFrame:
        """
        Загружает текущие котировки из директории данных.

        Ожидаемый формат: файл current_odds.parquet или current_odds.csv
        в директории data_dir. Обязательные колонки:
            event_id, bookmaker, market_key, selection, odds, book_margin_pct.

        Параметры
        ----------
        data_dir : Path
            Директория с файлами данных.

        Возвращает
        ----------
        pd.DataFrame
            DataFrame с текущими котировками.
        """
        parquet_path = data_dir / "current_odds.parquet"
        csv_path = data_dir / "current_odds.csv"

        if parquet_path.exists():
            return pd.read_parquet(parquet_path)
        elif csv_path.exists():
            return pd.read_csv(csv_path)
        else:
            # Возвращаем пустой DataFrame с корректной схемой
            return pd.DataFrame(
                columns=[
                    "event_id",
                    "bookmaker",
                    "market_key",
                    "selection",
                    "odds",
                    "book_margin_pct",
                ]
            )

    def load_reference_odds(self, data_dir: Path) -> pd.DataFrame:
        """
        Загружает эталонные «честные» курсы (без маржи букмекера).

        Эталонные курсы — это модельные вероятности, конвертированные в курсы,
        или усреднённые курсы с нескольких бирж/букмекеров с учётом маржи.
        Ожидаемый формат: reference_odds.parquet или reference_odds.csv.
        Обязательные колонки: event_id, selection, fair_odds.

        Параметры
        ----------
        data_dir : Path
            Директория с файлами данных.

        Возвращает
        ----------
        pd.DataFrame
            DataFrame с эталонными курсами.
        """
        parquet_path = data_dir / "reference_odds.parquet"
        csv_path = data_dir / "reference_odds.csv"

        if parquet_path.exists():
            return pd.read_parquet(parquet_path)
        elif csv_path.exists():
            return pd.read_csv(csv_path)
        else:
            return pd.DataFrame(columns=["event_id", "selection", "fair_odds"])

    def compute_edge(self, target_odds: float, reference_fair_odds: float) -> float:
        """
        Вычисляет преимущество (edge) ставки в процентах.

        Формула: (target_odds / reference_fair_odds - 1) * 100

        Положительный edge означает, что букмекерский курс выше «честного»
        (недооценённый исход — потенциальная ставка с преимуществом).
        Отрицательный edge означает переоценённый исход (не ставить).

        Параметры
        ----------
        target_odds : float
            Текущий курс букмекера.
        reference_fair_odds : float
            Эталонный «честный» курс.

        Возвращает
        ----------
        float
            Edge в процентах.
        """
        if reference_fair_odds <= 0:
            raise ValueError(
                f"reference_fair_odds должен быть > 0, получено: {reference_fair_odds}"
            )
        return (target_odds / reference_fair_odds - 1) * 100

    def apply_risk_filters(
        self,
        candidate: dict[str, Any],
        illness_features: dict[str, Any] | None = None,
    ) -> tuple[bool, list[str]]:
        """
        Применяет фильтры рисков к кандидату на генерацию сигнала.

        Проверяемые условия:
            1. edge_pct >= min_edge_pct
            2. book_margin_pct <= max_book_margin_pct
            3. Если no_bet_if_lineup_missing=True — наличие состава
            4. Качество источника данных >= min_source_quality_score

        Параметры
        ----------
        candidate : dict
            Словарь с данными кандидата. Ожидаемые ключи:
            edge_pct, book_margin_pct, lineup_available (опц.), source_quality (опц.).
        illness_features : dict | None, optional
            Признаки травм/отсутствия игроков из IllnessFeatureBuilder.
            Используется для проверки качества источника.

        Возвращает
        ----------
        tuple[bool, list[str]]
            (passed, risk_flags): True если сигнал прошёл все фильтры,
            список строк с описанием нарушенных условий.
        """
        risk_flags: list[str] = []

        # Проверка минимального edge
        edge_pct = candidate.get("edge_pct", 0.0)
        if edge_pct < self.risk_filter.min_edge_pct:
            risk_flags.append(
                f"edge_below_threshold: {edge_pct:.2f}% < {self.risk_filter.min_edge_pct}%"
            )

        # Проверка маржи букмекера
        book_margin = candidate.get("book_margin_pct", 0.0)
        if book_margin > self.risk_filter.max_book_margin_pct:
            risk_flags.append(
                f"margin_too_high: {book_margin:.2f}% > {self.risk_filter.max_book_margin_pct}%"
            )

        # Проверка наличия состава
        if self.risk_filter.no_bet_if_lineup_missing:
            lineup_available = candidate.get("lineup_available", True)
            if not lineup_available:
                risk_flags.append("lineup_missing: состав команды не известен")

        # Проверка качества источника данных (через illness_features)
        if illness_features is not None:
            min_quality = illness_features.get("min_source_quality", 1.0)
            if min_quality < self.risk_filter.min_source_quality_score:
                risk_flags.append(
                    f"low_source_quality: {min_quality:.2f} < "
                    f"{self.risk_filter.min_source_quality_score}"
                )

        # Общая проверка качества данных
        data_quality = candidate.get("data_quality_score", 1.0)
        if data_quality < self.risk_filter.no_bet_if_data_quality_below:
            risk_flags.append(
                f"data_quality_below_threshold: {data_quality:.2f} < "
                f"{self.risk_filter.no_bet_if_data_quality_below}"
            )

        passed = len(risk_flags) == 0
        return passed, risk_flags

    def generate_signal(
        self,
        event_id: str,
        bookmaker: str,
        market_key: str,
        selection: str,
        entry_odds: float,
        reference_fair_odds: float,
        illness_features: dict[str, Any] | None = None,
        extra_explain: list[str] | None = None,
    ) -> SignalRecord | None:
        """
        Генерирует торговый сигнал, если условия стратегии выполнены.

        Шаги:
            1. Вычисляет edge.
            2. Применяет фильтры рисков.
            3. Определяет уровень уверенности (confidence).
            4. Формирует SignalRecord.

        Параметры
        ----------
        event_id : str
            Нормализованный идентификатор события.
        bookmaker : str
            Название букмекера.
        market_key : str
            Ключ рынка.
        selection : str
            Название исхода.
        entry_odds : float
            Текущий курс букмекера.
        reference_fair_odds : float
            Эталонный «честный» курс.
        illness_features : dict | None, optional
            Признаки травм для оценки риска.
        extra_explain : list[str] | None, optional
            Дополнительные обоснования для поля explain.

        Возвращает
        ----------
        SignalRecord | None
            Объект сигнала или None, если сигнал не прошёл фильтрацию.
        """
        edge_pct = self.compute_edge(entry_odds, reference_fair_odds)

        candidate = {
            "edge_pct": edge_pct,
            "book_margin_pct": 0.0,  # рассчитывается при наличии данных
            "lineup_available": True,
            "data_quality_score": 1.0,
        }

        passed, risk_flags = self.apply_risk_filters(candidate, illness_features)
        if not passed:
            return None

        # Определение уровня уверенности по величине edge
        if edge_pct >= 5.0:
            confidence: Literal["high", "medium", "low"] = "high"
        elif edge_pct >= 3.0:
            confidence = "medium"
        else:
            confidence = "low"

        # Ожидаемое направление CLV
        clv_proxy: Literal["positive", "neutral", "negative", "unknown"] = (
            "positive" if edge_pct >= self.risk_filter.min_edge_pct else "neutral"
        )

        # Формируем объяснение сигнала
        explain: list[str] = [
            f"edge={edge_pct:.2f}%",
            f"entry_odds={entry_odds}",
            f"reference_fair_odds={reference_fair_odds}",
        ]
        if illness_features:
            absent = illness_features.get("number_of_absent_starters", 0)
            if absent > 0:
                explain.append(f"absent_starters={absent}")
        if extra_explain:
            explain.extend(extra_explain)

        self._signal_counter += 1

        return SignalRecord(
            signal_id=_make_signal_id(self._signal_counter),
            strategy_id=self.strategy_id,
            normalized_event_id=event_id,
            bookmaker=bookmaker,
            market_key=market_key,
            selection=selection,
            entry_odds=entry_odds,
            reference_fair_odds=reference_fair_odds,
            edge_pct=round(edge_pct, 4),
            clv_proxy_expected=clv_proxy,
            status="paper",
            confidence=confidence,
            explain=explain,
            timestamp_utc=datetime.now(tz=timezone.utc),
            risk_flags=risk_flags,
        )

    def scan(self, data_dir: Path, output_dir: Path) -> list[SignalRecord]:
        """
        Главная точка входа: запускает ежедневное сканирование сигналов.

        Алгоритм:
            1. Загружает текущие котировки и эталонные курсы.
            2. Объединяет данные по event_id + selection.
            3. Для каждого совпадения генерирует сигнал.
            4. Сохраняет все прошедшие фильтрацию сигналы в JSON.

        Параметры
        ----------
        data_dir : Path
            Директория с входными данными (current_odds, reference_odds).
        output_dir : Path
            Директория для сохранения сигналов.

        Возвращает
        ----------
        list[SignalRecord]
            Список сгенерированных сигналов.
        """
        # Сбрасываем счётчик сигналов для нового дня
        self._signal_counter = 0

        current_odds = self.load_current_odds(data_dir)
        reference_odds = self.load_reference_odds(data_dir)

        if current_odds.empty or reference_odds.empty:
            return []

        # Объединяем котировки по event_id и selection
        merged = current_odds.merge(
            reference_odds,
            on=["event_id", "selection"],
            how="inner",
        )

        signals: list[SignalRecord] = []

        for _, row in merged.iterrows():
            entry_odds = float(row.get("odds", 0))
            fair_odds = float(row.get("fair_odds", 0))

            if entry_odds <= 1.0 or fair_odds <= 1.0:
                continue

            signal = self.generate_signal(
                event_id=str(row.get("event_id", "")),
                bookmaker=str(row.get("bookmaker", "")),
                market_key=str(row.get("market_key", "")),
                selection=str(row.get("selection", "")),
                entry_odds=entry_odds,
                reference_fair_odds=fair_odds,
            )

            if signal is not None:
                signals.append(signal)

        # Сохраняем результаты
        if signals:
            self.save_signals(signals, output_dir)

        return signals

    def scan_active_sports(
        self,
        resolver: ActiveSportsResolver,
        output_dir: Path,
    ) -> ActiveSportsReport:
        """
        Resolves sports with available events before the signal scan.

        This method is intentionally separate from ``scan`` so the existing
        local CSV/parquet workflow keeps working. Online jobs can call it first,
        then fetch/normalize odds for the returned sport keys.
        """
        report = resolver.resolve()
        resolver.write_report(output_dir, report)
        if not report.active_sports:
            resolver.write_no_data_report(output_dir, report)
        return report

    def save_signals(self, signals: list[SignalRecord], output_dir: Path) -> Path:
        """
        Сохраняет список сигналов в JSON-файл.

        Имя файла: signals_{YYYYMMDD}.json (по дате UTC).
        Если файл за эту дату уже существует — данные дозаписываются.

        Параметры
        ----------
        signals : list[SignalRecord]
            Список сигналов для сохранения.
        output_dir : Path
            Директория для сохранения.

        Возвращает
        ----------
        Path
            Путь к сохранённому файлу.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(tz=timezone.utc).strftime("%Y%m%d")
        output_path = output_dir / f"signals_{date_str}.json"

        # Загружаем существующие сигналы за день (дозапись)
        existing: list[dict] = []
        if output_path.exists():
            existing = json.loads(output_path.read_text(encoding="utf-8"))

        new_records = [s.model_dump(mode="json") for s in signals]
        all_records = existing + new_records

        output_path.write_text(
            json.dumps(all_records, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return output_path


# ---------------------------------------------------------------------------
# Бумажный журнал ставок
# ---------------------------------------------------------------------------


class PaperLedger:
    """
    Журнал бумажных ставок для отслеживания P&L без реальных транзакций.

    Назначение:
        Сохраняет историю всех сигналов, поставленных «на бумаге»,
        и позволяет обновлять результаты по мере завершения матчей.
        Используется для оценки реальной работоспособности стратегии
        до перехода (если это когда-либо произойдёт) к реальной торговле.
    """

    def __init__(self) -> None:
        """Инициализирует пустой журнал."""
        # Структура: {signal_id: {signal_data, stake_pct, outcome, profit_loss}}
        self._entries: dict[str, dict[str, Any]] = {}

    def add_entry(self, signal: SignalRecord, stake_pct: float = 1.0) -> None:
        """
        Добавляет новую запись (бумажную ставку) в журнал.

        Параметры
        ----------
        signal : SignalRecord
            Сигнал, по которому фиксируется ставка.
        stake_pct : float, optional
            Размер ставки в % от банка (по умолчанию 1.0%).
        """
        if signal.signal_id in self._entries:
            return  # Дублирование не допускается

        self._entries[signal.signal_id] = {
            "signal_id": signal.signal_id,
            "strategy_id": signal.strategy_id,
            "normalized_event_id": signal.normalized_event_id,
            "bookmaker": signal.bookmaker,
            "market_key": signal.market_key,
            "selection": signal.selection,
            "entry_odds": signal.entry_odds,
            "reference_fair_odds": signal.reference_fair_odds,
            "edge_pct": signal.edge_pct,
            "confidence": signal.confidence,
            "stake_pct": stake_pct,
            "timestamp_utc": signal.timestamp_utc.isoformat(),
            "outcome": None,  # заполняется при update_result
            "profit_loss": None,  # заполняется при update_result
            "status": "open",  # open / settled / void
        }

    def update_result(self, signal_id: str, outcome: float) -> None:
        """
        Записывает результат ставки (победа, проигрыш или возврат).

        Параметры
        ----------
        signal_id : str
            Идентификатор сигнала из PaperLedger.
        outcome : float
            Результат: 1.0=победа, 0.0=проигрыш, -1.0=аннулировано (void).
        """
        if signal_id not in self._entries:
            raise KeyError(f"Сигнал {signal_id} не найден в журнале")

        entry = self._entries[signal_id]
        entry["outcome"] = outcome

        # Вычисляем P&L на единицу ставки
        if outcome == 1.0:
            entry["profit_loss"] = (entry["entry_odds"] - 1) * entry["stake_pct"]
        elif outcome == 0.0:
            entry["profit_loss"] = -entry["stake_pct"]
        else:
            # Аннулированная ставка (void)
            entry["profit_loss"] = 0.0

        entry["status"] = "settled" if outcome >= 0 else "void"

    def get_summary(self) -> dict[str, Any]:
        """
        Возвращает сводку по P&L всех бумажных ставок.

        Метрики:
            - total_bets: общее количество записей
            - settled_bets: количество завершённых ставок
            - open_bets: количество незакрытых ставок
            - total_pnl: суммарный P&L по завершённым ставкам
            - win_rate: доля выигрышных ставок
            - roi_pct: ROI (P&L / turnover * 100)
            - mean_edge_pct: средний edge по всем ставкам

        Возвращает
        ----------
        dict
            Сводная статистика журнала.
        """
        entries = list(self._entries.values())
        settled = [e for e in entries if e["status"] == "settled"]
        wins = [e for e in settled if e.get("outcome") == 1.0]

        total_pnl = sum(e["profit_loss"] for e in settled if e["profit_loss"] is not None)
        turnover = sum(e["stake_pct"] for e in settled)
        roi_pct = (total_pnl / turnover * 100) if turnover > 0 else 0.0

        mean_edge = sum(e["edge_pct"] for e in entries) / len(entries) if entries else 0.0

        return {
            "total_bets": len(entries),
            "settled_bets": len(settled),
            "open_bets": len([e for e in entries if e["status"] == "open"]),
            "total_pnl": round(total_pnl, 4),
            "win_rate": round(len(wins) / len(settled), 4) if settled else 0.0,
            "roi_pct": round(roi_pct, 4),
            "mean_edge_pct": round(mean_edge, 4),
            "turnover": round(turnover, 4),
        }

    def save(self, path: Path) -> None:
        """
        Сохраняет журнал на диск в формате JSON.

        Параметры
        ----------
        path : Path
            Путь к файлу JSON для сохранения журнала.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "1.0",
            "saved_at_utc": datetime.now(tz=timezone.utc).isoformat(),
            "entries": self._entries,
            "summary": self.get_summary(),
        }
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> PaperLedger:
        """
        Загружает журнал из JSON-файла.

        Параметры
        ----------
        path : Path
            Путь к ранее сохранённому файлу журнала.

        Возвращает
        ----------
        PaperLedger
            Восстановленный объект журнала.

        Исключения
        ----------
        FileNotFoundError
            Если файл не существует.
        """
        if not path.exists():
            raise FileNotFoundError(f"Файл журнала не найден: {path}")

        raw = json.loads(path.read_text(encoding="utf-8"))
        ledger = cls()
        ledger._entries = raw.get("entries", {})
        return ledger
