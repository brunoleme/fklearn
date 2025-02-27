from unittest.mock import create_autospec, patch

import numpy as np
import pandas as pd
import pytest
from fklearn.causal.cate_learning.meta_learners import (
    TREATMENT_FEATURE, _append_treatment_feature, _create_treatment_flag,
    _filter_by_treatment, _fit_by_treatment, _get_unique_treatments,
    _predict_by_treatment_flag, _simulate_treatment_effect,
    causal_s_classification_learner)
from fklearn.exceptions.exceptions import (MissingControlError,
                                           MissingTreatmentError,
                                           MultipleTreatmentsError)
from fklearn.training.classification import logistic_classification_learner
from fklearn.types import LearnerFnType
from pandas import DataFrame
from pandas.testing import assert_frame_equal


def test__append_treatment_feature():
    features = ["feat1", "feat2", "feat3"]
    treatment_feature = "treatment"

    assert _append_treatment_feature(features, treatment_feature) == features + [
        treatment_feature
    ]
    assert len(features) > 0
    assert treatment_feature


def test__get_unique_treatments():

    df = pd.DataFrame(
        {
            "feature": [1.0, 4.0, 1.0, 5.0, 3.0],
            "treatment": [
                "treatment_A",
                "treatment_C",
                "treatment_B",
                "treatment_A",
                "control",
            ],
            "target": [1, 1, 0, 0, 1],
        }
    )

    filtered = _get_unique_treatments(
        df, treatment_col="treatment", control_name="control"
    )
    expected = ["treatment_A", "treatment_B", "treatment_C"]

    assert sorted(filtered) == sorted(expected)


def test__filter_by_treatment():
    values = [
        [1.0, "treatment_A", 1],
        [4.0, "treatment_C", 1],
        [1.0, "treatment_B", 0],
        [5.0, "treatment_A", 0],
        [3.0, "control", 1],
    ]

    df = pd.DataFrame(data=values, columns=["feat1", "treatment", "target"])

    selected_treatment = "treatment_A"

    expected_values = [
        [1.0, "treatment_A", 1],
        [5.0, "treatment_A", 0],
        [3.0, "control", 1],
    ]

    expected: DataFrame = pd.DataFrame(
        data=expected_values, columns=["feat1", "treatment", "target"]
    )

    results = _filter_by_treatment(
        df,
        treatment_col="treatment",
        treatment_name=selected_treatment,
        control_name="control",
    )

    assert_frame_equal(results.reset_index(drop=True), expected)


def test__create_treatment_flag_missing_control():
    with pytest.raises(Exception) as e:
        df = pd.DataFrame(
            {
                "feature": [1.0, 4.0, 1.0, 5.0, 3.0],
                "treatment": [
                    "treatment_A",
                    "treatment_A",
                    "treatment_A",
                    "treatment_A",
                    "treatment_A",
                ],
                "target": [1, 1, 0, 0, 1],
            }
        )

        _create_treatment_flag(
            df,
            treatment_col="treatment",
            treatment_name="treatment_A",
            control_name="control",
        )

    assert e.type == MissingControlError
    assert e.value.args[0] == "Data does not contain the specified control."


def test__create_treatment_flag_missing_treatment():
    with pytest.raises(Exception) as e:
        df = pd.DataFrame(
            {
                "feature": [1.0, 4.0, 1.0, 5.0, 3.0],
                "treatment": [
                    "control",
                    "control",
                    "control",
                    "control",
                    "control",
                ],
                "target": [1, 1, 0, 0, 1],
            }
        )

        _create_treatment_flag(
            df,
            treatment_col="treatment",
            treatment_name="treatment_A",
            control_name="control",
        )

    assert e.type == MissingTreatmentError
    assert e.value.args[0] == "Data does not contain the specified treatment."


def test__create_treatment_flag_multiple_treatments():
    with pytest.raises(Exception) as e:
        df = pd.DataFrame(
            {
                "feature": [1.0, 4.0, 1.0, 5.0, 3.0],
                "treatment": [
                    "treatment_A",
                    "treatment_C",
                    "treatment_B",
                    "treatment_A",
                    "control",
                ],
                "target": [1, 1, 0, 0, 1],
            }
        )

        _create_treatment_flag(
            df,
            treatment_col="treatment",
            treatment_name="treatment_A",
            control_name="control",
        )

    assert e.type == MultipleTreatmentsError
    assert e.value.args[0] == "Data contains multiple treatments."


def test__create_treatment_flag():
    df = pd.DataFrame(
        {
            "feature": [1.3, 1.0, 1.8, -0.1],
            "group": ["treatment", "treatment", "control", "control"],
            "target": [1, 1, 1, 0],
        }
    )

    expected = pd.DataFrame(
        {
            "feature": [1.3, 1.0, 1.8, -0.1],
            "group": ["treatment", "treatment", "control", "control"],
            "target": [1, 1, 1, 0],
            TREATMENT_FEATURE: [1.0, 1.0, 0.0, 0.0],
        }
    )

    results = _create_treatment_flag(
        df, treatment_col="group", control_name="control", treatment_name="treatment"
    )

    assert_frame_equal(results, expected)


def test__fit_by_treatment():
    df = pd.DataFrame(
        {
            "x1": [1.3, 1.0, 1.8, -0.1, 0.0, 1.0, 2.2, 0.4, -5.0],
            "x2": [10, 4, 15, 6, 5, 12, 14, 5, 12],
            "treatment": [
                "A",
                "B",
                "A",
                "A",
                "B",
                "control",
                "control",
                "B",
                "control",
            ],
            "target": [1, 1, 1, 0, 0, 1, 0, 0, 1],
        }
    )

    learner_binary = logistic_classification_learner(
        features=["x1", "x2", TREATMENT_FEATURE],
        target="target",
        params={"max_iter": 10},
    )

    treatments = ["A", "B"]

    learners, logs = _fit_by_treatment(
        df,
        learner=learner_binary,
        treatment_col="treatment",
        control_name="control",
        treatments=treatments,
    )

    assert len(learners) == len(treatments)
    assert len(logs) == len(treatments)
    assert type(logs) == dict
    assert [type(learner) == LearnerFnType for learner in learners]


