"""
Модуль запуска бэктеста по методу walk-forward (скользящее окно).

Принцип работы:
    Данные разбиваются на последовательные фолды «обучение / тест».
    Модель обучается на историческом окне и оценивается на следующем
    временном отрезке, никогда не заглядывая вперёд (строгий OOS-тест).

ВАЖНО:
    Модуль предназначен исключительно для бумажной торговли (paper_trading_only=True).
    Реальные финансовые транзакции не выполняются ни при каких условиях.
"""

from __future__ import annotations

import hashlib
import json
import random
import uuid
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Конфигурация бэктеста
# ---------------------------------------------------------------------------


class BacktestConfig(BaseModel):
    """Параметры запуска walk-forward бэктеста."""

    strategy_id: str = Field(..., description="Уникальный идентификатор стратегии")
    sport: str = Field(..., description="Вид спорта (например, 'football', 'tennis')")
    league: str = Field(..., description="Лига/турнир (например, 'EPL', 'La_Liga')")
    market_key: str = Field(..., description="Ключ рынка (например, 'h2h', 'totals')")
    start_date: date = Field(..., description="Начальная дата диапазона бэктеста (включительно)")
    end_date: date = Field(..., description="Конечная дата диапазона бэктеста (включительно)")
    fold_size_days: int = Field(
        30,
        gt=0,
        description="Размер тестового окна в днях для каждого фолда (например, 30 дней)",
    )
    min_edge_pct: float = Field(
        2.0,
        description="Минимальный порог преимущества (edge) в % для входа в ставку",
    )
    max_margin_pct: float = Field(
        5.0,
        description="Максимально допустимая маржа букмекера в % (фильтр ликвидности)",
    )
    paper_trading_only: bool = Field(
        True,
        description="ВСЕГДА True: модуль работает исключительно в режиме бумажной торговли",
    )

    @field_validator("paper_trading_only")
    @classmethod
    def must_be_paper(cls, v: bool) -> bool:
        """Гарантирует, что бэктест всегда выполняется в бумажном режиме."""
        if not v:
            raise ValueError(
                "paper_trading_only должен быть True. "
                "Реальная торговля через этот модуль запрещена."
            )
        return v

    @field_validator("end_date")
    @classmethod
    def end_after_start(cls, v: date, info) -> date:
        """Проверяет, что end_date > start_date."""
        start = info.data.get("start_date")
        if start and v <= start:
            raise ValueError(f"end_date ({v}) должна быть позже start_date ({start})")
        return v


# ---------------------------------------------------------------------------
# Модель сделки бэктеста
# ---------------------------------------------------------------------------


class BacktestTrade(BaseModel):
    """Запись об одной смоделированной ставке в рамках бэктеста."""

    trade_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Уникальный идентификатор сделки (UUID)",
    )
    strategy_id: str = Field(..., description="Ссылка на стратегию")
    event_id: str = Field(..., description="Идентификатор спортивного события")
    bookmaker: str = Field(..., description="Название букмекера")
    market_key: str = Field(..., description="Ключ рынка (например, 'h2h', 'totals')")
    selection: str = Field(..., description="Название исхода (например, 'home', 'over')")
    entry_odds: float = Field(..., gt=1.0, description="Курс входа (десятичный)")
    reference_fair_odds: float = Field(
        ..., gt=1.0, description="Справочный «честный» курс без маржи букмекера"
    )
    edge_pct: float = Field(
        ..., description="Преимущество в %: (entry_odds / reference_fair_odds - 1) * 100"
    )
    result: float | None = Field(
        None,
        description="Результат ставки: 1.0=победа, 0.0=проигрыш, None=аннулирована (void)",
    )
    profit_loss: float = Field(
        0.0,
        description="P&L на единицу ставки (например, +1.1 при выигрыше по курсу 2.1)",
    )
    stake: float = Field(1.0, gt=0.0, description="Размер ставки (в условных единицах)")
    bet_date: date = Field(..., description="Дата размещения ставки")
    match_date: date = Field(..., description="Дата проведения матча")
    dataset_version: str = Field("", description="Версия набора данных для воспроизводимости")
    dataset_hash: str = Field(
        "", description="SHA256-хеш данных, использованных для генерации сигнала"
    )


# ---------------------------------------------------------------------------
# Метрики бэктеста
# ---------------------------------------------------------------------------


