# Методология бэктестинга — Betting Analytics MVP

**Версия:** 0.1.0  
**Дата:** 2026-05-26  
**Язык документации:** Русский

---

## 1. Обзор и принципы

Бэктестинг в данной системе построен на следующих неизменных принципах:

1. **Нет утечки данных из будущего.** Любое нарушение этого правила делает результаты бэктеста бесполезными.
2. **Воспроизводимость.** Каждый прогон должен давать идентичные результаты при одинаковых входных данных.
3. **Консерватизм.** При сомнении — ухудшаем условия бэктеста (хуже котировка, позднее время входа).
4. **Разделение train/test.** Никакие параметры стратегии не могут быть подобраны на test-данных.
5. **Статистическая строгость.** Единственный положительный P&L недостаточен — необходима значимость.

---

## 2. Walk-Forward дизайн

### 2.1 Схема

```
Временна́я ось →

IS (train)    │ OOS (test)  │
──────────────┼─────────────┼──────────────
              │             │
  Фолд 1:                   │
  Train: 2022-01 – 2022-12  │
  Test:  2023-01 – 2023-03  │
                             │
  Фолд 2:                   │
  Train: 2022-01 – 2023-03  │  (expanding window)
  Test:  2023-04 – 2023-06  │
                             │
  Фолд N:                   │
  Train: 2022-01 – 2024-09  │
  Test:  2024-10 – 2024-12  │
                                         │
  HOLD-OUT (заморожен):                  │
  2025-01 – 2025-12 ────────────────────►│ Только после финализации стратегии
```

### 2.2 Параметры окна

| Параметр | Значение по умолчанию | Описание |
|---------|----------------------|---------|
| `train_window_type` | `expanding` | Expanding window (train включает всё с начала) |
| `test_window_size_months` | `3` | Квартальные test-фолды |
| `min_train_months` | `12` | Минимум 12 месяцев для первого train-фолда |
| `gap_days` | `7` | Разрыв между концом train и началом test (предотвращает data snooping) |
| `min_trades_per_fold` | `30` | Если меньше 30 сделок на фолде — результат ненадёжен, помечается как `low_sample` |

### 2.3 Expanding vs Rolling Window

По умолчанию используется **expanding window** — тренировочный период всегда начинается с `global_start_date` и расширяется к каждому следующему фолду. Для стратегий, чувствительных к дрейфу рынка (например, H001), предусмотрен параметр `rolling_window_months = 24` — ограничение глубины train последними N месяцами.

---

## 3. Правила отсутствия утечки данных

### 3.1 Базовое правило

> Ни одна запись из периода `T > bet_simulated_at_utc` не должна влиять на решение о ставке в момент `bet_simulated_at_utc`.

### 3.2 Специфические запреты

#### Котировки

| Запрещено | Разрешено |
|-----------|----------|
| Использовать closing odds как entry | Использовать closing odds только как метрику CLV (пост-фактум) |
| Использовать snapshot котировки после `event_time_utc` | Snapshot только до `event_time_utc` |
| Выбирать «лучшую» точку входа, зная будущее движение | Использовать первый доступный snapshot из целевого временно́го окна |

#### Травмы и составы

| Запрещено | Разрешено |
|-----------|----------|
| Использовать новость о травме, опубликованную после `bet_simulated_at_utc` | Только события с `report_ts_utc < bet_simulated_at_utc` |
| Использовать финальный состав для определения ставки | Предматчевые составы по предварительным заявкам |
| Ретроспективно «знать» о замене в стартовом составе | Только официальные объявления до матча |

#### Результаты и статистика

| Запрещено | Разрешено |
|-----------|----------|
| Включать xG из разбираемого матча в features | xG только из предыдущих матчей команды |
| Статистика сезона, включающая будущие туры | Только сыгранные туры на момент `bet_simulated_at_utc` |
| Обновлённые рейтинги, включающие данные из будущего | Rolling рейтинги с явной датой отсечки |

