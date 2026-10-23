"""Price-forecasting domain logic for the equity pipeline package."""

from __future__ import annotations

import collections
import contextlib
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import joblib
import numpy as np
import polars as pl
import structlog
import xgboost as xgb
from pydantic import BaseModel, ConfigDict, Field
from sklearn.model_selection import GridSearchCV
from tqdm import tqdm

from equity_lake.core.availability import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    AVAILABILITY_SCHEMA_VERSION,
    FEATURE_ACTION_COVERAGE_PROOF_COLUMNS,
    PRICE_ADJUSTMENT_VERSION,
    AvailabilityContractError,
    validate_feature_action_coverage,
)
from equity_lake.core.instruments import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    IDENTITY_SCHEMA_VERSION,
    InstrumentIdentityError,
    InstrumentKey,
    instrument_identity,
)
from equity_lake.core.polars_utils import FrameLike, ensure_polars
from equity_lake.core.schemas import FEATURE_SCHEMA_VERSION
from equity_lake.ml.backends import (
    DEFAULT_BACKEND,
    ModelBackend,
    backend_of,
    build_estimator,
    canonical_training_params,
    fit_estimator,
    validate_backend,
)
from equity_lake.ml.candidates import DEFAULT_BACKTEST_STRATEGY, build_candidate_frame
from equity_lake.ml.feature_loader import FeatureLoader
from equity_lake.ml.labeling import apply_triple_barrier_labels
from equity_lake.ml.sample_weights import compute_uniqueness_weights
from equity_lake.ml.trainer import compute_class_weights, compute_shap_importance, optimize_threshold
from equity_lake.ml.validation import PurgedEmbargoedWalkForwardSplitter, run_purged_walk_forward_validation

logger = structlog.get_logger(__name__)

from equity_lake.core.paths import DATA_DIR  # noqa: E402

DEFAULT_MODEL_DIR = DATA_DIR / "models"


MODEL_MODES = {"v1_direction", "v2_meta_label"}
NON_FEATURE_COLUMNS = {
    "ticker",
    "symbol",
    "market",
    "mic",
    "identity_schema_version",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "next_day_return",
    "label_available_date",
    "feature_schema_version",
    "price_adjustment_version",
    "availability_schema_version",
    *FEATURE_ACTION_COVERAGE_PROOF_COLUMNS,
    "candidate_action",
    "candidate_source",
    "candidate_score",
    "meta_label",
    "barrier_outcome",
    "upper_barrier_return",
    "lower_barrier_return",
    "vertical_barrier_days",
    "target",
    "barrier_start_idx",
    "barrier_end_idx",
    "barrier_end_date",
    "barrier_available_date",
}
DEFAULT_V2_SETTINGS = {
    "vertical_barrier_days": 5,
    "pt_mult": 1.5,
    "sl_mult": 1.0,
    "embargo_days": 1,
    "meta_label_threshold": 0.55,
}
MISSING_VALUE_POLICY = "native_nan-v1"
DEFAULT_MAX_MODEL_AGE_DAYS = 7
MODEL_PROVENANCE_VERSION = 3
MODEL_AGE_POLICY = {"default_max_age_days": DEFAULT_MAX_MODEL_AGE_DAYS, "age_unit": "calendar_days", "anchor": "refit_boundary"}


class ModelUnavailableError(ValueError):
    """Prediction could not safely use a model; ``status`` is product-readable."""

    status = "model_unavailable"


class ModelMissingError(ModelUnavailableError):
    """No identity-proven model artifact is available for this instrument."""

    status = "missing_model"


class ModelProvenanceError(ModelUnavailableError):
    """Model provenance is absent, malformed, or cannot be trusted."""

    status = "model_provenance_unavailable"


class ModelIncompatibleError(ModelUnavailableError):
    """Model identity, features, or versioned inference contract is incompatible."""

    status = "model_incompatible"


class ModelExpiredError(ModelUnavailableError):
    """Model is older than the configured calendar-day inference bound."""

    status = "model_expired"


class ModelFutureError(ModelUnavailableError):
    """Model refit boundary is later than the requested inference date."""

    status = "model_from_future"


class NoCandidateConditionError(ValueError):
    """Strategy evaluation found no candidate condition for this date."""

    status = "no_condition"


@dataclass
class TrainingArtifacts:
    """Internal training outputs reused by the CLI and artifact writers."""

    summary: dict[str, Any]
    model_path: Path
    audit_frame: pl.DataFrame | None = None


class TrainingMetadataArtifact(BaseModel):
    """Pydantic write-boundary model for the auxiliary model sidecar."""

    model_config = ConfigDict(extra="allow")

    provenance_version: int = MODEL_PROVENANCE_VERSION
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_schema_version: int
    instrument: InstrumentKey
    ticker: str
    trained_on: str
    model_mode: str
    refit_boundary: str
    training_start: str
    training_feature_schema_version: int | None = None
    training_feature_schema_versions: list[int] = Field(default_factory=list)
    price_adjustment_version: str
    availability_schema_version: int
    features: list[str]
    feature_dtypes: dict[str, str]
    candidate_strategies: list[dict[str, Any]] = Field(default_factory=list)
    candidate_strategy_fingerprint: str
    missing_value_policy: str
    model_age_policy: dict[str, Any]
    training_config: dict[str, Any] = Field(default_factory=dict)