@dataclass
class BacktestMetrics:
    """Сводные метрики качества стратегии по итогам бэктеста."""

    roi: float = 0.0
    """ROI = суммарный P&L / суммарный объём ставок * 100%"""

    yield_pct: float = 0.0
    """Yield = суммарный P&L / количество ставок * 100%"""

    clv_mean: float = 0.0
    """Среднее значение CLV по всем ставкам (ключевой показатель качества)"""

    max_drawdown: float = 0.0
    """Максимальная просадка от пика кривой капитала"""

    turnover: float = 0.0
    """Суммарный объём поставленных средств"""

    win_rate: float = 0.0
    """Доля выигрышных ставок (не считая аннулированных)"""

    n_bets: int = 0
    """Общее количество ставок"""

    p_value: float = 1.0
    """p-значение bootstrap-теста (H0: ROI <= 0)"""

    is_significant: bool = False
    """True, если p_value < 0.05 (статистически значимый результат)"""

    oos_roi: float = 0.0
    """Out-of-sample ROI: среднее ROI по всем тестовым фолдам"""


# ---------------------------------------------------------------------------
# Walk-forward бэктестер
# ---------------------------------------------------------------------------


class WalkForwardBacktester:
    """
    Реализует walk-forward бэктест для беттинговых стратегий.

    Метод walk-forward разбивает временной ряд на последовательные фолды:
        [train_1 | test_1] -> [train_2 | test_2] -> ...
    Стратегия применяется только к тестовому окну каждого фолда,
    что обеспечивает строгую out-of-sample оценку.

    Параметры
    ----------
    config : BacktestConfig
        Параметры бэктеста.
    odds_df : pd.DataFrame
        DataFrame с историческими котировками. Обязательные колонки:
        ['event_id', 'bookmaker', 'market_key', 'selection',
         'odds', 'match_date', 'closing_odds'].
    results_df : pd.DataFrame
        DataFrame с результатами матчей. Обязательные колонки:
        ['event_id', 'selection', 'result'].
    """

    # Колонки, обязательные для DataFrame котировок
    REQUIRED_ODDS_COLS: frozenset[str] = frozenset(
        {"event_id", "bookmaker", "market_key", "selection", "odds", "match_date"}
    )

    # Колонки, обязательные для DataFrame результатов
    REQUIRED_RESULTS_COLS: frozenset[str] = frozenset({"event_id", "selection", "result"})

    def __init__(
        self,
        config: BacktestConfig,
        odds_df: pd.DataFrame,
        results_df: pd.DataFrame,
    ) -> None:
        """
        Инициализация бэктестера с проверкой входных данных.

        Параметры
        ----------
        config : BacktestConfig
            Конфигурация бэктеста.
        odds_df : pd.DataFrame
            Исторические котировки.
        results_df : pd.DataFrame
            Результаты матчей.
        """
        self.config = config
        self.odds_df = odds_df.copy()
        self.results_df = results_df.copy()

        # Проверка наличия обязательных колонок
        self._validate_dataframes()

        # Нормализация типов дат
        self.odds_df["match_date"] = pd.to_datetime(self.odds_df["match_date"]).dt.date

        # Хеш данных для воспроизводимости
        self._odds_hash = self.compute_dataset_hash(self.odds_df)
        self._results_hash = self.compute_dataset_hash(self.results_df)

    def _validate_dataframes(self) -> None:
        """Проверяет наличие обязательных колонок во входных DataFrame."""
        missing_odds = self.REQUIRED_ODDS_COLS - set(self.odds_df.columns)
        if missing_odds:
            raise KeyError(f"odds_df: отсутствуют колонки {missing_odds}")

        missing_results = self.REQUIRED_RESULTS_COLS - set(self.results_df.columns)
        if missing_results:
            raise KeyError(f"results_df: отсутствуют колонки {missing_results}")

    def run(self) -> tuple[list[BacktestTrade], BacktestMetrics]:
        """
        Главная точка входа: запускает полный walk-forward бэктест.

        Возвращает
        ----------
        tuple[list[BacktestTrade], BacktestMetrics]
            Список всех сделок и сводные метрики по всем фолдам.
        """
        folds = self._compute_folds()

        all_trades: list[BacktestTrade] = []
        fold_rois: list[float] = []

        for fold_idx, (train_start, train_end, test_start, test_end) in enumerate(folds):
            # Нарезаем данные для текущего фолда
            train_df = self.odds_df[
                (self.odds_df["match_date"] >= train_start)
                & (self.odds_df["match_date"] < test_start)
            ]
            test_df = self.odds_df[
                (self.odds_df["match_date"] >= test_start)
                & (self.odds_df["match_date"] <= test_end)
            ]

            if test_df.empty:
                continue

            # Применяем стратегию к тестовому фолду
            fold_trades = self._run_fold(train_df, test_df)
            all_trades.extend(fold_trades)

            # ROI фолда для расчёта OOS ROI
            if fold_trades:
                fold_pnl = sum(t.profit_loss for t in fold_trades)
                fold_turnover = sum(t.stake for t in fold_trades)
                fold_roi = (fold_pnl / fold_turnover * 100) if fold_turnover > 0 else 0.0
                fold_rois.append(fold_roi)

        # Итоговые метрики
        metrics = self._compute_metrics(all_trades)
        metrics.oos_roi = (sum(fold_rois) / len(fold_rois)) if fold_rois else 0.0

        return all_trades, metrics

    def _compute_folds(self) -> list[tuple[date, date, date, date]]:
        """
        Разбивает временной диапазон на фолды walk-forward.

        Каждый фолд: (train_start, train_end, test_start, test_end).
        Тренировочное окно расширяется с каждым фолдом (expanding window).

        Возвращает
        ----------
        list[tuple[date, date, date, date]]
            Список фолдов в формате (train_start, train_end, test_start, test_end).
        """
        folds = []
        current_test_start = self.config.start_date
        fold_delta = timedelta(days=self.config.fold_size_days)

        while current_test_start < self.config.end_date:
            test_end = min(
                current_test_start + fold_delta - timedelta(days=1),
                self.config.end_date,
            )
            # Тренировочное окно: всё до начала тестового фолда
            train_end = current_test_start - timedelta(days=1)
            folds.append((self.config.start_date, train_end, current_test_start, test_end))
            current_test_start = test_end + timedelta(days=1)

        return folds

    def _run_fold(
        self,
        train_df: pd.DataFrame,
        test_df: pd.DataFrame,
    ) -> list[BacktestTrade]:
        """
        Применяет стратегию к одному тестовому фолду.

        Параметры
        ----------
        train_df : pd.DataFrame
            Тренировочные данные фолда (используются для калибровки).
        test_df : pd.DataFrame
            Тестовые данные фолда — ставки формируются только по ним.

        Возвращает
        ----------
        list[BacktestTrade]
            Список сделок, сгенерированных в тестовом окне.
        """
        trades: list[BacktestTrade] = []

        # Вычисляем «честный» курс как медиану по тренировочным данным
        # (упрощённая эвристика; в реальной системе — модельная вероятность)
        fair_odds_map: dict[tuple[str, str], float] = {}
        if not train_df.empty and "odds" in train_df.columns:
            for (sel,), grp in train_df.groupby(["selection"]):
                fair_odds_map[sel] = float(grp["odds"].median())

        # Присоединяем результаты к тестовому фолду
        test_with_results = test_df.merge(self.results_df, on=["event_id", "selection"], how="left")

        for _, row in test_with_results.iterrows():
            # Получаем справочный курс для исхода
            reference_fair_odds = fair_odds_map.get(row.get("selection", ""), None)
            if reference_fair_odds is None:
                # Если нет обучающих данных — пропускаем
                continue

            trade = self._apply_strategy(row, reference_fair_odds)
            if trade is not None:
                trades.append(trade)

        return trades

    def _apply_strategy(
        self,
        row: pd.Series,
        reference_fair_odds: float,
    ) -> BacktestTrade | None:
        """
        Применяет правила стратегии к одной строке данных.

        Логика:
            1. Вычисляет edge = (odds / reference_fair_odds - 1) * 100.
            2. Если edge >= min_edge_pct — формирует ставку.
            3. Рассчитывает P&L на основе результата матча.

        Параметры
        ----------
        row : pd.Series
            Строка тестового DataFrame (одна котировка с результатом).
        reference_fair_odds : float
            «Честный» курс для данного исхода.

        Возвращает
        ----------
        BacktestTrade | None
            Объект сделки или None, если условия стратегии не выполнены.
        """
        entry_odds = float(row.get("odds", 0))
        if entry_odds <= 1.0 or reference_fair_odds <= 1.0:
            return None

        # Вычисляем преимущество
        edge_pct = (entry_odds / reference_fair_odds - 1) * 100

        # Фильтр по минимальному edge
        if edge_pct < self.config.min_edge_pct:
            return None

        # Результат ставки
        raw_result = row.get("result", None)
        result: float | None = None
        profit_loss: float = 0.0
        stake = 1.0

        if raw_result is not None and not pd.isna(raw_result):
            result = float(raw_result)
            if result == 1.0:
                profit_loss = (entry_odds - 1) * stake
            elif result == 0.0:
                profit_loss = -stake
            # Аннулированная ставка (void): profit_loss = 0.0

        match_date_val = row.get("match_date", date.today())
        if isinstance(match_date_val, str):
            match_date_val = date.fromisoformat(match_date_val)
        elif isinstance(match_date_val, datetime):
            match_date_val = match_date_val.date()

        return BacktestTrade(
            strategy_id=self.config.strategy_id,
            event_id=str(row.get("event_id", "")),
            bookmaker=str(row.get("bookmaker", "")),
            market_key=self.config.market_key,
            selection=str(row.get("selection", "")),
            entry_odds=entry_odds,
            reference_fair_odds=reference_fair_odds,
            edge_pct=round(edge_pct, 4),
            result=result,
            profit_loss=round(profit_loss, 6),
            stake=stake,
            bet_date=match_date_val,
            match_date=match_date_val,
            dataset_version="1.0",
            dataset_hash=self._odds_hash[:16],
        )

    def _compute_metrics(self, trades: list[BacktestTrade]) -> BacktestMetrics:
        """
        Вычисляет сводные метрики по всем сделкам бэктеста.

        Параметры
        ----------
        trades : list[BacktestTrade]
            Полный список сделок.

        Возвращает
        ----------
        BacktestMetrics
            Заполненный объект метрик.
        """
        if not trades:
            return BacktestMetrics()

        # Учитываем только завершённые ставки (не void)
        settled = [t for t in trades if t.result is not None]
        n_bets = len(settled)

        if n_bets == 0:
            return BacktestMetrics(n_bets=0)

        total_pnl = sum(t.profit_loss for t in settled)
        total_turnover = sum(t.stake for t in settled)
        wins = sum(1 for t in settled if t.result == 1.0)

        roi = (total_pnl / total_turnover * 100) if total_turnover > 0 else 0.0
        yield_pct = (total_pnl / n_bets * 100) if n_bets > 0 else 0.0
        win_rate = wins / n_bets if n_bets > 0 else 0.0

        # Кривая капитала для расчёта просадки
        equity_curve: list[float] = []
        cumulative = 0.0
        for t in settled:
            cumulative += t.profit_loss
            equity_curve.append(cumulative)

        max_drawdown = self._compute_max_drawdown(equity_curve)
        clv_mean = self._compute_clv(trades)
        p_value = self._bootstrap_pvalue(settled)

        return BacktestMetrics(
            roi=round(roi, 4),
            yield_pct=round(yield_pct, 4),
            clv_mean=round(clv_mean, 4),
            max_drawdown=round(max_drawdown, 4),
            turnover=round(total_turnover, 4),
            win_rate=round(win_rate, 4),
            n_bets=n_bets,
            p_value=round(p_value, 4),
            is_significant=p_value < 0.05,
        )

    def _compute_max_drawdown(self, equity_curve: list[float]) -> float:
        """
        Вычисляет максимальную просадку по кривой капитала.

        Максимальная просадка = максимальное падение от пика до впадины
        в абсолютных единицах.

        Параметры
        ----------
        equity_curve : list[float]
            Последовательность значений накопленного P&L.

        Возвращает
        ----------
        float
            Максимальная просадка (неотрицательное число).
        """
        if not equity_curve:
            return 0.0

        max_drawdown = 0.0
        peak = equity_curve[0]

        for value in equity_curve:
            if value > peak:
                peak = value
            drawdown = peak - value
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        return max_drawdown

    def _bootstrap_pvalue(
        self,
        trades: list[BacktestTrade],
        n_bootstrap: int = 1000,
    ) -> float:
        """
        Вычисляет p-значение через bootstrap-тест (H0: ROI <= 0).

        Метод: многократно перемешиваем результаты ставок (permutation test),
        вычисляем долю симуляций, в которых случайный ROI >= наблюдаемого.

        Параметры
        ----------
        trades : list[BacktestTrade]
            Список завершённых ставок.
        n_bootstrap : int, optional
            Количество bootstrap-итераций (по умолчанию 1000).

        Возвращает
        ----------
        float
            p-значение. Значение < 0.05 означает статистически значимый результат.
        """
        if not trades:
            return 1.0

        observed_pnl = sum(t.profit_loss for t in trades)
        total_turnover = sum(t.stake for t in trades)
        if total_turnover == 0:
            return 1.0

        observed_roi = observed_pnl / total_turnover

        # Перемешиваем P&L для bootstrap
        pnl_values = [t.profit_loss for t in trades]
        count_exceeding = 0

        for _ in range(n_bootstrap):
            # Случайная выборка с возвратом
            bootstrap_sample = random.choices(pnl_values, k=len(pnl_values))
            bootstrap_roi = sum(bootstrap_sample) / total_turnover
            if bootstrap_roi >= observed_roi:
                count_exceeding += 1

        return count_exceeding / n_bootstrap

    def _compute_clv(self, trades: list[BacktestTrade]) -> float:
        """
        Вычисляет среднее значение CLV по всем ставкам.

        CLV (Closing Line Value) сравнивает курс входа с курсом закрытия.
        В рамках бэктеста используется приближение: если closing_odds
        доступны в данных, используем их; иначе — возвращаем 0.0.

        Параметры
        ----------
        trades : list[BacktestTrade]
            Список сделок бэктеста.

        Возвращает
        ----------
        float
            Среднее значение CLV в процентах.
        """
        # Получаем курсы закрытия из исходного DataFrame
        if "closing_odds" not in self.odds_df.columns:
            return 0.0

        clv_values: list[float] = []
        closing_map = (
            self.odds_df.set_index(["event_id", "selection"])["closing_odds"].to_dict()
            if "event_id" in self.odds_df.columns
            else {}
        )

        for trade in trades:
            key = (trade.event_id, trade.selection)
            closing = closing_map.get(key)
            if closing and closing > 1.0:
                clv = (trade.entry_odds / closing - 1) * 100
                clv_values.append(clv)

        return (sum(clv_values) / len(clv_values)) if clv_values else 0.0

    def save_results(
        self,
        trades: list[BacktestTrade],
        metrics: BacktestMetrics,
        output_dir: Path,
    ) -> None:
        """
        Сохраняет результаты бэктеста: сделки в формате Parquet, метрики в JSON.

        Структура выходных файлов:
            output_dir/
                trades_{strategy_id}.parquet  — все сделки
                metrics_{strategy_id}.json    — сводные метрики

        Параметры
        ----------
        trades : list[BacktestTrade]
            Список сделок бэктеста.
        metrics : BacktestMetrics
            Сводные метрики.
        output_dir : Path
            Директория для сохранения результатов.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        strategy_id = self.config.strategy_id

        # Сохраняем сделки в Parquet
        if trades:
            trades_records = [t.model_dump() for t in trades]
            trades_df = pd.DataFrame(trades_records)
            parquet_path = output_dir / f"trades_{strategy_id}.parquet"
            trades_df.to_parquet(parquet_path, index=False)

        # Сохраняем метрики в JSON
        metrics_dict = asdict(metrics)
        metrics_dict["strategy_id"] = strategy_id
        metrics_dict["config"] = self.config.model_dump(mode="json")
        metrics_dict["odds_hash"] = self._odds_hash
        metrics_dict["results_hash"] = self._results_hash
        metrics_dict["generated_at_utc"] = datetime.now(tz=timezone.utc).isoformat()

        metrics_path = output_dir / f"metrics_{strategy_id}.json"
        metrics_path.write_text(
            json.dumps(metrics_dict, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    def compute_dataset_hash(self, df: pd.DataFrame) -> str:
        """
        Вычисляет SHA256-хеш содержимого DataFrame для воспроизводимости.

        Хеш строится по сериализованному CSV-представлению DataFrame,
        отсортированному по всем колонкам для детерминизма.

        Параметры
        ----------
        df : pd.DataFrame
            DataFrame, для которого вычисляется хеш.

        Возвращает
        ----------
        str
            Шестнадцатеричная строка SHA256 (64 символа).
        """
        if df.empty:
            return hashlib.sha256(b"empty").hexdigest()

        # Сортируем для детерминизма (порядок строк не важен)
        sorted_df = df.sort_values(by=list(df.columns)).reset_index(drop=True)
        content = sorted_df.to_csv(index=False).encode("utf-8")
        return hashlib.sha256(content).hexdigest()