### 3.3 Timestamp-Aware Merge — алгоритм

```python
def timestamp_aware_merge(events_df, features_df, t_decision: str = "bet_simulated_at_utc"):
    """
    Мёрж features к событиям с учётом временно́й границы.
    Для каждого события берётся только последнее известное значение feature
    на момент t_decision.
    """
    result = []
    for _, event in events_df.iterrows():
        t_cutoff = event[t_decision]
        
        # Фильтруем только записи, ИЗВЕСТНЫЕ до момента ставки
        available_features = features_df[
            features_df["report_ts_utc"] < t_cutoff
        ]
        
        # Берём самое актуальное значение (last known)
        latest = available_features.sort_values("report_ts_utc").iloc[-1]
        
        result.append({**event.to_dict(), **latest.to_dict()})
    
    return pd.DataFrame(result)
```

**Критические поля для временно́й фильтрации:**

| Источник данных | Поле временно́й метки | Правило |
|----------------|---------------------|---------|
| `injury_event` | `report_ts_utc` | < `bet_simulated_at_utc` |
| `normalized_odds` | `snapshot_ts_utc` | < `bet_simulated_at_utc` |
| `result_core` (предыдущие матчи) | `confirmed_at_utc` | < `bet_simulated_at_utc` |
| Рейтинги/статистика | `calculated_as_of_utc` | < `bet_simulated_at_utc` |

---

## 4. Версионирование и воспроизводимость

### 4.1 Тройная привязка

Каждый прогон бэктеста уникально идентифицируется тремя компонентами:

```
backtest_run_id = hash(strategy_version + dataset_version + dataset_fingerprint)
```

| Компонент | Формат | Пример |
|-----------|--------|--------|
| `strategy_version` | SemVer | `H001_v1.2.3` |
| `dataset_version` | Дата снимка | `2024-12-31` |
| `dataset_fingerprint` | SHA-256 входных файлов | `a3f9b2c1...` |
| `backtest_run_id` | UUID | `550e8400-e29b-41d4-a716-446655440000` |

### 4.2 Хранение артефактов прогона

```
data/core/backtest_runs/
└── {backtest_run_id}/
    ├── metadata.json          # strategy_version, dataset_version, параметры
    ├── backtest_trade.parquet # Все сделки
    ├── metrics.json           # Агрегированные метрики
    ├── config_snapshot.yaml   # Полная копия конфига на момент прогона
    └── fold_metrics.parquet   # Метрики по каждому фолду
```

### 4.3 Dataset Fingerprint

```python
import hashlib
import pandas as pd

def compute_dataset_fingerprint(df: pd.DataFrame) -> str:
    """SHA-256 fingerprint датафрейма для воспроизводимости."""
    canonical = df.sort_values(by=df.columns.tolist()).reset_index(drop=True)
    buffer = canonical.to_parquet()
    return hashlib.sha256(buffer).hexdigest()
```

---

## 5. Метрики бэктеста

### 5.1 Базовые метрики

| Метрика | Формула | Интерпретация |
|---------|---------|--------------|
| **ROI** | `sum(profit) / sum(entry_odds * stake)` | Возврат на инвестиции |
| **Yield** | `sum(profit) / sum(stake)` | Доходность на единицу ставки |
| **CLV** | `mean(log(closing_odds / entry_odds))` | Качество тайминга входа |
| **Win Rate** | `wins / total_bets` | Частота выигрышных ставок |
| **Turnover** | `sum(stake)` | Общий объём ставок |
| **Profit Units** | `sum(profit_units)` | Абсолютная прибыль в единицах |

### 5.2 Метрики риска

| Метрика | Формула | Целевой порог |
|---------|---------|--------------|
| **Max Drawdown** | `max(peak - trough) / peak` | < 20% банкролла |
| **Max Consecutive Losses** | `max длина серии убыточных ставок` | < 15 ставок |
| **Sharpe Ratio** | `mean(profit) / std(profit)` | > 0.5 |
| **Profit Factor** | `sum(wins) / abs(sum(losses))` | > 1.1 |

