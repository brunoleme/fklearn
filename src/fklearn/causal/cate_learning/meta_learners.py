import copy
import inspect
from typing import Callable, List, Tuple

import numpy as np
import pandas as pd
from toolz import curry

from fklearn.common_docstrings import (learner_pred_fn_docstring,
                                       learner_return_docstring)
from fklearn.exceptions.exceptions import (MissingControlError,
                                           MissingTreatmentError,
                                           MultipleTreatmentsError)
from fklearn.training.pipeline import build_pipeline
from fklearn.types import LearnerFnType, LearnerReturnType, PredictFnType

TREATMENT_FEATURE = "is_treatment"


def _append_treatment_feature(features: list, treatment_feature: str) -> list:
    return features + [treatment_feature]


def _get_learner_features(learner: Callable) -> list:
    return inspect.signature(learner).parameters["features"].default


def _get_unique_treatments(
    df: pd.DataFrame, treatment_col: str, control_name: str
) -> list:
    if control_name not in df[treatment_col].unique():
        raise MissingControlError()

    return [col for col in df[treatment_col].unique() if col != control_name]


def _filter_by_treatment(
    df: pd.DataFrame, treatment_col: str, treatment_name: str, control_name: str
) -> pd.DataFrame:
    treatment_control_values = df[treatment_col].unique()

    if control_name not in treatment_control_values:
        raise MissingControlError()

    if treatment_name not in treatment_control_values:
        raise MissingTreatmentError()

    treatment_control_df = df.loc[
        (df[treatment_col] == treatment_name) | (df[treatment_col] == control_name), :
    ]
    return treatment_control_df


def _create_treatment_flag(
    df: pd.DataFrame, treatment_col: str, treatment_name: str, control_name: str
) -> pd.DataFrame:
    df = df.copy()

    treatment_control_values = df[treatment_col].unique()

    if len(_get_unique_treatments(df, treatment_col, control_name)) > 1:
        raise MultipleTreatmentsError()

    if control_name not in treatment_control_values:
        raise MissingControlError()

    if treatment_name not in treatment_control_values:
        raise MissingTreatmentError()

    treatment_flag = np.where(df[treatment_col] == treatment_name, 1.0, 0.0)
    df[TREATMENT_FEATURE] = treatment_flag

    return df


def _fit_by_treatment(
    df: pd.DataFrame,
    learner: LearnerFnType,
    treatment_col: str,
    control_name: str,
    treatments: list,
) -> Tuple[dict, dict]:
    fitted_learners = {}
    learners_logs = {}
    for treatment in treatments:
        treatment_control_df = _filter_by_treatment(
            df=df,
            treatment_col=treatment_col,
            treatment_name=treatment,
            control_name=control_name,
        )
        treatment_control_df = _create_treatment_flag(
            df=treatment_control_df,
            treatment_col=treatment_col,
            treatment_name=treatment,
            control_name=control_name,
        )
        learner_fcn, _, learner_log = learner(treatment_control_df)
        fitted_learners[treatment] = learner_fcn
        learners_logs[treatment] = learner_log

    return fitted_learners, learners_logs


def _predict_by_treatment_flag(
    df: pd.DataFrame,
    learner_fcn: PredictFnType,
    is_treatment: bool,
    prediction_column: str,
) -> np.ndarray:

    treatment_flag = np.ones(df.shape[0]) if is_treatment else np.zeros(df.shape[0])
    df[TREATMENT_FEATURE] = treatment_flag
    prediction_df = learner_fcn(df)
    df.drop(columns=[TREATMENT_FEATURE], inplace=True)

    return prediction_df[prediction_column].values


