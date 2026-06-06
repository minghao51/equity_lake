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

import duckdb
import joblib
import pandas as pd
import structlog
import xgboost as xgb
from sklearn.model_selection import GridSearchCV
from tqdm import tqdm

from equity_lake.ml.candidates import DEFAULT_BACKTEST_STRATEGY, build_candidate_frame
from equity_lake.ml.labeling import apply_triple_barrier_labels
from equity_lake.ml.validation import PurgedEmbargoedWalkForwardSplitter, run_purged_walk_forward_validation

logger = structlog.get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
LAKE_DIR = DATA_DIR / "lake"
DEFAULT_MODEL_DIR = DATA_DIR / "models"
FEATURE_GLOB = str(LAKE_DIR / "features" / "**" / "*.parquet")
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
    audit_frame: pd.DataFrame | None = None


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
        self.conn = duckdb.connect(":memory:")
        with contextlib.suppress(Exception):
            self.conn.execute("INSTALL delta; LOAD delta;")
        self._loaded_models: collections.OrderedDict[Path, xgb.XGBClassifier] = collections.OrderedDict()
        self.ml_config = dict(ml_config or {})
        self.v2_settings = {**DEFAULT_V2_SETTINGS, **self.ml_config}
        self.candidate_strategies = self._load_candidate_strategies()
        self._last_training_artifacts: TrainingArtifacts | None = None
        self._setup_feature_view()

    def _setup_feature_view(self) -> None:
        from deltalake import DeltaTable

        features_path = LAKE_DIR / "features"
        if DeltaTable.is_deltatable(str(features_path)):
            scan = f"delta_scan('{features_path}')"
        else:
            scan = f"read_parquet('{FEATURE_GLOB}', hive_partitioning=1, union_by_name=true)"
        self.conn.execute(
            f"""
            CREATE OR REPLACE VIEW features_all AS
            SELECT * REPLACE (CAST(date AS TIMESTAMP) AS date)
            FROM {scan}
            """
        )
        logger.info("duckdb_feature_view_ready", model_mode=self.model_mode)

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

    def load_features(
        self,
        ticker: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """Load features for a ticker and date range."""
        query = """
            SELECT * FROM features_all
            WHERE ticker = $1
            AND date BETWEEN $2 AND $3
            ORDER BY date
        """
        df = self.conn.execute(query, [ticker, start_date, end_date]).df()
        if df.empty:
            logger.warning(
                "features_not_found",
                ticker=ticker,
                start_date=str(start_date),
                end_date=str(end_date),
                model_mode=self.model_mode,
            )
        return df

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

        df = self.load_features(ticker, start_date, end_date)
        if df.empty:
            raise ValueError(f"No features available for {ticker}")

        label_horizon = label_horizon_days or self._label_horizon_days()
        training_df = self._prepare_training_frame(df)
        if training_df.empty:
            raise ValueError(f"No training rows available for {ticker} in mode {self.model_mode}")

        feature_cols = self._get_feature_columns(training_df)
        target_column = "meta_label" if self.model_mode == "v2_meta_label" else "target"
        df_clean = training_df.dropna(subset=[target_column]).copy()
        if df_clean.empty:
            raise ValueError(f"No non-null training rows available for {ticker}")

        X = df_clean[feature_cols].apply(pd.to_numeric, errors="coerce").fillna(0)
        y = df_clean[target_column].astype(int)

        split_idx = max(int(len(X) * 0.8), 1)
        X_train, X_val = X.iloc[:split_idx], X.iloc[split_idx:]
        y_train, y_val = y.iloc[:split_idx], y.iloc[split_idx:]

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

        if tune_hyperparams:
            model = self._tune_hyperparameters(
                X_train,
                y_train,
                train_window=train_window,
                test_window=test_window,
                embargo_window=embargo_window,
                label_horizon_days=label_horizon,
            )
        else:
            model = xgb.XGBClassifier(**default_params)
            fit_kwargs: dict[str, Any] = {"verbose": False}
            if len(X_val) > 0:
                fit_kwargs["eval_set"] = [(X_val, y_val)]
            model.fit(X_train, y_train, **fit_kwargs)

        model_path = self.model_dir / self._build_model_filename(ticker, end_date)
        joblib.dump(model, model_path)

        audit_frame = df_clean if self.model_mode == "v2_meta_label" else None
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
        )
        summary = self._build_training_summary(
            ticker=ticker,
            trained_on=end_date,
            model_path=model_path,
            train_rows=len(X_train),
            val_rows=len(X_val),
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
        X_train: pd.DataFrame,
        y_train: pd.Series,
        *,
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

        grid_search = GridSearchCV(
            estimator=xgb.XGBClassifier(
                objective="binary:logistic",
                eval_metric="logloss",
                random_state=42,
                n_jobs=-1,
            ),
            param_grid=param_grid,
            cv=cv,
            scoring="accuracy",
            n_jobs=-1,
            verbose=0,
        )
        grid_search.fit(X_train, y_train)
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
        df = self.load_features(ticker, start_date, date)
        if df.empty:
            raise ValueError(f"No features available for {ticker} on {date}")

        scoring_df = self._prepare_scoring_frame(df, date)
        if scoring_df.empty:
            raise ValueError(f"No scoring features found for {ticker} on {date}")

        model_path: Path | None = None
        if model is None:
            model_path = self._resolve_model_path(ticker, date)
            if model_path is None:
                raise ValueError(f"No trained model found for {ticker}")
            model = self._load_model(model_path)

        X = self._prepare_scoring_matrix(scoring_df, model_path)
        probability = float(model.predict_proba(X)[0][1])
        prediction = int(probability >= 0.5)

        if self.model_mode == "v2_meta_label":
            candidate_row = scoring_df.iloc[0]
            threshold = float(self.v2_settings["meta_label_threshold"])
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
    ) -> pd.DataFrame:
        """Walk-forward backtest that retrains the model periodically."""
        df = self.load_features(ticker, start_date, end_date)
        training_df = self._prepare_training_frame(df)
        if len(training_df) < train_window + 10:
            raise ValueError(f"Not enough data for backtesting. Need at least {train_window + 10} days")

        target_column = "meta_label" if self.model_mode == "v2_meta_label" else "target"
        results: list[dict[str, Any]] = []
        feature_cols = self._get_feature_columns(training_df)
        last_train_idx = 0

        for i in tqdm(range(len(training_df) - train_window), desc="Backtesting"):
            test_idx = i + train_window
            if test_idx >= len(training_df):
                break

            if i == 0 or (test_idx - last_train_idx) >= retrain_interval:
                train_start = max(0, test_idx - train_window)
                X_tr = training_df.iloc[train_start:test_idx][feature_cols].fillna(0)
                y_tr = training_df.iloc[train_start:test_idx][target_column].astype(int)
                model = xgb.XGBClassifier(
                    max_depth=5,
                    learning_rate=0.05,
                    n_estimators=200,
                    objective="binary:logistic",
                    eval_metric="logloss",
                    random_state=42,
                    n_jobs=-1,
                )
                model.fit(X_tr, y_tr, verbose=False)
                last_train_idx = test_idx

            X_test = training_df.iloc[test_idx : test_idx + 1][feature_cols].fillna(0)
            y_true = int(training_df.iloc[test_idx][target_column])
            proba = float(model.predict_proba(X_test)[0][1])
            pred = int(proba > 0.5)
            result = {
                "date": training_df.iloc[test_idx]["date"],
                "prediction": pred,
                "probability": proba,
                "actual": y_true,
                "model_trained_at": str(training_df.iloc[max(0, test_idx - train_window)]["date"]),
            }
            if self.model_mode == "v1_direction":
                result["actual_return"] = training_df.iloc[test_idx]["next_day_return"]
            else:
                result["candidate_action"] = training_df.iloc[test_idx]["candidate_action"]
                result["barrier_outcome"] = training_df.iloc[test_idx]["barrier_outcome"]
            results.append(result)

        return pd.DataFrame(results)

    def _prepare_training_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        frame = df.sort_values("date").reset_index(drop=True).copy()
        if self.model_mode == "v2_meta_label":
            candidates = self._build_candidate_frame(frame)
            if candidates.empty:
                return candidates
            return self._apply_triple_barrier_labels(candidates, frame)

        frame["target"] = (frame["next_day_return"] > 0).astype(int)
        return frame

    def _prepare_scoring_frame(self, df: pd.DataFrame, prediction_date: date) -> pd.DataFrame:
        frame = df.sort_values("date").reset_index(drop=True).copy()
        target_ts = pd.Timestamp(prediction_date)
        if self.model_mode == "v2_meta_label":
            candidates = self._build_candidate_frame(frame)
            return candidates[candidates["date"] == target_ts].tail(1)
        return frame[frame["date"] == target_ts].tail(1)

    def _build_candidate_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        return build_candidate_frame(df, self.candidate_strategies)

    def _apply_triple_barrier_labels(self, candidates: pd.DataFrame, full_df: pd.DataFrame) -> pd.DataFrame:
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
        X: pd.DataFrame,
        y: pd.Series,
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

    def _get_feature_columns(self, df: pd.DataFrame) -> list[str]:
        """Return columns that should be fed into the model."""
        return [col for col in df.columns if col not in NON_FEATURE_COLUMNS]

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
        self._loaded_models[model_path] = cast(xgb.XGBClassifier, joblib.load(model_path))
        return self._loaded_models[model_path]

    def _save_training_metadata(
        self,
        *,
        model_path: Path,
        ticker: str,
        start_date: date,
        end_date: date,
        feature_cols: list[str],
        X_train: pd.DataFrame,
        X_val: pd.DataFrame,
        y_train: pd.Series,
        y_val: pd.Series,
        model: xgb.XGBClassifier,
        validation_settings: dict[str, Any],
        validation_metrics: dict[str, Any] | None,
    ) -> None:
        metadata_path = model_path.with_suffix(".training_metadata.json")
        train_hash = hashlib.sha256(pd.util.hash_pandas_object(X_train, index=True).values.tobytes()).hexdigest()[:12]
        train_acc = float((model.predict(X_train) == y_train.values).mean())
        val_acc = float((model.predict(X_val) == y_val.values).mean()) if len(X_val) > 0 else None
        metadata: dict[str, Any] = {
            "ticker": ticker,
            "trained_on": str(end_date),
            "model_mode": self.model_mode,
            "data_range": {"start": str(start_date), "end": str(end_date)},
            "train_rows": len(X_train),
            "val_rows": len(X_val),
            "features": feature_cols,
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

    def _save_training_audit_artifact(self, model_path: Path, audit_frame: pd.DataFrame) -> None:
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
        audit_frame.loc[:, audit_columns].to_parquet(audit_path, index=False)
        logger.debug("training_audit_saved", path=str(audit_path))

    def _prepare_scoring_matrix(self, scoring_df: pd.DataFrame, model_path: Path | None) -> pd.DataFrame:
        feature_cols = self._get_feature_columns(scoring_df)
        if model_path is None:
            return scoring_df[feature_cols].apply(pd.to_numeric, errors="coerce").fillna(0)

        metadata = self._load_training_metadata_for_model(model_path)
        trained_features = metadata.get("features") if metadata else None
        if not trained_features:
            return scoring_df[feature_cols].apply(pd.to_numeric, errors="coerce").fillna(0)

        scoring_matrix = scoring_df.copy()
        for feature in trained_features:
            if feature not in scoring_matrix.columns:
                scoring_matrix[feature] = 0.0
        return scoring_matrix[list(trained_features)].apply(pd.to_numeric, errors="coerce").fillna(0)

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
        """Close the DuckDB connection."""
        self.conn.close()
