from abc import ABC, abstractmethod
from collections import OrderedDict

import pandas as pd

from .components import Estimator, handle_component
from .graphs import make_feature_importance_graph, make_pipeline_graph

from evalml.objectives import get_objective
from evalml.problem_types import handle_problem_types
from evalml.utils import Logger

logger = Logger()


class PipelineBase(ABC):
    """Base class for all pipelines."""

    @property
    @classmethod
    @abstractmethod
    def component_graph(cls):
        return NotImplementedError("This pipeline must have `component_graph` as a class variable.")

    @property
    @classmethod
    @abstractmethod
    def problem_types(cls):
        return NotImplementedError("This pipeline must have `problem_types` as a class variable.")

    def __init__(self, parameters):
        """Machine learning pipeline made out of transformers and a estimator.

        Required Class Variables:
            component_graph (list): List of components in order. Accepts strings or ComponentBase objects in the list
            problem_types (list): List of problem types for this pipeline. Accepts strings or ProbemType enum in the list.

        Arguments:
            parameters (dict): dictionary with component names as keys and dictionary of that component's parameters as values.
                If `random_state`, `n_jobs`, or 'number_features' are provided as component parameters they will override the corresponding
                value provided as arguments to the pipeline. An empty dictionary {} implies using all default values for component parameters.
        """
        self.component_graph = [self._instantiate_component(c, parameters) for c in self.component_graph]
        self.problem_types = [handle_problem_types(problem_type) for problem_type in self.problem_types]
        self.input_feature_names = {}
        self.results = {}
        self.parameters = parameters

        self.estimator = self.component_graph[-1] if isinstance(self.component_graph[-1], Estimator) else None
        if self.estimator is None:
            raise ValueError("A pipeline must have an Estimator as the last component in component_graph.")

        self.name = self._generate_name()
        self._validate_problem_types(self.problem_types)

    def _generate_name(self):
        "Generates name from components in self.component_graph"
        if self.estimator is not None:
            name = "{}".format(self.estimator.name)
        else:
            name = "Pipeline"
        for index, component in enumerate(self.component_graph[:-1]):
            if index == 0:
                name += " w/ {}".format(component.name)
            else:
                name += " + {}".format(component.name)

        return name

    def _validate_problem_types(self, problem_types):
        """Validates provided `problem_types` against the estimator in `self.component_graph`

        Arguments:
            problem_types (list): list of ProblemTypes
        """
        estimator_problem_types = self.estimator.problem_types
        for problem_type in self.problem_types:
            if problem_type not in estimator_problem_types:
                raise ValueError("Problem type {} not valid for this component graph. Valid problem types include {}.".format(problem_type, estimator_problem_types))

    def _instantiate_component(self, component, parameters):
        """Instantiates components with parameters in `parameters`"""
        component = handle_component(component)
        component_class = component.__class__
        component_name = component.name
        try:
            component_parameters = parameters.get(component_name, {})
            new_component = component_class(**component_parameters)
        except (ValueError, TypeError) as e:
            err = "Error received when instantiating component {} with the following arguments {}".format(component_name, component_parameters)
            raise ValueError(err) from e
        return new_component

    def __getitem__(self, index):
        if isinstance(index, slice):
            raise NotImplementedError('Slicing pipelines is currently not supported.')
        elif isinstance(index, int):
            return self.component_graph[index]
        else:
            return self.get_component(index)

    def __setitem__(self, index, value):
        raise NotImplementedError('Setting pipeline components is not supported.')

    def get_component(self, name):
        """Returns component by name

        Arguments:
            name (str): name of component

        Returns:
            Component: component to return

        """
        return next((component for component in self.component_graph if component.name == name), None)

    def describe(self, return_dict=False):
        """Outputs pipeline details including component parameters

        Arguments:
            return_dict (bool): If True, return dictionary of information about pipeline. Defaults to false

        Returns:
            dict: dictionary of all component parameters if return_dict is True, else None
        """
        logger.log_title(self.name)
        logger.log("Problem Types: {}".format(', '.join([str(problem_type) for problem_type in self.problem_types])))
        logger.log("Model Type: {}".format(str(self.model_type)))

        if self.estimator.name in self.input_feature_names:
            logger.log("Number of features: {}".format(len(self.input_feature_names[self.estimator.name])))

        # Summary of steps
        logger.log_subtitle("Pipeline Steps")
        for number, component in enumerate(self.component_graph, 1):
            component_string = str(number) + ". " + component.name
            logger.log(component_string)
            component.describe(print_name=False)

        if return_dict:
            return self.parameters

    def _transform(self, X):
        X_t = X
        for component in self.component_graph[:-1]:
            X_t = component.transform(X_t)
        return X_t

    def _fit(self, X, y):
        X_t = X
        y_t = y
        for component in self.component_graph[:-1]:
            self.input_feature_names.update({component.name: list(pd.DataFrame(X_t))})
            X_t = component.fit_transform(X_t, y_t)

        self.input_feature_names.update({self.estimator.name: list(pd.DataFrame(X_t))})
        self.estimator.fit(X_t, y_t)

    def fit(self, X, y, objective=None, objective_fit_size=0.2):
        """Build a model

        Arguments:
            X (pd.DataFrame or np.array): the input training data of shape [n_samples, n_features]

            y (pd.Series): the target training labels of length [n_samples]

            objective (Object or string): the objective to optimize

            objective_fit_size (float): the proportion of the dataset to include in the test split.
        Returns:

            self

        """
        if not isinstance(X, pd.DataFrame):
            X = pd.DataFrame(X)
        if not isinstance(y, pd.Series):
            y = pd.Series(y)

        self._fit(X, y)
        return self

    def predict(self, X, objective=None):
        """Make predictions using selected features.

        Args:
            X (pd.DataFrame or np.array) : data of shape [n_samples, n_features]
            objective (Object or string): the objective to use to predict

        Returns:
            pd.Series : estimated labels
        """
        if not isinstance(X, pd.DataFrame):
            X = pd.DataFrame(X)

        X_t = self._transform(X)
        return self.estimator.predict(X_t)

    def score(self, X, y, objectives):
        """Evaluate model performance on current and additional objectives

        Args:
            X (pd.DataFrame or np.array) : data of shape [n_samples, n_features]
            y (pd.Series) : true labels of length [n_samples]
            objectives (list): Non-empty list of objectives to score on

        Returns:
            dict: ordered dictionary of objective scores
        """
        if not isinstance(X, pd.DataFrame):
            X = pd.DataFrame(X)

        if not isinstance(y, pd.Series):
            y = pd.Series(y)

        objectives = [get_objective(o) for o in objectives]
        y_predicted = None
        y_predicted_proba = None

        scores = OrderedDict()
        for objective in objectives:
            if objective.score_needs_proba:
                if y_predicted_proba is None:
                    y_predicted_proba = self.predict_proba(X)
                y_predictions = y_predicted_proba
            else:
                if y_predicted is None:
                    y_predicted = self.predict(X)
                y_predictions = y_predicted

            if objective.uses_extra_columns:
                scores.update({objective.name: objective.score(y_predictions, y, X)})
            else:
                scores.update({objective.name: objective.score(y_predictions, y)})

        return scores

    def get_plot_data(self, X, y, plot_metrics):
        """Generates plotting data for the pipeline for each specified plot metric

        Args:
            X (pd.DataFrame or np.array) : data of shape [n_samples, n_features]
            y (pd.Series) : true labels of length [n_samples]
            plot_metrics (list): list of plot metrics to generate data for

        Returns:
            dict: ordered dictionary of plot metric data (scores)
        """
        if not isinstance(X, pd.DataFrame):
            X = pd.DataFrame(X)

        if not isinstance(y, pd.Series):
            y = pd.Series(y)
        y_predicted = None
        y_predicted_proba = None
        scores = OrderedDict()
        for plot_metric in plot_metrics:
            if plot_metric.score_needs_proba:
                if y_predicted_proba is None:
                    y_predicted_proba = self.predict_proba(X)
                y_predictions = y_predicted_proba
            else:
                if y_predicted is None:
                    y_predicted = self.predict(X)
                y_predictions = y_predicted
            scores.update({plot_metric.name: plot_metric.score(y_predictions, y)})
        return scores

    def graph(self, filepath=None):
        """Generate an image representing the pipeline graph

        Arguments:
            filepath (str, optional) : Path to where the graph should be saved. If set to None (as by default), the graph will not be saved.

        Returns:
            graphviz.Digraph: Graph object that can be directly displayed in Jupyter notebooks.
        """
        return make_pipeline_graph(self.component_graph, self.name, filepath=filepath)

    @property
    def model_type(self):
        """Returns model family of this pipeline template"""
        return self.estimator.model_type

    @property
    def feature_importances(self):
        """Return feature importances. Features dropped by feature selection are excluded"""
        feature_names = self.input_feature_names[self.estimator.name]
        importances = list(zip(feature_names, self.estimator.feature_importances))  # note: this only works for binary
        importances.sort(key=lambda x: -abs(x[1]))
        df = pd.DataFrame(importances, columns=["feature", "importance"])
        return df

    def feature_importance_graph(self, show_all_features=False):
        """Generate a bar graph of the pipeline's feature importances

        Arguments:
            show_all_features (bool, optional) : If true, graph features with an importance value of zero. Defaults to false.

        Returns:
            plotly.Figure, a bar graph showing features and their importances
        """
        return make_feature_importance_graph(self.feature_importances, show_all_features=show_all_features)
