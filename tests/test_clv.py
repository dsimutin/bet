"""
Тесты для вычисления CLV (Closing Line Value — ценность относительно линии закрытия).

CLV является ключевой метрикой качества беттинговых решений:
    - Положительный CLV: ставка сделана по курсу выше закрытия → реальное преимущество.
    - Отрицательный CLV: ставка сделана по курсу ниже закрытия → проигрышная стратегия.
    - Нулевой CLV: ставка сделана по курсу закрытия → ставка «по рынку».

Формула: CLV% = ((entry_odds / closing_odds) - 1) * 100

Все тесты используют синтетические данные без внешних зависимостей.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from src.features.clv_features import CLVAnalyzer, CLVRecord


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture
def analyzer() -> CLVAnalyzer:
    """Экземпляр CLVAnalyzer для всех тестов."""
    return CLVAnalyzer()


@pytest.fixture
def fixed_ts() -> datetime:
    """Фиксированная временная метка UTC для создания CLVRecord в тестах."""
    return datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_clv_record(
    entry_odds: float,
    closing_odds: float,
    ts: datetime,
    threshold: float = 0.0,
) -> CLVRecord:
    """
    Создаёт CLVRecord с предвычисленным clv_pct.

    Параметры
    ----------
    entry_odds : float
        Коэффициент входа (> 1.0).
    closing_odds : float
        Коэффициент закрытия (> 1.0).
    ts : datetime
        Базовая временная метка для entry_ts и closing_ts.
    threshold : float
        Пороговое значение для is_positive_clv.
    """
    from datetime import timedelta

    clv_pct = ((entry_odds / closing_odds) - 1) * 100
    return CLVRecord(
        event_id="EVT_TEST",
        selection="home_win",
        entry_odds=entry_odds,
        entry_ts=ts,
        closing_odds=closing_odds,
        closing_ts=ts + timedelta(hours=2),
        clv_pct=clv_pct,
        is_positive_clv=clv_pct > threshold,
    )


# ---------------------------------------------------------------------------
# Тест 1: Положительный CLV при entry_odds > closing_odds
# ---------------------------------------------------------------------------


def test_clv_positive_when_entry_better_than_close(analyzer: CLVAnalyzer) -> None:
    """
    Проверяет, что CLV положительный, когда коэффициент входа выше закрытия.

    Сценарий: вошли по 2.10, рынок закрылся на 1.95.
    Это означает, что на момент входа получили лучший курс, чем итоговая рыночная оценка.
    CLV = (2.10/1.95 - 1) * 100 ≈ +7.69%
    """
    entry_odds = 2.10
    closing_odds = 1.95

    clv = analyzer.compute_clv(entry_odds, closing_odds)

    assert clv > 0.0, (
        f"Ожидался положительный CLV (entry={entry_odds} > closing={closing_odds}), "
        f"получено CLV={clv:.4f}%"
    )

    # Проверяем приближённое значение
    expected_clv = (entry_odds / closing_odds - 1) * 100
    assert abs(clv - expected_clv) < 1e-10, (
        f"CLV={clv:.6f}%, ожидалось {expected_clv:.6f}%"
    )

    # Метод is_positive должен вернуть True
    assert analyzer.is_positive(clv) is True


# ---------------------------------------------------------------------------
# Тест 2: Отрицательный CLV при entry_odds < closing_odds
# ---------------------------------------------------------------------------


def test_clv_negative_when_entry_worse_than_close(analyzer: CLVAnalyzer) -> None:
    """
    Проверяет, что CLV отрицательный, когда коэффициент входа ниже закрытия.

    Сценарий: вошли по 1.85, рынок закрылся на 2.05.
    Рынок «подтвердил», что исход менее вероятен — вошли по невыгодному курсу.
    CLV = (1.85/2.05 - 1) * 100 ≈ -9.76%
    """
    entry_odds = 1.85
    closing_odds = 2.05

    clv = analyzer.compute_clv(entry_odds, closing_odds)

    assert clv < 0.0, (
        f"Ожидался отрицательный CLV (entry={entry_odds} < closing={closing_odds}), "
        f"получено CLV={clv:.4f}%"
    )

    # Метод is_positive должен вернуть False
    assert analyzer.is_positive(clv) is False


# ---------------------------------------------------------------------------
# Тест 3: Нулевой CLV при entry_odds == closing_odds
# ---------------------------------------------------------------------------


def test_clv_zero_when_equal(analyzer: CLVAnalyzer) -> None:
    """
    Проверяет, что CLV ≈ 0 при совпадении коэффициента входа и закрытия.

    Сценарий: вошли по 2.00, рынок закрылся также на 2.00.
    CLV = (2.00/2.00 - 1) * 100 = 0.0%
    """
    odds = 2.00
    clv = analyzer.compute_clv(odds, odds)

    assert abs(clv) < 1e-10, (
        f"Ожидался CLV ≈ 0.0 при entry=closing={odds}, получено {clv}"
    )


# ---------------------------------------------------------------------------
# Тест 4: Проверка формулы CLV
# ---------------------------------------------------------------------------


def test_clv_formula(analyzer: CLVAnalyzer) -> None:
    """
    Верифицирует точную формулу вычисления CLV:
        CLV% = (entry_odds / closing_odds - 1) * 100

    Тестирует несколько пар коэффициентов и сравнивает с ожидаемыми значениями.
    """
    test_cases = [
        # (entry_odds, closing_odds, expected_clv)
        (2.10, 2.00, (2.10 / 2.00 - 1) * 100),   # +5.0%
        (1.90, 2.10, (1.90 / 2.10 - 1) * 100),   # ≈ -9.52%
        (3.00, 3.00, 0.0),                         # 0.0%
        (2.50, 2.20, (2.50 / 2.20 - 1) * 100),   # ≈ +13.64%
        (1.50, 1.60, (1.50 / 1.60 - 1) * 100),   # ≈ -6.25%
    ]

    for entry, closing, expected in test_cases:
        computed = analyzer.compute_clv(entry, closing)
        assert abs(computed - expected) < 1e-10, (
            f"CLV({entry}, {closing}): вычислено {computed:.6f}%, ожидалось {expected:.6f}%"
        )


# ---------------------------------------------------------------------------
# Тест 5: Пакетное вычисление CLV по DataFrame
# ---------------------------------------------------------------------------


def test_clv_batch_compute(analyzer: CLVAnalyzer) -> None:
    """
    Проверяет пакетное вычисление CLV методом batch_compute для DataFrame.

    Ожидаемые добавленные колонки: clv_pct, is_positive_clv.
    Проверяет корректность значений для каждой строки.
    """
    df = pd.DataFrame({
        "event_id": ["E1", "E2", "E3", "E4"],
        "entry_odds":   [2.10, 1.85, 2.00, 3.00],
        "closing_odds": [1.95, 2.05, 2.00, 2.80],
    })

    result = analyzer.batch_compute(df)

    # Проверяем наличие новых колонок
    assert "clv_pct" in result.columns, "Колонка clv_pct должна присутствовать в результате"
    assert "is_positive_clv" in result.columns, "Колонка is_positive_clv должна присутствовать"

    # Проверяем размер результата
    assert len(result) == len(df), "Количество строк не должно измениться"

    # Проверяем значения CLV для каждой строки
    expected_clv_values = [
        (2.10 / 1.95 - 1) * 100,  # E1: положительный
        (1.85 / 2.05 - 1) * 100,  # E2: отрицательный
        (2.00 / 2.00 - 1) * 100,  # E3: ноль
        (3.00 / 2.80 - 1) * 100,  # E4: положительный
    ]

    for i, expected in enumerate(expected_clv_values):
        actual = result.iloc[i]["clv_pct"]
        assert abs(actual - expected) < 1e-10, (
            f"Строка {i}: clv_pct={actual:.6f}%, ожидалось {expected:.6f}%"
        )

    # Проверяем флаги is_positive_clv
    assert result.iloc[0]["is_positive_clv"] is True or result.iloc[0]["is_positive_clv"] == True
    assert result.iloc[1]["is_positive_clv"] is False or result.iloc[1]["is_positive_clv"] == False
    assert result.iloc[2]["is_positive_clv"] is False or result.iloc[2]["is_positive_clv"] == False
    assert result.iloc[3]["is_positive_clv"] is True or result.iloc[3]["is_positive_clv"] == True


# ---------------------------------------------------------------------------
# Тест 6: Сводная статистика CLV
# ---------------------------------------------------------------------------


def test_clv_summary_stats(analyzer: CLVAnalyzer, fixed_ts: datetime) -> None:
    """
    Проверяет вычисление сводной статистики по списку CLVRecord.

    Создаём 5 записей с известными значениями CLV и сверяем:
        - mean_clv_pct
        - positive_clv_rate
        - n_records
        - min_clv_pct / max_clv_pct
    """
    # Создаём записи с предопределёнными парами коэффициентов
    records_data = [
        (2.10, 1.95),  # CLV ≈ +7.69% (положительный)
        (1.85, 2.05),  # CLV ≈ -9.76% (отрицательный)
        (2.00, 2.00),  # CLV = 0.0% (нейтральный)
        (2.50, 2.20),  # CLV ≈ +13.64% (положительный)
        (1.75, 1.90),  # CLV ≈ -7.89% (отрицательный)
    ]

    clv_records = [
        _make_clv_record(entry, closing, fixed_ts)
        for entry, closing in records_data
    ]

    # Ожидаемые значения
    expected_clv_values = [(e / c - 1) * 100 for e, c in records_data]
    expected_mean = sum(expected_clv_values) / len(expected_clv_values)
    expected_positive_count = sum(1 for v in expected_clv_values if v > 0)
    expected_positive_rate = expected_positive_count / len(expected_clv_values) * 100

    stats = analyzer.summarize(clv_records)

    # Количество записей
    assert stats["n_records"] == 5, f"Ожидалось 5 записей, получено {stats['n_records']}"

    # Среднее CLV
    assert abs(stats["mean_clv_pct"] - expected_mean) < 0.01, (
        f"mean_clv_pct={stats['mean_clv_pct']:.4f}%, ожидалось {expected_mean:.4f}%"
    )

    # Доля положительных CLV (2 из 5 = 40%)
    assert abs(stats["positive_clv_rate"] - expected_positive_rate) < 0.01, (
        f"positive_clv_rate={stats['positive_clv_rate']:.2f}%, ожидалось {expected_positive_rate:.2f}%"
    )

    # Min и max CLV
    assert stats["min_clv_pct"] == min(round(v, 4) for v in expected_clv_values) or \
           abs(stats["min_clv_pct"] - min(expected_clv_values)) < 0.01
    assert stats["max_clv_pct"] == max(round(v, 4) for v in expected_clv_values) or \
           abs(stats["max_clv_pct"] - max(expected_clv_values)) < 0.01


# ---------------------------------------------------------------------------
# Тест 7: CLV > порога корректно флагируется
# ---------------------------------------------------------------------------


def test_clv_significance(analyzer: CLVAnalyzer) -> None:
    """
    Проверяет, что метод is_positive корректно применяет пороговое значение.

    Тестируем разные пороги:
        - Порог 0.0%: положительный CLV = любое значение > 0.
        - Порог 2.0%: только CLV > 2% считается значимым.
        - Порог 5.0%: только CLV > 5% считается значимым.
    """
    # Тест-пары: (entry_odds, closing_odds) → известный CLV
    entry_odds = 2.10
    closing_odds = 1.95
    clv = analyzer.compute_clv(entry_odds, closing_odds)  # ≈ +7.69%

    # Порог 0.0%: CLV ~7.69% > 0 → True
    assert analyzer.is_positive(clv, threshold=0.0) is True

    # Порог 2.0%: CLV ~7.69% > 2.0 → True
    assert analyzer.is_positive(clv, threshold=2.0) is True

    # Порог 5.0%: CLV ~7.69% > 5.0 → True
    assert analyzer.is_positive(clv, threshold=5.0) is True

    # Порог 10.0%: CLV ~7.69% < 10.0 → False
    assert analyzer.is_positive(clv, threshold=10.0) is False

    # Тест с пограничным значением: entry == closing → CLV = 0.0
    clv_zero = analyzer.compute_clv(2.0, 2.0)
    assert analyzer.is_positive(clv_zero, threshold=0.0) is False, (
        "CLV=0.0% не должен считаться значимым при пороге 0.0% (strict >)"
    )

    # Тест с отрицательным CLV
    clv_negative = analyzer.compute_clv(1.85, 2.05)  # ≈ -9.76%
    assert analyzer.is_positive(clv_negative, threshold=0.0) is False
    assert analyzer.is_positive(clv_negative, threshold=-5.0) is False, (
        "CLV ≈ -9.76% не должен превышать порог -5.0%"
    )
    assert analyzer.is_positive(clv_negative, threshold=-15.0) is True, (
        "CLV ≈ -9.76% должен превышать порог -15.0%"
    )
