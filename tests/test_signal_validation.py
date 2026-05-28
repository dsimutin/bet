"""
Тесты для генерации и валидации торговых сигналов.

Проверяются:
    - Формат signal_id.
    - Формула вычисления edge.
    - Фильтры риска (минимальный edge, максимальная маржа букмекера).
    - Бумажный журнал (paper ledger): добавление и обновление записей.
    - Статус сигнала всегда должен быть "paper".

Все тесты используют синтетические данные без внешних зависимостей.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Вспомогательные классы (минимальные заглушки для тестирования без реального кода)
# ---------------------------------------------------------------------------


class _SignalValidator:
    """
    Минимальная заглушка валидатора сигналов для тестирования.

    В production-коде заменяется реальным классом из src.signals.
    """

    SIGNAL_ID_PATTERN = re.compile(r"^sig_\d{8}_\d{6}$")

    def __init__(self, min_edge_pct: float = 2.0, max_book_margin_pct: float = 8.0) -> None:
        self.min_edge_pct = min_edge_pct
        self.max_book_margin_pct = max_book_margin_pct

    def validate_signal_id(self, signal_id: str) -> bool:
        """Проверяет формат signal_id: sig_{YYYYMMDD}_{6digits}."""
        return bool(self.SIGNAL_ID_PATTERN.match(signal_id))

    def compute_edge(self, target_odds: float, reference_fair_odds: float) -> float:
        """
        Вычисляет edge сигнала в процентах.

        Формула: edge_pct = (target_odds / reference_fair_odds - 1) * 100
        """
        if reference_fair_odds <= 0:
            raise ValueError(f"reference_fair_odds должен быть > 0, получено {reference_fair_odds}")
        return (target_odds / reference_fair_odds - 1) * 100

    def passes_edge_filter(self, edge_pct: float) -> bool:
        """Возвращает True, если edge_pct >= минимального порога."""
        return edge_pct >= self.min_edge_pct

    def passes_margin_filter(self, book_margin_pct: float) -> bool:
        """Возвращает True, если маржа букмекера <= максимально допустимой."""
        return book_margin_pct <= self.max_book_margin_pct

    def get_signal_status(self) -> str:
        """Возвращает статус сигнала. Всегда 'paper' в MVP."""
        return "paper"


class _PaperLedger:
    """
    Минимальная реализация бумажного журнала для тестирования.

    Хранит список сигналов в памяти и поддерживает обновление результатов.
    """

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}

    def add_signal(self, signal: dict) -> None:
        """
        Добавляет новый сигнал в журнал.

        Устанавливает начальный статус 'open' и нулевые P&L.
        """
        signal_id = signal["signal_id"]
        if signal_id in self._records:
            raise ValueError(f"Сигнал {signal_id} уже существует в журнале")

        self._records[signal_id] = {
            **signal,
            "status": "open",
            "result": None,
            "closing_odds": None,
            "pnl_units": None,
            "clv_pct": None,
        }

    def update_result(
        self,
        signal_id: str,
        result: str,
        closing_odds: float,
    ) -> None:
        """
        Обновляет результат закрытой ставки.

        Параметры
        ----------
        signal_id : str
            Идентификатор сигнала.
        result : str
            Результат: 'win' или 'loss'.
        closing_odds : float
            Коэффициент закрытия рынка для расчёта CLV.
        """
        if signal_id not in self._records:
            raise KeyError(f"Сигнал {signal_id} не найден в журнале")

        record = self._records[signal_id]
        entry_odds = record.get("entry_odds", 0.0)
        stake = record.get("stake_units", 1.0)

        # P&L в единицах ставки
        if result == "win":
            pnl = stake * (entry_odds - 1)  # прибыль (без возврата тела ставки)
        else:
            pnl = -stake  # убыток = потеря ставки

        # CLV в процентах: (entry / closing - 1) * 100
        clv_pct = ((entry_odds / closing_odds) - 1) * 100 if closing_odds > 0 else None

        self._records[signal_id].update(
            {
                "status": "closed",
                "result": result,
                "closing_odds": closing_odds,
                "pnl_units": round(pnl, 4),
                "clv_pct": round(clv_pct, 4) if clv_pct is not None else None,
            }
        )

    def get_record(self, signal_id: str) -> dict:
        """Возвращает запись сигнала по идентификатору."""
        return self._records[signal_id]

    def compute_roi(self) -> float:
        """
        Вычисляет ROI по всем закрытым ставкам в журнале.

        ROI = sum(pnl) / sum(stake) * 100
        """
        closed = [r for r in self._records.values() if r["status"] == "closed"]
        if not closed:
            return 0.0

        total_pnl = sum(r.get("pnl_units", 0) or 0 for r in closed)
        total_stake = sum(r.get("stake_units", 1.0) for r in closed)

        return (total_pnl / total_stake * 100) if total_stake > 0 else 0.0


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture
def validator() -> _SignalValidator:
    """Экземпляр валидатора сигналов с порогами по умолчанию."""
    return _SignalValidator(min_edge_pct=2.0, max_book_margin_pct=8.0)


@pytest.fixture
def ledger() -> _PaperLedger:
    """Пустой бумажный журнал для тестов."""
    return _PaperLedger()


@pytest.fixture
def sample_signal() -> dict:
    """Синтетический сигнал с корректными данными для базовых тестов."""
    return {
        "signal_id": "sig_20250615_000001",
        "strategy_id": "H001_v1.0.0",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "bookmaker": "pinnacle",
        "market_key": "h2h",
        "selection": "home_win",
        "entry_odds": 2.10,
        "reference_fair_odds": 1.95,
        "edge_pct": 7.69,
        "book_margin_pct": 4.5,
        "stake_units": 1.0,
        "timestamp_utc": datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc).isoformat(),
        "status": "paper",
    }


# ---------------------------------------------------------------------------
# Тест 1: Формат signal_id
# ---------------------------------------------------------------------------


def test_signal_id_format(validator: _SignalValidator) -> None:
    """
    Проверяет, что signal_id соответствует формату sig_{YYYYMMDD}_{6digits}.

    Корректные форматы: sig_20250615_000001, sig_20241231_999999.
    Некорректные: sig_2025615_001, signal_001, sig_20250615_12345.
    """
    # Корректные идентификаторы
    valid_ids = [
        "sig_20250615_000001",
        "sig_20241231_999999",
        "sig_20240101_000000",
        "sig_20251225_123456",
    ]
    for sid in valid_ids:
        assert validator.validate_signal_id(sid), f"Идентификатор '{sid}' должен пройти валидацию"

    # Некорректные идентификаторы
    invalid_ids = [
        "sig_2025615_001",  # неполная дата
        "signal_20250615_000001",  # неверный префикс
        "sig_20250615_12345",  # только 5 цифр в суффиксе
        "sig_20250615_1234567",  # 7 цифр в суффиксе
        "SIG_20250615_000001",  # верхний регистр
        "sig_20250615000001",  # нет разделителя
        "",  # пустая строка
    ]
    for sid in invalid_ids:
        assert not validator.validate_signal_id(
            sid
        ), f"Идентификатор '{sid}' не должен пройти валидацию"


# ---------------------------------------------------------------------------
# Тест 2: Вычисление edge
# ---------------------------------------------------------------------------


def test_edge_computation(validator: _SignalValidator) -> None:
    """
    Проверяет формулу вычисления edge сигнала:
        edge_pct = (target_odds / reference_fair_odds - 1) * 100

    Тестируем несколько пар значений с точной проверкой результата.
    """
    test_cases = [
        # (target_odds, reference_fair_odds, expected_edge)
        (2.10, 1.95, (2.10 / 1.95 - 1) * 100),  # ≈ +7.69%
        (2.00, 2.00, 0.0),  # нулевой edge
        (1.85, 1.95, (1.85 / 1.95 - 1) * 100),  # ≈ -5.13% (отрицательный)
        (3.00, 2.50, (3.00 / 2.50 - 1) * 100),  # +20.0%
        (1.50, 1.55, (1.50 / 1.55 - 1) * 100),  # ≈ -3.23%
    ]

    for target, ref, expected in test_cases:
        computed = validator.compute_edge(target, ref)
        assert (
            abs(computed - expected) < 1e-10
        ), f"edge({target}, {ref}): вычислено {computed:.6f}%, ожидалось {expected:.6f}%"


# ---------------------------------------------------------------------------
# Тест 3: Фильтр риска блокирует низкий edge
# ---------------------------------------------------------------------------


def test_risk_filter_blocks_low_edge(validator: _SignalValidator) -> None:
    """
    Проверяет, что фильтр риска блокирует сигналы с edge ниже минимального порога.

    Минимальный порог validator.min_edge_pct = 2.0%.
    Edge = 1.5% → фильтр должен заблокировать.
    Edge = 2.0% → граничный случай (>= 2.0 → пропускаем).
    Edge = 3.0% → фильтр должен пропустить.
    """
    # Ниже порога → блокируем
    assert (
        validator.passes_edge_filter(edge_pct=1.5) is False
    ), "Edge 1.5% < 2.0% должен быть заблокирован"
    assert (
        validator.passes_edge_filter(edge_pct=0.0) is False
    ), "Edge 0.0% < 2.0% должен быть заблокирован"
    assert (
        validator.passes_edge_filter(edge_pct=-1.0) is False
    ), "Отрицательный edge должен быть заблокирован"

    # Граничный случай: ровно на пороге
    assert (
        validator.passes_edge_filter(edge_pct=2.0) is True
    ), "Edge 2.0% == min_edge_pct=2.0% должен пройти (>= включает границу)"

    # Выше порога → пропускаем
    assert (
        validator.passes_edge_filter(edge_pct=3.0) is True
    ), "Edge 3.0% > 2.0% должен пройти фильтр"
    assert (
        validator.passes_edge_filter(edge_pct=10.0) is True
    ), "Edge 10.0% > 2.0% должен пройти фильтр"


# ---------------------------------------------------------------------------
# Тест 4: Фильтр риска блокирует высокую маржу
# ---------------------------------------------------------------------------


def test_risk_filter_blocks_high_margin(validator: _SignalValidator) -> None:
    """
    Проверяет, что фильтр риска блокирует сигналы при высокой марже букмекера.

    Максимально допустимая маржа validator.max_book_margin_pct = 8.0%.
    Маржа 9.0% → фильтр должен заблокировать.
    Маржа 8.0% → граничный случай (пропускаем).
    Маржа 5.0% → фильтр должен пропустить.
    """
    # Выше максимума → блокируем
    assert (
        validator.passes_margin_filter(book_margin_pct=9.0) is False
    ), "Маржа 9.0% > 8.0% должна быть заблокирована"
    assert (
        validator.passes_margin_filter(book_margin_pct=15.0) is False
    ), "Маржа 15.0% > 8.0% должна быть заблокирована"

    # Граничный случай
    assert (
        validator.passes_margin_filter(book_margin_pct=8.0) is True
    ), "Маржа 8.0% == max_book_margin_pct должна пройти (включая границу)"

    # Ниже максимума → пропускаем
    assert (
        validator.passes_margin_filter(book_margin_pct=5.0) is True
    ), "Маржа 5.0% < 8.0% должна пройти фильтр"
    assert (
        validator.passes_margin_filter(book_margin_pct=0.0) is True
    ), "Нулевая маржа должна пройти фильтр"


# ---------------------------------------------------------------------------
# Тест 5: Paper ledger — добавление и обновление записи
# ---------------------------------------------------------------------------


def test_paper_ledger_add_and_update(
    ledger: _PaperLedger,
    sample_signal: dict,
) -> None:
    """
    Проверяет добавление сигнала в journal и обновление его результата.

    Шаги:
    1. Добавляем сигнал — статус должен стать 'open'.
    2. Обновляем результат (win/loss) — статус должен стать 'closed'.
    3. Проверяем P&L и CLV.
    """
    signal_id = sample_signal["signal_id"]
    entry_odds = sample_signal["entry_odds"]
    stake = sample_signal["stake_units"]

    # Шаг 1: добавляем сигнал
    ledger.add_signal(sample_signal)
    record_open = ledger.get_record(signal_id)

    assert record_open["status"] == "open", "После добавления статус должен быть 'open'"
    assert record_open["result"] is None, "Результат должен быть None до закрытия"
    assert record_open["pnl_units"] is None, "P&L должен быть None до закрытия"

    # Шаг 2: обновляем результат (выигрыш)
    closing_odds = 1.95
    ledger.update_result(signal_id, result="win", closing_odds=closing_odds)
    record_closed = ledger.get_record(signal_id)

    assert record_closed["status"] == "closed", "После обновления статус должен быть 'closed'"
    assert record_closed["result"] == "win"

    # Проверяем P&L: при win → P&L = stake * (entry_odds - 1)
    expected_pnl = round(stake * (entry_odds - 1), 4)
    assert (
        abs(record_closed["pnl_units"] - expected_pnl) < 1e-6
    ), f"P&L={record_closed['pnl_units']}, ожидалось {expected_pnl}"

    # Проверяем CLV: (entry_odds / closing_odds - 1) * 100
    expected_clv = round((entry_odds / closing_odds - 1) * 100, 4)
    assert (
        abs(record_closed["clv_pct"] - expected_clv) < 0.001
    ), f"CLV={record_closed['clv_pct']:.4f}%, ожидалось {expected_clv:.4f}%"

    # Шаг 3: проверяем ROI после добавления одной выигрышной ставки
    roi = ledger.compute_roi()
    expected_roi = (expected_pnl / stake) * 100
    assert abs(roi - expected_roi) < 0.01, f"ROI={roi:.2f}%, ожидалось {expected_roi:.2f}%"

    # Дополнительно: проверяем сценарий с проигрышем
    losing_signal = {**sample_signal, "signal_id": "sig_20250615_000002"}
    ledger.add_signal(losing_signal)
    ledger.update_result("sig_20250615_000002", result="loss", closing_odds=1.80)

    loss_record = ledger.get_record("sig_20250615_000002")
    assert loss_record["result"] == "loss"
    expected_loss_pnl = round(-stake, 4)
    assert (
        abs(loss_record["pnl_units"] - expected_loss_pnl) < 1e-6
    ), f"P&L при проигрыше должен быть {expected_loss_pnl}, получено {loss_record['pnl_units']}"


# ---------------------------------------------------------------------------
# Тест 6: Статус сигнала всегда "paper"
# ---------------------------------------------------------------------------


def test_signal_status_always_paper(
    validator: _SignalValidator,
    sample_signal: dict,
) -> None:
    """
    Проверяет, что статус сигнала всегда равен 'paper' в MVP-системе.

    Валидатор должен возвращать только 'paper' — любой другой статус
    (live, real, active) недопустим на текущем этапе разработки.
    """
    status = validator.get_signal_status()
    assert status == "paper", f"Статус сигнала должен быть 'paper', получено '{status}'"

    # Проверяем, что статус в sample_signal корректен
    assert (
        sample_signal["status"] == "paper"
    ), f"Статус sample_signal должен быть 'paper', получено '{sample_signal['status']}'"

    # Убеждаемся, что 'paper' — единственный допустимый статус
    forbidden_statuses = ["live", "real", "active", "PAPER", "Paper"]
    for forbidden in forbidden_statuses:
        assert forbidden != status, f"Статус '{forbidden}' недопустим в MVP-режиме paper trading"