class PriceForecaster:
    """XGBoost-based forecaster for v1 direction and v2 meta-label models."""

    def __init__(
        self,
        model_dir: str | None = None,
        model_mode: str = "v1_direction",
        ml_config: dict[str, Any] | None = None,
        backend: str = DEFAULT_BACKEND,
        candidate_strategies: list[dict[str, Any]] | None = None,
    ) -> None:
        if model_mode not in MODEL_MODES:
            raise ValueError(f"Unsupported model_mode={model_mode!r}")

        self.model_mode = model_mode
        self.backend = validate_backend(backend)
        self.model_dir = Path(model_dir) if model_dir else DEFAULT_MODEL_DIR
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self._feature_loader = FeatureLoader()
        self._loaded_models: collections.OrderedDict[Path, ModelBackend] = collections.OrderedDict()
        self._loaded_model_digests: dict[Path, str] = {}
        self.ml_config = dict(ml_config or {})
        self.v2_settings = {**DEFAULT_V2_SETTINGS, **self.ml_config}
        self.candidate_strategies = self._load_candidate_strategies(candidate_strategies)
        self._last_training_artifacts: TrainingArtifacts | None = None

    def load_features(
        self,
        ticker: str | InstrumentKey,
        start_date: date,
        end_date: date,
        *,
        require_available_by: date | None = None,
    ) -> pl.DataFrame:
        return self._feature_loader.load_features(ticker, start_date, end_date, require_available_by=require_available_by)

    def _load_candidate_strategies(self, candidate_strategies: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        configured = self.ml_config.get("candidate_strategies")
        if configured is not None:
            if not configured:
                raise ValueError("ml_config.candidate_strategies must not be empty when explicitly configured")
            return [dict(strategy) for strategy in configured]
        if candidate_strategies is not None:
            if not candidate_strategies:
                raise ValueError("candidate_strategies must not be empty when explicitly provided")
            return [dict(strategy) for strategy in candidate_strategies]
        logger.info("ml_candidate_strategies_defaulted", strategy=DEFAULT_BACKTEST_STRATEGY["name"])
        return [dict(DEFAULT_BACKTEST_STRATEGY)]

    @staticmethod
    def _candidate_strategy_fingerprint(strategies: Sequence[Mapping[str, Any]]) -> str:
        """Fingerprint ordered candidate rules only, excluding execution thresholds."""
        try:
            canonical = json.dumps(list(strategies), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Candidate strategy configuration is not canonical JSON: {exc}") from exc
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _validate_training_frame_contract(self, frame: pl.DataFrame) -> tuple[str, int]:
        """Require current, observed feature/adjustment contracts before fitting."""
        required = {
            "identity_schema_version",
            "feature_schema_version",
            "price_adjustment_version",
            "availability_schema_version",
            "action_coverage",
            "action_source_age_days",
            *FEATURE_ACTION_COVERAGE_PROOF_COLUMNS,
        }
        missing = required - set(frame.columns)
        if missing:
            if set(FEATURE_ACTION_COVERAGE_PROOF_COLUMNS) & missing:
                raise ValueError(f"Training feature source-availability proof is unknown: missing {sorted(missing)}")
            raise ValueError(f"Training feature contract is unknown: missing {sorted(missing)}")
        identity_versions = frame["identity_schema_version"].drop_nulls().unique().to_list()
        feature_versions = self._feature_schema_versions(frame)
        adjustment_versions = frame["price_adjustment_version"].drop_nulls().unique().to_list()
        availability_versions = frame["availability_schema_version"].drop_nulls().unique().to_list()
        coverage = frame["action_coverage"].to_list()
        if (
            identity_versions != [IDENTITY_SCHEMA_VERSION]
            or frame["identity_schema_version"].null_count()
            or any(not isinstance(value, int) or isinstance(value, bool) for value in identity_versions)
            or feature_versions != [FEATURE_SCHEMA_VERSION]
            or frame["feature_schema_version"].null_count()
            or adjustment_versions != [PRICE_ADJUSTMENT_VERSION]
            or availability_versions != [AVAILABILITY_SCHEMA_VERSION]
            or any(not isinstance(value, int) or isinstance(value, bool) for value in availability_versions)
            or frame["price_adjustment_version"].null_count()
            or frame["availability_schema_version"].null_count()
            or any(not isinstance(value, int) or isinstance(value, bool) or value != 1 for value in coverage)
        ):
            raise ValueError("Training feature rows have unknown or incompatible identity, schema, adjustment, or availability semantics")
        try:
            for row in frame.to_dicts():
                validate_feature_action_coverage(row)
        except AvailabilityContractError as exc:
            raise ValueError(f"Training feature source-availability proof is unknown or expired: {exc}") from exc
        return adjustment_versions[0], availability_versions[0]

    def train_model(
        self,
        ticker: str | InstrumentKey,
        start_date: date,
        end_date: date,
        params: dict[str, Any] | None = None,
        tune_hyperparams: bool = False,
        validate: bool = False,
        max_model_age_days: int = DEFAULT_MAX_MODEL_AGE_DAYS,
        validation_mode: str = "purged_walk_forward",
        train_window: int = 252,
        test_window: int = 21,
        embargo_window: int = 1,
        label_horizon_days: int | None = None,
    ) -> ModelBackend:
        """Train and persist a backend classifier (XGBoost or LightGBM)."""
        self._validate_max_model_age_days(max_model_age_days)
        self._last_training_artifacts = None
        ticker_symbol = ticker if isinstance(ticker, str) else ticker.symbol
        availability_cutoff = end_date if self.model_mode == "v1_direction" else None
        df = ensure_polars(self.load_features(ticker, start_date, end_date, require_available_by=availability_cutoff))
        if df.is_empty():
            raise ValueError(f"No features available for {ticker_symbol}")
        instrument = instrument_identity(df)
        if not isinstance(ticker, str) and instrument != ticker:
            raise InstrumentIdentityError(f"Loaded features do not match requested identity {instrument.display_key}")
        ticker_symbol = instrument.symbol
        price_adjustment_version, availability_schema_version = self._validate_training_frame_contract(df)
        label_horizon = label_horizon_days or self._label_horizon_days()
        validation_settings = {
            "validation_mode": validation_mode,
            "train_window": train_window,
            "test_window": test_window,
            "embargo_window": embargo_window,
            "label_horizon_days": label_horizon,
        }
        if max_model_age_days > 0:
            try:
                existing_path = self._resolve_model_path(instrument, end_date)
            except ModelUnavailableError:
                # This is an explicit train command: an incompatible or legacy
                # artifact is not reused; proceed with a new fit below.
                existing_path = None
            if existing_path is not None:
                existing_metadata = self._load_training_metadata_for_model(existing_path)
                if existing_metadata is not None and self._training_model_is_reusable(
                    model_path=existing_path,
                    metadata=existing_metadata,
                    frame=df,
                    instrument=instrument,
                    start_date=start_date,
                    end_date=end_date,
                    max_model_age_days=max_model_age_days,
                    expected_training_config={
                        "backend": self.backend,
                        "model_mode": self.model_mode,
                        "model_params": canonical_training_params(params),
                        "tune_hyperparams": tune_hyperparams,
                        "validation": validation_settings,
                        "ml_config": self.ml_config,
                        "effective_v2_settings": self.v2_settings,
                    },
                ):
                    trained_on, _age_source = self._model_training_date(existing_path, existing_metadata)
                    assert trained_on is not None
                    age_days = (end_date - trained_on).days
                    logger.info(
                        "model_skipped_fresh",
                        instrument=instrument.display_key,
                        trained_on=str(trained_on),
                        age_days=age_days,
                        model_mode=self.model_mode,
                    )
                    self._last_training_artifacts = TrainingArtifacts(
                        summary={
                            **instrument.as_columns(),
                            "trained_on": str(trained_on),
                            "model_mode": self.model_mode,
                            "model_file": existing_path.name,
                            "status": "reused_fresh_model",
                        },
                        model_path=existing_path,
                    )
                    return self._load_model(existing_path)

        if df.is_empty():
            raise ValueError(f"No features available for {ticker}")
        if "date" in df.columns:
            df = df.with_columns(pl.col("date").cast(pl.Date))

        training_df = self._prepare_training_frame(df)
        if training_df.is_empty():
            raise ValueError(f"No training rows available for {ticker} in mode {self.model_mode}")

        target_column = "meta_label" if self.model_mode == "v2_meta_label" else "target"
        df_clean = self._filter_complete_labels(training_df, target_column, end_date)
        if df_clean.is_empty():
            raise ValueError(f"No complete labels available for {ticker} by {end_date}")

        feature_cols = self._get_feature_columns(df_clean)
        X = self._prepare_training_matrix(df_clean, feature_cols)
        y = self._prepare_target_series(df_clean, target_column)
        split_idx = max(int(df_clean.height * 0.8), 1)
        fit_candidates = df_clean.slice(0, split_idx)
        validation_frame = df_clean.slice(split_idx)
        if validation_frame.height > 0:
            validation_start = self._as_date(validation_frame["date"][0])
            fit_frame = self._purge_crossing_labels(fit_candidates, validation_start)
        else:
            fit_frame = fit_candidates
        if fit_frame.is_empty():
            raise ValueError("No fit labels remain after purging events crossing the validation boundary")

        X_train = self._prepare_training_matrix(fit_frame, feature_cols)
        X_val = self._prepare_training_matrix(validation_frame, feature_cols)
        y_train = self._prepare_target_series(fit_frame, target_column)
        y_val = self._prepare_target_series(validation_frame, target_column)
        if self.model_mode == "v2_meta_label":
            sample_weights_train = compute_uniqueness_weights(fit_frame)
            sample_weights_val = compute_uniqueness_weights(validation_frame)
        else:
            sample_weights_train = np.ones(fit_frame.height, dtype=np.float64)
            sample_weights_val = np.ones(validation_frame.height, dtype=np.float64)

        metrics: dict[str, Any] | None = None
        if validate:
            metrics = self._validate_model(
                X=X,
                y=y,
                validation_mode=validation_mode,
                train_window=train_window,
                test_window=test_window,
                embargo_window=embargo_window,
                label_horizon_days=label_horizon,
                event_end_dates=df_clean[self._label_end_column()].to_list(),
                decision_dates=df_clean["date"].to_list(),
                decision_cutoff=end_date,
            )
            logger.info("model_validation_completed", ticker=ticker, model_mode=self.model_mode, **metrics)

        default_params = canonical_training_params(params)
        class_counts = compute_class_weights(y_train)

        if tune_hyperparams:
            model = self._tune_hyperparameters(
                X_train,
                y_train,
                sample_weight=sample_weights_train,
                train_window=train_window,
                test_window=test_window,
                embargo_window=embargo_window,
                label_horizon_days=label_horizon,
                event_end_dates=fit_frame[self._label_end_column()].to_list(),
                decision_dates=fit_frame["date"].to_list(),
                decision_cutoff=end_date,
            )
        else:
            model = build_estimator(self.backend, default_params, scale_pos_weight=class_counts["scale_pos_weight"])
            fit_estimator(
                model,
                X_train,
                y_train.to_numpy(),
                sample_weight=sample_weights_train,
                eval_set=[(X_val, y_val.to_numpy())] if X_val.height > 0 else None,
                eval_sample_weight=[sample_weights_val] if X_val.height > 0 else None,
                verbose=False,
            )

        model_path = self.model_dir / self._build_model_filename(instrument, end_date)
        joblib.dump(model, model_path)

        audit_frame = df_clean if self.model_mode == "v2_meta_label" else None
        shap_importance = compute_shap_importance(model, X_val if X_val.height > 0 else X_train, feature_cols)
        optimized_threshold: float | None = None
        if X_val.height > 0:
            y_val_proba = model.predict_proba(X_val)[:, 1]
            optimized_threshold = optimize_threshold(y_val, y_val_proba)
        feature_dtypes = {column: str(fit_frame[column].dtype) for column in feature_cols if column in fit_frame.columns}
        schema_versions = self._feature_schema_versions(fit_frame)
        training_start = self._as_date(fit_frame["date"].min())
        if schema_versions != [FEATURE_SCHEMA_VERSION]:
            raise ValueError("Training fit rows do not have one known current feature-schema version")
        training_config = {
            "backend": self.backend,
            "model_mode": self.model_mode,
            "model_params": default_params,
            "tune_hyperparams": tune_hyperparams,
            "validation": validation_settings,
            "ml_config": self.ml_config,
            "effective_v2_settings": self.v2_settings,
        }
        self._save_training_metadata(
            model_path=model_path,
            ticker=ticker_symbol,
            instrument=instrument,
            start_date=start_date,
            end_date=end_date,
            training_start=training_start,
            refit_boundary=end_date,
            feature_cols=feature_cols,
            feature_dtypes=feature_dtypes,
            training_feature_schema_versions=schema_versions,
            candidate_strategies=self.candidate_strategies,
            price_adjustment_version=price_adjustment_version,
            availability_schema_version=availability_schema_version,
            training_config=training_config,
            X_train=X_train,
            X_val=X_val,
            y_train=y_train,
            y_val=y_val,
            model=model,
            validation_settings=validation_settings,
            validation_metrics=metrics,
            class_counts=class_counts,
            shap_importance=shap_importance,
            optimized_threshold=optimized_threshold,
        )
        summary = self._build_training_summary(
            ticker=ticker_symbol,
            instrument=instrument,
            trained_on=end_date,
            model_path=model_path,
            training_start=training_start,
            train_rows=X_train.height,
            val_rows=X_val.height,
            validation_metrics=metrics,
        )
        self._save_training_summary(model_path, summary)
        if audit_frame is not None:
            self._save_training_audit_artifact(model_path, audit_frame)
        self._last_training_artifacts = TrainingArtifacts(summary=summary, model_path=model_path, audit_frame=audit_frame)

        logger.info("model_saved", instrument=instrument.display_key, path=str(model_path), model_mode=self.model_mode)
        return model

    def _tune_hyperparameters(
        self,
        X_train: pl.DataFrame,
        y_train: pl.Series,
        *,
        sample_weight: np.ndarray | None = None,
        train_window: int,
        test_window: int,
        embargo_window: int,
        label_horizon_days: int,
        event_end_dates: Sequence[date | datetime | str | None] | None = None,
        decision_dates: Sequence[date | datetime | str | None] | None = None,
        decision_cutoff: date | datetime | str | None = None,
    ) -> ModelBackend:
        """Run purged walk-forward grid search for hyperparameters."""
        param_grid = {
            "max_depth": [3, 5, 7],
            "learning_rate": [0.01, 0.05, 0.1],
            "n_estimators": [100, 200, 300],
            "subsample": [0.8, 0.9, 1.0],
        }
        splitter = PurgedEmbargoedWalkForwardSplitter(
            train_window=train_window,
            test_window=test_window,
            embargo_window=embargo_window,
            label_horizon=label_horizon_days,
            event_end_dates=event_end_dates,
            decision_dates=decision_dates,
            decision_cutoff=decision_cutoff,
        )
        # Never fall back to unpurged KFold (handoff 08 A2): the silent fallback
        # fired exactly when history is short — when leakage hurts most — and
        # deterministically shrinking the windows would silently change what the
        # tuned model was evaluated on. Fail fast and keep the caller in control
        # of the CV geometry instead.
        if splitter.get_n_splits(X_train) < 1:
            raise ValueError(
                f"Hyperparameter tuning requires at least one purged walk-forward fold, "
                f"but the training window has only {X_train.height} rows "
                f"(train_window={train_window}, test_window={test_window}, "
                f"embargo_window={embargo_window}, label_horizon_days={label_horizon_days}). "
                f"Widen the feature date range or explicitly reduce train_window/test_window "
                f"so one purged fold fits; refusing to fall back to unpurged KFold CV."
            )
        cv: Any = splitter

        class_counts = compute_class_weights(y_train)
        estimator_kwargs: dict[str, Any] = {
            "objective": "binary:logistic",
            "eval_metric": "logloss",
            "random_state": 42,
            "n_jobs": -1,
        }
        if self.backend == "lightgbm":
            # D2: GridSearchCV sets ``subsample`` via ``set_params``, bypassing
            # ``normalize_params``' subsample_freq injection — so LightGBM bagging
            # would stay off (bagging_freq=0) during tuning. Inject it here.
            estimator_kwargs["subsample_freq"] = 1
        estimator = build_estimator(self.backend, estimator_kwargs, scale_pos_weight=class_counts["scale_pos_weight"])

        fit_kwargs: dict[str, Any] = {}
        if sample_weight is not None:
            fit_kwargs["sample_weight"] = sample_weight

        grid_search = GridSearchCV(
            estimator=estimator,
            param_grid=param_grid,
            cv=cv,
            scoring="accuracy",
            n_jobs=-1,
            verbose=0,
        )
        grid_search.fit(X_train, y_train.to_numpy(), **fit_kwargs)
        logger.info("model_tuned", best_params=grid_search.best_params_, model_mode=self.model_mode)
        return cast(ModelBackend, grid_search.best_estimator_)

    def predict(
        self,
        ticker: str | InstrumentKey,
        date: date,
        model: ModelBackend | None = None,
        *,
        model_metadata: Mapping[str, Any] | None = None,
        max_model_age_days: int = DEFAULT_MAX_MODEL_AGE_DAYS,
    ) -> dict[str, Any]:
        """Predict only when model provenance and every inference contract are trusted.

        ``max_model_age_days`` is an inclusive calendar-day bound. An in-memory
        estimator is accepted only alongside an explicit metadata mapping;
        filenames and mtimes never fill missing provenance.
        """
        self._validate_max_model_age_days(max_model_age_days)
        ticker_symbol = ticker if isinstance(ticker, str) else ticker.symbol
        start_date = date - timedelta(days=180 if self.model_mode == "v2_meta_label" else 60)
        df = ensure_polars(self.load_features(ticker, start_date, date))
        if df.is_empty():
            raise ValueError(f"No features available for {ticker_symbol} on {date}")
        instrument = instrument_identity(df)
        if not isinstance(ticker, str) and instrument != ticker:
            raise InstrumentIdentityError(f"Loaded features do not match requested identity {instrument.display_key}")
        ticker_symbol = instrument.symbol
        if "date" in df.columns:
            df = df.with_columns(pl.col("date").cast(pl.Date))

        scoring_df = self._prepare_scoring_frame(df, date)
        if scoring_df.is_empty():
            if self.model_mode == "v2_meta_label":
                raise NoCandidateConditionError(f"No candidate condition for {instrument.display_key} on {date}")
            raise ValueError(f"No scoring features found for {ticker_symbol} on {date}")

        model_path: Path | None = None
        if model is None:
            model_path = self._resolve_model_path(instrument, date)
            if model_path is None:
                raise ModelMissingError(f"No identity-compatible trained model found for {instrument.display_key}")
            metadata = self._load_training_metadata_for_model(model_path)
        else:
            raise ModelProvenanceError("In-memory models cannot be verified against artifact SHA-256; use a saved model artifact")
        if metadata is None:
            raise ModelProvenanceError(f"Model metadata is missing for {instrument.display_key}")

        training_schema_versions, model_trained_on, adjustment_compatibility, schema_compatibility = self._validate_inference_contract(
            scoring_df=scoring_df,
            metadata=metadata,
            instrument=instrument,
            as_of_date=date,
            max_model_age_days=max_model_age_days,
            model_path=model_path,
        )
        if self.model_mode == "v2_meta_label":
            self._validate_v2_candidate_contract(scoring_df, metadata)

        if self.model_mode == "v2_meta_label":
            fallback_threshold = float(self.v2_settings["meta_label_threshold"])
            fallback_source = "configured_fallback"
        else:
            fallback_threshold = 0.5
            fallback_source = "default"
        # Threshold validation is also a policy gate and precedes model loading.
        threshold, threshold_source = self._effective_threshold(metadata, fallback_threshold, fallback_source)
        feature_cols = self._get_feature_columns(scoring_df)
        X = self._prepare_training_matrix(scoring_df, feature_cols)

        if model is None:
            assert model_path is not None
            model = self._load_model(model_path)
        probability = float(model.predict_proba(X)[0][1])

        training_schema_version = training_schema_versions[0]
        inference_schema_version = self._single_feature_schema_version(scoring_df)
        model_age_days = (date - model_trained_on).days
        provenance = {
            "model_trained_at": model_trained_on,
            "model_age_days": model_age_days,
            "model_age_source": "training_metadata",
            "training_feature_schema_version": training_schema_version,
            "inference_feature_schema_version": inference_schema_version,
            "feature_schema_compatibility": schema_compatibility,
            "adjustment_compatibility": adjustment_compatibility,
            "price_adjustment_version": PRICE_ADJUSTMENT_VERSION,
            "availability_schema_version": AVAILABILITY_SCHEMA_VERSION,
            "missing_value_policy": MISSING_VALUE_POLICY,
            "model_max_age_days": max_model_age_days,
            "candidate_strategy_fingerprint": metadata.get("candidate_strategy_fingerprint"),
            "feature_schema_version": training_schema_version,
            "threshold_used": threshold,
            "threshold_source": threshold_source,
            "horizon_days": self._label_horizon_days(),
            "identity_schema_version": IDENTITY_SCHEMA_VERSION,
        }

        if self.model_mode == "v2_meta_label":
            candidate_row = scoring_df.row(0, named=True)
            return {
                **instrument.as_columns(),
                "identity_schema_version": IDENTITY_SCHEMA_VERSION,
                "date": date,
                "prediction": int(probability >= threshold),
                "probability": probability,
                "execution_probability": probability,
                "candidate_action": candidate_row["candidate_action"],
                "candidate_source": candidate_row["candidate_source"],
                "meta_label_threshold": threshold,
                "should_execute": bool(probability >= threshold),
                "candidate_explanation": {
                    "action": candidate_row["candidate_action"],
                    "source": candidate_row["candidate_source"],
                    "score": float(candidate_row["candidate_score"]),
                    "threshold_used": threshold,
                    "threshold_source": threshold_source,
                },
                "barrier_settings": {
                    "vertical_barrier_days": int(self.v2_settings["vertical_barrier_days"]),
                    "pt_mult": float(self.v2_settings["pt_mult"]),
                    "sl_mult": float(self.v2_settings["sl_mult"]),
                },
                "model_mode": self.model_mode,
                "model_version": model_path.stem if model_path else "provided_model",
                **provenance,
            }

        return {
            **instrument.as_columns(),
            "identity_schema_version": IDENTITY_SCHEMA_VERSION,
            "date": date,
            "prediction": int(probability >= threshold),
            "probability": probability,
            "model_mode": self.model_mode,
            "model_version": model_path.stem if model_path else "provided_model",
            **provenance,
        }

    def backtest(
        self,
        ticker: str | InstrumentKey,
        start_date: date,
        end_date: date,
        train_window: int = 500,
        retrain_interval: int = 63,
    ) -> pl.DataFrame:
        """Walk forward with labels matured by each individual refit cutoff.

        Training and threshold-selection labels are independently restricted to
        events available at the refit boundary. Fit events whose outcome reaches
        the first validation event are purged. Returned actuals contain only
        labels complete by the requested outer end date.
        """
        ticker_symbol = ticker if isinstance(ticker, str) else ticker.symbol
        availability_cutoff = end_date if self.model_mode == "v1_direction" else None
        features = ensure_polars(self.load_features(ticker, start_date, end_date, require_available_by=availability_cutoff))
        if features.is_empty():
            raise ValueError(f"No features available for {ticker_symbol}")
        instrument = instrument_identity(features)
        if not isinstance(ticker, str) and instrument != ticker:
            raise InstrumentIdentityError(f"Loaded features do not match requested identity {instrument.display_key}")
        features = features.with_columns(pl.col("date").cast(pl.Date))
        target_column = "meta_label" if self.model_mode == "v2_meta_label" else "target"
        all_labeled = self._prepare_training_frame(features)
        evaluation_df = self._filter_complete_labels(all_labeled, target_column, end_date)
        if evaluation_df.height < train_window + 10:
            raise ValueError(f"Not enough complete labels for backtesting. Need at least {train_window + 10} days")

        results: list[dict[str, Any]] = []
        feature_cols = self._get_feature_columns(evaluation_df)
        last_train_idx = -1
        model: ModelBackend | None = None
        fallback_threshold = float(self.v2_settings["meta_label_threshold"]) if self.model_mode == "v2_meta_label" else 0.5
        fallback_threshold_source = "configured_fallback" if self.model_mode == "v2_meta_label" else "default"
        backtest_threshold = fallback_threshold
        threshold_source = fallback_threshold_source
        refit_boundary: date | None = None
        training_start: date | None = None

        for i in tqdm(range(evaluation_df.height - train_window), desc="Backtesting"):
            test_idx = i + train_window
            if test_idx >= evaluation_df.height:
                break
            test_row = evaluation_df.row(test_idx, named=True)
            decision_cutoff = self._as_date(test_row["date"])

            if model is None or (test_idx - last_train_idx) >= retrain_interval:
                # Each new model starts from the preserved configured/default
                # threshold. An empty validation tail must not retain the prior
                # refit's optimized value.
                backtest_threshold = fallback_threshold
                threshold_source = fallback_threshold_source
                window_start_idx = max(0, test_idx - train_window)
                window_start = self._as_date(evaluation_df["date"][window_start_idx])
                # Include bars through the decision cutoff to mature prior
                # events, but exclude a candidate that starts on the test row.
                # Pre-window history remains available for rolling features.
                refit_features = features.filter(pl.col("date") <= pl.lit(decision_cutoff))
                refit_labeled = self._prepare_training_frame(refit_features)
                eligible_train = self._filter_complete_labels(refit_labeled, target_column, decision_cutoff).filter(
                    (pl.col("date") >= pl.lit(window_start)) & (pl.col("date") < pl.lit(decision_cutoff))
                )
                if eligible_train.is_empty():
                    raise ValueError(f"No complete training labels available by refit boundary {decision_cutoff}")

                split_idx = max(int(eligible_train.height * 0.8), 1)
                fit_candidates = eligible_train.slice(0, split_idx)
                validation_frame = eligible_train.slice(split_idx)
                if validation_frame.height > 0:
                    validation_start = self._as_date(validation_frame["date"][0])
                    fit_frame = self._purge_crossing_labels(fit_candidates, validation_start)
                else:
                    fit_frame = fit_candidates
                if fit_frame.is_empty():
                    raise ValueError(f"No fit labels remain after purging events crossing refit validation boundary {decision_cutoff}")

                X_tr = self._prepare_training_matrix(fit_frame, feature_cols)
                y_tr = self._prepare_target_series(fit_frame, target_column)
                class_counts = compute_class_weights(y_tr)
                model = build_estimator(self.backend, canonical_training_params(), scale_pos_weight=class_counts["scale_pos_weight"])
                fit_estimator(model, X_tr, y_tr.to_numpy(), verbose=False)
                if validation_frame.height > 0:
                    X_val = self._prepare_training_matrix(validation_frame, feature_cols)
                    y_val = self._prepare_target_series(validation_frame, target_column)
                    backtest_threshold = optimize_threshold(y_val, model.predict_proba(X_val)[:, 1])
                    threshold_source = "refit_validation"
                last_train_idx = test_idx
                refit_boundary = decision_cutoff
                training_start = self._as_date(fit_frame["date"].min())

            assert model is not None and refit_boundary is not None and training_start is not None
            test_slice = evaluation_df.slice(test_idx, 1)
            X_test = self._prepare_training_matrix(test_slice, feature_cols)
            y_true = int(test_row[target_column])
            proba = float(model.predict_proba(X_test)[0][1])
            pred = int(proba >= backtest_threshold)
            result = {
                **instrument.as_columns(),
                "identity_schema_version": IDENTITY_SCHEMA_VERSION,
                "date": decision_cutoff,
                "prediction": pred,
                "probability": proba,
                "actual": y_true,
                "model_trained_at": str(refit_boundary),
                "refit_boundary": str(refit_boundary),
                "training_start": str(training_start),
                "threshold_used": float(backtest_threshold),
                "threshold_source": threshold_source,
                "horizon_days": self._label_horizon_days(),
            }
            if self.model_mode == "v1_direction":
                result["actual_return"] = test_row["next_day_return"]
            else:
                result["candidate_action"] = test_row["candidate_action"]
                result["barrier_outcome"] = test_row["barrier_outcome"]
            results.append(result)

        return pl.DataFrame(results)

    def _prepare_training_frame(self, df: FrameLike) -> pl.DataFrame:
        frame = ensure_polars(df).sort("date")
        if self.model_mode == "v2_meta_label":
            candidates = self._build_candidate_frame(frame)
            if candidates.is_empty():
                return candidates
            return self._apply_triple_barrier_labels(candidates, frame)

        return frame.with_columns((pl.col("next_day_return") > 0).cast(pl.Int8).alias("target"))

    def _prepare_scoring_frame(self, df: FrameLike, prediction_date: date) -> pl.DataFrame:
        frame = ensure_polars(df).sort("date")
        if self.model_mode == "v2_meta_label":
            candidates = self._build_candidate_frame(frame)
            if candidates.is_empty():
                return candidates
            return candidates.filter(pl.col("date").cast(pl.Date) == pl.lit(prediction_date)).tail(1)
        return frame.filter(pl.col("date").cast(pl.Date) == pl.lit(prediction_date)).tail(1)

    def _build_candidate_frame(self, df: FrameLike) -> pl.DataFrame:
        return build_candidate_frame(df, self.candidate_strategies)

    def _apply_triple_barrier_labels(self, candidates: FrameLike, full_df: FrameLike) -> pl.DataFrame:
        return apply_triple_barrier_labels(
            candidates,
            full_df,
            vertical_barrier_days=int(self.v2_settings["vertical_barrier_days"]),
            pt_mult=float(self.v2_settings["pt_mult"]),
            sl_mult=float(self.v2_settings["sl_mult"]),
        )

    def _label_horizon_days(self) -> int:
        return int(self.v2_settings["vertical_barrier_days"]) if self.model_mode == "v2_meta_label" else 1

    def _label_end_column(self) -> str:
        return "barrier_end_date" if self.model_mode == "v2_meta_label" else "label_available_date"

    def _filter_complete_labels(self, frame: FrameLike, target_column: str, cutoff: date) -> pl.DataFrame:
        """Strictly keep labels with known maturity no later than *cutoff*."""
        labeled = ensure_polars(frame)
        end_column = self._label_end_column()
        if target_column not in labeled.columns or end_column not in labeled.columns:
            return labeled.head(0)
        return labeled.filter(
            pl.col(target_column).is_not_null()
            & pl.col(end_column).is_not_null()
            & (pl.col(end_column).cast(pl.Date, strict=False) <= pl.lit(cutoff))
        )

    def _purge_crossing_labels(self, frame: FrameLike, validation_start: date) -> pl.DataFrame:
        """Remove fit labels whose event interval reaches the validation edge."""
        labeled = ensure_polars(frame)
        end_column = self._label_end_column()
        if "date" not in labeled.columns or end_column not in labeled.columns:
            return labeled.head(0)
        return labeled.filter(pl.col(end_column).cast(pl.Date, strict=False) < pl.lit(validation_start))

    @staticmethod
    def _as_date(value: Any) -> date:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            return date.fromisoformat(value[:10])
        raise ValueError(f"Cannot interpret {value!r} as a date")

    @staticmethod
    def _feature_schema_versions(frame: FrameLike) -> list[int]:
        features = ensure_polars(frame)
        if "feature_schema_version" not in features.columns or features["feature_schema_version"].null_count():
            return []
        values = features["feature_schema_version"].unique().to_list()
        versions: set[int] = set()
        for value in values:
            if isinstance(value, bool) or not isinstance(value, int):
                return []
            versions.add(value)
        return sorted(versions)

    @staticmethod
    def _metadata_schema_versions(metadata: dict[str, Any] | None) -> list[int]:
        if metadata is None:
            return []
        raw_versions = metadata.get("training_feature_schema_versions")
        single_version = metadata.get("training_feature_schema_version")
        if (
            not isinstance(raw_versions, list)
            or len(raw_versions) != 1
            or any(not isinstance(value, int) or isinstance(value, bool) for value in raw_versions)
            or not isinstance(single_version, int)
            or isinstance(single_version, bool)
            or raw_versions[0] != single_version
        ):
            return []
        return [single_version]

    @classmethod
    def _single_feature_schema_version(cls, frame: FrameLike) -> int | None:
        versions = cls._feature_schema_versions(frame)
        return versions[0] if len(versions) == 1 else None

    def _adjustment_contract_compatibility(self, scoring_frame: FrameLike, metadata: dict[str, Any] | None) -> str:
        if metadata is None:
            return "unknown"
        scoring = ensure_polars(scoring_frame)
        required = {
            "date",
            "market",
            "mic",
            "symbol",
            "availability_schema_version",
            "action_coverage",
            "action_source_age_days",
            *FEATURE_ACTION_COVERAGE_PROOF_COLUMNS,
            "price_adjustment_version",
        }
        if not required.issubset(scoring.columns):
            return "unknown"
        adjustment_versions = scoring["price_adjustment_version"].drop_nulls().unique().to_list()
        availability_versions = scoring["availability_schema_version"].drop_nulls().unique().to_list()
        coverage = scoring["action_coverage"].to_list()
        if (
            adjustment_versions != [PRICE_ADJUSTMENT_VERSION]
            or availability_versions != [AVAILABILITY_SCHEMA_VERSION]
            or any(not isinstance(value, int) or isinstance(value, bool) for value in availability_versions)
            or scoring["price_adjustment_version"].null_count()
            or scoring["availability_schema_version"].null_count()
            or any(not isinstance(value, int) or isinstance(value, bool) or value != 1 for value in coverage)
            or metadata.get("price_adjustment_version") != PRICE_ADJUSTMENT_VERSION
            or not isinstance(metadata.get("availability_schema_version"), int)
            or isinstance(metadata.get("availability_schema_version"), bool)
            or metadata.get("availability_schema_version") != AVAILABILITY_SCHEMA_VERSION
        ):
            return "mismatch"
        try:
            for row in scoring.to_dicts():
                validate_feature_action_coverage(row)
        except AvailabilityContractError:
            return "mismatch"
        return "known"

    @staticmethod
    def _validate_max_model_age_days(max_model_age_days: int) -> None:
        if (
            isinstance(max_model_age_days, bool)
            or not isinstance(max_model_age_days, int)
            or not 0 <= max_model_age_days <= DEFAULT_MAX_MODEL_AGE_DAYS
        ):
            raise ValueError(f"max_model_age_days must be an integer in [0, {DEFAULT_MAX_MODEL_AGE_DAYS}]")

    @staticmethod
    def _sha256_file(model_path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with model_path.open("rb") as model_file:
                for chunk in iter(lambda: model_file.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise ModelProvenanceError(f"Could not read model artifact {model_path}: {exc}") from exc
        return digest.hexdigest()

    @staticmethod
    def _validate_artifact_digest(metadata: Mapping[str, Any], model_path: Path, actual: str) -> str:
        expected = metadata.get("artifact_sha256")
        if not isinstance(expected, str) or len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
            raise ModelProvenanceError("Model artifact SHA-256 is missing or malformed")
        if actual != expected:
            raise ModelProvenanceError(f"Model artifact SHA-256 does not match sidecar for {model_path.name}")
        return actual

    def _verify_model_artifact(self, metadata: Mapping[str, Any], model_path: Path) -> str:
        if not model_path.is_file():
            raise ModelProvenanceError(f"Model artifact is missing or is not a file: {model_path}")
        return self._validate_artifact_digest(metadata, model_path, self._sha256_file(model_path))

    @staticmethod
    def _metadata_date(metadata: Mapping[str, Any], field: str) -> date:
        raw = metadata.get(field)
        if not isinstance(raw, str):
            raise ModelProvenanceError(f"Model metadata {field} is missing or is not an ISO date")
        try:
            parsed = date.fromisoformat(raw)
        except ValueError as exc:
            raise ModelProvenanceError(f"Model metadata {field} is not an ISO date: {raw!r}") from exc
        if parsed.isoformat() != raw:
            raise ModelProvenanceError(f"Model metadata {field} must be a date, not a timestamp: {raw!r}")
        return parsed

    def _validate_inference_contract(
        self,
        *,
        scoring_df: pl.DataFrame,
        metadata: Mapping[str, Any],
        instrument: InstrumentKey,
        as_of_date: date,
        max_model_age_days: int,
        model_path: Path | None,
    ) -> tuple[list[int], date, str, str]:
        """Validate trusted provenance and exact inference compatibility before model loading."""
        if not isinstance(metadata, dict) or metadata.get("provenance_version") != MODEL_PROVENANCE_VERSION:
            raise ModelProvenanceError(f"Model requires provenance_version={MODEL_PROVENANCE_VERSION} metadata")
        if "identity_schema_version" not in scoring_df.columns or scoring_df["identity_schema_version"].null_count():
            raise ModelIncompatibleError("Inference identity schema provenance is missing")
        scoring_identity_versions = scoring_df["identity_schema_version"].unique().to_list()
        if scoring_identity_versions != [IDENTITY_SCHEMA_VERSION] or any(
            not isinstance(value, int) or isinstance(value, bool) for value in scoring_identity_versions
        ):
            raise ModelIncompatibleError("Inference identity schema version is unknown or incompatible")
        raw_identity = metadata.get("instrument")
        metadata_identity_version = metadata.get("identity_schema_version")
        if (
            not isinstance(metadata_identity_version, int)
            or isinstance(metadata_identity_version, bool)
            or metadata_identity_version != IDENTITY_SCHEMA_VERSION
            or not isinstance(raw_identity, dict)
        ):
            raise ModelProvenanceError("Model canonical identity provenance is missing or unsupported")
        try:
            model_instrument = InstrumentKey.model_validate(raw_identity)
        except (TypeError, ValueError) as exc:
            raise ModelProvenanceError(f"Model instrument identity is invalid: {exc}") from exc
        if model_instrument != instrument or metadata.get("ticker") != instrument.symbol:
            raise ModelIncompatibleError(f"Model identity does not match requested instrument {instrument.display_key}")
        if metadata.get("model_mode") != self.model_mode or metadata.get("backend") != self.backend:
            raise ModelIncompatibleError("Model mode/backend provenance does not match this forecaster")
        if model_path is None or metadata.get("model_file") != model_path.name:
            raise ModelProvenanceError("Model sidecar does not identify a verifiable saved model artifact")
        self._verify_model_artifact(metadata, model_path)

        refit_boundary = self._metadata_date(metadata, "refit_boundary")
        trained_on = self._metadata_date(metadata, "trained_on")
        training_start = self._metadata_date(metadata, "training_start")
        data_range = metadata.get("data_range")
        if not isinstance(data_range, dict):
            raise ModelProvenanceError("Model training data range is missing or malformed")
        data_range_start = self._metadata_date(data_range, "start")
        data_range_end = self._metadata_date(data_range, "end")
        if trained_on != refit_boundary or not data_range_start <= training_start <= refit_boundary or data_range_end != refit_boundary:
            raise ModelProvenanceError("Model training timestamps do not identify a coherent actual training cutoff")
        if refit_boundary > as_of_date:
            raise ModelFutureError(f"Model refit boundary {refit_boundary} is after inference date {as_of_date}")
        model_age_days = (as_of_date - refit_boundary).days
        if model_age_days > max_model_age_days:
            raise ModelExpiredError(f"Model age is {model_age_days} calendar days; maximum allowed is {max_model_age_days}")

        if metadata.get("missing_value_policy") != MISSING_VALUE_POLICY:
            raise ModelIncompatibleError("Model missing-value contract is unknown or incompatible")
        if metadata.get("model_age_policy") != MODEL_AGE_POLICY:
            raise ModelProvenanceError("Model age policy must use the trusted refit boundary and calendar-day default")
        training_schema_versions = self._metadata_schema_versions(dict(metadata))
        if training_schema_versions != [FEATURE_SCHEMA_VERSION]:
            raise ModelIncompatibleError("Model training feature-schema provenance is unknown or incompatible")
        if self._single_feature_schema_version(scoring_df) != FEATURE_SCHEMA_VERSION:
            raise ModelIncompatibleError("Inference feature-schema version is unknown or incompatible")

        adjustment_compatibility = self._adjustment_contract_compatibility(scoring_df, dict(metadata))
        if adjustment_compatibility != "known":
            raise ModelIncompatibleError(f"Model adjustment/availability contract is {adjustment_compatibility}")
        schema_compatibility = self._feature_schema_compatibility(scoring_df, dict(metadata), training_schema_versions)
        if schema_compatibility != "known":
            raise ModelIncompatibleError(f"Model feature names/dtypes/schema are {schema_compatibility}")
        return training_schema_versions, refit_boundary, adjustment_compatibility, schema_compatibility

    def _training_model_is_reusable(
        self,
        *,
        model_path: Path,
        metadata: dict[str, Any],
        frame: pl.DataFrame,
        instrument: InstrumentKey,
        start_date: date,
        end_date: date,
        max_model_age_days: int,
        expected_training_config: dict[str, Any],
    ) -> bool:
        """Reuse only a fresh artifact whose complete inference/training contracts match."""
        try:
            self._validate_inference_contract(
                scoring_df=frame,
                metadata=metadata,
                instrument=instrument,
                as_of_date=end_date,
                max_model_age_days=max_model_age_days,
                model_path=model_path,
            )
            data_range = metadata["data_range"]
            if self._metadata_date(data_range, "start") != start_date:
                return False
            stored_training_config = metadata.get("training_config")
            expected_config = expected_training_config
            if self.model_mode == "v2_meta_label":
                stored_training_config = self._without_execution_threshold(stored_training_config)
                expected_config = self._without_execution_threshold(expected_training_config)
            if stored_training_config != expected_config:
                return False
            if self.model_mode == "v2_meta_label":
                self._validate_v2_model_semantics(metadata)
            else:
                target_settings = metadata.get("target_settings")
                horizon = target_settings.get("label_horizon_days") if isinstance(target_settings, dict) else None
                if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon != self._label_horizon_days():
                    return False
        except (ModelUnavailableError, TypeError, ValueError):
            return False
        return True

    @staticmethod
    def _without_execution_threshold(training_config: Any) -> Any:
        if not isinstance(training_config, dict):
            return training_config
        normalized = dict(training_config)
        for key in ("ml_config", "effective_v2_settings"):
            nested = normalized.get(key)
            if isinstance(nested, dict):
                normalized[key] = {name: value for name, value in nested.items() if name != "meta_label_threshold"}
        return normalized

    def _validate_v2_model_semantics(self, metadata: Mapping[str, Any]) -> None:
        strategies = metadata.get("candidate_strategies")
        stored_fingerprint = metadata.get("candidate_strategy_fingerprint")
        if not isinstance(strategies, list) or not strategies or any(not isinstance(item, dict) for item in strategies):
            raise ModelProvenanceError("V2 model candidate-strategy provenance is missing")
        if not isinstance(stored_fingerprint, str) or not stored_fingerprint:
            raise ModelProvenanceError("V2 model candidate-strategy fingerprint is missing")
        if self._candidate_strategy_fingerprint(strategies) != stored_fingerprint:
            raise ModelProvenanceError("V2 model candidate-strategy metadata fingerprint is inconsistent")
        if self._candidate_strategy_fingerprint(self.candidate_strategies) != stored_fingerprint:
            raise ModelIncompatibleError("Configured candidate strategies differ from the model training contract")

        target_settings = metadata.get("target_settings")
        required = ("vertical_barrier_days", "pt_mult", "sl_mult")
        if not isinstance(target_settings, dict) or any(key not in target_settings for key in required):
            raise ModelProvenanceError("V2 model barrier settings are missing")
        for key in required:
            try:
                stored_value = float(target_settings[key])
                active_value = float(self.v2_settings[key])
            except (TypeError, ValueError) as exc:
                raise ModelProvenanceError(f"V2 model barrier setting {key!r} is invalid") from exc
            if not math.isfinite(stored_value) or stored_value != active_value:
                raise ModelIncompatibleError(f"V2 model barrier setting {key!r} differs from the training contract")
        # meta_label_threshold is deliberately separate: it is only the fallback
        # execution threshold and never changes candidate-strategy identity.

    def _validate_v2_candidate_contract(self, scoring_df: pl.DataFrame, metadata: Mapping[str, Any]) -> None:
        self._validate_v2_model_semantics(metadata)
        strategies = metadata["candidate_strategies"]
        candidate_source = scoring_df["candidate_source"][0] if "candidate_source" in scoring_df.columns else None
        strategy_names = {str(item.get("name", "momentum")) for item in strategies}
        if not isinstance(candidate_source, str) or candidate_source not in strategy_names:
            raise ModelIncompatibleError("Selected candidate strategy is not present in the model training contract")

    def _feature_schema_compatibility(
        self,
        scoring_frame: FrameLike,
        metadata: dict[str, Any] | None,
        training_versions: list[int],
    ) -> str:
        inference_versions = self._feature_schema_versions(scoring_frame)
        if len(training_versions) != 1 or len(inference_versions) != 1 or metadata is None:
            return "unknown"
        trained_features = metadata.get("features")
        trained_dtypes = metadata.get("feature_dtypes")
        if (
            not isinstance(trained_features, list)
            or not trained_features
            or any(not isinstance(feature, str) for feature in trained_features)
            or len(set(trained_features)) != len(trained_features)
            or not isinstance(trained_dtypes, dict)
            or set(trained_dtypes) != set(trained_features)
        ):
            return "unknown"

        scoring = ensure_polars(scoring_frame)
        inference_features = self._get_feature_columns(scoring)
        if set(trained_features) != set(inference_features):
            return "mismatch"
        if any(not isinstance(trained_dtypes[feature], str) for feature in trained_features):
            return "unknown"
        if any(feature not in scoring.columns or str(scoring[feature].dtype) != trained_dtypes[feature] for feature in trained_features):
            return "mismatch"
        return "known" if training_versions[0] == inference_versions[0] else "mismatch"

    @staticmethod
    def _effective_threshold(
        metadata: dict[str, Any] | None,
        fallback: float,
        fallback_source: str,
    ) -> tuple[float, str]:
        if metadata is not None and metadata.get("optimized_threshold") is not None:
            stored = metadata["optimized_threshold"]
            if isinstance(stored, bool) or not isinstance(stored, int | float) or not math.isfinite(float(stored)) or not 0.0 <= float(stored) <= 1.0:
                raise ValueError(f"Stored optimized_threshold must be finite and in [0, 1], got {stored!r}")
            return float(stored), "stored_optimized"
        if not math.isfinite(fallback) or not 0.0 <= fallback <= 1.0:
            raise ValueError(f"Configured prediction threshold must be finite and in [0, 1], got {fallback!r}")
        return float(fallback), fallback_source

    def _model_training_date(self, model_path: Path | None, metadata: Mapping[str, Any] | None) -> tuple[date | None, str]:
        del model_path  # Filename and filesystem timestamps are never provenance.
        if metadata is None:
            return None, "unknown"
        try:
            boundary = self._metadata_date(metadata, "refit_boundary")
            trained_on = self._metadata_date(metadata, "trained_on")
        except ModelProvenanceError:
            return None, "unknown"
        return (boundary, "training_metadata") if boundary == trained_on else (None, "unknown")

    def _validate_model(
        self,
        *,
        X: pl.DataFrame,
        y: pl.Series,
        validation_mode: str,
        train_window: int,
        test_window: int,
        embargo_window: int,
        label_horizon_days: int,
        event_end_dates: Sequence[date | datetime | str | None] | None = None,
        decision_dates: Sequence[date | datetime | str | None] | None = None,
        decision_cutoff: date | datetime | str | None = None,
    ) -> dict[str, float | int]:
        if validation_mode != "purged_walk_forward":
            raise ValueError(f"Unsupported validation_mode={validation_mode!r}")
        return run_purged_walk_forward_validation(
            X=X,
            y=y,
            train_window=train_window,
            test_window=test_window,
            embargo_window=embargo_window,
            label_horizon_days=label_horizon_days,
            event_end_dates=event_end_dates,
            decision_dates=decision_dates,
            decision_cutoff=decision_cutoff,
            backend=self.backend,
        )

    def _get_feature_columns(self, df: FrameLike) -> list[str]:
        """Return columns that should be fed into the model."""
        return [col for col in ensure_polars(df).columns if col not in NON_FEATURE_COLUMNS]

    def _build_model_filename(self, instrument: InstrumentKey, trained_on: date) -> str:
        return f"instrument-v{IDENTITY_SCHEMA_VERSION}-{instrument.artifact_token}_{self.backend}_{self.model_mode}_{trained_on.isoformat()}.pkl"

    def _parse_model_path(self, model_path: Path) -> tuple[str, date]:
        # The backend token separates ``{ticker}_{backend}_{mode}_{date}``. Try
        # ``_lightgbm_`` first (longer token); ``_xgboost_`` stays loadable for
        # pre-Phase-2 artifacts (DEFAULT_BACKEND == "xgboost").
        stem = model_path.stem
        suffix: str | None = None
        for token in ("_lightgbm_", "_xgboost_"):
            if token in stem:
                _, _, suffix = stem.partition(token)
                break
        if not suffix:
            raise ValueError(f"Unrecognized model filename: {model_path.name}")
        if "_" in suffix:
            mode_part, date_part = suffix.rsplit("_", 1)
            if mode_part in MODEL_MODES:
                return mode_part, date.fromisoformat(date_part)
        return "v1_direction", date.fromisoformat(suffix)

    def _metadata_instrument(self, model_path: Path) -> InstrumentKey | None:
        metadata = self._load_training_metadata_for_model(model_path)
        version = metadata.get("identity_schema_version") if metadata is not None else None
        if isinstance(version, bool) or version != IDENTITY_SCHEMA_VERSION or metadata is None:
            return None
        raw_instrument = metadata.get("instrument")
        if not isinstance(raw_instrument, dict):
            return None
        try:
            instrument = InstrumentKey.model_validate(raw_instrument)
        except (ValueError, TypeError):
            return None
        if metadata.get("ticker") != instrument.symbol:
            return None
        return instrument

    def _resolve_model_path(self, ticker: str | InstrumentKey, as_of_date: date) -> Path | None:
        """Resolve the newest identity-compatible artifact by trusted refit date."""
        instrument: InstrumentKey
        if isinstance(ticker, InstrumentKey):
            instrument = ticker
        else:
            possible = [
                *self.model_dir.glob(f"instrument-v{IDENTITY_SCHEMA_VERSION}-*_{self.backend}_{self.model_mode}_*.pkl"),
                *self.model_dir.glob(f"{ticker}_{self.backend}_*.pkl"),
            ]
            identities = {
                identity.tuple: identity
                for path in possible
                if (identity := self._metadata_instrument(path)) is not None and identity.symbol == ticker
            }
            if len(identities) > 1:
                raise InstrumentIdentityError(f"Bare model symbol {ticker!r} has ambiguous artifact identity; pass an InstrumentKey")
            if not identities:
                return None
            instrument = next(iter(identities.values()))

        canonical_prefix = f"instrument-v{IDENTITY_SCHEMA_VERSION}-{instrument.artifact_token}_"
        paths = set(
            [
                *self.model_dir.glob(f"{canonical_prefix}{self.backend}_{self.model_mode}_*.pkl"),
                *self.model_dir.glob(f"{instrument.symbol}_{self.backend}_*.pkl"),
            ]
        )
        candidates: list[tuple[date, int, Path]] = []
        has_future_model = False
        has_unknown_provenance = False
        for model_path in paths:
            try:
                filename_mode, _filename_date = self._parse_model_path(model_path)
            except ValueError:
                continue
            if filename_mode != self.model_mode:
                continue
            metadata = self._load_training_metadata_for_model(model_path)
            if metadata is None or self._metadata_instrument(model_path) != instrument:
                continue
            selected_artifact_matches = (
                metadata.get("provenance_version") == MODEL_PROVENANCE_VERSION
                and metadata.get("model_mode") == self.model_mode
                and metadata.get("backend") == self.backend
                and metadata.get("model_file") == model_path.name
            )
            if not selected_artifact_matches:
                has_unknown_provenance = True
                continue
            try:
                self._verify_model_artifact(metadata, model_path)
                refit_boundary = self._metadata_date(metadata, "refit_boundary")
                trained_on = self._metadata_date(metadata, "trained_on")
            except ModelProvenanceError:
                has_unknown_provenance = True
                continue
            if trained_on != refit_boundary:
                has_unknown_provenance = True
                continue
            if refit_boundary > as_of_date:
                has_future_model = True
                continue
            canonical_priority = 1 if model_path.name.startswith(canonical_prefix) else 0
            candidates.append((refit_boundary, canonical_priority, model_path))

        if not candidates:
            if has_future_model:
                raise ModelFutureError(f"All identity-compatible model refits are after inference date {as_of_date}")
            if has_unknown_provenance:
                raise ModelProvenanceError(f"Matching model artifact has no trusted refit-boundary provenance for {instrument.display_key}")
            return None
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[-1][2]

    _MAX_CACHED_MODELS = 20

    def _load_model(self, model_path: Path) -> ModelBackend:
        metadata = self._load_training_metadata_for_model(model_path)
        if metadata is None or metadata.get("provenance_version") != MODEL_PROVENANCE_VERSION or metadata.get("model_file") != model_path.name:
            raise ModelProvenanceError("Model requires current sidecar metadata bound to the selected artifact")
        try:
            artifact_file = model_path.open("rb")
        except OSError as exc:
            raise ModelProvenanceError(f"Could not open model artifact {model_path}: {exc}") from exc
        with artifact_file:
            digest = hashlib.sha256()
            for chunk in iter(lambda: artifact_file.read(1024 * 1024), b""):
                digest.update(chunk)
            artifact_digest = self._validate_artifact_digest(metadata, model_path, digest.hexdigest())
            if model_path in self._loaded_models and self._loaded_model_digests.get(model_path) == artifact_digest:
                self._loaded_models.move_to_end(model_path)
                return self._loaded_models[model_path]
            self._loaded_models.pop(model_path, None)
            self._loaded_model_digests.pop(model_path, None)
            if len(self._loaded_models) >= self._MAX_CACHED_MODELS:
                evicted_path, _ = self._loaded_models.popitem(last=False)
                self._loaded_model_digests.pop(evicted_path, None)
                logger.debug("model_cache_evict", path=str(evicted_path))
            artifact_file.seek(0)
            model = cast(ModelBackend, joblib.load(artifact_file))
        # D10: ``_xgb_version`` is XGBoost-internal; LightGBM models have no such
        # attribute, so the version-mismatch check is XGBoost-only.
        if backend_of(model) == "xgboost":
            loaded_xgb = getattr(model, "_xgb_version", "unknown")
            current_xgb = xgb.__version__
            if loaded_xgb != "unknown" and loaded_xgb != current_xgb:
                logger.warning("model_version_mismatch", loaded_xgboost=loaded_xgb, current_xgboost=current_xgb, model=str(model_path))
        self._loaded_models[model_path] = model
        self._loaded_model_digests[model_path] = artifact_digest
        return self._loaded_models[model_path]

    def _save_training_metadata(
        self,
        *,
        model_path: Path,
        ticker: str,
        instrument: InstrumentKey,
        start_date: date,
        end_date: date,
        training_start: date,
        refit_boundary: date,
        feature_cols: list[str],
        feature_dtypes: dict[str, str],
        training_feature_schema_versions: list[int],
        candidate_strategies: list[dict[str, Any]],
        price_adjustment_version: str,
        availability_schema_version: int,
        training_config: dict[str, Any],
        X_train: pl.DataFrame,
        X_val: pl.DataFrame,
        y_train: pl.Series,
        y_val: pl.Series,
        model: ModelBackend,
        validation_settings: dict[str, Any],
        validation_metrics: dict[str, Any] | None,
        class_counts: dict[str, Any] | None = None,
        shap_importance: dict[str, float] | None = None,
        optimized_threshold: float | None = None,
    ) -> None:
        metadata_path = model_path.with_suffix(".training_metadata.json")
        train_hash = hashlib.sha256(X_train.hash_rows().to_numpy().tobytes()).hexdigest()[:12]
        train_preds = model.predict(X_train)
        train_acc = float((train_preds == y_train.to_numpy()).mean())
        val_acc = float((model.predict(X_val) == y_val.to_numpy()).mean()) if X_val.height > 0 else None
        metadata: dict[str, Any] = {
            "provenance_version": MODEL_PROVENANCE_VERSION,
            "artifact_sha256": self._sha256_file(model_path),
            "identity_schema_version": IDENTITY_SCHEMA_VERSION,
            "instrument": instrument.model_dump(mode="json"),
            "ticker": ticker,
            "trained_on": str(refit_boundary),
            "refit_boundary": str(refit_boundary),
            "training_start": str(training_start),
            "model_mode": self.model_mode,
            "backend": self.backend,
            "data_range": {"start": str(start_date), "end": str(end_date)},
            "train_rows": X_train.height,
            "val_rows": X_val.height,
            "features": feature_cols,
            "feature_dtypes": feature_dtypes,
            "training_feature_schema_version": training_feature_schema_versions[0] if len(training_feature_schema_versions) == 1 else None,
            "training_feature_schema_versions": training_feature_schema_versions,
            "price_adjustment_version": price_adjustment_version,
            "availability_schema_version": availability_schema_version,
            "candidate_strategies": candidate_strategies,
            "candidate_strategy_fingerprint": self._candidate_strategy_fingerprint(candidate_strategies),
            "missing_value_policy": MISSING_VALUE_POLICY,
            "model_age_policy": MODEL_AGE_POLICY,
            "training_config": training_config,
            "data_hash": train_hash,
            "metrics": {"train_accuracy": round(train_acc, 4)},
            "model_file": model_path.name,
            "validation": validation_settings,
            "target_settings": self.v2_settings if self.model_mode == "v2_meta_label" else {"label_horizon_days": 1},
        }
        if val_acc is not None:
            metadata["metrics"]["val_accuracy"] = round(val_acc, 4)
        if validation_metrics:
            metadata["validation_metrics"] = validation_metrics
        if class_counts:
            metadata["class_balance"] = class_counts
        if shap_importance:
            metadata["shap_feature_importance"] = shap_importance
        if optimized_threshold is not None:
            metadata["optimized_threshold"] = optimized_threshold
        with contextlib.suppress(Exception):
            metadata["params"] = model.get_params()
        artifact = TrainingMetadataArtifact.model_validate(metadata)
        metadata_path.write_text(json.dumps(artifact.model_dump(mode="python"), indent=2, default=str))
        logger.debug("metadata_saved", path=str(metadata_path))

    def _build_training_summary(
        self,
        *,
        ticker: str,
        instrument: InstrumentKey,
        trained_on: date,
        model_path: Path,
        training_start: date,
        train_rows: int,
        val_rows: int,
        validation_metrics: dict[str, Any] | None,
    ) -> dict[str, Any]:
        summary: dict[str, Any] = {
            **instrument.as_columns(),
            "identity_schema_version": IDENTITY_SCHEMA_VERSION,
            "trained_on": str(trained_on),
            "refit_boundary": str(trained_on),
            "training_start": str(training_start),
            "model_mode": self.model_mode,
            "model_file": model_path.name,
            "train_rows": train_rows,
            "validation_rows": val_rows,
            "validation_fold_count": int((validation_metrics or {}).get("folds", 0)),
            "mean_accuracy": float((validation_metrics or {}).get("mean_accuracy", 0.0)),
            "mean_precision": float((validation_metrics or {}).get("mean_precision", 0.0)),
            "mean_recall": float((validation_metrics or {}).get("mean_recall", 0.0)),
            "status": "trained",
        }
        if self.model_mode == "v2_meta_label":
            summary["barrier_settings"] = {
                "vertical_barrier_days": int(self.v2_settings["vertical_barrier_days"]),
                "pt_mult": float(self.v2_settings["pt_mult"]),
                "sl_mult": float(self.v2_settings["sl_mult"]),
                "meta_label_threshold": float(self.v2_settings["meta_label_threshold"]),
            }
        return summary

    def _save_training_summary(self, model_path: Path, summary: dict[str, Any]) -> None:
        summary_path = model_path.with_suffix(".training_summary.json")
        summary_path.write_text(json.dumps(summary, indent=2, default=str))
        logger.debug("training_summary_saved", path=str(summary_path))

    def _save_training_audit_artifact(self, model_path: Path, audit_frame: pl.DataFrame) -> None:
        audit_path = model_path.with_suffix(".training_audit.parquet")
        audit_columns = [
            "market",
            "mic",
            "symbol",
            "identity_schema_version",
            "ticker",
            "date",
            "candidate_action",
            "candidate_source",
            "candidate_score",
            "meta_label",
            "barrier_outcome",
            "upper_barrier_return",
            "lower_barrier_return",
            "vertical_barrier_days",
        ]
        audit_frame.select(audit_columns).write_parquet(audit_path)
        logger.debug("training_audit_saved", path=str(audit_path))

    def _prepare_scoring_matrix(self, scoring_df: FrameLike, model_path: Path | None) -> pl.DataFrame:
        scoring_frame = ensure_polars(scoring_df)
        if model_path is None:
            raise ModelProvenanceError("Scoring requires a metadata-backed model artifact")
        metadata = self._load_training_metadata_for_model(model_path)
        if metadata is None:
            raise ModelProvenanceError("Scoring model metadata is missing")
        trained_features = metadata.get("features")
        if not isinstance(trained_features, list) or set(trained_features) != set(self._get_feature_columns(scoring_frame)):
            raise ModelIncompatibleError("Scoring feature names differ from the model training contract")
        return self._prepare_training_matrix(scoring_frame, trained_features)

    def _prepare_training_matrix(self, df: FrameLike, feature_cols: list[str]) -> pl.DataFrame:
        """Cast numeric features and preserve missing observations as native NaN."""
        frame = ensure_polars(df)
        for column in feature_cols:
            if column not in frame.columns or not frame[column].dtype.is_numeric():
                raise ModelIncompatibleError(f"Feature {column!r} has no supported numeric dtype")
        return frame.select([pl.col(column).cast(pl.Float64).fill_null(float("nan")).alias(column) for column in feature_cols])

    def _check_feature_skew(self, scoring_frame: pl.DataFrame, trained_features: list[str], metadata: dict[str, Any]) -> None:
        inference_cols = set(scoring_frame.columns)
        trained_set = set(trained_features)
        missing_in_inference = trained_set - inference_cols
        # Intentional non-predictors (identity, target, availability metadata,
        # candidate bookkeeping) are never "extra" features.
        extra_in_inference = inference_cols - trained_set - NON_FEATURE_COLUMNS
        if missing_in_inference or extra_in_inference:
            logger.warning(
                "feature_skew_detected",
                missing_from_inference=sorted(missing_in_inference),
                extra_in_inference=sorted(extra_in_inference),
                trained_feature_count=len(trained_features),
                inference_feature_count=len(inference_cols),
            )
        trained_dtypes = metadata.get("feature_dtypes")
        if trained_dtypes:
            for col_name, expected_type in trained_dtypes.items():
                if col_name in scoring_frame.columns and str(scoring_frame[col_name].dtype) != expected_type:
                    logger.warning(
                        "feature_dtype_mismatch",
                        column=col_name,
                        expected=expected_type,
                        actual=str(scoring_frame[col_name].dtype),
                    )

    def _prepare_target_series(self, df: FrameLike, target_column: str) -> pl.Series:
        frame = ensure_polars(df)
        return frame[target_column].cast(pl.Int64, strict=False)

    def _load_training_metadata_for_model(self, model_path: Path) -> dict[str, Any] | None:
        metadata_path = model_path.with_suffix(".training_metadata.json")
        if not metadata_path.exists():
            return None
        try:
            raw_metadata = json.loads(metadata_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ModelProvenanceError(f"Could not read training metadata {metadata_path}: {exc}") from exc
        if not isinstance(raw_metadata, dict):
            raise ModelProvenanceError(f"Training metadata {metadata_path} must contain a JSON object")
        return cast(dict[str, Any], raw_metadata)

    def load_training_metadata(self, ticker: str, as_of_date: date) -> dict[str, Any] | None:
        model_path = self._resolve_model_path(ticker, as_of_date)
        if model_path is None:
            return None
        return self._load_training_metadata_for_model(model_path)

    def load_training_summary(self, ticker: str, as_of_date: date) -> dict[str, Any] | None:
        model_path = self._resolve_model_path(ticker, as_of_date)
        if model_path is None:
            return None
        summary_path = model_path.with_suffix(".training_summary.json")
        if not summary_path.exists():
            return None
        try:
            return cast(dict[str, Any], json.loads(summary_path.read_text()))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not read training summary {summary_path}: {exc}") from exc

    def last_training_summary(self) -> dict[str, Any] | None:
        """Return the summary for the most recent training or reuse action."""
        if self._last_training_artifacts is None:
            return None
        return self._last_training_artifacts.summary

    def close(self) -> None:
        self._feature_loader.close()