### 5.3 Статистическая значимость

#### Bootstrap тест

```python
def bootstrap_yield(profits: np.ndarray, n_iterations: int = 10_000) -> dict:
    """
    Bootstrap доверительный интервал для Yield.
    H0: Yield <= 0 (нет края)
    H1: Yield > 0
    """
    n = len(profits)
    bootstrap_yields = []
    
    for _ in range(n_iterations):
        sample = np.random.choice(profits, size=n, replace=True)
        bootstrap_yields.append(sample.mean() / abs(sample).mean())
    
    bootstrap_yields = np.array(bootstrap_yields)
    p_value = (bootstrap_yields <= 0).mean()
    
    return {
        "yield_observed": profits.mean() / abs(profits).mean(),
        "yield_ci_95_lower": np.percentile(bootstrap_yields, 2.5),
        "yield_ci_95_upper": np.percentile(bootstrap_yields, 97.5),
        "p_value": p_value,
    }
```

#### Критерии значимости

| Критерий | Порог | Обязательность |
|---------|-------|---------------|
| p-value (bootstrap) | < 0.05 | Обязательно |
| Количество сделок OOS | ≥ 200 | Обязательно |
| CLV > 0 (медиана) | Да | Обязательно (независимое подтверждение) |
| Stability ratio (OOS/IS yield) | ≥ 0.5 | Рекомендательно |

### 5.4 OOS-специфичные метрики

```python
oos_metrics = {
    "oos_yield": ...,              # Yield на OOS фолдах
    "oos_roi": ...,                # ROI на OOS
    "oos_stability": std(fold_yields) / mean(fold_yields),  # CV по фолдам
    "oos_vs_is_ratio": oos_yield / is_yield,  # деградация
    "best_fold_yield": max(fold_yields),
    "worst_fold_yield": min(fold_yields),
    "positive_folds_pct": sum(y > 0 for y in fold_yields) / len(fold_yields),
}
```

---

## 6. Ставочная методология (staking)

### 6.1 Flat Stakes (paper trading и бэктест по умолчанию)

Для paper trading и первичных бэктестов используются **flat stakes** (одинаковый размер ставки в единицах):

```
stake_units = 1.0  # для всех ставок
```

**Обоснование:** Flat stakes позволяют измерить чистый край стратегии (edge quality) без влияния метода распределения банкролла. Kelly или другие методы могут вводить дополнительные предположения.

### 6.2 Запрещённые методы в бэктесте

| Метод | Почему запрещён |
|-------|---------------|
| Kelly criterion с full Kelly | Нереалистичен в live (overfit к распределению) |
| Fibonacci/Martingale | Создаёт иллюзию прибыльности за счёт variance |
| Optimal f (Ralph Vince) | Требует знания максимального убытка — look-ahead |
| Adaptive Kelly (с look-ahead) | Нельзя адаптироваться к будущим данным |

### 6.3 Переход к live staking

После валидации стратегии в paper trading рассматривается переход к fractional Kelly:

```
kelly_fraction = 0.25  # 1/4 Kelly — консервативно
kelly_stake = kelly_fraction * (edge / (odds - 1))
```

---

## 7. Базовые стратегии (baselines)

Каждая гипотеза сравнивается с базовыми стратегиями:

| Baseline | Описание | Назначение |
|---------|---------|-----------|
| **Random** | Случайные ставки с uniform stake | Нижняя граница |
| **Always Home** | Ставка на победу хозяев в каждом матче | Проверка home bias |
| **Always Favourite** | Ставка на исход с min odds | Проверка favourite-longshot |
| **Market Benchmark** | Ставки по closing Pinnacle (без маржи) | Верхняя граница |
| **Null Strategy** | Не делать ставок | P&L = 0 |

**Требование:** Целевая стратегия обязана превзойти ALL baselines на OOS, иначе результат не считается валидным.

---

## 8. Оценка качества данных

