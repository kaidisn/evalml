import numpy as np
import pandas as pd

from evalml.objectives import PrecisionMicro
from evalml.pipelines import XGBoostPipeline


def test_xg_multi(X_y_multi):
    X, y = X_y_multi
    X = pd.DataFrame(X)
    y = pd.Series(y)

    objective = PrecisionMicro()
    clf = XGBoostPipeline(objective=objective, eta=0.1, min_child_weight=1, max_depth=3, impute_strategy='mean', percent_features=1.0, number_features=len(X.columns))
    clf.fit(X, y)
    clf.score(X, y)
    y_pred = clf.predict(X)
    assert len(np.unique(y_pred)) == 3
