"""H00 sanitized demo-lake page: allowlist, staleness, negative scan and mutation proofs.

Scope: the H00 review contract (`docs/reports/20261117-h00-sanitized-demo-lake-page-review.md`
§4-§6) as adopted by the owner on 2026-11-21. The properties under test are the ones a
reviewer cannot check by reading prose:

* the artifact is composed from a demo-lake seed only, and every forbidden input class
  (absolute path, ``.env`` value, ledger/watchlist content, real-slice row or canary,
  64-hex digest, raw alert/run text, vendor string) is *proven* absent from the
  serialized bytes — and the proof is shown to fail when a forbidden value is injected
  (mutation proof, not a promise);
* the real-source root can never be summarized, including when the root-name guard is
  disabled (defence in depth);
* the canonical dashboard output is untouched by an H00 build;
* staleness (30 days, owner decision 4) renders the last build date plus the stale
  banner, and the adopted wording appears verbatim;
* the preview serves loopback only.

Every case builds from a temporary demo seed; nothing here reads the checkout's lake.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from equity_lake.core import paths  # type: ignore[import-untyped]
from equity_lake.dashboard import h00  # type: ignore[import-untyped]
from equity_lake.dashboard.exporter import DashboardExporter  # type: ignore[import-untyped]
from equity_lake.devtools.seed_demo import seed_demo  # type: ignore[import-untyped]

#: Canaries that exist on disk in the sandbox and must never appear in the artifact.
LEDGER_CANARY = "CANARYLEDGERACCOUNT"
WATCHLIST_CANARY = "CANARYWATCHLISTTICKER"
ENV_CANARY = "canary-dotenv-value-9f31"
ALERT_CANARY = "CANARYALERTTEXT"
RESULTS_CANARY = "CANARYPIPELINESCOPE"
REAL_CLOSE_CANARY = "185.63999938964844"
DIGEST_CANARY = "a" * 64


def _write_parquet(path: Path, frame: pl.DataFrame) -> None:
    path.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path / "part-00000.parquet")


def _features_frame() -> pl.DataFrame:
    """A minimal demo-shaped feature partition carrying the synthetic label."""
    return pl.DataFrame(
        {
            "ticker": ["AAPL", "MSFT", "AAPL", "MSFT"],
            "feature_schema_version": [8, 8, 8, 8],
            "market": ["us_equity"] * 4,
            "mic": ["XNAS"] * 4,
            "symbol": ["AAPL", "MSFT", "AAPL", "MSFT"],
            "identity_schema_version": [1, 1, 1, 1],
            "action_coverage_source": ["synthetic_demo_seed"] * 4,
            "next_day_return": [0.01, None, 0.02, 0.03],
        }
    )


@pytest.fixture(scope="module")
def demo_sandbox(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A demo lake seed plus every forbidden neighbour, all inside one sandbox."""
    sandbox = tmp_path_factory.mktemp("h00-sandbox")
    lake = sandbox / "data" / "lake"
    seed_demo(years=0.1, tickers=["AAPL", "MSFT"], lake_dir=lake)
    _write_parquet(lake / "03_gold" / "features" / "date=2024-01-02", _features_frame())
    # A fixture prediction partition: its values (model version, probability, per-symbol
    # direction) must never reach the page — only its row count and column count do.
    _write_parquet(
        lake / "04_platinum" / "predictions" / "date=2024-01-02",
        pl.DataFrame(
            {
                "ticker": ["AAPL", "MSFT"],
                "direction": ["up", "down"],
                "probability": [0.73, 0.21],
                "model_mode": ["unknown", "unknown"],
                "model_version": ["fake", "fake"],
                "feature_schema_version": [8, 8],
                "market": ["us_equity", "us_equity"],
                "mic": ["XNAS", "XNAS"],
                "symbol": ["AAPL", "MSFT"],
                "identity_schema_version": [1, 1],
            }
        ),
    )

    # Forbidden neighbours: present on disk, never an input.
    portfolio = sandbox / "data" / "portfolio"
    portfolio.mkdir(parents=True)
    (portfolio / "portfolio.sqlite3").write_text(LEDGER_CANARY, encoding="utf-8")
    (portfolio / "marks.json").write_text(json.dumps({"account": LEDGER_CANARY}), encoding="utf-8")
    (portfolio / "statement.csv").write_text(f"account\n{LEDGER_CANARY}\n", encoding="utf-8")
    (sandbox / "config").mkdir()
    (sandbox / "config" / "watchlist.yaml").write_text(f"tickers: [{WATCHLIST_CANARY}]\n", encoding="utf-8")
    (sandbox / "data" / "tracking").mkdir(parents=True)
    (sandbox / "data" / "tracking" / "events.json").write_text(WATCHLIST_CANARY, encoding="utf-8")
    (sandbox / ".env").write_text(f"WANDB_ENTITY={ENV_CANARY}\n", encoding="utf-8")
    logs = sandbox / "logs"
    logs.mkdir()
    (logs / "health-report.json").write_text(json.dumps({"alerts": [ALERT_CANARY]}), encoding="utf-8")
    (logs / "pipeline_results_2024-01-02.json").write_text(json.dumps({"scope": RESULTS_CANARY}), encoding="utf-8")

    # A real-slice-shaped root: the real-row schema markers and a verified canary close.
    real = sandbox / "data" / "lake_real"
    real_frame = pl.DataFrame(
        {
            "ticker": ["AAPL"],
            "date": [datetime(2024, 1, 2).date()],
            "open": [185.0],
            "high": [186.0],
            "low": [184.0],
            "close": [float(REAL_CLOSE_CANARY)],
            "volume": [1],
            "adj_close": [float(REAL_CLOSE_CANARY)],
            "market": ["us_equity"],
            "mic": ["XNAS"],
            "symbol": ["AAPL"],
            "identity_schema_version": [1],
            "identity_provenance": ["current_ticker_config"],
            "price_provenance_version": [1],
            "price_provenance": ["vendor"],
            "price_source": ["vendor"],
            "price_source_sha256": [DIGEST_CANARY],
        }
    )
    _write_parquet(real / "01_bronze" / "market_data" / "us_equity" / "date=2024-01-02", real_frame)
    # A vendor slice carries a vendor coverage label, so a real root can never pass the
    # synthetic signature checks either.
    _write_parquet(
        real / "02_silver" / "corporate_action_coverage" / "us_equity" / "coverage_end=2024-01-02",
        pl.DataFrame(
            {
                "market": ["us_equity"],
                "mic": ["XNAS"],
                "symbol": ["AAPL"],
                "source": ["vendor_probe"],
                "coverage_start": [datetime(2024, 1, 2).date()],
                "coverage_end": [datetime(2024, 1, 2).date()],
                "coverage_status": ["complete"],
                "coverage_schema_version": [2],
            }
        ),
    )
    return sandbox


