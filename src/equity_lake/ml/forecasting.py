"""Price-forecasting domain logic for the equity pipeline package."""

from __future__ import annotations

import collections
import contextlib
import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

import joblib
import numpy as np
import polars as pl
import structlog
import xgboost as xgb
from sklearn.model_selection import GridSearchCV
from tqdm import tqdm

from equity_lake.core.polars_utils import FrameLike, ensure_polars
from equity_lake.ml.candidates import DEFAULT_BACKTEST_STRATEGY, build_candidate_frame
from equity_lake.ml.feature_loader import FeatureLoader
from equity_lake.ml.labeling import apply_triple_barrier_labels
from equity_lake.ml.sample_weights import compute_uniqueness_weights
from equity_lake.ml.trainer import compute_class_weights, compute_shap_importance, optimize_threshold
from equity_lake.ml.validation import PurgedEmbargoedWalkForwardSplitter, run_purged_walk_forward_validation

logger = structlog.get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_MODEL_DIR = DATA_DIR / "models"
MODEL_MODES = {"v1_direction", "v2_meta_label"}
NON_FEATURE_COLUMNS = {
    "ticker",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "next_day_return",
    "feature_schema_version",
    "candidate_action",
    "candidate_source",
    "candidate_score",
    "meta_label",
    "barrier_outcome",
    "upper_barrier_return",
    "lower_barrier_return",
    "vertical_barrier_days",
}
DEFAULT_V2_SETTINGS = {
    "vertical_barrier_days": 5,
    "pt_mult": 1.5,
    "sl_mult": 1.0,
    "embargo_days": 1,
    "meta_label_threshold": 0.55,
}


@dataclass
class TrainingArtifacts:
    """Internal training outputs reused by the CLI and artifact writers."""

    summary: dict[str, Any]
    model_path: Path
    audit_frame: pl.DataFrame | None = None


