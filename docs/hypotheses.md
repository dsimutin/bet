# Гипотезы стратегий — Betting Analytics MVP

**Версия:** 0.1.0  
**Дата:** 2026-05-26  
**Язык документации:** Русский

---

## Общие правила оформления гипотез

Каждая гипотеза оформляется по единому шаблону:

- **hypothesis_id** — уникальный код (H001–H005)
- **hypothesis_name** — краткое название
- **rationale** — научное/практическое обоснование
- **required_data** — необходимые данные и источники
- **formula** — математическая формулировка
- **leakage_risks** — риски утечки данных из будущего
- **validation_protocol** — порядок валидации
- **expected_failure_modes** — ожидаемые причины провала
- **deployment_readiness** — условия готовности к развёртыванию

**Статусы гипотез:** `draft` → `in_backtest` → `validated` → `paper_trading` → `deployed` / `rejected`

---

## H001 — devigged_reference_value

### Карточка гипотезы

| Поле | Значение |
|------|---------|
| `hypothesis_id` | `H001` |
| `hypothesis_name` | `devigged_reference_value` |
| `status` | `in_backtest` |
| `sport` | Футбол (расширяемо на другие) |
| `market` | `h2h`, `totals` |

### Обоснование (rationale)

Букмекеры с мягкими лимитами (soft books: Fonbet, Winline, BetBoom) систематически медленнее реагируют на движение котировок, чем острые рынки (sharp books: Pinnacle, Betfair Exchange). Если котировка у мягкого букмекера значительно превышает «справедливую» котировку, вычисленную из острого рынка методом devigging — существует положительный математический край (expected value > 0).

**Ключевое допущение:** Pinnacle/Betfair являются достаточно эффективными прокси для «истинной вероятности» и могут использоваться как reference line.

### Необходимые данные (required_data)

- `normalized_odds` — котировки мягких букмекеров (fonbet, winline, betboom, olimpbet)
- `normalized_odds` — котировки Pinnacle и/или Betfair Exchange (reference)
- `result_core` — финальные результаты для расчёта P&L
- Минимум: 2 года исторических данных по целевым лигам

### Формула (formula)

```
# Шаг 1: Devigging — расчёт fair odds из Pinnacle (метод Shin или power method)
overround = sum(1 / odds_i for i in outcomes)
fair_prob_i = (1 / odds_i) / overround           # простая нормализация
fair_odds_i = 1 / fair_prob_i

# Метод Shin (более точный):
# решает систему уравнений для нахождения z (доля инсайдерских ставок)
# fair_prob_i = (sqrt(z^2 + 4*(1-z)*p_i^2) - z) / (2*(1-z))

# Шаг 2: Расчёт края
edge_pct = (entry_odds_soft / fair_odds_reference - 1) * 100

# Шаг 3: Критерий входа
SIGNAL if edge_pct >= EDGE_THRESHOLD  # типичный порог: 3–5%

# Шаг 4: Ожидаемая стоимость
EV = entry_odds_soft * fair_prob - 1
```

**Пример:**
- Pinnacle даёт 1.90 / 3.60 / 4.20 (победа хозяев / ничья / победа гостей)
- Fair odds после devigging: 2.00 / 3.80 / 4.40
- Fonbet даёт 2.15 на победу хозяев
- Edge: (2.15 / 2.00 - 1) * 100 = **+7.5%**

### Риски утечки данных (leakage_risks)

| Риск | Описание | Митигация |
|------|---------|----------|
| **Closing odds leakage** | Использование closing line Pinnacle как fair odds в момент принятия решения | Использовать только pre-match snapshot до `event_time_utc - T_min` |
| **Survivor bias** | Выбор пар только тех событий, для которых Pinnacle опубликовал котировки | Фиксировать доступность котировок на момент T_decision |
| **Retroactive normalization** | Применение нормализационных правил, которые стали известны только post-hoc | Версионирование `qc-normalizer` с датой введения правила |

### Протокол валидации (validation_protocol)

1. **In-sample (IS):** 2022–2023 гг. — Топ-5 европейских лиг + РПЛ
2. **Out-of-sample (OOS):** 2024 г. — те же лиги (walk-forward, квартальные фолды)
3. **Hold-out:** 2025 г. — заморожен до завершения OOS
4. **Минимальный размер выборки:** 500 сделок на тест-окне
5. **Тесты значимости:** Bootstrap (10 000 итераций), p-value < 0.05
6. **Чек деградации:** Yield на OOS не ниже 50% от IS yield
7. **CLV-тест:** Медианный CLV > 0 (положительная CLV = независимое подтверждение края)