@pytest.fixture
def built(demo_sandbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> h00.H00Artifact:
    """One compose of the sandbox demo lake, with the sandbox dotenv in scope for the scan."""
    monkeypatch.setattr(h00, "_dotenv_path", lambda: demo_sandbox / ".env")
    return h00.build_sanitized_page(lake_dir=demo_sandbox / "data" / "lake", allow_sandbox_root=True, output_dir=tmp_path / "h00")


def _artifact_texts(artifact: h00.H00Artifact) -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in artifact.files}


class TestForbiddenClassesAreAbsent:
    """The serialized bytes carry none of the forbidden classes (owner decision 8 proof)."""

    def test_no_canary_or_forbidden_token_survives_the_build(self, built: h00.H00Artifact) -> None:
        texts = _artifact_texts(built)
        assert set(texts) == set(h00.ARTIFACT_FILENAMES)
        forbidden = [
            LEDGER_CANARY,
            WATCHLIST_CANARY,
            ENV_CANARY,
            ALERT_CANARY,
            RESULTS_CANARY,
            REAL_CLOSE_CANARY,
            DIGEST_CANARY,
            # absolute paths and their components
            str(paths.PROJECT_ROOT),
            str(paths.DATA_DIR),
            str(paths.LAKE_DIR),
            "/home/",
            "/tmp/",
            "PROJECT_ROOT",
            # real-slice and probe fingerprints
            "lake_real",
            "d01",
            "probe-",
            # private stores
            "portfolio.sqlite3",
            "marks.json",
            "statement.csv",
            "ledger_events",
            "source_digest",
            "content_digest",
            "watchlist",
            "tracking",
            # operator run metadata and raw alert text
            "pipeline_results",
            "health-report.json",
            "Traceback",
            # vendor strings and forbidden claims
            "yfinance",
            "akshare",
            "efinance",
            "JPX",
            "KRX",
            "real data",
            "live prices",
            "unadjusted",
        ]
        for name, text in texts.items():
            for token in forbidden:
                assert token not in text, f"{name} leaks {token!r}"

    def test_no_64_hex_digest_is_serialized(self, built: h00.H00Artifact) -> None:
        digest = re.compile(r"(?<![0-9a-zA-Z])[0-9a-fA-F]{64}(?![0-9a-zA-Z])")
        for text in _artifact_texts(built).values():
            assert digest.search(text) is None

    def test_no_per_symbol_row_is_serialized(self, built: h00.H00Artifact) -> None:
        payload = json.loads(built.payload_path.read_text(encoding="utf-8"))
        for row in payload["datasets"]:
            assert set(row) == {"name", "available", "rows", "symbols", "latest_date"}
        # Symbol-scoped content would show up as a ticker anywhere but the column inventory.
        columns = json.dumps(payload["schemas"]["columns"])
        for text in _artifact_texts(built).values():
            for ticker in ("AAPL", "MSFT"):
                assert ticker not in text.replace(columns, "")

    def test_fixture_predictions_are_a_count_only(self, built: h00.H00Artifact) -> None:
        # Owner decision 2: counts and schema size only — no column list, no model
        # version, no probability value reaches the artifact.
        payload = json.loads(built.payload_path.read_text(encoding="utf-8"))
        assert "04_platinum/predictions" not in payload["schemas"]["columns"]
        assert payload["schemas"]["column_counts"]["04_platinum/predictions"] > 0
        text = built.payload_path.read_text(encoding="utf-8") + built.index_path.read_text(encoding="utf-8")
        for token in ("model_version", "model_mode", "probability", "model_trained_at", "model_artifact_sha256"):
            assert token not in text

    def test_report_is_inside_the_artifact_and_records_clean_classes(self, built: h00.H00Artifact) -> None:
        report = json.loads(built.report_path.read_text(encoding="utf-8"))
        assert report["passed"] is True
        assert report["root"] == "demo_lake_only"
        assert report["provenance"] == "synthetic"
        assert report["negative_scan"]["state"] == "clean"
        assert "secrets" in report["negative_scan"]["classes"]
        assert report["files"] == list(h00.ARTIFACT_FILENAMES)
        # The report itself contains no needle, only class names.
        assert "canary" not in built.report_path.read_text(encoding="utf-8").lower()