def _simulate_treatment_effect(
    df: pd.DataFrame,
    treatments: list,
    control_name: str,
    learners: dict,
    prediction_column: str,
) -> pd.DataFrame:
    uplift_cols = []
    scored_df = df.copy()
    for treatment in treatments:
        learner_fcn = learners[treatment]

        scored_df[
            f"treatment_{treatment}__{prediction_column}_on_treatment"
        ] = _predict_by_treatment_flag(
            df=scored_df,
            learner_fcn=learner_fcn,
            is_treatment=True,
            prediction_column=prediction_column,
        )
        scored_df[
            f"treatment_{treatment}__{prediction_column}_on_control"
        ] = _predict_by_treatment_flag(
            df=scored_df,
            learner_fcn=learner_fcn,
            is_treatment=False,
            prediction_column=prediction_column,
        )

        uplift_cols.append(f"treatment_{treatment}__uplift")
        scored_df[uplift_cols[-1]] = (
            scored_df[f"treatment_{treatment}__{prediction_column}_on_treatment"]
            - scored_df[f"treatment_{treatment}__{prediction_column}_on_control"]
        )

    scored_df["uplift"] = scored_df[uplift_cols].max(axis=1).values
    scored_df["suggested_treatment"] = np.where(
        scored_df["uplift"].values <= 0,
        control_name,
        scored_df[uplift_cols].idxmax(axis=1).values,
    )
    scored_df["suggested_treatment"] = (
        scored_df["suggested_treatment"]
        .apply(lambda x: x.replace("__uplift", ""))
        .values
    )

    return scored_df


@curry
def causal_s_classification_learner(
    df: pd.DataFrame,
    treatment_col: str,
    control_name: str,
    prediction_column: str,
    learner: Callable,
    learner_transformers: List[LearnerFnType] = None,
) -> LearnerReturnType:
    """
    Fits a Causal S-Learner classifier. The S-learner is a meta-learner which
    learns the Conditional Average Treatment Effect (CATE) through the creation
    of an auxiliary binary feature T that indicates if the samples is in the
    treatment (T = 1) or in the control (T = 0) group. Then, this feature can
    then be used to perform inference by artificially simulating the conversion
    of a new sample for both scenarios, i.e., with T = 0 and T = 1. The CATE τ
    is defined as τ(xi) = M(X=xi, T=1) - M(X=xi, T=0), being M a Machine Learning
    Model.

    References:
    [1] https://matheusfacure.github.io/python-causality-handbook/21-Meta-Learners.html
    [2] https://causalml.readthedocs.io/en/latest/methodology.html

    Parameters
    ----------

    df : pd.DataFrame
        A Pandas' DataFrame with features and target columns.
        The model will be trained to predict the target column
        from the features.

    treatment_col: str
        The name of the column in `df` which contains the names of
        the treatments or control to which each data sample was subjected.

    control_name: str
        The name of the control group.

    prediction_column : str
        The name of the column with the predictions from the provided learner.

    learner: Callable
        A fklearn classification learner function.

    learner_transformers: list
        A list of fklearn transformer functions to be applied after the learner and before estimating the CATE.
        This parameter may be useful, for example, to estimate the CATE with calibrated classifiers.
    """

    learner = copy.deepcopy(learner)
    features = _get_learner_features(learner)
    features_with_treatment = _append_treatment_feature(features, TREATMENT_FEATURE)
    unique_treatments = _get_unique_treatments(df, treatment_col, control_name)

    if learner_transformers is not None:
        learner_transformers = copy.deepcopy(learner_transformers)
        learner_pipe = build_pipeline(
            *[
                learner(features=features_with_treatment),
            ]
            + learner_transformers
        )
    else:
        learner_pipe = learner(features=features_with_treatment)

    fitted_learners, learners_logs = _fit_by_treatment(
        df=df,
        learner=learner_pipe,
        treatment_col=treatment_col,
        control_name=control_name,
        treatments=unique_treatments,
    )

    def p(new_df: pd.DataFrame) -> pd.DataFrame:
        scored_df = _simulate_treatment_effect(
            df=new_df,
            treatments=unique_treatments,
            learners=fitted_learners,
            control_name=control_name,
            prediction_column=prediction_column,
        )
        return scored_df

    p.__doc__ = learner_pred_fn_docstring("causal_s_classification_learner")
    log = {
        "causal_s_classification_learner": {
            **learners_logs,
            "causal_features": features_with_treatment,
        }
    }

    return p, p(df), log


causal_s_classification_learner.__doc__ += learner_return_docstring(
    "Causal S-Learner Classifier"
)
