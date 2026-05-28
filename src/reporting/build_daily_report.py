"""
Модуль для построения ежедневных Markdown-отчётов по результатам
paper trading и сигналам стратегий.

Отчёт включает:
- Сводку по сигналам дня
- Метрики эффективности стратегий (ROI, Yield, CLV, Drawdown)
- Статус качества данных
- Таблицу статусов гипотез
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

# Версия шаблона отчёта
REPORT_TEMPLATE_VERSION = "v0.1.0"

# Статусы гипотез для отображения в отчёте
HYPOTHESIS_IDS = ["H001", "H002", "H003", "H004", "H005"]


# ---------------------------------------------------------------------------
# Датаклассы конфигурации и метрик
# ---------------------------------------------------------------------------


@dataclass
class DailyReportConfig:
    """
    Конфигурация для построения ежедневного отчёта.

    Атрибуты
    ----------
    report_date : date
        Дата отчёта (по умолчанию — сегодня UTC).
    strategies : list[str]
        Список идентификаторов стратегий, включаемых в отчёт.
    include_signals : bool
        Включать ли раздел с сигналами дня.
    include_performance : bool
        Включать ли раздел с метриками эффективности.
    include_data_quality : bool
        Включать ли раздел с качеством данных.
    """

    report_date: date = field(default_factory=lambda: datetime.now(timezone.utc).date())
    strategies: list[str] = field(default_factory=lambda: ["H001", "H002", "H003", "H004", "H005"])
    include_signals: bool = True
    include_performance: bool = True
    include_data_quality: bool = True


@dataclass
class StrategyPerformance:
    """
    Метрики эффективности одной стратегии.

    Атрибуты
    ----------
    strategy_id : str
        Идентификатор стратегии, например 'H001_v1.0.0'.
    n_bets : int
        Количество ставок в выборке.
    roi : float
        Return on Investment в процентах.
    yield_pct : float
        Yield (прибыль / общая сумма ставок) в процентах.
    clv_mean : float
        Среднее значение Closing Line Value.
    max_drawdown : float
        Максимальная просадка банкролла в процентах.
    win_rate : float
        Доля выигрышных ставок от 0.0 до 1.0.
    sample_note : str
        Примечание о размере выборки (например, 'N<30, предварительно').
    oos_roi : float | None
        Out-of-sample ROI из walk-forward бэктеста (None если недоступен).
    data_quality_notes : list[str]
        Список замечаний по качеству данных для стратегии.
    """

    strategy_id: str
    n_bets: int
    roi: float
    yield_pct: float
    clv_mean: float
    max_drawdown: float
    win_rate: float
    sample_note: str = ""
    oos_roi: float | None = None
    data_quality_notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Основной класс построителя отчёта
# ---------------------------------------------------------------------------


class DailyReportBuilder:
    """
    Строитель ежедневного Markdown-отчёта для аналитики ставок.

    Параметры
    ----------
    config : DailyReportConfig
        Настройки отчёта: дата, стратегии, разделы.
    output_dir : Path
        Директория для сохранения готовых отчётов.
    """

    def __init__(self, config: DailyReportConfig, output_dir: Path) -> None:
        self.config = config
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "DailyReportBuilder инициализирован. Дата: %s, директория: %s",
            config.report_date,
            self.output_dir,
        )

    # ------------------------------------------------------------------
    # Загрузка данных
    # ------------------------------------------------------------------

    def load_signals(self, signals_dir: Path) -> list[dict]:
        """
        Загружает сигналы за сегодня из JSON-файлов в указанной директории.

        Ожидается, что каждый файл содержит либо одиночный словарь сигнала,
        либо список сигналов. Фильтрация по дате отчёта производится по полю
        ``signal_date`` или по дате из ``timestamp_utc``.

        Параметры
        ----------
        signals_dir : Path
            Директория с JSON-файлами сигналов.

        Возвращает
        ----------
        list[dict]
            Список сигналов за дату отчёта.
        """
        signals_dir = Path(signals_dir)
        signals: list[dict] = []
        report_date_str = str(self.config.report_date)

        if not signals_dir.exists():
            logger.warning("Директория сигналов не найдена: %s", signals_dir)
            return signals

        json_files = sorted(signals_dir.glob("*.json"))
        logger.info("Найдено JSON-файлов с сигналами: %d", len(json_files))

        for fpath in json_files:
            try:
                with fpath.open("r", encoding="utf-8") as fh:
                    raw = json.load(fh)

                # Поддержка как одиночного объекта, так и списка
                items: list[dict] = raw if isinstance(raw, list) else [raw]

                for item in items:
                    # Фильтрация: берём только сигналы за дату отчёта
                    ts = item.get("timestamp_utc", "")
                    sig_date = item.get("signal_date", ts[:10] if len(ts) >= 10 else "")
                    if sig_date == report_date_str:
                        signals.append(item)

            except (json.JSONDecodeError, OSError) as exc:
                logger.error("Ошибка чтения файла сигналов %s: %s", fpath, exc)

        logger.info("Загружено сигналов за %s: %d", report_date_str, len(signals))
        return signals

    def load_performance(self, ledger_path: Path) -> dict:
        """
        Загружает метрики эффективности из бумажного журнала (paper ledger).

        Ожидается JSON-файл со структурой:
        ``{"strategies": {strategy_id: {метрики}}, "summary": {...}}``.

        Параметры
        ----------
        ledger_path : Path
            Путь к JSON-файлу с агрегированными метриками журнала.

        Возвращает
        ----------
        dict
            Словарь с метриками по стратегиям и итоговой сводкой.
        """
        ledger_path = Path(ledger_path)
        if not ledger_path.exists():
            logger.warning("Файл журнала не найден: %s", ledger_path)
            return {"strategies": {}, "summary": {}}

        try:
            with ledger_path.open("r", encoding="utf-8") as fh:
                data: dict = json.load(fh)
            logger.info("Метрики журнала загружены из: %s", ledger_path)
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Ошибка чтения журнала %s: %s", ledger_path, exc)
            return {"strategies": {}, "summary": {}}

    def load_data_quality(self, qc_path: Path) -> dict:
        """
        Загружает отчёт о качестве данных (QC-отчёт).

        Ожидается JSON-файл с полями:
        ``{"overall_score": float, "sources": {...}, "issues": [...]}``.

        Параметры
        ----------
        qc_path : Path
            Путь к JSON-файлу QC-отчёта.

        Возвращает
        ----------
        dict
            Словарь с метриками качества данных.
        """
        qc_path = Path(qc_path)
        if not qc_path.exists():
            logger.warning("QC-отчёт не найден: %s", qc_path)
            return {"overall_score": None, "sources": {}, "issues": []}

        try:
            with qc_path.open("r", encoding="utf-8") as fh:
                data: dict = json.load(fh)
            logger.info("QC-отчёт загружен из: %s", qc_path)
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Ошибка чтения QC-отчёта %s: %s", qc_path, exc)
            return {"overall_score": None, "sources": {}, "issues": []}

    # ------------------------------------------------------------------
    # Форматирование разделов
    # ------------------------------------------------------------------

    def _format_signal_section(self, signals: list[dict]) -> str:
        """
        Форматирует раздел сигналов как Markdown-таблицу.

        Столбцы таблицы: Матч, Рынок, Букмекер, Коэфф., Справ. цена, Edge, Уверенность.

        Параметры
        ----------
        signals : list[dict]
            Список словарей сигналов.

        Возвращает
        ----------
        str
            Отформатированный Markdown-раздел.
        """
        if not signals:
            return "_Сигналов за сегодня не обнаружено._\n"

        lines = [
            "| Матч | Рынок | Букмекер | Коэфф. | Справ. цена | Edge | Уверенность |",
            "|------|-------|----------|--------|-------------|------|-------------|",
        ]

        for sig in signals:
            home = sig.get("home_team", "?")
            away = sig.get("away_team", "?")
            match_str = f"{home} vs {away}"
            market = sig.get("market_key", "—")
            book = sig.get("bookmaker", "—")
            entry = sig.get("entry_odds", 0.0)
            ref = sig.get("reference_fair_odds", 0.0)
            edge = sig.get("edge_pct", 0.0)
            conf = sig.get("confidence", 0.0)

            # Форматирование числовых значений
            entry_str = f"{entry:.3f}" if isinstance(entry, float) else str(entry)
            ref_str = f"{ref:.3f}" if isinstance(ref, float) else str(ref)
            edge_str = (
                f"+{edge:.2f}%" if isinstance(edge, (int, float)) and edge >= 0 else f"{edge:.2f}%"
            )
            conf_str = f"{conf:.0%}" if isinstance(conf, float) else str(conf)

            lines.append(
                f"| {match_str} | {market} | {book} | {entry_str} | {ref_str} | {edge_str} | {conf_str} |"
            )

        return "\n".join(lines) + "\n"

    def _format_performance_section(self, perf: dict) -> str:
        """
        Форматирует раздел метрик эффективности стратегий.

        Включает сводные метрики по каждой стратегии из конфига:
        ROI, Yield, CLV, Drawdown, Win Rate, OOS ROI, размер выборки.

        Параметры
        ----------
        perf : dict
            Словарь с ключом 'strategies' → {strategy_id: {метрики}}.

        Возвращает
        ----------
        str
            Отформатированный Markdown-раздел.
        """
        strategies_data: dict[str, Any] = perf.get("strategies", {})

        if not strategies_data:
            return "_Данные о производительности стратегий отсутствуют._\n"

        lines = [
            "| Стратегия | N ставок | ROI | Yield | CLV (ср.) | Drawdown | Win Rate | OOS ROI | Примечание |",
            "|-----------|----------|-----|-------|-----------|----------|----------|---------|------------|",
        ]

        for strat_id in self.config.strategies:
            metrics = strategies_data.get(strat_id, {})
            if not metrics:
                lines.append(f"| {strat_id} | — | — | — | — | — | — | — | _нет данных_ |")
                continue

            n_bets = metrics.get("n_bets", 0)
            roi = metrics.get("roi", 0.0)
            yld = metrics.get("yield_pct", 0.0)
            clv = metrics.get("clv_mean", 0.0)
            dd = metrics.get("max_drawdown", 0.0)
            wr = metrics.get("win_rate", 0.0)
            oos = metrics.get("oos_roi", None)
            note = metrics.get("sample_note", "")

            roi_str = f"{roi:+.1f}%"
            yld_str = f"{yld:+.2f}%"
            clv_str = f"{clv:+.3f}"
            dd_str = f"{dd:.1f}%"
            wr_str = f"{wr:.0%}"
            oos_str = f"{oos:+.1f}%" if oos is not None else "—"

            lines.append(
                f"| {strat_id} | {n_bets} | {roi_str} | {yld_str} | {clv_str} | {dd_str} | {wr_str} | {oos_str} | {note} |"
            )

        # Итоговая сводка по всем стратегиям
        summary = perf.get("summary", {})
        if summary:
            total_bets = summary.get("total_bets", "—")
            total_roi = summary.get("total_roi", None)
            total_roi_str = f"{total_roi:+.1f}%" if isinstance(total_roi, (int, float)) else "—"
            lines.append("")
            lines.append(
                f"**Итого по всем стратегиям:** {total_bets} ставок, ROI = {total_roi_str}"
            )

        return "\n".join(lines) + "\n"

    def _format_hypothesis_status(self) -> str:
        """
        Форматирует таблицу статусов текущей валидации гипотез.

        Статусы: 🔬 Тестируется / ✅ Подтверждена / ❌ Отклонена / ⏸ Приостановлена.

        Возвращает
        ----------
        str
            Отформатированная Markdown-таблица статусов гипотез.
        """
        # Статические описания гипотез (в MVP — хардкод, в будущем из конфига)
        hypothesis_meta: dict[str, dict[str, str]] = {
            "H001": {
                "name": "Отклонение котировок от эффективного рынка",
                "status": "🔬 Тестируется",
                "phase": "Paper trading",
                "n_signals": "—",
            },
            "H002": {
                "name": "Ценность closing line как предиктора",
                "status": "🔬 Тестируется",
                "phase": "Бэктест",
                "n_signals": "—",
            },
            "H003": {
                "name": "Эффект открытия рынка (line movement)",
                "status": "🔬 Тестируется",
                "phase": "Бэктест",
                "n_signals": "—",
            },
            "H004": {
                "name": "Шок новостей о травмах ключевых игроков",
                "status": "🔬 Тестируется",
                "phase": "Paper trading",
                "n_signals": "—",
            },
            "H005": {
                "name": "Арбитраж между букмекерами",
                "status": "⏸ Приостановлена",
                "phase": "—",
                "n_signals": "—",
            },
        }

        lines = [
            "| Гипотеза | Описание | Статус | Фаза | Сигналов |",
            "|----------|---------|--------|------|----------|",
        ]

        for hyp_id in HYPOTHESIS_IDS:
            meta = hypothesis_meta.get(
                hyp_id,
                {
                    "name": "—",
                    "status": "—",
                    "phase": "—",
                    "n_signals": "—",
                },
            )
            lines.append(
                f"| {hyp_id} | {meta['name']} | {meta['status']} | {meta['phase']} | {meta['n_signals']} |"
            )

        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # Сборка полного отчёта
    # ------------------------------------------------------------------

    def build_report(
        self,
        signals: list[dict],
        performance: dict,
        quality: dict,
    ) -> str:
        """
        Генерирует полный Markdown-отчёт из загруженных данных.

        Параметры
        ----------
        signals : list[dict]
            Список сигналов за день.
        performance : dict
            Метрики эффективности стратегий.
        quality : dict
            Показатели качества данных.

        Возвращает
        ----------
        str
            Полный текст отчёта в формате Markdown.
        """
        report_date_str = str(self.config.report_date)
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Топовый сигнал по edge для executive summary
        top_signal_str = "—"
        if signals:
            best = max(signals, key=lambda s: s.get("edge_pct", 0.0))
            home = best.get("home_team", "?")
            away = best.get("away_team", "?")
            edge_val = best.get("edge_pct", 0.0)
            book = best.get("bookmaker", "?")
            top_signal_str = f"{home} vs {away} @ {book} (edge: +{edge_val:.2f}%)"

        # QC-метрики для summary
        qc_score = quality.get("overall_score", None)
        qc_score_str = f"{qc_score:.2f}" if isinstance(qc_score, float) else "н/д"
        qc_issues = quality.get("issues", [])
        qc_issues_count = len(qc_issues)

        sections: list[str] = []

        # ---- Заголовок ----
        sections.append(
            f"# Ежедневный отчёт по ставкам — {report_date_str}\n"
            f"\n"
            f"**Версия шаблона:** {REPORT_TEMPLATE_VERSION}  \n"
            f"**Сгенерировано:** {generated_at}  \n"
            f"**Режим:** Paper Trading (только учебный)\n"
        )

        # ---- Executive summary ----
        sections.append(
            f"## Краткая сводка\n"
            f"\n"
            f"| Показатель | Значение |\n"
            f"|------------|----------|\n"
            f"| Дата отчёта | {report_date_str} |\n"
            f"| Сигналов за день | {len(signals)} |\n"
            f"| Лучшая возможность | {top_signal_str} |\n"
            f"| QC-оценка данных | {qc_score_str} |\n"
            f"| Проблем с качеством | {qc_issues_count} |\n"
            f"| Стратегий в отчёте | {len(self.config.strategies)} |\n"
        )

        # ---- Раздел сигналов ----
        if self.config.include_signals:
            sections.append(
                f"## Сигналы за {report_date_str}\n" f"\n" + self._format_signal_section(signals)
            )

        # ---- Раздел производительности ----
        if self.config.include_performance:
            sections.append(
                "## Сводка по эффективности стратегий\n"
                "\n"
                "> Метрики рассчитаны по paper trading журналу. "
                "Малые выборки (N<30) имеют низкую статистическую значимость.\n"
                "\n" + self._format_performance_section(performance)
            )

        # ---- Раздел качества данных ----
        if self.config.include_data_quality:
            sources_info = quality.get("sources", {})
            sources_lines = []
            for src_name, src_data in sources_info.items():
                src_score = src_data.get("score", "—")
                src_records = src_data.get("records", "—")
                sources_lines.append(f"- **{src_name}**: score={src_score}, записей={src_records}")

            sources_block = (
                "\n".join(sources_lines) if sources_lines else "_Нет данных об источниках._"
            )
            issues_block = (
                "\n".join(f"- ⚠️ {issue}" for issue in qc_issues)
                if qc_issues
                else "_Проблем не обнаружено._"
            )

            sections.append(
                f"## Статус качества данных\n"
                f"\n"
                f"**Итоговый QC-score:** {qc_score_str}\n"
                f"\n"
                f"### Источники\n"
                f"\n"
                f"{sources_block}\n"
                f"\n"
                f"### Выявленные проблемы\n"
                f"\n"
                f"{issues_block}\n"
            )

        # ---- Таблица статусов гипотез ----
        sections.append("## Статус валидации гипотез\n" "\n" + self._format_hypothesis_status())

        # ---- Подвал с дисклеймером ----
        sections.append(
            "---\n"
            "\n"
            "> **Дисклеймер:** Только paper trading. "
            "Не является инвестиционной рекомендацией.  \n"
            "> Все сигналы генерируются исключительно в аналитических и образовательных целях.  \n"
            "> Пользователь самостоятельно несёт ответственность за любые решения о реальных ставках.\n"
        )

        return "\n".join(sections)

    # ------------------------------------------------------------------
    # Сохранение и полный пайплайн
    # ------------------------------------------------------------------

    def save_report(self, content: str, report_date: str) -> Path:
        """
        Сохраняет готовый Markdown-отчёт в директорию data/reports/.

        Имя файла: ``daily_report_{report_date}.md``.

        Параметры
        ----------
        content : str
            Текст отчёта в формате Markdown.
        report_date : str
            Дата в формате YYYY-MM-DD (используется в имени файла).

        Возвращает
        ----------
        Path
            Абсолютный путь к сохранённому файлу.
        """
        filename = f"daily_report_{report_date}.md"
        output_path = self.output_dir / filename

        with output_path.open("w", encoding="utf-8") as fh:
            fh.write(content)

        logger.info("Отчёт сохранён: %s (%d символов)", output_path, len(content))
        return output_path

    def run(self, signals_dir: Path, ledger_path: Path) -> Path:
        """
        Запускает полный пайплайн построения отчёта.

        Шаги:
        1. Загрузка сигналов из signals_dir
        2. Загрузка метрик производительности из ledger_path
        3. Загрузка QC-отчёта (ищется рядом с ledger_path)
        4. Построение Markdown-отчёта
        5. Сохранение в output_dir

        Параметры
        ----------
        signals_dir : Path
            Директория с JSON-файлами сигналов.
        ledger_path : Path
            Путь к файлу метрик бумажного журнала.

        Возвращает
        ----------
        Path
            Путь к сохранённому файлу отчёта.
        """
        logger.info(
            "Запуск пайплайна отчёта за %s...",
            self.config.report_date,
        )

        # Шаг 1: загрузка сигналов
        signals = self.load_signals(signals_dir)

        # Шаг 2: загрузка метрик производительности
        performance = self.load_performance(ledger_path)

        # Шаг 3: поиск и загрузка QC-отчёта (рядом с ledger или в staging)
        qc_path = Path(ledger_path).parent / "qc_report.json"
        quality = self.load_data_quality(qc_path)

        # Шаг 4: построение отчёта
        report_content = self.build_report(signals, performance, quality)

        # Шаг 5: сохранение
        report_date_str = str(self.config.report_date)
        output_path = self.save_report(report_content, report_date_str)

        logger.info("Пайплайн отчёта завершён. Файл: %s", output_path)
        return output_path
