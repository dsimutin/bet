"""
Модуль отправки уведомлений о сигналах через Telegram Bot API.

Поддерживает режим dry_run: в этом режиме сообщения НЕ отправляются в Telegram,
а только логируются в stdout и сохраняются в JSON-файл.

ВАЖНО:
    JSON-payload сохраняется ВСЕГДА — как в реальном режиме, так и в dry_run.
    Это гарантирует аудит всех отправленных/подготовленных сообщений.
"""

from __future__ import annotations

import json
import logging
from html import escape
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

# Базовый URL Telegram Bot API
TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"

# Обязательные поля сигнала для валидации перед отправкой
REQUIRED_SIGNAL_FIELDS: frozenset[str] = frozenset(
    {
        "strategy_id",
        "home_team",
        "away_team",
        "bookmaker",
        "market_key",
        "entry_odds",
        "reference_fair_odds",
        "edge_pct",
        "timestamp_utc",
    }
)


# ---------------------------------------------------------------------------
# Pydantic-модели конфигурации и сообщения
# ---------------------------------------------------------------------------


class TelegramConfig(BaseModel):
    """
    Конфигурация Telegram-бота для отправки уведомлений.

    Атрибуты
    ----------
    bot_token : str
        Токен Telegram Bot API (получить у @BotFather).
    chat_id : str
        Идентификатор чата или канала для отправки сообщений.
    dry_run : bool
        Если True — сообщения не отправляются в Telegram, только логируются.
        По умолчанию True (безопасный режим).
    max_message_length : int
        Максимальная длина одного сообщения (лимит Telegram — 4096 символов).
    """

    bot_token: str = Field(..., description="Токен Telegram Bot API")
    chat_id: str = Field(..., description="ID чата или канала получателя")
    dry_run: bool = Field(True, description="Режим без реальной отправки (только лог)")
    max_message_length: int = Field(
        4096,
        ge=1,
        le=4096,
        description="Максимальная длина сообщения (лимит Telegram API)",
    )


class SignalMessage(BaseModel):
    """
    Модель сообщения о торговом сигнале для отправки в Telegram.

    Содержит все поля исходного сигнала плюс отформатированный текст сообщения.

    Атрибуты
    ----------
    signal_id : str
        Уникальный идентификатор сигнала.
    strategy_id : str
        Идентификатор стратегии.
    home_team : str
        Название домашней команды.
    away_team : str
        Название гостевой команды.
    bookmaker : str
        Идентификатор букмекера.
    market_key : str
        Ключ рынка (например, h2h, totals).
    selection_ru : str
        Исход на русском языке.
    entry_odds : float
        Коэффициент входа.
    reference_fair_odds : float
        Справедливая (референсная) цена без маржи.
    edge_pct : float
        Edge в процентах.
    explain_formatted : str
        Форматированный комментарий о сигнале.
    timestamp_utc : str
        Временная метка генерации сигнала (UTC, ISO-формат).
    formatted_message : str
        Готовый текст сообщения для Telegram.
    """

    signal_id: str = Field("", description="Идентификатор сигнала")
    strategy_id: str = Field(..., description="Идентификатор стратегии")
    home_team: str = Field(..., description="Домашняя команда")
    away_team: str = Field(..., description="Гостевая команда")
    bookmaker: str = Field(..., description="Букмекер")
    market_key: str = Field(..., description="Ключ рынка")
    selection_ru: str = Field("", description="Исход (на русском)")
    entry_odds: float = Field(..., gt=1.0, description="Коэффициент входа")
    reference_fair_odds: float = Field(..., gt=1.0, description="Справедливый коэффициент")
    edge_pct: float = Field(..., description="Edge в процентах")
    explain_formatted: str = Field("", description="Форматированный комментарий к сигналу")
    timestamp_utc: str = Field(..., description="Временная метка UTC (ISO)")
    formatted_message: str = Field("", description="Готовый текст для Telegram")


# ---------------------------------------------------------------------------
# Основной класс отправщика
# ---------------------------------------------------------------------------