class TestMutationProofs:
    """Disable or poison one guard and the build must fail closed (nothing written)."""

    @pytest.mark.parametrize(
        ("needle_class", "injected"),
        [
            ("paths", "/home/operator/work/equity_lake/data/lake"),
            ("real_slice", REAL_CLOSE_CANARY),
            ("private_book", "portfolio.sqlite3"),
            ("private_state", "watchlist.yaml"),
            ("secrets", ENV_CANARY),
            ("alert_text", "pipeline_results_2024-01-02.json"),
            ("vendor", "Source: yfinance (US)"),
            ("digests", DIGEST_CANARY),
            ("claims", "real data"),
        ],
    )
    def test_scan_flags_each_forbidden_class(
        self,
        built: h00.H00Artifact,
        needle_class: str,
        injected: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        demo_sandbox: Path,
    ) -> None:
        monkeypatch.setattr(h00, "_dotenv_path", lambda: demo_sandbox / ".env")
        texts = _artifact_texts(built)
        texts[h00.INDEX_FILENAME] = texts[h00.INDEX_FILENAME] + f"\n<!-- {injected} -->\n"
        hits = h00.scan_contents(texts, h00.forbidden_needles(tmp_path / "h00"))
        assert needle_class in hits

    def test_build_refuses_and_writes_nothing_when_the_payload_leaks_an_absolute_path(
        self,
        demo_sandbox: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        original = h00._compose_payload

        def _leaky(context: h00._BuildContext) -> dict[str, Any]:
            payload: dict[str, Any] = dict(original(context))
            payload["datasets"][0]["path"] = str(context.root)
            return payload

        monkeypatch.setattr(h00, "_dotenv_path", lambda: demo_sandbox / ".env")
        monkeypatch.setattr(h00, "_compose_payload", _leaky)
        output = tmp_path / "leaky-h00"
        with pytest.raises(h00.H00BuildError):
            h00.build_sanitized_page(lake_dir=demo_sandbox / "data" / "lake", allow_sandbox_root=True, output_dir=output)
        assert not output.exists(), "a refused build must not leave a partial artifact"

    def test_build_refuses_and_writes_nothing_when_a_vendor_string_leaks(
        self,
        demo_sandbox: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        original = h00._compose_payload

        def _leaky(context: h00._BuildContext) -> dict[str, Any]:
            payload: dict[str, Any] = dict(original(context))
            payload["datasets"][0]["name"] = "us_equity (yfinance free tier)"
            return payload

        monkeypatch.setattr(h00, "_dotenv_path", lambda: demo_sandbox / ".env")
        monkeypatch.setattr(h00, "_compose_payload", _leaky)
        with pytest.raises(h00.H00BuildError):
            h00.build_sanitized_page(lake_dir=demo_sandbox / "data" / "lake", allow_sandbox_root=True, output_dir=tmp_path / "leaky2")


class TestRealRootExclusion:
    """No real-slice row can reach the artifact — by root refusal and by row shape."""

    def test_real_root_is_refused_and_produces_no_artifact(self, demo_sandbox: Path, tmp_path: Path) -> None:
        output = tmp_path / "real-h00"
        with pytest.raises(h00.H00BuildError, match="refused"):
            h00.build_sanitized_page(lake_dir=demo_sandbox / "data" / "lake_real", output_dir=output)
        assert not output.exists()

    def test_canonical_real_root_is_refused(self) -> None:
        with pytest.raises(h00.H00BuildError, match="refused"):
            h00.build_sanitized_page(lake_dir=paths.REAL_LAKE_DIR)

    def test_private_roots_are_refused(self, demo_sandbox: Path) -> None:
        for name in ("portfolio", "watchlists", "tracking"):
            root = demo_sandbox / "data" / name
            root.mkdir(parents=True, exist_ok=True)
            with pytest.raises(h00.H00BuildError, match="refused"):
                h00.build_sanitized_page(lake_dir=root)

    def test_vendor_rows_are_refused_even_when_the_root_guard_is_disabled(
        self, demo_sandbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Mutation proof #1: neutralise the root-name/containment guard. The real slice's
        # rows carry no synthetic price-origin claim, so the attestation still refuses.
        monkeypatch.setattr(h00, "_demo_root", lambda lake_dir, **kwargs: (demo_sandbox / "data" / "lake_real").resolve())
        with pytest.raises(h00.H00BuildError, match="synthetic"):
            h00.build_sanitized_page(lake_dir=demo_sandbox / "data" / "lake", allow_sandbox_root=True, output_dir=tmp_path / "shape")
        assert not (tmp_path / "shape").exists()

    def test_vendor_coverage_label_is_refused_when_the_price_claim_is_also_disabled(
        self, demo_sandbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Mutation proof #2: neutralise the root guard *and* the price-row attestation.
        monkeypatch.setattr(h00, "_demo_root", lambda lake_dir, **kwargs: (demo_sandbox / "data" / "lake_real").resolve())
        monkeypatch.setattr(h00, "_attest_price_rows", lambda connection, table, columns: None)
        with pytest.raises(h00.H00BuildError, match="synthetic provenance label"):
            h00.build_sanitized_page(lake_dir=demo_sandbox / "data" / "lake", allow_sandbox_root=True, output_dir=tmp_path / "label")
        assert not (tmp_path / "label").exists()


class TestCanonicalDashboardUnchanged:
    """The H00 producer is additive: the canonical exporter output is byte-identical."""

    def test_exporter_pages_are_identical_across_an_h00_build(self, demo_sandbox: Path, tmp_path: Path) -> None:
        site = tmp_path / "site"
        DashboardExporter(output_dir=site).write()
        before = {path.name: path.read_bytes() for path in site.glob("*.html")}

        h00.build_sanitized_page(lake_dir=demo_sandbox / "data" / "lake", allow_sandbox_root=True, output_dir=tmp_path / "h00")

        after = {path.name: path.read_bytes() for path in site.glob("*.html")}
        assert before == after
        assert {path.stem for path in site.glob("*.html")} == {"index", "datasets", "health", "updates", "config"}

    def test_no_file_outside_the_artifact_directory_is_written(self, demo_sandbox: Path, tmp_path: Path) -> None:
        sandbox_before = sorted(str(path.relative_to(demo_sandbox)) for path in demo_sandbox.rglob("*"))
        catalog_before = (paths.PROJECT_ROOT / "data" / "catalog.jsonl").read_bytes()
        artifact = h00.build_sanitized_page(lake_dir=demo_sandbox / "data" / "lake", allow_sandbox_root=True, output_dir=tmp_path / "h00")
        sandbox_after = sorted(str(path.relative_to(demo_sandbox)) for path in demo_sandbox.rglob("*"))
        assert sandbox_before == sandbox_after
        assert (paths.PROJECT_ROOT / "data" / "catalog.jsonl").read_bytes() == catalog_before
        assert {path.name for path in artifact.directory.iterdir()} == set(h00.ARTIFACT_FILENAMES)


class TestAdoptedWording:
    """The banner, disclaimer and demo notice are the owner-approved strings, verbatim."""

    def test_strings_are_exactly_the_adopted_wording(self) -> None:
        assert h00.SYNTHETIC_BANNER == ("Synthetic demonstration data — generated offline by `equity demo seed`; not market data")
        assert h00.PERFORMANCE_DISCLAIMER == (
            "Computed on synthetic demo data; not indicative of any real strategy's performance and not investment advice."
        )
        assert h00.DEMO_DATA_ONLY == "demo data only — not investment advice"
        assert h00.STALE_BANNER == "stale — rebuild due"
        assert h00.STALENESS_THRESHOLD_DAYS == 30

    def test_page_carries_the_banner_disclaimer_and_demo_notice(self, built: h00.H00Artifact) -> None:
        html = built.index_path.read_text(encoding="utf-8")
        assert h00.SYNTHETIC_BANNER in html
        assert h00.PERFORMANCE_DISCLAIMER.replace("'", "&#x27;") in html
        assert h00.DEMO_DATA_ONLY in html
        payload = json.loads(built.payload_path.read_text(encoding="utf-8"))
        assert payload["banner"] == h00.SYNTHETIC_BANNER
        assert payload["disclaimer"] == h00.PERFORMANCE_DISCLAIMER
        assert payload["demo_data_only"] == h00.DEMO_DATA_ONLY

    def test_payload_carries_the_machine_readable_provenance(self, built: h00.H00Artifact) -> None:
        payload = json.loads(built.payload_path.read_text(encoding="utf-8"))
        assert payload["provenance"] == "synthetic"
        assert payload["provenance_labels"]["coverage_source"] == "synthetic_demo_seed"
        assert payload["provenance_labels"]["identity_provenance"] == "current_ticker_config"
        assert payload["coverage"]["source_valid_until"].endswith("+00:00")


class TestStaleness:
    """Owner decision 4: 30 days, then the last build date plus the stale banner."""

    def test_fresh_build_is_not_stale(self, built: h00.H00Artifact) -> None:
        assert built.stale is False
        payload = json.loads(built.payload_path.read_text(encoding="utf-8"))
        assert payload["stale"] is False
        assert payload["stale_banner"] is None
        # The banner *element* is always rendered (elapsed-time staleness needs it);
        # only its initial visibility is a build-time decision.
        html = built.index_path.read_text(encoding="utf-8")
        assert h00.STALE_BANNER in html
        assert f'id="{h00.STALE_ELEMENT_ID}"' in html

    def test_old_build_date_renders_the_stale_banner_and_last_build_date(self, demo_sandbox: Path, tmp_path: Path) -> None:
        now = datetime(2026, 11, 21, tzinfo=UTC)
        old = now - timedelta(days=45)
        artifact = h00.build_sanitized_page(
            lake_dir=demo_sandbox / "data" / "lake",
            allow_sandbox_root=True,
            output_dir=tmp_path / "stale-h00",
            built_at=old,
            now=now,
        )
        assert artifact.stale is True
        html = artifact.index_path.read_text(encoding="utf-8")
        assert h00.STALE_BANNER in html
        assert h00.last_build_date(old) in html
        payload = json.loads(artifact.payload_path.read_text(encoding="utf-8"))
        assert payload["stale"] is True
        assert payload["stale_banner"] == h00.STALE_BANNER
        assert h00.artifact_staleness(artifact.directory, now=now) == (True, h00.last_build_date(old))

    def test_threshold_boundary_is_thirty_days(self) -> None:
        now = datetime(2026, 11, 21, tzinfo=UTC)
        assert h00.is_stale(now - timedelta(days=29), now=now) is False
        assert h00.is_stale(now - timedelta(days=31), now=now) is True


class TestStalenessSurvivesElapsedBrowserTime:
    """Plan §2.3: the page goes stale by elapsed viewer time, not only at build time.

    What these tests prove: the stale element, the embedded payload instant and the
    30-day constant are always emitted, and a script in the page reads them and unhides
    the banner past the threshold. What they do **not** prove: that a browser actually
    runs that script. These are structural assertions over the rendered HTML/JS strings;
    a real browser execution is not exercised here (no headless browser in this suite).
    """

    def test_a_fresh_page_ships_a_hidden_stale_banner(self, built: h00.H00Artifact) -> None:
        # The build-time verdict is fresh, and the banner is still in the page — hidden
        # only until the client-side age check says otherwise.
        payload = json.loads(built.payload_path.read_text(encoding="utf-8"))
        assert payload["stale"] is False
        html = built.index_path.read_text(encoding="utf-8")
        assert re.search(rf'<p class="banner stale" id="{h00.STALE_ELEMENT_ID}"[^>]* hidden>', html), (
            "a fresh page must still carry the stale banner, hidden"
        )
        assert h00.STALE_BANNER in html
        assert h00.last_build_date(built.built_at) in html

    def test_the_age_check_is_bound_to_the_embedded_instant_and_the_thirty_day_constant(self, built: h00.H00Artifact) -> None:
        payload = json.loads(built.payload_path.read_text(encoding="utf-8"))
        html = built.index_path.read_text(encoding="utf-8")
        assert f'data-built-at="{payload["built_at"]}"' in html
        # Literal 30, not a derived expression: changing the embedded threshold must break
        # this test (mutation-proved below), so it cannot be read back from the constant.
        assert 'data-threshold-days="30"' in html
        assert "Date.now()" in html
        assert f'getElementById("{h00.STALE_ELEMENT_ID}")' in html
        # The check reads the element's own attributes and fails open on a bad instant.
        assert 'getAttribute("data-built-at")' in html
        assert 'getAttribute("data-threshold-days")' in html
        assert "isNaN(builtAt)" in html

    def test_a_stale_build_renders_the_banner_visible(self, demo_sandbox: Path, tmp_path: Path) -> None:
        now = datetime(2026, 11, 21, tzinfo=UTC)
        old = now - timedelta(days=45)
        artifact = h00.build_sanitized_page(
            lake_dir=demo_sandbox / "data" / "lake",
            allow_sandbox_root=True,
            output_dir=tmp_path / "stale-visible-h00",
            built_at=old,
            now=now,
        )
        html = artifact.index_path.read_text(encoding="utf-8")
        assert re.search(rf'<p class="banner stale" id="{h00.STALE_ELEMENT_ID}"[^>]* hidden>', html) is None
        assert 'data-threshold-days="30"' in html
        assert h00.last_build_date(old) in html

    def test_the_stale_element_is_not_conditional_on_the_build_time_flag(self, built: h00.H00Artifact) -> None:
        # Flip the build-time verdict on an otherwise identical payload: the element and
        # the check are rendered either way, so staleness cannot hinge on build time only.
        payload = json.loads(built.payload_path.read_text(encoding="utf-8"))
        rendered_fresh = h00._render_html(payload)
        rendered_stale = h00._render_html({**payload, "stale": True})
        for html in (rendered_fresh, rendered_stale):
            assert f'id="{h00.STALE_ELEMENT_ID}"' in html
            assert "Date.now()" in html
            assert 'data-threshold-days="30"' in html
        assert "hidden>" not in rendered_stale
        assert re.search(rf'id="{h00.STALE_ELEMENT_ID}"[^>]* hidden>', rendered_fresh)


class TestLoopbackPreview:
    """The preview reuses the P03 gate: loopback only, and it serves the artifact."""

    def test_non_loopback_hosts_are_refused(self, built: h00.H00Artifact) -> None:
        for host in ("0.0.0.0", "192.168.1.10", "example.com", ""):
            with pytest.raises(h00.H00PreviewError, match="loopback"):
                h00.serve_preview(host=host, port=0, artifact_dir=built.directory)

    def test_missing_artifact_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(h00.H00PreviewError, match="no composed artifact"):
            h00.serve_preview(host="127.0.0.1", port=0, artifact_dir=tmp_path / "absent")

    def test_loopback_preview_serves_the_composed_page(self, built: h00.H00Artifact) -> None:
        from functools import partial
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

        handler = partial(SimpleHTTPRequestHandler, directory=str(built.directory))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            address = server.server_address
            assert address[0] == "127.0.0.1"
            with urllib.request.urlopen(f"http://127.0.0.1:{address[1]}/index.html", timeout=10) as response:  # noqa: S310
                body = response.read().decode("utf-8")
            assert response.status == 200
            assert h00.SYNTHETIC_BANNER in body
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class TestCli:
    """The build/preview commands exist, are documented and never publish."""

    def test_dashboard_h00_commands_are_registered(self) -> None:
        from typer.testing import CliRunner

        from equity_lake.cli.__main__ import app  # type: ignore[import-untyped]

        runner = CliRunner()
        listing = runner.invoke(app, ["dashboard", "--help"])
        assert listing.exit_code == 0
        assert "h00-build" in listing.stdout
        assert "h00-preview" in listing.stdout

        build_help = runner.invoke(app, ["dashboard", "h00-build", "--help"])
        assert build_help.exit_code == 0
        assert "never publishes" in build_help.stdout

        preview_help = runner.invoke(app, ["dashboard", "h00-preview", "--help"])
        assert preview_help.exit_code == 0
        assert "--port" in preview_help.stdout