### Ожидаемые причины провала (expected_failure_modes)

- Pinnacle ужесточил лимиты → наш сниффинг не успевает → stale reference
- Арбитражники закрывают гэп быстрее нашего поллинга (< 5 минут)
- Fonbet внедрил алгоритм сужения котировок при обнаружении арбитража
- Малая выборка по конкретным рынкам (правильный счёт, азиатские гандикапы)
- Overfitting порогового значения EDGE_THRESHOLD на IS-данных

### Готовность к развёртыванию (deployment_readiness)

- [ ] Бэктест завершён с ≥ 500 сделками OOS
- [ ] p-value < 0.05 на OOS
- [ ] CLV > 0 на OOS медиана
- [ ] Нет утечки данных (проверено независимым ревьювером)
- [ ] Paper trading ≥ 60 дней без деградации
- [ ] Настроен мониторинг деградации стратегии

---

## H002 — closing_line_timing

### Карточка гипотезы

| Поле | Значение |
|------|---------|
| `hypothesis_id` | `H002` |
| `hypothesis_name` | `closing_line_timing` |
| `status` | `draft` |
| `sport` | Футбол |
| `market` | `h2h`, `totals` |

### Обоснование (rationale)

Closing Line Value (CLV) — разность между котировкой входа и финальной котировкой букмекера перед стартом матча — является широко признанным прокси долгосрочной прибыльности. Гипотеза: существует оптимальное временно́е окно для входа относительно закрытия линии. Ранний вход (48–72 часа) захватывает движение новостей, поздний (1–3 часа) отражает финальные составы и трафик ставок.

**Ключевое допущение:** CLV положительный = мы лучше среднего участника рынка оцениваем вероятности.

### Необходимые данные (required_data)

- `normalized_odds` — серия котировок по каждому событию (тайм-серия, не одна точка)
- Метки времени снимков котировок с точностью до минут
- `result_core` — результаты
- Минимум: 1 год тайм-серийных данных (не менее 3 снимков на событие)

### Формула (formula)

```python
# Разбивка по временны́м бакетам относительно kick-off
time_to_ko_hours = (event_time_utc - snapshot_ts_utc).total_seconds() / 3600

buckets = {
    "T_minus_72_to_48": (48, 72),
    "T_minus_48_to_24": (24, 48),
    "T_minus_24_to_6":  (6, 24),
    "T_minus_6_to_1":   (1, 6),
    "T_minus_1_to_0":   (0, 1),  # closing window
}

# CLV для каждого входа
clv_log = log(closing_odds / entry_odds)

# Средний CLV по бакетам
avg_clv_per_bucket = df.groupby("time_bucket")["clv_log"].mean()

# Гипотеза: существует бакет с avg_clv > 0 устойчиво
SIGNAL: войти в бакет с max(avg_clv) и edge_pct >= threshold
```

### Риски утечки данных (leakage_risks)

| Риск | Описание | Митигация |
|------|---------|----------|
| **Closing odds leakage** | Closing odds нельзя использовать как ВХОД в стратегию | Closing line только как пост-фактум метрика CLV |
| **Survivorship** | События без closing odds систематически другие | Анализ пропущенных closing линий |
| **Look-ahead в bucket assignment** | Знание будущего движения при разметке бакетов | Бакеты определяются по entry_time, не по движению |

### Протокол валидации (validation_protocol)

1. Описательная статистика CLV по бакетам на IS-данных
2. ANOVA / Kruskal-Wallis для сравнения бакетов
3. Walk-forward: выбор «лучшего» бакета только на train, тест на test
4. Bootstrap доверительные интервалы для CLV каждого бакета
5. Тест на автокорреляцию (momentum vs mean-reversion движения линии)

### Ожидаемые причины провала (expected_failure_modes)

- Нет устойчивого бакета — CLV случаен по всем окнам
- Spread между entry и closing слишком мал для покрытия комиссии
- Стратегия работает только на конкретных лигах (overfitting лиги)
- Данные тайм-серии котировок недостаточной частоты

### Готовность к развёртыванию (deployment_readiness)

- [ ] Минимум 3 снимка на событие в training data
- [ ] Статистически значимый CLV на OOS по целевому бакету
- [ ] Чек: closing odds не используются как input сигнала (только как метрика)
- [ ] Paper trading ≥ 90 дней

---

## H003 — favorite_longshot_buckets

### Карточка гипотезы

