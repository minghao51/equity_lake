"""Tests for the FindingCard model + writer (Phase 1 foundation)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from equity_lake.findings import (
    FindingCard,
    evidence_dir,
    load_finding_cards,
    write_finding_card,
)


def _sample_card(card_id: str = "strategy-comparison") -> FindingCard:
    return FindingCard(
        id=card_id,
        axis="strategy",
        claim="Meta-labeled ensemble beats equal-weight strategies OOS",
        verdict="inconclusive",
        conclusion="No significant difference after costs.",
        metrics={"sharpe": 1.12, "max_drawdown": -0.18},
        evidence_refs=["strategy-comparison/equity.parquet"],
        run_date=date(2026, 8, 4),
        scope={"tickers": 50, "window": "2021-2026", "costs": "realistic"},
    )


class TestFindingCardModel:
    def test_valid_card_roundtrips(self) -> None:
        card = _sample_card()
        parsed = FindingCard.model_validate_json(card.model_dump_json())
        assert parsed == card

    def test_rejects_unknown_axis(self) -> None:
        data = _sample_card().model_dump()
        data["axis"] = "unknown"
        with pytest.raises(ValidationError):
            FindingCard.model_validate(data)

    def test_rejects_unknown_verdict(self) -> None:
        data = _sample_card().model_dump()
        data["verdict"] = "maybe"
        with pytest.raises(ValidationError):
            FindingCard.model_validate(data)

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            FindingCard.model_validate({**_sample_card().model_dump(), "surplus": True})

    def test_negative_verdict_is_valid(self) -> None:
        card = _sample_card().model_copy(update={"verdict": "negative"})
        assert card.verdict == "negative"


class TestWriter:
    def test_write_and_load_roundtrip(self, tmp_path: Path) -> None:
        card = _sample_card()
        path = write_finding_card(card, base=tmp_path)
        assert path == tmp_path / "strategy-comparison.json"
        assert path.exists()

        loaded = load_finding_cards(base=tmp_path)
        assert len(loaded) == 1
        assert loaded[0] == card

    def test_load_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        assert load_finding_cards(base=tmp_path / "nope") == []

    def test_load_skips_invalid_json(self, tmp_path: Path) -> None:
        write_finding_card(_sample_card("good"), base=tmp_path)
        (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
        loaded = load_finding_cards(base=tmp_path)
        assert [c.id for c in loaded] == ["good"]

    def test_evidence_dir_is_created(self, tmp_path: Path) -> None:
        d = evidence_dir("strategy-comparison", base=tmp_path)
        assert d == tmp_path / "strategy-comparison"
        assert d.is_dir()

    def test_evidence_ref_path_aligns_with_evidence_dir(self, tmp_path: Path) -> None:
        card = _sample_card()
        ev = evidence_dir(card.id, base=tmp_path)
        # evidence_refs are relative to <base>/<id>/ — verify the convention holds
        assert all(not Path(ref).is_absolute() for ref in card.evidence_refs)
        assert ev.name == card.id

    def test_write_overwrites_same_id(self, tmp_path: Path) -> None:
        write_finding_card(_sample_card().model_copy(update={"conclusion": "first"}), base=tmp_path)
        write_finding_card(_sample_card().model_copy(update={"conclusion": "second"}), base=tmp_path)
        loaded = load_finding_cards(base=tmp_path)
        assert len(loaded) == 1
        assert loaded[0].conclusion == "second"
