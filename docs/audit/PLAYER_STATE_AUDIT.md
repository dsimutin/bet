# PLAYER STATE AUDIT

## Итог по каждому пункту проверки

| # | Вопрос | Статус |
|---|---|---|
| 1 | Откуда реально поступают сведения о травмах? | NOT_IMPLEMENTED — нет реального источника |
| 2 | Есть ли действующий API/feed для травм? | NOT_IMPLEMENTED — apifootball_injuries.py не существует |
| 3 | Запускается ли загрузка автоматически? | NOT_IMPLEMENTED |
| 4 | Где данные сохраняются? | NOT_IMPLEMENTED — путь data/injuries/ есть в CLAUDE.md, но нет ingest |
| 5 | Есть ли schema validation? | IMPLEMENTED — IllnessEvent (Pydantic v2) в illness_features.py |
| 6 | Есть ли timestamp публикации? | IMPLEMENTED — report_ts (UTC, обязательное поле) |
| 7 | Есть ли bet_cutoff_utc? | PARTIALLY — filter_by_cutoff принимает cutoff_ts, но не вызывается |
| 8 | Отбрасываются ли события после cutoff? | IMPLEMENTED (в isolation) — filter_by_cutoff корректен |
| 9 | Превращаются ли события в числовые признаки? | IMPLEMENTED (в isolation) — compute_team_absence_score работает |
| 10 | Влияют ли признаки на вероятность исхода? | NOT_IMPLEMENTED — Dixon-Coles не получает illness features |
| 11 | Попадают ли признаки в football model? | NOT_IMPLEMENTED |
| 12 | Используются ли только как фильтр риска? | NOT_IMPLEMENTED — apply_risk_filters проверяет if illness_features is not None, но они всегда None |
| 13 | Есть ли тесты pre/post-cutoff? | WORKING — test_no_lookahead.py::test_illness_filter_* |
| 14 | Что происходит при отсутствии данных? | Сигнал генерируется нормально (illness_features=None пропускается) |
| 15 | Насколько свежи данные? | N/A — нет источника |
| 16 | Отсутствие информации = здоровый состав? | YES (проблема) — нет警告 при illness_features=None |
| 17 | Защита от повторных сообщений? | IMPLEMENTED (в isolation) — partial_fit в DixonColes |
| 18 | Quality score источника? | IMPLEMENTED (в isolation) — SOURCE_QUALITY_SCORES + min_source_quality |
| 19 | Стартовые составы за X часов до матча? | NOT_IMPLEMENTED |
| 20 | Учёт неопределённости до подтверждения? | NOT_IMPLEMENTED |

## Дополнительные аспекты состояния команды

| Аспект | Статус |
|---|---|
| Травмы | NOT_IMPLEMENTED |
| Дисквалификации | NOT_IMPLEMENTED |
| Подтверждённые стартовые составы | NOT_IMPLEMENTED |
| Отсутствие ключевых игроков | NOT_IMPLEMENTED |
| Восстановление после травмы | NOT_IMPLEMENTED |
| Ротация состава | NOT_IMPLEMENTED |
| Плотность календаря (fatigue) | PARTIALLY_IMPLEMENTED — rest_days.py считает дни между матчами |
| Домашнее / выездное поле | WORKING_AND_INTEGRATED — home_advantage в Dixon-Coles |
| Форма команды | WORKING_AND_INTEGRATED — rolling_form.py, используется как фича |
| Серия матчей | WORKING_AND_INTEGRATED — rolling_form.py |
| Погодные условия | NOT_IMPLEMENTED |
| Текущие новости | NOT_IMPLEMENTED |

## Минимальный безопасный план интеграции

1. Выбрать источник (apifootball.com бесплатный tier — 100 req/day):
   ```
   GET https://v3.football.api-sports.io/injuries?fixture={fixture_id}
   ```
2. Создать `src/ingest/apifootball_injuries.py` с IllnessEvent-валидацией
3. Сохранять в `data/injuries/YYYY-MM-DD_injuries.json`
4. В `src/signals/run_signal_scan.py` перед генерацией сигнала:
   ```python
   injury_builder = IllnessFeatureBuilder()
   events = injury_builder.load_events(injury_path)
   filtered = injury_builder.filter_by_cutoff(events, cutoff_ts=match_kickoff)
   illness_features = injury_builder.compute_team_absence_score_with_cutoff(filtered, team_id, cutoff_ts)
   ```
5. Убедиться, что при отсутствии данных функция явно логирует предупреждение (не молчит)