class PriceForecaster:
    """XGBoost-based forecaster for v1 direction and v2 meta-label models."""

    def __init__(
        self,
        model_dir: str | None = None,
        model_mode: str = "v1_direction",
        ml_config: dict[str, Any] | None = None,
    ) -> None:
        if model_mode not in MODEL_MODES:
            raise ValueError(f"Unsupported model_mode={model_mode!r}")

        self.model_mode = model_mode
        self.model_dir = Path(model_dir) if model_dir else DEFAULT_MODEL_DIR
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self._feature_loader = FeatureLoader()
        self._loaded_models: collections.OrderedDict[Path, xgb.XGBClassifier] = collections.OrderedDict()
        self.ml_config = dict(ml_config or {})
        self.v2_settings = {**DEFAULT_V2_SETTINGS, **self.ml_config}
        self.candidate_strategies = self._load_candidate_strategies()
        self._last_training_artifacts: TrainingArtifacts | None = None

    def load_features(
        self,
        ticker: str,
        start_date: date,
        end_date: date,
    ) -> pl.DataFrame:
        return self._feature_loader.load_features(ticker, start_date, end_date)

    def _load_candidate_strategies(self) -> list[dict[str, Any]]:
        configured = self.ml_config.get("candidate_strategies")
        if configured:
            return [dict(strategy) for strategy in configured]

        with contextlib.suppress(Exception):
            from equity_lake.signals.config import load_signal_config

            backtest = load_signal_config().backtest
            strategies = backtest.get("strategies", [])
            if strategies:
                return [dict(strategy) for strategy in strategies]

        return [dict(DEFAULT_BACKTEST_STRATEGY)]

    def train_model(
        self,
        ticker: str,
        start_date: date,
        end_date: date,
        params: dict[str, Any] | None = None,
        tune_hyperparams: bool = False,
        validate: bool = False,
        max_model_age_days: int = 7,
        validation_mode: str = "purged_walk_forward",
        train_window: int = 252,
        test_window: int = 21,
        embargo_window: int = 1,
        label_horizon_days: int | None = None,
    ) -> xgb.XGBClassifier:
        """Train and persist an XGBoost classifier."""
        self._last_training_artifacts = None
        if max_model_age_days > 0:
            existing_path = self._resolve_model_path(ticker, end_date)
            if existing_path is not None:
                trained_on = self._parse_model_path(existing_path)[1]
                age_days = (end_date - trained_on).days
                if age_days <= max_model_age_days:
                    logger.info(
                        "model_skipped_fresh",
                        ticker=ticker,
                        trained_on=str(trained_on),
                        age_days=age_days,
                        model_mode=self.model_mode,
                    )
                    self._last_training_artifacts = TrainingArtifacts(
                        summary={
                            "ticker": ticker,
                            "trained_on": str(trained_on),
                            "model_mode": self.model_mode,
                            "model_file": existing_path.name,
                            "status": "reused_fresh_model",
                        },
                        model_path=existing_path,
                    )
                    return self._load_model(existing_path)

        df = ensure_polars(self.load_features(ticker, start_date, end_date))
        if df.is_empty():
            raise ValueError(f"No features available for {ticker}")

        label_horizon = label_horizon_days or self._label_horizon_days()
        training_df = self._prepare_training_frame(df)
        if training_df.is_empty():
            raise ValueError(f"No training rows available for {ticker} in mode {self.model_mode}")

        feature_cols = self._get_feature_columns(training_df)
        target_column = "meta_label" if self.model_mode == "v2_meta_label" else "target"
        df_clean = training_df.filter(pl.col(target_column).is_not_null())
        if df_clean.is_empty():
            raise ValueError(f"No non-null training rows available for {ticker}")

        X = self._prepare_training_matrix(df_clean, feature_cols)
        y = self._prepare_target_series(df_clean, target_column)

        if self.model_mode == "v2_meta_label":
            sample_weights_full = compute_uniqueness_weights(df_clean)
        else:
            sample_weights_full = np.ones(df_clean.height, dtype=np.float64)

        split_idx = max(int(df_clean.height * 0.8), 1)
        embargo = max(label_horizon, 0)
        train_end = max(split_idx - embargo, 1)
        X_train = X.slice(0, train_end)
        X_val = X.slice(split_idx)
        y_train = y.slice(0, train_end)
        y_val = y.slice(split_idx)
        sample_weights_train = sample_weights_full[:train_end]
        sample_weights_val = sample_weights_full[split_idx:]

        validation_settings = {
            "validation_mode": validation_mode,
            "train_window": train_window,
            "test_window": test_window,
            "embargo_window": embargo_window,
            "label_horizon_days": label_horizon,
        }

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
            )
            logger.info("model_validation_completed", ticker=ticker, model_mode=self.model_mode, **metrics)

        default_params: dict[str, Any] = {
            "max_depth": 5,
            "learning_rate": 0.05,
            "n_estimators": 200,
            "subsample": 0.9,
            "colsample_bytree": 0.9,
            "objective": "binary:logistic",
            "eval_metric": "logloss",
            "random_state": 42,
            "n_jobs": -1,
        }
        if params:
            default_params.update(params)

        class_counts = compute_class_weights(y_train)
        if class_counts["scale_pos_weight"] != 1.0:
            default_params["scale_pos_weight"] = class_counts["scale_pos_weight"]

        if tune_hyperparams:
            model = self._tune_hyperparameters(
                X_train,
                y_train,
                sample_weight=sample_weights_train,
                train_window=train_window,
                test_window=test_window,
                embargo_window=embargo_window,
                label_horizon_days=label_horizon,
            )
        else:
            model = xgb.XGBClassifier(**default_params)
            fit_kwargs: dict[str, Any] = {"verbose": False}
            if X_val.height > 0:
                fit_kwargs["eval_set"] = [(X_val, y_val.to_numpy())]
                fit_kwargs["sample_weight_eval_set"] = [sample_weights_val]
            model.fit(X_train, y_train.to_numpy(), sample_weight=sample_weights_train, **fit_kwargs)

        model_path = self.model_dir / self._build_model_filename(ticker, end_date)
        joblib.dump(model, model_path)

        audit_frame = df_clean if self.model_mode == "v2_meta_label" else None
        shap_importance = compute_shap_importance(model, X_val if X_val.height > 0 else X_train, feature_cols)
        optimized_threshold: float | None = None
        if X_val.height > 0:
            y_val_proba = model.predict_proba(X_val)[:, 1]
            optimized_threshold = optimize_threshold(y_val, y_val_proba)
        self._save_training_metadata(
            model_path=model_path,
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            feature_cols=feature_cols,
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
            ticker=ticker,
            trained_on=end_date,
            model_path=model_path,
            train_rows=X_train.height,
            val_rows=X_val.height,
            validation_metrics=metrics,
        )
        self._save_training_summary(model_path, summary)
        if audit_frame is not None:
            self._save_training_audit_artifact(model_path, audit_frame)
        self._last_training_artifacts = TrainingArtifacts(summary=summary, model_path=model_path, audit_frame=audit_frame)

        logger.info("model_saved", ticker=ticker, path=str(model_path), model_mode=self.model_mode)
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
    ) -> xgb.XGBClassifier:
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
        )
        cv = splitter if splitter.get_n_splits(X_train) > 0 else 2

        class_counts = compute_class_weights(y_train)
        estimator_kwargs: dict[str, Any] = {
            "objective": "binary:logistic",
            "eval_metric": "logloss",
            "random_state": 42,
            "n_jobs": -1,
        }
        if class_counts["scale_pos_weight"] != 1.0:
            estimator_kwargs["scale_pos_weight"] = class_counts["scale_pos_weight"]

        fit_kwargs: dict[str, Any] = {}
        if sample_weight is not None:
            fit_kwargs["sample_weight"] = sample_weight

        grid_search = GridSearchCV(
            estimator=xgb.XGBClassifier(**estimator_kwargs),
            param_grid=param_grid,
            cv=cv,
            scoring="accuracy",
            n_jobs=-1,
            verbose=0,
        )
        grid_search.fit(X_train, y_train.to_numpy(), **fit_kwargs)
        logger.info("model_tuned", best_params=grid_search.best_params_, model_mode=self.model_mode)
        return cast(xgb.XGBClassifier, grid_search.best_estimator_)

    def predict(
        self,
        ticker: str,
        date: date,
        model: xgb.XGBClassifier | None = None,
    ) -> dict[str, Any]:
        """Generate a prediction for a single ticker."""
        start_date = date - timedelta(days=180 if self.model_mode == "v2_meta_label" else 60)
        df = ensure_polars(self.load_features(ticker, start_date, date))
        if df.is_empty():
            raise ValueError(f"No features available for {ticker} on {date}")

        scoring_df = self._prepare_scoring_frame(df, date)
        if scoring_df.is_empty():
            raise ValueError(f"No scoring features found for {ticker} on {date}")

        model_path: Path | None = None
        if model is None:
            model_path = self._resolve_model_path(ticker, date)
            if model_path is None:
                raise ValueError(f"No trained model found for {ticker}")
            model = self._load_model(model_path)

        X = self._prepare_scoring_matrix(scoring_df, model_path)
        probability = float(model.predict_proba(X)[0][1])
        optimized_threshold: float | None = None
        if model_path is not None:
            metadata = self._load_training_metadata_for_model(model_path)
            if metadata:
                opt = metadata.get("optimized_threshold")
                if isinstance(opt, int | float):
                    optimized_threshold = float(opt)
        prediction_threshold = optimized_threshold if optimized_threshold is not None else 0.5
        prediction = int(probability >= prediction_threshold)

        if self.model_mode == "v2_meta_label":
            candidate_row = scoring_df.row(0, named=True)
            threshold = optimized_threshold if optimized_threshold is not None else float(self.v2_settings["meta_label_threshold"])
            return {
                "ticker": ticker,
                "date": date,
                "prediction": int(probability >= threshold),
                "probability": probability,
                "execution_probability": probability,
                "candidate_action": candidate_row["candidate_action"],
                "candidate_source": candidate_row["candidate_source"],
                "meta_label_threshold": threshold,
                "should_execute": bool(probability >= threshold),
                "barrier_settings": {
                    "vertical_barrier_days": int(self.v2_settings["vertical_barrier_days"]),
                    "pt_mult": float(self.v2_settings["pt_mult"]),
                    "sl_mult": float(self.v2_settings["sl_mult"]),
                },
                "model_mode": self.model_mode,
                "model_version": model_path.stem if model_path else "provided_model",
            }

        return {
            "ticker": ticker,
            "date": date,
            "prediction": prediction,
            "probability": probability,
            "model_mode": self.model_mode,
            "model_version": model_path.stem if model_path else "provided_model",
        }

    def backtest(
        self,
        ticker: str,
        start_date: date,
        end_date: date,
        train_window: int = 500,
        retrain_interval: int = 63,
    ) -> pl.DataFrame:
        """Walk-forward backtest that retrains the model periodically."""
        df = ensure_polars(self.load_features(ticker, start_date, end_date))
        training_df = self._prepare_training_frame(df)
        if training_df.height < train_window + 10:
            raise ValueError(f"Not enough data for backtesting. Need at least {train_window + 10} days")

        target_column = "meta_label" if self.model_mode == "v2_meta_label" else "target"
        results: list[dict[str, Any]] = []
        feature_cols = self._get_feature_columns(training_df)
        last_train_idx = 0

        for i in tqdm(range(training_df.height - train_window), desc="Backtesting"):
            test_idx = i + train_window
            if test_idx >= training_df.height:
                break

            if i == 0 or (test_idx - last_train_idx) >= retrain_interval:
                train_start = max(0, test_idx - train_window)
                train_slice = training_df.slice(train_start, test_idx - train_start)
                X_tr = self._prepare_training_matrix(train_slice, feature_cols)
                y_tr = self._prepare_target_series(train_slice, target_column)
                model = xgb.XGBClassifier(
                    max_depth=5,
                    learning_rate=0.05,
                    n_estimators=200,
                    objective="binary:logistic",
                    eval_metric="logloss",
                    random_state=42,
                    n_jobs=-1,
                )
                model.fit(X_tr, y_tr.to_numpy(), verbose=False)
                last_train_idx = test_idx

            test_slice = training_df.slice(test_idx, 1)
            X_test = self._prepare_training_matrix(test_slice, feature_cols)
            test_row = training_df.row(test_idx, named=True)
            y_true = int(test_row[target_column])
            proba = float(model.predict_proba(X_test)[0][1])
            pred = int(proba > 0.5)
            result = {
                "date": test_row["date"],
                "prediction": pred,
                "probability": proba,
                "actual": y_true,
                "model_trained_at": str(training_df.row(max(0, test_idx - train_window), named=True)["date"]),
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
        )

    def _get_feature_columns(self, df: FrameLike) -> list[str]:
        """Return columns that should be fed into the model."""
        return [col for col in ensure_polars(df).columns if col not in NON_FEATURE_COLUMNS]

    def _build_model_filename(self, ticker: str, trained_on: date) -> str:
        return f"{ticker}_xgboost_{self.model_mode}_{trained_on.isoformat()}.pkl"

    def _parse_model_path(self, model_path: Path) -> tuple[str, date]:
        ticker_prefix, _, suffix = model_path.stem.partition("_xgboost_")
        if not ticker_prefix or not suffix:
            raise ValueError(f"Unrecognized model filename: {model_path.name}")
        if "_" in suffix:
            mode_part, date_part = suffix.rsplit("_", 1)
            if mode_part in MODEL_MODES:
                return mode_part, date.fromisoformat(date_part)
        return "v1_direction", date.fromisoformat(suffix)

    def _resolve_model_path(self, ticker: str, as_of_date: date) -> Path | None:
        """Return the latest model path available on or before the target date."""
        candidates: list[tuple[date, Path]] = []
        for model_path in self.model_dir.glob(f"{ticker}_xgboost_*.pkl"):
            try:
                mode, trained_on = self._parse_model_path(model_path)
            except ValueError:
                continue
            if mode != self.model_mode:
                continue
            if trained_on <= as_of_date:
                candidates.append((trained_on, model_path))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[-1][1]

    _MAX_CACHED_MODELS = 20

    def _load_model(self, model_path: Path) -> xgb.XGBClassifier:
        if model_path in self._loaded_models:
            self._loaded_models.move_to_end(model_path)
            return self._loaded_models[model_path]
        if len(self._loaded_models) >= self._MAX_CACHED_MODELS:
            evicted_path, _ = self._loaded_models.popitem(last=False)
            logger.debug("model_cache_evict", path=str(evicted_path))
        model = cast(xgb.XGBClassifier, joblib.load(model_path))
        loaded_xgb = getattr(model, "_xgb_version", "unknown")
        current_xgb = xgb.__version__
        if loaded_xgb != "unknown" and loaded_xgb != current_xgb:
            logger.warning("model_version_mismatch", loaded_xgboost=loaded_xgb, current_xgboost=current_xgb, model=str(model_path))
        self._loaded_models[model_path] = model
        return self._loaded_models[model_path]

    def _save_training_metadata(
        self,
        *,
        model_path: Path,
        ticker: str,
        start_date: date,
        end_date: date,
        feature_cols: list[str],
        X_train: pl.DataFrame,
        X_val: pl.DataFrame,
        y_train: pl.Series,
        y_val: pl.Series,
        model: xgb.XGBClassifier,
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
            "ticker": ticker,
            "trained_on": str(end_date),
            "model_mode": self.model_mode,
            "data_range": {"start": str(start_date), "end": str(end_date)},
            "train_rows": X_train.height,
            "val_rows": X_val.height,
            "features": feature_cols,
            "feature_dtypes": {col: str(X_train[col].dtype) for col in feature_cols if col in X_train.columns},
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
        metadata_path.write_text(json.dumps(metadata, indent=2, default=str))
        logger.debug("metadata_saved", path=str(metadata_path))

    def _build_training_summary(
        self,
        *,
        ticker: str,
        trained_on: date,
        model_path: Path,
        train_rows: int,
        val_rows: int,
        validation_metrics: dict[str, Any] | None,
    ) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "ticker": ticker,
            "trained_on": str(trained_on),
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
        feature_cols = self._get_feature_columns(scoring_df)
        if model_path is None:
            return self._prepare_training_matrix(scoring_frame, feature_cols)

        metadata = self._load_training_metadata_for_model(model_path)
        trained_features = metadata.get("features") if metadata else None
        if not trained_features:
            return self._prepare_training_matrix(scoring_frame, feature_cols)

        self._check_feature_skew(scoring_frame, trained_features, metadata or {})

        scoring_matrix = scoring_frame
        for feature in trained_features:
            if feature not in scoring_matrix.columns:
                scoring_matrix = scoring_matrix.with_columns(pl.lit(None).cast(pl.Float64).alias(feature))
        return self._prepare_training_matrix(scoring_matrix, list(trained_features))

    def _prepare_training_matrix(self, df: FrameLike, feature_cols: list[str]) -> pl.DataFrame:
        frame = ensure_polars(df)
        return frame.select([pl.col(column).cast(pl.Float64, strict=False).alias(column) for column in feature_cols])

    def _check_feature_skew(self, scoring_frame: pl.DataFrame, trained_features: list[str], metadata: dict[str, Any]) -> None:
        inference_cols = set(scoring_frame.columns)
        trained_set = set(trained_features)
        missing_in_inference = trained_set - inference_cols
        extra_in_inference = inference_cols - trained_set - {"ticker", "date", "candidate_action", "candidate_source", "candidate_score"}
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
        return cast(dict[str, Any], json.loads(metadata_path.read_text()))

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
        return cast(dict[str, Any], json.loads(summary_path.read_text()))

    def last_training_summary(self) -> dict[str, Any] | None:
        """Return the summary for the most recent training or reuse action."""
        if self._last_training_artifacts is None:
            return None
        return self._last_training_artifacts.summary

    def close(self) -> None:
        self._feature_loader.close()