| Поле | Значение |
|------|---------|
| `hypothesis_id` | `H003` |
| `hypothesis_name` | `favorite_longshot_buckets` |
| `status` | `draft` |
| `sport` | Футбол, теннис |
| `market` | `h2h` |

### Обоснование (rationale)

Эффект «фаворит-аутсайдер» (favorite-longshot bias) — систематическое явление: публичные игроки переоценивают аутсайдеров (высокие котировки = завышенная предполагаемая вероятность) и недооценивают фаворитов. В эффективном рынке такого не должно быть. Гипотеза: существуют децильные группы котировок, где реализованная частота побед систематически отличается от имплицированной вероятности.

**Ключевое допущение:** Эффект достаточно стабилен во времени, чтобы использовать в walk-forward.

### Необходимые данные (required_data)

- `normalized_odds` — pre-match котировки h2h
- `result_core` — финальные исходы (победа 1/X/2)
- Рекомендуемый объём: ≥ 10 000 матчей для надёжного децильного анализа
- Исторические данные: Football-Data.co.uk (2015–2025)

### Формула (formula)

```python
# Шаг 1: Перевести котировку в имплицированную вероятность (без маржи)
# Используем devigged probability от reference book
implied_prob = devigged_prob(entry_odds, market_overround)

# Шаг 2: Разбить на децили по implied_prob
decile = pd.qcut(df["implied_prob"], q=10, labels=False)

# Шаг 3: Для каждого дециля — реализованная частота побед
realized_freq = df.groupby("decile")["is_win"].mean()

# Шаг 4: Calibration error
calibration_error = realized_freq - df.groupby("decile")["implied_prob"].mean()

# Положительный calibration_error → систематически ставим на недооценённые исходы

# Шаг 5: Обратная ставка
# Децили с calibration_error > THRESHOLD → signal
fair_odds_adj = 1 / realized_freq  # скорректированная fair odds
edge_pct = (entry_odds / fair_odds_adj - 1) * 100
```

**Пример:**
- Дециль 1 (фавориты, implied_prob 70–85%): реализованная частота = 80%, имплицированная = 76% → фавориты недооценены в этом сегменте

### Риски утечки данных (leakage_risks)

| Риск | Описание | Митигация |
|------|---------|----------|
| **Overfitting деци-бакетов** | Оптимальные децили найдены на IS, не работают на OOS | Жёсткие фолды: калибровка только на train |
| **Look-ahead в калибровке** | Использование результатов из OOS для определения границ децилей | pd.qcut только на train, применять .transform на test |
| **League selection bias** | Эффект устойчив только в отдельных лигах | Кросс-лиговая валидация |

### Протокол валидации (validation_protocol)

1. Калибровочный график (reliability diagram) на IS
2. Brier Score и ECE (Expected Calibration Error)
3. Walk-forward: границы децилей из train, P&L на test
4. Сравнение с naive baseline: «всегда ставить на фаворита»
5. Bootstrap на OOS: 10 000 итераций

### Ожидаемые причины провала (expected_failure_modes)

- Эффект исчез из-за арбитражной активности (рынок стал эффективнее)
- Эффект зависит от лиги/периода → нестабилен в walk-forward
- Маржа букмекера полностью поглощает выявленный край
- Малый объём выборки по конкретному децилю

### Готовность к развёртыванию (deployment_readiness)

- [ ] ≥ 10 000 матчей в тренировочных данных
- [ ] Calibration error устойчив на 3+ независимых OOS-окнах
- [ ] Yield > 0 на OOS net of margin
- [ ] Paper trading ≥ 90 дней

---

## H004 — illness_shock

### Карточка гипотезы

| Поле | Значение |
|------|---------|
| `hypothesis_id` | `H004` |
| `hypothesis_name` | `illness_shock` |
| `status` | `draft` |
| `sport` | Футбол |
| `market` | `h2h`, `totals`, `asian_handicap` |

### Обоснование (rationale)

Поздние новости о травме/болезни ключевого игрока (< 6 часов до матча) создают временно́й асимметричный информационный шок: рынок реагирует с задержкой из-за разрозненности источников и инерции ставок публики. Если удастся зафиксировать новость быстрее, чем рынок полностью её переварит — возникает краткосрочный положительный край.

**Ключевое допущение:** Реакция котировок статистически измерима и предсказуема по типу игрока (основной состав, вратарь, топ-бомбардир).

### Необходимые данные (required_data)