def ones_or_zeros_model(df):
    def p(new_df: pd.DataFrame) -> pd.DataFrame:
        pred = new_df[TREATMENT_FEATURE].values

        col_dict = {"prediction": pred[:]}

        return new_df.assign(**col_dict)

    return p(df)


def test__predict_by_treatment_flag_positive():
    df = pd.DataFrame(
        {
            "x1": [1.3, 1.0, 1.8, -0.1],
            "x2": [10, 4, 15, 6],
            "target": [1, 1, 1, 0],
        }
    )

    assert (
        _predict_by_treatment_flag(df, ones_or_zeros_model, True, "prediction")
        == np.ones(df.shape[0])
    ).all()


def test__predict_by_treatment_flag_negative():
    df = pd.DataFrame(
        {
            "x1": [1.3, 1.0, 1.8, -0.1],
            "x2": [10, 4, 15, 6],
            "target": [1, 1, 1, 0],
        }
    )

    assert (
        _predict_by_treatment_flag(df, ones_or_zeros_model, False, "prediction")
        == np.zeros(df.shape[0])
    ).all()


@patch("fklearn.causal.cate_learning.meta_learners._predict_by_treatment_flag")
def test__simulate_treatment_effect(mock_predict_by_treatment_flag):
    df = pd.DataFrame(
        {
            "x1": [1.3, 1.0, 1.8, -0.1],
            "x2": [10, 4, 15, 6],
            "treatment": ["A", "B", "A", "control"],
            "target": [0, 0, 0, 1],
        }
    )

    expected = pd.DataFrame(
        {
            "x1": [1.3, 1.0, 1.8, -0.1],
            "x2": [10, 4, 15, 6],
            "treatment": ["A", "B", "A", "control"],
            "target": [0, 0, 0, 1],
            "treatment_A__prediction_on_treatment": [0.3, 0.3, 0.0, 1.0],
            "treatment_A__prediction_on_control": [0.2, 0.5, 0.3, 0.0],
            "treatment_A__uplift": [0.1, -0.2, -0.3, 1.0],
            "treatment_B__prediction_on_treatment": [0.6, 0.7, 0.0, 1.0],
            "treatment_B__prediction_on_control": [1.0, 0.5, 1.0, 1.0],
            "treatment_B__uplift": [-0.4, 0.2, -1.0, 0.0],
            "uplift": [0.1, 0.2, -0.3, 1.0],
            "suggested_treatment": [
                "treatment_A",
                "treatment_B",
                "control",
                "treatment_A",
            ],
        }
    )

    treatments = ["A", "B"]
    control_name = "control"

    # This test will score the model for all treatments available and for all treatment-control pairs. In this test,
    # since we have control and treatment for treatments A and B, we expect to have 4 model outputs - two for each
    # treatment. The output of the following data will be used to calculate the uplift.

    mock_predict_by_treatment_flag.side_effect = [
        [0.3, 0.3, 0.0, 1.0],
        # treatment = A, apply treatment = 1
        [0.2, 0.5, 0.3, 0.0],
        # treatment = A, apply treatment = 0
        [0.6, 0.7, 0.0, 1.0],
        # treatment = B, apply treatment = 1
        [1.0, 0.5, 1.0, 1.0]
        # treatment = B, apply treatment = 0
    ]

    learners = {
        "A": logistic_classification_learner,
        "B": logistic_classification_learner,
    }

    results = _simulate_treatment_effect(
        df,
        treatments=treatments,
        control_name=control_name,
        learners=learners,
        prediction_column="prediction",
    )

    assert_frame_equal(results, expected)


@patch("fklearn.causal.cate_learning.meta_learners._simulate_treatment_effect")
@patch("fklearn.causal.cate_learning.meta_learners._fit_by_treatment")
@patch("fklearn.causal.cate_learning.meta_learners._get_unique_treatments")
@patch("fklearn.causal.cate_learning.meta_learners._append_treatment_feature")
@patch("fklearn.causal.cate_learning.meta_learners._get_learner_features")
def test_causal_s_classification_learner(
    mock_get_learner_features,
    mock_append_treatment_feature,
    mock_get_unique_treatments,
    mock_fit_by_treatment,
    mock_simulate_treatment_effect,
):

    df = pd.DataFrame(
        {
            "x1": [1.3, 1.0, 1.8, -0.1, 0.0, 1.0, 2.2, 0.4, -5.0],
            "x2": [10, 4, 15, 6, 5, 12, 14, 5, 12],
            "treatment": [
                "A",
                "B",
                "A",
                "A",
                "B",
                "control",
                "control",
                "B",
                "control",
            ],
            "target": [1, 1, 1, 0, 0, 1, 0, 0, 1],
        }
    )

    mock_model = create_autospec(logistic_classification_learner)
    mock_fit_by_treatment.side_effect = [
        # treatment = A
        (ones_or_zeros_model, dict()),
        # treatment = b
        (ones_or_zeros_model, dict()),
    ]

    causal_s_classification_learner(
        df,
        treatment_col="treatment",
        control_name="control",
        prediction_column="prediction",
        learner=mock_model,
    )

    mock_get_learner_features.assert_called()
    mock_append_treatment_feature.assert_called()
    mock_get_unique_treatments.assert_called()
    mock_fit_by_treatment.assert_called()
    mock_simulate_treatment_effect.assert_called()