### 8.1 Data Quality Score (DQS)

Каждый прогон бэктеста получает оценку качества входных данных:

```python
def compute_data_quality_score(df: pd.DataFrame) -> float:
    """
    Оценка качества данных от 0.0 до 1.0.
    Влияет на интерпретацию результатов бэктеста.
    """
    scores = []
    
    # Полнота (completeness)
    scores.append(1 - df.isnull().mean().mean())
    
    # Покрытие референсных котировок
    ref_coverage = (df["reference_fair_odds"].notna()).mean()
    scores.append(ref_coverage)
    
    # Временна́я плотность (≥ 3 снимка на событие)
    snapshot_density = df.groupby("normalized_event_id")["snapshot_ts_utc"].count()
    scores.append((snapshot_density >= 3).mean())
    
    # Source quality (доля official/paid vs scrape/social)
    if "source_quality" in df.columns:
        high_quality = df["source_quality"].isin(
            ["official_report", "official_vendor", "paid_vendor"]
        ).mean()
        scores.append(high_quality)
    
    return np.mean(scores)
```

### 8.2 Пороги DQS

| DQS | Интерпретация | Действие |
|-----|--------------|---------|
| ≥ 0.90 | Высокое качество | Прогон принимается |
| 0.75–0.90 | Приемлемое качество | Прогон с предупреждением |
| 0.60–0.75 | Низкое качество | Результаты маркируются `low_dqs` |
| < 0.60 | Критически низкое | Прогон отклоняется |

---

## 9. Репортинг результатов

### 9.1 Обязательные поля отчёта

```json
{
  "backtest_run_id": "...",
  "strategy_id": "H001_v1.2.3",
  "dataset_version": "2024-12-31",
  "dataset_fingerprint": "a3f9b2c1...",
  "data_quality_score": 0.92,
  "period": {"start": "2022-01-01", "end": "2024-12-31"},
  "n_folds": 12,
  "is_metrics": {
    "yield": 0.043,
    "roi": 0.038,
    "n_trades": 1240,
    "max_drawdown": 0.12
  },
  "oos_metrics": {
    "yield": 0.031,
    "roi": 0.027,
    "n_trades": 312,
    "max_drawdown": 0.18,
    "p_value": 0.031,
    "clv_median": 0.014,
    "stability_ratio": 0.72
  },
  "fold_metrics": [...],
  "leakage_checks_passed": true,
  "reproducibility_verified": true
}
```

### 9.2 Чеклист перед публикацией результатов

- [ ] Leakage check: все поля временно́й фильтрации проверены
- [ ] Dataset fingerprint зафиксирован
- [ ] Hold-out данные НЕ использовались
- [ ] Количество сделок ≥ 200 на OOS
- [ ] p-value < 0.05 (bootstrap, 10 000 итераций)
- [ ] CLV медиана > 0
- [ ] DQS ≥ 0.75
- [ ] Сравнение с baselines выполнено
- [ ] Независимый ревью кода бэктеста (второй человек или code review)

---

## 10. Ограничения и оговорки

### 10.1 Структурные ограничения бэктеста

- **Execution slippage:** В реальной торговле котировка может измениться между генерацией сигнала и размещением ставки. Бэктест не моделирует задержку исполнения.
- **Liquidity constraints:** Бэктест не учитывает, что большие ставки могут двигать линию (актуально при масштабировании).
- **Account restrictions:** Российские букмекеры ограничивают «умных» клиентов (снижение лимитов, верификация). Бэктест предполагает неограниченный доступ.
- **Survivorship bias источников:** Некоторые источники котировок могут перестать существовать.

### 10.2 Интерпретация результатов

Положительный результат бэктеста при соблюдении всех правил — **необходимое, но не достаточное** условие для реальной торговли. Следующим обязательным этапом является paper trading не менее 60 дней.

---

*Документ является нормативным для всех прогонов бэктестов системы. Отступления от методологии должны быть явно задокументированы в `metadata.json` соответствующего прогона.*