- `injury_event` — события с `is_late_breaking = true` и `source_quality IN ('official_report', 'official_vendor')`
- `normalized_odds` — тайм-серия котировок вокруг времени публикации новости
- `result_core` — результаты
- Минимум: 200 событий типа «поздняя травма ключевого игрока»

### Формула (formula)

```python
# Шаг 1: Идентификация события шока
shock_event = injury_event[
    (injury_event.is_late_breaking == True) &
    (injury_event.status == "out") &
    (injury_event.tag.isin(["key_player_out", "gk_out"])) &
    (injury_event.source_quality.isin(["official_report", "official_vendor"]))
]

# Шаг 2: Временны́е окна котировок
# Pre-news: снимок до report_ts_utc (closest before)
odds_pre_news = get_closest_snapshot(event_id, before=report_ts_utc)
# Post-reaction: снимок через 15–60 минут после
odds_post_reaction = get_closest_snapshot(event_id, after=report_ts_utc + timedelta(hours=1))

# Шаг 3: Размер движения линии
line_move_pct = (odds_post_reaction - odds_pre_news) / odds_pre_news * 100

# Шаг 4: Сигнал
# Если мы обнаружили новость РАНЬШЕ, чем рынок отреагировал:
if snapshot_ts_available < report_ts_utc + REACTION_LAG:
    entry_odds = odds_pre_news  # войти по котировке до движения
    direction = "against_injured_team"  # ставка против ослабленной команды

edge_pct = (entry_odds / fair_odds_post_move - 1) * 100

# fair_odds_post_move: reference odds после полной реакции рынка
```

### Риски утечки данных (leakage_risks)

| Риск | Описание | Митигация |
|------|---------|----------|
| **News timestamp leakage** | Использование новости ПОСЛЕ момента имитируемой ставки | `bet_simulated_at_utc` строго < `report_ts_utc` только если новость не использована |
| **Line move leakage** | Использование post-reaction котировки как entry | Entry = pre-news snapshot |
| **Поздний report_ts** | Источник мог датировать новость постфактум | Использовать только `source_quality >= official_vendor` |
| **Result correlation** | Ключевой игрок отсутствует — команда уже проигрывает | Убедиться что отсутствие объявлено до матча, а не в ходе |

### Протокол валидации (validation_protocol)

1. **Event study:** Средняя реакция котировок по событиям шока (event window -6h / +6h)
2. **Power analysis:** Достаточность выборки (≥ 200 событий) для статистических выводов
3. **Временно́й арбитраж:** Медианное время нашей реакции vs медианное время рынка
4. **Walk-forward:** Стратегия только на событиях из test-фолда
5. **Sensitivity analysis:** Порог `source_quality`, минимальный `expected_minutes_proxy`

### Ожидаемые причины провала (expected_failure_modes)

- Наш мониторинг новостей медленнее шарп-игроков (задержка > 30 минут)
- Малая выборка событий (редкое явление) — низкая статистическая мощность
- Эффект зависит от конкретного игрока → нет обобщения
- Источники травм ненадёжны (social_signal, слухи)
- Российские букмекеры блокируют аккаунты, которые ставят быстро по движению

### Готовность к развёртыванию (deployment_readiness)

- [ ] Инфраструктура мониторинга новостей с задержкой < 5 минут
- [ ] ≥ 200 событий «поздняя травма + ключевой игрок» в training data
- [ ] Временно́й арбитраж: нашa реакция < медианной реакции рынка
- [ ] p-value < 0.05 на OOS
- [ ] Paper trading: отдельный трекинг для этой стратегии ≥ 60 дней

---

## H005 — calibrated_probability

### Карточка гипотезы

| Поле | Значение |
|------|---------|
| `hypothesis_id` | `H005` |
| `hypothesis_name` | `calibrated_probability` |
| `status` | `draft` |
| `sport` | Футбол |
| `market` | `h2h`, `totals` |

### Обоснование (rationale)

Вместо использования рыночных котировок как прокси справедливых вероятностей — строим независимую вероятностную модель (Dixon-Coles, двумерная Пуассоновская, или машинное обучение). Если наша модель систематически точнее рынка по определённым типам матчей — возникает край. Комбинируется с overlay H001: edge только если одновременно модель И рынок согласны о недооценённом исходе.

### Необходимые данные (required_data)

- `result_core` — исторические результаты с голами (минимум 5 лет, ≥ 3 000 матчей)
- Статистика команд: xG, голы, PPDA, ожидаемые очки
- `normalized_odds` — для калибровки и оценки края
- Расписание и форма команд (последние N матчей)

