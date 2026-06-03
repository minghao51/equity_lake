"""Price-forecasting domain logic for the equity pipeline package."""

from __future__ import annotations

import collections
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

import duckdb
import joblib
import pandas as pd
import structlog
import xgboost as xgb
from sklearn.model_selection import GridSearchCV, TimeSeriesSplit
from tqdm import tqdm

logger = structlog.get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
LAKE_DIR = DATA_DIR / "lake"
DEFAULT_MODEL_DIR = DATA_DIR / "models"
FEATURE_GLOB = str(LAKE_DIR / "features" / "**" / "*.parquet")
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
}


class PriceForecaster:
    """XGBoost-based price forecaster for equity feature data."""

    def __init__(self, model_dir: str | None = None) -> None:
        self.model_dir = Path(model_dir) if model_dir else DEFAULT_MODEL_DIR
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(":memory:")
        self._loaded_models: collections.OrderedDict[Path, xgb.XGBClassifier] = collections.OrderedDict()
        self._setup_feature_view()

    def _setup_feature_view(self) -> None:
        """Expose feature parquet data through DuckDB."""
        self.conn.execute(
            f"""
            CREATE OR REPLACE VIEW features_all AS
            SELECT *
            FROM read_parquet('{FEATURE_GLOB}', hive_partitioning=1)
            """
        )
        logger.info("duckdb_feature_view_ready")

    def load_features(
        self,
        ticker: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """Load features for a ticker and date range."""
        query = f"""
            SELECT * FROM features_all
            WHERE ticker = '{ticker}'
            AND date BETWEEN '{start_date}' AND '{end_date}'
            ORDER BY date
        """
        df = self.conn.execute(query).df()
        if df.empty:
            logger.warning(
                "features_not_found",
                ticker=ticker,
                start_date=str(start_date),
                end_date=str(end_date),
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
    ) -> xgb.XGBClassifier:
        """Train and persist an XGBoost classifier.

        Skips retraining if a model file already exists and is newer than
        max_model_age_days. Set max_model_age_days=0 to always retrain.
        """
        if max_model_age_days > 0:
            existing_path = self._resolve_model_path(ticker, end_date)
            if existing_path is not None:
                suffix = existing_path.stem.removeprefix(f"{ticker}_xgboost_")
                try:
                    trained_on = date.fromisoformat(suffix)
                    age_days = (end_date - trained_on).days
                    if age_days <= max_model_age_days:
                        logger.info(
                            "model_skipped_fresh",
                            ticker=ticker,
                            trained_on=str(trained_on),
                            age_days=age_days,
                        )
                        return self._load_model(existing_path)
                except ValueError:
                    pass

        df = self.load_features(ticker, start_date, end_date)
        if df.empty:
            raise ValueError(f"No features available for {ticker}")

        feature_cols = self._get_feature_columns(df)
        df_clean = df.dropna(subset=feature_cols + ["next_day_return"]).copy()
        X = df_clean[feature_cols]
        y_binary = (df_clean["next_day_return"] > 0).astype(int)

        split_idx = int(len(X) * 0.8)
        X_train, X_val = X[:split_idx], X[split_idx:]
        y_train, y_val = y_binary[:split_idx], y_binary[split_idx:]

        if validate and split_idx > 60:
            wf_metrics = self._walk_forward_validate(X[:split_idx], y_binary[:split_idx], feature_cols)
            logger.info("walk_forward_validation", ticker=ticker, **wf_metrics)

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
            model = self._tune_hyperparameters(X_train, y_train)
        else:
            model = xgb.XGBClassifier(**default_params)
            if len(X_train) < 100:
                model.fit(X_train, y_train, verbose=False)
            else:
                model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)

        model_path = self.model_dir / f"{ticker}_xgboost_{end_date}.pkl"
        joblib.dump(model, model_path)
        logger.info("model_saved", ticker=ticker, path=str(model_path))
        return model

    def _tune_hyperparameters(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
    ) -> xgb.XGBClassifier:
        """Run time-series-aware grid search for hyperparameters."""
        param_grid = {
            "max_depth": [3, 5, 7],
            "learning_rate": [0.01, 0.05, 0.1],
            "n_estimators": [100, 200, 300],
            "subsample": [0.8, 0.9, 1.0],
        }

        grid_search = GridSearchCV(
            estimator=xgb.XGBClassifier(
                objective="binary:logistic",
                eval_metric="logloss",
                random_state=42,
                n_jobs=-1,
            ),
            param_grid=param_grid,
            cv=TimeSeriesSplit(n_splits=3),
            scoring="accuracy",
            n_jobs=-1,
            verbose=0,
        )
        grid_search.fit(X_train, y_train)
        logger.info("model_tuned", best_params=grid_search.best_params_)
        return cast(xgb.XGBClassifier, grid_search.best_estimator_)

    def predict(
        self,
        ticker: str,
        date: date,
        model: xgb.XGBClassifier | None = None,
    ) -> dict[str, Any]:
        """Generate a next-day direction prediction for a single ticker."""
        start_date = date - timedelta(days=60)
        df = self.load_features(ticker, start_date, date)
        if df.empty:
            raise ValueError(f"No features available for {ticker} on {date}")

        latest = df[df["date"] == pd.Timestamp(date)]
        if latest.empty:
            raise ValueError(f"No features found for {ticker} on {date}")

        model_path: Path | None = None
        if model is None:
            model_path = self._resolve_model_path(ticker, date)
            if model_path is None:
                raise ValueError(f"No trained model found for {ticker}")
            model = self._load_model(model_path)

        feature_cols = self._get_feature_columns(df)
        X = latest[feature_cols].fillna(0)
        probability = float(model.predict_proba(X)[0][1])
        prediction = int(probability > 0.5)

        return {
            "ticker": ticker,
            "date": date,
            "prediction": prediction,
            "probability": probability,
            "model_version": model_path.stem if model_path else "provided_model",
        }

    def backtest(
        self,
        ticker: str,
        start_date: date,
        end_date: date,
        train_window: int = 500,
    ) -> pd.DataFrame:
        """Run a simple walk-forward backtest using the latest saved model."""
        df = self.load_features(ticker, start_date, end_date)
        if len(df) < train_window + 10:
            raise ValueError(f"Not enough data for backtesting. Need at least {train_window + 10} days")

        results: list[dict[str, Any]] = []
        feature_cols = self._get_feature_columns(df)
        model_path = self._resolve_model_path(ticker, end_date)
        if model_path is None:
            raise ValueError(f"No trained model found for {ticker}")

        model = self._load_model(model_path)
        for i in tqdm(range(len(df) - train_window), desc="Backtesting"):
            test_idx = i + train_window
            if test_idx >= len(df):
                break

            X_test = df.iloc[test_idx : test_idx + 1][feature_cols].fillna(0)
            y_true = (df.iloc[test_idx : test_idx + 1]["next_day_return"] > 0).astype(int).values[0]
            proba = float(model.predict_proba(X_test)[0][1])
            pred = int(proba > 0.5)
            results.append(
                {
                    "date": df.iloc[test_idx]["date"],
                    "prediction": pred,
                    "probability": proba,
                    "actual": int(y_true),
                    "actual_return": df.iloc[test_idx]["next_day_return"],
                }
            )

        return pd.DataFrame(results)

    def _get_feature_columns(self, df: pd.DataFrame) -> list[str]:
        """Return columns that should be fed into the model."""
        return [col for col in df.columns if col not in NON_FEATURE_COLUMNS]

    def _resolve_model_path(self, ticker: str, as_of_date: date) -> Path | None:
        """Return the latest model path available on or before the target date."""
        candidates: list[tuple[date, Path]] = []
        for model_path in self.model_dir.glob(f"{ticker}_xgboost_*.pkl"):
            suffix = model_path.stem.removeprefix(f"{ticker}_xgboost_")
            try:
                trained_on = date.fromisoformat(suffix)
            except ValueError:
                continue
            if trained_on <= as_of_date:
                candidates.append((trained_on, model_path))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[-1][1]

    _MAX_CACHED_MODELS = 20

    def _load_model(self, model_path: Path) -> xgb.XGBClassifier:
        """Load and cache a trained model artifact (LRU bounded to 20 entries)."""
        if model_path in self._loaded_models:
            self._loaded_models.move_to_end(model_path)
            return self._loaded_models[model_path]
        if len(self._loaded_models) >= self._MAX_CACHED_MODELS:
            evicted_path, _ = self._loaded_models.popitem(last=False)
            logger.debug("model_cache_evict", path=str(evicted_path))
        self._loaded_models[model_path] = cast(xgb.XGBClassifier, joblib.load(model_path))
        return self._loaded_models[model_path]

    def _walk_forward_validate(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        feature_cols: list[str],
        train_window: int = 252,
        test_window: int = 21,
    ) -> dict[str, float]:
        """Run walk-forward validation on training data.

        Returns dict with mean_accuracy, mean_sharpe, and stability metrics.
        """
        n_folds = 0
        correct = 0
        total = 0
        fold_accuracies: list[float] = []

        step = test_window
        start = 0
        while start + train_window + test_window <= len(X):
            X_tr = X.iloc[start : start + train_window]
            y_tr = y.iloc[start : start + train_window]
            X_te = X.iloc[start + train_window : start + train_window + test_window]
            y_te = y.iloc[start + train_window : start + train_window + test_window]

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
            preds = (model.predict_proba(X_te)[:, 1] > 0.5).astype(int)
            acc = float((preds == y_te.values).mean())
            fold_accuracies.append(acc)
            correct += int((preds == y_te.values).sum())
            total += len(y_te)
            n_folds += 1
            start += step

        if n_folds == 0:
            return {"folds": 0, "mean_accuracy": 0.0}

        import statistics

        return {
            "folds": n_folds,
            "mean_accuracy": correct / total if total > 0 else 0.0,
            "std_accuracy": statistics.stdev(fold_accuracies) if len(fold_accuracies) > 1 else 0.0,
        }

    def close(self) -> None:
        """Close the DuckDB connection."""
        self.conn.close()
