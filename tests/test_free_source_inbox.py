from __future__ import annotations

import json

from src.ingest.free_source_inbox import FreeSourceInboxLoader


def test_free_source_inbox_loads_csv_and_json(tmp_path) -> None:
    inbox = tmp_path / "free_sources"
    inbox.mkdir()
    (inbox / "telegram.csv").write_text(
        "date,home_team,away_team,home_odds,draw_odds,away_odds,source_channel\n"
        "29/05/2026,Arsenal,Chelsea,1.90,3.40,4.20,@odds_channel\n",
        encoding="utf-8",
    )
    (inbox / "discord.json").write_text(
        json.dumps(
            [
                {
                    "event_date": "30/05/2026",
                    "home": "Liverpool",
                    "away": "Everton",
                    "b365h": 1.8,
                    "b365d": 3.6,
                    "b365a": 4.5,
                    "channel": "discord:football",
                }
            ]
        ),
        encoding="utf-8",
    )

    result = FreeSourceInboxLoader().load(inbox)

    assert len(result.dataframe) == 2
    assert len(result.loaded_files) == 2
    assert not result.skipped_files
    assert {"Date", "HomeTeam", "AwayTeam", "B365H", "B365D", "B365A"} <= set(
        result.dataframe.columns
    )


def test_free_source_inbox_reports_bad_files(tmp_path) -> None:
    inbox = tmp_path / "free_sources"
    inbox.mkdir()
    (inbox / "bad.csv").write_text("home,away\nA,B\n", encoding="utf-8")

    result = FreeSourceInboxLoader().load(inbox)

    assert result.dataframe.empty
    assert result.skipped_files


def test_free_source_inbox_parses_telegram_json_export(tmp_path) -> None:
    inbox = tmp_path / "free_sources"
    inbox.mkdir()
    (inbox / "telegram_export.json").write_text(
        json.dumps(
            {
                "name": "Odds channel",
                "messages": [
                    {
                        "id": 17,
                        "date": "2026-05-28T10:00:00",
                        "text": [
                            "29/05/2026\n",
                            "Arsenal vs Chelsea\n",
                            "1X2: 1.90 3.40 4.20",
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = FreeSourceInboxLoader().load(inbox)

    assert len(result.dataframe) == 1
    row = result.dataframe.iloc[0]
    assert row["Date"] == "29/05/2026"
    assert row["HomeTeam"] == "Arsenal"
    assert row["AwayTeam"] == "Chelsea"
    assert row["B365H"] == 1.90
    assert row["B365D"] == 3.40
    assert row["B365A"] == 4.20
    assert row["source_type"] == "message_export"


def test_free_source_inbox_parses_discord_jsonl_export(tmp_path) -> None:
    inbox = tmp_path / "free_sources"
    inbox.mkdir()
    (inbox / "discord.ndjson").write_text(
        json.dumps(
            {
                "id": "msg-1",
                "timestamp": "2026-05-28T12:00:00Z",
                "channel": "discord:football",
                "content": "Liverpool - Everton\n30/05/2026\nodds 1.80 3.60 4.50",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = FreeSourceInboxLoader().load(inbox)

    assert len(result.dataframe) == 1
    row = result.dataframe.iloc[0]
    assert row["HomeTeam"] == "Liverpool"
    assert row["AwayTeam"] == "Everton"
    assert row["source_channel"] == "discord:football"


def test_free_source_inbox_parses_text_blocks(tmp_path) -> None:
    inbox = tmp_path / "free_sources"
    inbox.mkdir()
    (inbox / "events.txt").write_text(
        "29/05/2026\nArsenal vs Chelsea\n1.90 3.40 4.20\n\n"
        "30/05/2026\nLiverpool vs Everton\n1.80 3.60 4.50\n",
        encoding="utf-8",
    )

    result = FreeSourceInboxLoader().load(inbox)

    assert len(result.dataframe) == 2
    assert set(result.dataframe["HomeTeam"]) == {"Arsenal", "Liverpool"}