class TelegramSender:
    """
    Отправщик уведомлений о сигналах в Telegram.

    В режиме dry_run=True:
        - Сообщение НЕ отправляется в Telegram API.
        - Текст логируется в stdout через logging.
        - JSON-payload сохраняется на диск.

    В режиме dry_run=False:
        - Выполняется реальный HTTP-запрос к Telegram Bot API.
        - JSON-payload также сохраняется на диск.

    Параметры
    ----------
    config : TelegramConfig
        Конфигурация бота (токен, chat_id, dry_run).
    """

    # Шаблон сообщения о сигнале
    _SIGNAL_TEMPLATE = (
        "🟡 PAPER SIGNAL\n"
        "Стратегия: {strategy_id}\n"
        "Матч: {home_team} vs {away_team}\n"
        "Букмекер: {bookmaker}\n"
        "Рынок: {market_key} / {selection_ru}\n"
        "Коэфф.: {entry_odds}\n"
        "Справедливая цена: {reference_fair_odds}\n"
        "Edge: +{edge_pct:.2f}%\n"
        "Paper stake: {paper_stake_units:.2f}u\n"
        "Комментарий: {explain_formatted}\n"
        "Статус: только paper trading\n"
        "🕐 {timestamp_utc}"
    )

    def __init__(self, config: TelegramConfig) -> None:
        self.config = config
        logger.info(
            "TelegramSender инициализирован. dry_run=%s, chat_id=%s",
            config.dry_run,
            config.chat_id,
        )

    # ------------------------------------------------------------------
    # Форматирование сообщений
    # ------------------------------------------------------------------

    def format_signal_message(self, signal: dict) -> str:
        """
        Форматирует словарь сигнала в текст Telegram-сообщения по шаблону.

        Шаблон (строго по спецификации):
            🟡 PAPER SIGNAL
            Стратегия: {strategy_id}
            Матч: {home_team} vs {away_team}
            Букмекер: {bookmaker}
            Рынок: {market_key} / {selection_ru}
            Коэфф.: {entry_odds}
            Справедливая цена: {reference_fair_odds}
            Edge: +{edge_pct:.2f}%
            Комментарий: {explain_formatted}
            Статус: только paper trading
            🕐 {timestamp_utc}

        Параметры
        ----------
        signal : dict
            Словарь с данными сигнала.

        Возвращает
        ----------
        str
            Отформатированный текст сообщения.
        """
        strategy_id = signal.get("strategy_id", "—")
        home_team = signal.get("home_team", "—")
        away_team = signal.get("away_team", "—")
        bookmaker = signal.get("bookmaker", "—")
        market_key = signal.get("market_key", "—")
        selection_ru = signal.get("selection_ru", signal.get("selection", "—"))
        entry_odds = signal.get("entry_odds", 0.0)
        reference_fair_odds = signal.get("reference_fair_odds", 0.0)
        edge_pct = signal.get("edge_pct", 0.0)
        paper_stake_units = float(signal.get("paper_stake_units", signal.get("stake_units", 1.0)))
        explain_formatted = signal.get("explain_formatted", signal.get("comment", "—"))
        timestamp_utc = signal.get("timestamp_utc", "—")

        # Форматируем коэффициенты с тремя знаками после запятой
        entry_odds_str = f"{entry_odds:.3f}" if isinstance(entry_odds, float) else str(entry_odds)
        ref_odds_str = (
            f"{reference_fair_odds:.3f}"
            if isinstance(reference_fair_odds, float)
            else str(reference_fair_odds)
        )
        edge_pct_val = float(edge_pct) if not isinstance(edge_pct, float) else edge_pct

        text = self._SIGNAL_TEMPLATE.format(
            strategy_id=_html_text(strategy_id),
            home_team=_html_text(home_team),
            away_team=_html_text(away_team),
            bookmaker=_html_text(bookmaker),
            market_key=_html_text(market_key),
            selection_ru=_html_text(selection_ru),
            entry_odds=entry_odds_str,
            reference_fair_odds=ref_odds_str,
            edge_pct=edge_pct_val,
            paper_stake_units=paper_stake_units,
            explain_formatted=_html_text(explain_formatted),
            timestamp_utc=_html_text(timestamp_utc),
        )

        # Обрезаем до максимальной длины сообщения
        if len(text) > self.config.max_message_length:
            logger.warning(
                "Сообщение обрезано: %d → %d символов",
                len(text),
                self.config.max_message_length,
            )
            text = text[: self.config.max_message_length]

        return text

    # ------------------------------------------------------------------
    # Отправка сообщений
    # ------------------------------------------------------------------

    def send_message(self, text: str) -> dict:
        """
        Отправляет текстовое сообщение в Telegram.

        В режиме dry_run:
            - Сообщение логируется в stdout.
            - Возвращается словарь с dry_run=True и текстом сообщения.

        В режиме реальной отправки:
            - Выполняется HTTP POST к Telegram Bot API.
            - Возвращается ответ API.

        Параметры
        ----------
        text : str
            Текст сообщения (до max_message_length символов).

        Возвращает
        ----------
        dict
            Результат операции: ответ API или dry_run-заглушка.
        """
        if self.config.dry_run:
            # Режим dry_run: только лог, без реального запроса
            logger.info(
                "[DRY RUN] Telegram сообщение (chat_id=%s):\n%s",
                self.config.chat_id,
                text,
            )
            return {
                "dry_run": True,
                "ok": True,
                "chat_id": self.config.chat_id,
                "text": text,
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }

        # Реальная отправка через Telegram Bot API
        url = TELEGRAM_API_BASE.format(token=self.config.bot_token)
        payload = {
            "chat_id": self.config.chat_id,
            "text": text,
            "parse_mode": "HTML",
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                response_body = resp.read().decode("utf-8")
                result: dict = json.loads(response_body)
                logger.info(
                    "Telegram сообщение отправлено в chat_id=%s, ok=%s",
                    self.config.chat_id,
                    result.get("ok"),
                )
                return result
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            logger.error("Ошибка отправки Telegram-сообщения: %s", exc)
            return {"ok": False, "error": str(exc)}

    def send_signal(self, signal: dict) -> dict:
        """
        Форматирует и отправляет сигнал в Telegram.

        Шаги:
        1. Валидация обязательных полей сигнала.
        2. Форматирование сообщения по шаблону.
        3. Отправка (или dry_run лог).

        Параметры
        ----------
        signal : dict
            Словарь с данными сигнала.

        Возвращает
        ----------
        dict
            Результат отправки (ответ API или dry_run-заглушка).
        """
        if not self._validate_signal_before_send(signal):
            logger.error(
                "Сигнал не прошёл валидацию — отправка отменена. signal_id=%s",
                signal.get("signal_id", "N/A"),
            )
            return {"ok": False, "error": "Сигнал не прошёл валидацию обязательных полей"}

        text = self.format_signal_message(signal)
        logger.debug(
            "Отправка сигнала signal_id=%s, strategy=%s",
            signal.get("signal_id", "N/A"),
            signal.get("strategy_id", "—"),
        )
        return self.send_message(text)

    def send_daily_summary(self, summary: dict) -> dict:
        """
        Отправляет ежедневную сводку в Telegram.

        Формат сообщения:
            📊 Ежедневная сводка — {date}
            Сигналов за день: {n_signals}
            Лучший edge: {top_edge}
            Стратегий активно: {n_strategies}
            Статус: только paper trading

        Параметры
        ----------
        summary : dict
            Словарь с ключами: date, n_signals, top_edge, n_strategies, notes.

        Возвращает
        ----------
        dict
            Результат отправки.
        """
        report_date = summary.get("date", "—")
        n_signals = summary.get("n_signals", 0)
        top_edge = summary.get("top_edge", 0.0)
        n_strategies = summary.get("n_strategies", 0)
        notes = summary.get("notes", "")

        top_edge_str = f"+{top_edge:.2f}%" if isinstance(top_edge, (int, float)) else str(top_edge)

        lines = [
            f"📊 Ежедневная сводка — {report_date}",
            f"Сигналов за день: {n_signals}",
            f"Лучший edge: {top_edge_str}",
            f"Стратегий активно: {n_strategies}",
        ]
        if notes:
            lines.append(f"Примечание: {notes}")
        lines.append("Статус: только paper trading")

        text = "\n".join(lines)

        # Обрезаем при необходимости
        if len(text) > self.config.max_message_length:
            text = text[: self.config.max_message_length]

        logger.info(
            "Отправка ежедневной сводки за %s (сигналов: %d)",
            report_date,
            n_signals,
        )
        return self.send_message(text)

    # ------------------------------------------------------------------
    # Сохранение payload и валидация
    # ------------------------------------------------------------------

    def save_payload(self, payload: dict, output_dir: Path) -> Path:
        """
        Сохраняет payload сообщения в JSON-файл.

        Файл сохраняется ВСЕГДА — как в режиме dry_run, так и при реальной отправке.
        Имя файла: ``tg_payload_{timestamp}.json``.

        Параметры
        ----------
        payload : dict
            Словарь с данными payload (сигнал или сводка).
        output_dir : Path
            Директория для сохранения файлов.

        Возвращает
        ----------
        Path
            Абсолютный путь к сохранённому JSON-файлу.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Временная метка с точностью до микросекунд для уникальности имени
        ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        filename = f"tg_payload_{ts_str}.json"
        output_path = output_dir / filename

        # Добавляем метаданные сохранения
        save_record = {
            "saved_at_utc": datetime.now(timezone.utc).isoformat(),
            "dry_run": self.config.dry_run,
            "chat_id": self.config.chat_id,
            "payload": payload,
        }

        with output_path.open("w", encoding="utf-8") as fh:
            json.dump(save_record, fh, ensure_ascii=False, indent=2, default=str)

        logger.info("Payload сохранён: %s", output_path)
        return output_path

    def _validate_signal_before_send(self, signal: dict) -> bool:
        """
        Проверяет наличие всех обязательных полей в сигнале перед отправкой.

        Обязательные поля (из REQUIRED_SIGNAL_FIELDS):
            strategy_id, home_team, away_team, bookmaker, market_key,
            entry_odds, reference_fair_odds, edge_pct, timestamp_utc.

        Параметры
        ----------
        signal : dict
            Словарь с данными сигнала.

        Возвращает
        ----------
        bool
            True, если все обязательные поля присутствуют и не пусты.
        """
        missing: list[str] = []

        for field_name in REQUIRED_SIGNAL_FIELDS:
            value = signal.get(field_name)
            # Поле отсутствует или содержит пустое значение
            if value is None or value == "" or value == []:
                missing.append(field_name)

        if missing:
            logger.warning(
                "Сигнал signal_id=%s: отсутствуют обязательные поля: %s",
                signal.get("signal_id", "N/A"),
                missing,
            )
            return False

        # Дополнительная проверка: коэффициенты должны быть > 1.0
        entry_odds = signal.get("entry_odds", 0)
        ref_odds = signal.get("reference_fair_odds", 0)

        try:
            if float(entry_odds) <= 1.0:
                logger.warning(
                    "Сигнал signal_id=%s: entry_odds=%s <= 1.0 — некорректный коэффициент",
                    signal.get("signal_id", "N/A"),
                    entry_odds,
                )
                return False
            if float(ref_odds) <= 1.0:
                logger.warning(
                    "Сигнал signal_id=%s: reference_fair_odds=%s <= 1.0 — некорректный коэффициент",
                    signal.get("signal_id", "N/A"),
                    ref_odds,
                )
                return False
        except (TypeError, ValueError) as exc:
            logger.warning(
                "Сигнал signal_id=%s: ошибка валидации коэффициентов: %s",
                signal.get("signal_id", "N/A"),
                exc,
            )
            return False

        return True


def _html_text(value: Any) -> str:
    return escape(str(value), quote=False)
