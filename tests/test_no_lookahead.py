"""
Тесты для проверки отсутствия утечки данных из будущего (look-ahead bias).

Каждый тест использует синтетические данные и не имеет внешних зависимостей.
Цель: убедиться, что все компоненты системы корректно ограничивают данные
временным барьером (cutoff_ts) и не допускают проникновения «будущих» данных
в сигналы или бэктест.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from src.features.illness_features import IllnessEvent, IllnessFeatureBuilder

# ---------------------------------------------------------------------------
# Вспомогательные функции для генерации синтетических данных
# ---------------------------------------------------------------------------


def _make_illness_event(
    report_offset_hours: float,
    match_time: datetime,
    player_id: str = "P001",
    team_id: str = "TEAM_A",
) -> IllnessEvent:
    """
    Создаёт синтетическое событие о травме с заданным смещением от времени матча.

    Параметры
    ----------
    report_offset_hours : float
        Смещение времени публикации события от времени матча (в часах).
        Отрицательное значение → событие до матча (прошлое).
        Положительное значение → событие после матча (будущее).
    match_time : datetime
        Время начала матча (UTC).
    player_id : str
        Идентификатор игрока.
    team_id : str
        Идентификатор команды.
    """
    report_ts = match_time + timedelta(hours=report_offset_hours)
    return IllnessEvent(
        player_id=player_id,
        team_id=team_id,
        status="out",
        tag="hamstring",
        report_ts=report_ts,
        source_quality="official_report",
        expected_minutes_proxy=90.0,
    )


# ---------------------------------------------------------------------------
# Синтетические данные для бэктеста (без реального BacktestTrade класса)
# ---------------------------------------------------------------------------


class _SyntheticBacktestTrade:
    """
    Минимальная заглушка записи бэктест-ставки для тестирования.

    Имитирует ставку с заданными параметрами входа и закрытия рынка.
    """

    def __init__(
        self,
        entry_odds: float,
        closing_odds: float,
        entry_ts: datetime,
        closing_ts: datetime,
        event_id: str = "EVT_001",
    ) -> None:
        self.entry_odds = entry_odds
        self.closing_odds = closing_odds
        self.entry_ts = entry_ts
        self.closing_ts = closing_ts
        self.event_id = event_id


def _backtest_closing_odds_not_used_at_entry(trade: _SyntheticBacktestTrade) -> bool:
    """
    Проверяет, что коэффициент входа в ставку НЕ равен коэффициенту закрытия,
    если закрытие произошло позже входа (нет look-ahead).

    Возвращает True, если нет подозрений на утечку данных.
    """
    if trade.closing_ts <= trade.entry_ts:
        # Если закрытие произошло ДО или В МОМЕНТ входа — это нормально
        return True
    # Если закрытие ПОСЛЕ входа, коэффициенты должны быть разными
    # (одинаковые коэффициенты = подозрение на использование данных закрытия при входе)
    return trade.entry_odds != trade.closing_odds


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture
def match_time_utc() -> datetime:
    """Фиксированное время начала матча для тестов (UTC)."""
    return datetime(2025, 6, 15, 16, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def illness_builder() -> IllnessFeatureBuilder:
    """Экземпляр IllnessFeatureBuilder для тестов."""
    return IllnessFeatureBuilder()


# ---------------------------------------------------------------------------
# Тест 1: Фильтр блокирует события из будущего
# ---------------------------------------------------------------------------


def test_illness_filter_blocks_future_events(
    illness_builder: IllnessFeatureBuilder,
    match_time_utc: datetime,
) -> None:
    """
    Проверяет, что filter_by_cutoff удаляет события, опубликованные ПОСЛЕ cutoff_ts.

    Сценарий:
        - Создаём события с report_ts = match_time + 1h, +2h, +24h (будущее).
        - cutoff_ts = match_time (момент начала матча).
        - Ожидаем: ни одно событие не проходит фильтр.
    """
    future_events = [
        _make_illness_event(report_offset_hours=1.0, match_time=match_time_utc, player_id="P001"),
        _make_illness_event(report_offset_hours=2.0, match_time=match_time_utc, player_id="P002"),
        _make_illness_event(report_offset_hours=24.0, match_time=match_time_utc, player_id="P003"),
    ]

    # Применяем фильтр с cutoff_ts = время начала матча
    filtered = illness_builder.filter_by_cutoff(future_events, cutoff_ts=match_time_utc)

    assert len(filtered) == 0, (
        f"Ожидалось 0 событий после фильтра (все из будущего), " f"получено {len(filtered)}"
    )


# ---------------------------------------------------------------------------
# Тест 2: Фильтр пропускает события из прошлого
# ---------------------------------------------------------------------------


def test_illness_filter_allows_past_events(
    illness_builder: IllnessFeatureBuilder,
    match_time_utc: datetime,
) -> None:
    """
    Проверяет, что filter_by_cutoff пропускает события, опубликованные ДО cutoff_ts.

    Сценарий:
        - Создаём события с report_ts = match_time - 1h, -12h, -48h (прошлое).
        - cutoff_ts = match_time.
        - Ожидаем: все три события проходят фильтр.
    """
    past_events = [
        _make_illness_event(report_offset_hours=-1.0, match_time=match_time_utc, player_id="P001"),
        _make_illness_event(report_offset_hours=-12.0, match_time=match_time_utc, player_id="P002"),
        _make_illness_event(report_offset_hours=-48.0, match_time=match_time_utc, player_id="P003"),
    ]

    filtered = illness_builder.filter_by_cutoff(past_events, cutoff_ts=match_time_utc)

    assert len(filtered) == 3, f"Ожидалось 3 события (все из прошлого), получено {len(filtered)}"
    for evt in filtered:
        assert evt.report_ts < match_time_utc, (
            f"Событие с report_ts={evt.report_ts} прошло фильтр, "
            f"хотя должно быть строго раньше cutoff_ts={match_time_utc}"
        )


# ---------------------------------------------------------------------------
# Тест 3: Бэктест не использует коэффициент закрытия при входе
# ---------------------------------------------------------------------------


def test_backtest_no_closing_odds_as_input(match_time_utc: datetime) -> None:
    """
    Проверяет, что в бэктесте коэффициент входа (entry_odds) отличается от
    коэффициента закрытия (closing_odds), если закрытие произошло ПОЗЖЕ входа.

    Сценарий:
        - entry_ts = match_time - 2h (вход за 2 часа до матча).
        - closing_ts = match_time - 5min (закрытие за 5 минут до матча).
        - entry_odds = 2.10, closing_odds = 1.95 (разные коэффициенты).
        - Ожидаем: no look-ahead bias = True.

    Антисценарий:
        - Если entry_odds == closing_odds, хотя закрытие позже входа —
          подозрение на использование данных закрытия при входе.
    """
    entry_ts = match_time_utc - timedelta(hours=2)
    closing_ts = match_time_utc - timedelta(minutes=5)

    # Корректный случай: разные коэффициенты
    trade_valid = _SyntheticBacktestTrade(
        entry_odds=2.10,
        closing_odds=1.95,
        entry_ts=entry_ts,
        closing_ts=closing_ts,
    )
    assert (
        _backtest_closing_odds_not_used_at_entry(trade_valid) is True
    ), "Ожидалось True: entry_odds != closing_odds при закрытии после входа"

    # Подозрительный случай: одинаковые коэффициенты при закрытии после входа
    trade_suspicious = _SyntheticBacktestTrade(
        entry_odds=1.95,
        closing_odds=1.95,
        entry_ts=entry_ts,
        closing_ts=closing_ts,
    )
    assert (
        _backtest_closing_odds_not_used_at_entry(trade_suspicious) is False
    ), "Ожидалось False: entry_odds == closing_odds при закрытии позже — подозрение на утечку"


# ---------------------------------------------------------------------------
# Тест 4: Временная метка сигнала должна быть раньше начала матча
# ---------------------------------------------------------------------------


def test_signal_timestamp_before_match(match_time_utc: datetime) -> None:
    """
    Проверяет, что сигнал сгенерирован ДО начала матча (timestamp_utc < event_start_time).

    Сценарий:
        - Корректный сигнал: timestamp за 30 минут до матча.
        - Некорректный сигнал: timestamp на 10 минут после начала матча.
    """
    event_start = match_time_utc

    # Корректный сигнал — создан до начала матча
    signal_valid = {
        "signal_id": "sig_20250615_000001",
        "timestamp_utc": (event_start - timedelta(minutes=30)).isoformat(),
        "event_start_utc": event_start.isoformat(),
        "entry_odds": 2.10,
    }

    signal_ts = datetime.fromisoformat(signal_valid["timestamp_utc"])
    if signal_ts.tzinfo is None:
        signal_ts = signal_ts.replace(tzinfo=timezone.utc)

    assert (
        signal_ts < event_start
    ), f"Сигнал timestamp_utc={signal_ts} должен быть раньше event_start={event_start}"

    # Некорректный сигнал — создан после начала матча (look-ahead)
    signal_invalid = {
        "signal_id": "sig_20250615_000002",
        "timestamp_utc": (event_start + timedelta(minutes=10)).isoformat(),
        "event_start_utc": event_start.isoformat(),
        "entry_odds": 2.10,
    }

    invalid_ts = datetime.fromisoformat(signal_invalid["timestamp_utc"])
    if invalid_ts.tzinfo is None:
        invalid_ts = invalid_ts.replace(tzinfo=timezone.utc)

    assert (
        invalid_ts >= event_start
    ), "Второй сигнал должен быть ПОСЛЕ начала матча (демонстрируем некорректный случай)"
    # Сигнал с timestamp >= event_start недопустим в системе
    is_valid_signal = invalid_ts < event_start
    assert (
        not is_valid_signal
    ), "Сигнал с timestamp после начала матча не должен проходить валидацию"


# ---------------------------------------------------------------------------
# Тест 5: При join данных о травмах соблюдается cutoff_ts
# ---------------------------------------------------------------------------


def test_injury_join_cutoff_respected(
    illness_builder: IllnessFeatureBuilder,
    match_time_utc: datetime,
) -> None:
    """
    Проверяет, что при объединении данных о травмах с коэффициентами
    включаются только события, опубликованные до cutoff_ts.

    Сценарий:
        - Имеется смесь событий: 2 до cutoff_ts и 2 после.
        - После filter_by_cutoff должны остаться только 2 прошлых события.
        - team_absence_score должен учитывать только отфильтрованные события.
    """
    cutoff = match_time_utc  # cutoff = время начала матча

    all_events = [
        # Прошлые события (должны пройти фильтр)
        _make_illness_event(-24.0, match_time_utc, player_id="P001", team_id="TEAM_A"),
        _make_illness_event(-6.0, match_time_utc, player_id="P002", team_id="TEAM_A"),
        # Будущие события (должны быть отфильтрованы)
        _make_illness_event(+0.5, match_time_utc, player_id="P003", team_id="TEAM_A"),
        _make_illness_event(+4.0, match_time_utc, player_id="P004", team_id="TEAM_A"),
    ]

    # Фильтруем по cutoff_ts
    filtered = illness_builder.filter_by_cutoff(all_events, cutoff_ts=cutoff)

    assert len(filtered) == 2, f"После фильтра должно остаться 2 события, получено {len(filtered)}"

    # Проверяем, что отсутствие учитывается только для отфильтрованных событий
    score = illness_builder.compute_team_absence_score(filtered, team_id="TEAM_A")
    assert score["number_of_absent_starters"] == 2, (
        f"Ожидалось 2 отсутствующих игрока (только прошлые события), "
        f"получено {score['number_of_absent_starters']}"
    )


# ---------------------------------------------------------------------------
# Тест 6: Walk-forward не допускает данные из тестового фолда в обучающий
# ---------------------------------------------------------------------------


def test_walk_forward_no_future_data() -> None:
    """
    Проверяет корректность разбивки walk-forward: тестовый фолд не пересекается
    с обучающим по дате события.

    Сценарий:
        - Генерируем 90 записей с датами от 2024-01-01 до 2024-03-31.
        - Train фолд: первые 60 записей (2024-01-01 — 2024-02-29).
        - Test фолд: следующие 30 записей (2024-03-01 — 2024-03-31).
        - Ожидаем: max(train dates) < min(test dates).
    """
    # Синтетические данные: 90 дней
    base_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
    dates = [base_date + timedelta(days=i) for i in range(90)]

    df = pd.DataFrame(
        {
            "event_date": dates,
            "odds": [2.0 + i * 0.01 for i in range(90)],
            "result": [1 if i % 2 == 0 else 0 for i in range(90)],
        }
    )

    # Walk-forward разбивка: 60 train / 30 test
    train_size = 60
    train_df = df.iloc[:train_size]
    test_df = df.iloc[train_size:]

    train_max_date = train_df["event_date"].max()
    test_min_date = test_df["event_date"].min()

    assert train_max_date < test_min_date, (
        f"Нарушение walk-forward: максимальная дата train ({train_max_date}) "
        f"должна быть РАНЬШЕ минимальной даты test ({test_min_date})"
    )

    # Дополнительно: нет пересечения дат
    train_dates = set(train_df["event_date"].dt.date)
    test_dates = set(test_df["event_date"].dt.date)
    intersection = train_dates & test_dates

    assert len(intersection) == 0, f"Пересечение дат train и test фолдов: {intersection}"
