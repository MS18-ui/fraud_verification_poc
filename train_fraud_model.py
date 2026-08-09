"""
train_fraud_model.py

Trains the fraud risk model on the master dataset and saves it in a shape
scoring/fraud_model.py picks up automatically via the FRAUD_MODEL_PATH
environment variable -- no code changes needed anywhere else once this
runs; fraud_probability() switches from the heuristic to this model the
moment FRAUD_MODEL_PATH points at a real file.

Usage:
    python train_fraud_model.py /path/to/master_results.csv [output_path]

Prefers XGBoost (industry default for fraud -- handles class imbalance
well via scale_pos_weight); falls back to scikit-learn's
GradientBoostingClassifier if xgboost isn't installed, so this still runs
somewhere without it.
"""

from __future__ import annotations
import sys

import joblib
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score, classification_report, confusion_matrix

from data.master_dataset_adapter import build_training_frame

try:
    import xgboost as xgb
    HAVE_XGBOOST = True
except ImportError:
    from sklearn.ensemble import GradientBoostingClassifier
    HAVE_XGBOOST = False


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python train_fraud_model.py /path/to/master_results.csv [output_path]")
        sys.exit(1)

    dataset_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else "fraud_model.joblib"

    print(f"Building training frame from {dataset_path} ...")
    frame = build_training_frame(dataset_path)
    n_fraud = int(frame["is_fraud"].sum())
    print(f"Labeled rows: {len(frame)}  (fraud: {n_fraud}, clean: {len(frame) - n_fraud}, "
          f"fraud rate: {n_fraud / len(frame):.2%})")

    # entity_id/typology ride along for record-keeping only, never as model features.
    feature_cols = [c for c in frame.columns if c not in ("is_fraud", "entity_id", "typology")]
    X = frame[feature_cols]
    y = frame["is_fraud"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.15, stratify=y, random_state=42,
    )

    if HAVE_XGBOOST:
        scale_pos_weight = (y_train == 0).sum() / max((y_train == 1).sum(), 1)
        print(f"Using XGBoost. scale_pos_weight = {scale_pos_weight:.1f}")
        model = xgb.XGBClassifier(
            n_estimators=300, max_depth=5, learning_rate=0.05,
            scale_pos_weight=scale_pos_weight, eval_metric="aucpr",
        )
        model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
    else:
        print("xgboost not installed -- using sklearn GradientBoostingClassifier instead.")
        model = GradientBoostingClassifier(n_estimators=200, max_depth=4, learning_rate=0.05, random_state=42)
        model.fit(X_train, y_train)

    probs = model.predict_proba(X_test)[:, 1]
    preds = (probs >= 0.5).astype(int)

    print()
    print("=== Evaluation on held-out test set ===")
    print(f"AUC: {roc_auc_score(y_test, probs):.4f}")
    print(classification_report(y_test, preds, target_names=["clean", "fraud"], zero_division=0))
    print("Confusion matrix (rows=actual, cols=predicted):")
    print(confusion_matrix(y_test, preds))

    importances = pd.Series(model.feature_importances_, index=feature_cols).sort_values(ascending=False)
    print()
    print("Top 10 most important features:")
    print(importances.head(10).to_string())

    joblib.dump(model, out_path)
    print()
    print(f"Model saved to: {out_path}")
    print(f"To use it, set:  FRAUD_MODEL_PATH={out_path}")


if __name__ == "__main__":
    main()