### Формула (formula)

```python
# Модель 1: Dixon-Coles (двумерное Пуассоновское распределение)
# lambda_home = exp(attack_h + defense_a + home_advantage)
# lambda_away = exp(attack_a + defense_h)
# P(score_h, score_a) = tau_correction * Poisson(lambda_home) * Poisson(lambda_away)

# Параметры модели: attack[i], defense[i] для каждой команды i
# Коррекция Диксона-Коулса для низких счётов (0:0, 1:0, 0:1, 1:1)

# Шаг 1: Обучение модели на rolling window (252 дня, взвешенная по давности)
model = DixonColesModel(half_life_days=252)
model.fit(train_data)  # использует только данные из train-окна

# Шаг 2: Получение вероятностей
probs = model.predict(home_team, away_team)
# {'home_win': 0.45, 'draw': 0.28, 'away_win': 0.27}

# Шаг 3: Калибровка (Platt scaling / isotonic regression)
calibrated_probs = calibrator.transform(probs)  # обучен на hold-out

# Шаг 4: Value overlay
fair_odds_model = 1 / calibrated_probs["home_win"]
edge_pct = (entry_odds / fair_odds_model - 1) * 100

# Шаг 5: Двойной фильтр (модель + рынок согласны)
combined_signal = (
    edge_pct >= MODEL_EDGE_THRESHOLD and          # модель видит edge
    market_edge_pct >= MARKET_EDGE_THRESHOLD      # H001 тоже видит edge
)
```

**Brier Score целевой:** < 0.22 для h2h футбол (baseline публичных моделей ≈ 0.23–0.24)

### Риски утечки данных (leakage_risks)

| Риск | Описание | Митигация |
|------|---------|----------|
| **Training leakage** | Тренировка модели на данных, содержащих будущее | Rolling window с явной датой отсечки |
| **Calibration leakage** | Калибровщик обучен на тех же данных, что предсказывает | Отдельный calibration hold-out |
| **Feature leakage** | Статистика команд включает пост-матчевые данные | Только pre-match статистика на момент T_decision |
| **xG leakage** | xG пересчитывается задним числом | Использовать live xG (если доступен) или исключить |

### Протокол валидации (validation_protocol)

1. **Model validation:** Brier Score, Log Loss, ROC-AUC на OOS
2. **Calibration plot:** Reliability diagram (10 bins)
3. **Walk-forward P&L:** Стратегия применяется только на test-фолде
4. **Comparison:** vs H001 (market-only) и naive baseline
5. **Двойной overlay:** Separately track combined signal vs each alone
6. **Bootstrap:** Confidence interval для Yield на OOS

### Ожидаемые причины провала (expected_failure_modes)

- Модель не превосходит рыночный консенсус (рынок содержит больше информации)
- Overfitting параметров Dixon-Coles на IS
- Нестабильность модели при добавлении новых команд (новый сезон, повышение/вылет)
- Данные о составах/травмах не обновляются достаточно быстро для rolling window
- Переобучение калибровочного слоя

### Готовность к развёртыванию (deployment_readiness)

- [ ] Brier Score < 0.22 на OOS (минимум 500 матчей)
- [ ] Calibration plot показывает monotone curve
- [ ] Yield > 0 на OOS при combined signal (H005 + H001)
- [ ] Model retrain pipeline автоматизирован (еженедельно)
- [ ] Paper trading ≥ 90 дней с мониторингом деградации

---

## Матрица зависимостей гипотез

| Гипотеза | Зависит от | Усиливает |
|----------|-----------|----------|
| H001 | `normalized_odds` (reference book) | H002, H005 |
| H002 | H001 + тайм-серийные котировки | H001 (CLV-таргетинг) |
| H003 | `normalized_odds` + `result_core` | H001 (bucket-специфичный порог) |
| H004 | `injury_event` + тайм-серия котировок | H001 (entry timing) |
| H005 | `result_core` исторические (модель) | H001 (двойной фильтр) |

---

## Приоритет разработки

1. **H001** — фундаментальная, минимальные требования к данным, быстрая проверка
2. **H003** — простая, опирается на существующие данные Football-Data.co.uk
3. **H002** — требует тайм-серийных котировок (инфраструктурная зависимость)
4. **H005** — высокая ценность, но требует хорошей модели и много данных
5. **H004** — высокий потенциал, но требует инфраструктуры мониторинга новостей

---

*Документ обновляется при изменении статуса гипотезы или появлении новых данных бэктеста.*
