"""
This module defines "Statistical Clear Sky Fitting" algorithm.
"""

from time import time
import numpy as np
from numpy.linalg import norm
import cvxpy as cvx
from statistical_clear_sky.algorithm.util.time_shifts import fix_time_shifts
from statistical_clear_sky.algorithm.initialization.linearization_helper\
 import LinearizationHelper
from statistical_clear_sky.algorithm.initialization.weight_setting\
 import WeightSetting
from statistical_clear_sky.solver_type import SolverType
from statistical_clear_sky.algorithm.exception import ProblemStatusError
from statistical_clear_sky.algorithm.minimization import LeftMatrixMinimization
from statistical_clear_sky.algorithm.minimization import RightMatrixMinimization
from statistical_clear_sky.algorithm.serialization.state_data import StateData
from statistical_clear_sky.algorithm.serialization.serialization_mixin\
 import SerializationMixin
from statistical_clear_sky.algorithm.plot.plot_mixin import PlotMixin

class IterativeFitting(SerializationMixin, PlotMixin):
    """
    Implementation of "Statistical Clear Sky Fitting" algorithm.
    """

    def __init__(self, power_signals_d, rank_k=4, solver_type=SolverType.ecos,
                 reserve_test_data=False, auto_fix_time_shifts=True):

        self._solver_type = solver_type

        self._power_signals_d = self._handle_time_shift(power_signals_d,
                                                        auto_fix_time_shifts)
        self._rank_k = rank_k

        left_low_rank_matrix_u, singular_values_sigma, right_low_rank_matrix_v \
            = np.linalg.svd(power_signals_d)
        left_low_rank_matrix_u, right_low_rank_matrix_v = \
            self._adjust_low_rank_matrices(left_low_rank_matrix_u,
                                           right_low_rank_matrix_v)
        self._left_low_rank_matrix_u = left_low_rank_matrix_u
        self._singular_values_sigma = singular_values_sigma
        self._right_low_rank_matrix_v = right_low_rank_matrix_v

        self._matrix_l0 = self._left_low_rank_matrix_u[:, :rank_k]
        self._matrix_r0 = np.diag(self._singular_values_sigma[:rank_k]).dot(
            right_low_rank_matrix_v[:rank_k, :])

        self._linearization_helper = LinearizationHelper(
            solver_type=self._solver_type)

        self._weight_setting = WeightSetting(solver_type=self._solver_type)

        self._set_testdays(power_signals_d, reserve_test_data)

        # Stores the current state of the object:
        self._state_data = StateData()
        self._store_initial_state_data(auto_fix_time_shifts)

        self._set_residuals()

    def execute(self, mu_l=1.0, mu_r=20.0, tau=0.8, exit_criterion_epsilon=1e-3,
                max_iteration=100, is_degradation_calculated=True,
                max_degradation=None, min_degradation=None,
                verbose=True):

        # mu_l, mu_r, tau = self._use_stored_date_if_any(mu_l, mu_r, tau)
        l_cs_value, r_cs_value, beta_value = self._obtain_initial_values()
        component_r0 = self._obtain_initial_component_r0()
        weights = self._obtain_weights()

        self._minimize_objective(l_cs_value, r_cs_value, beta_value,
            component_r0, weights, mu_l=mu_l, mu_r=mu_r, tau=tau,
            exit_criterion_epsilon=exit_criterion_epsilon,
            max_iteration=max_iteration,
            is_degradation_calculated=is_degradation_calculated,
            max_degradation=max_degradation, min_degradation=min_degradation,
            verbose=verbose)

        self._store_final_state_data(weights)

    @property
    def l_cs_value(self):
        return self._l_cs_value

    @property
    def r_cs_value(self):
        return self._r_cs_value

    @property
    def beta_value(self):
        return self._beta_value

    @property
    def state_data(self):
        return self._state_data

    # Alias method for l_cs_value accessor (with property decorator):
    def left_low_rank_matrix(self):
        return self.l_cs_value

    # Alias method for r_cs_value accessor (with property decorator):
    def right_low_rank_matrix(self):
        return self.r_cs_value

    # Alias method for beta_value accessor (with property decorator):
    def degradation_rate(self):
        return self.beta_value

    def clear_sky_signals(self):
        return self._l_cs_value.dot(self._r_cs_value)

    def _minimize_objective(self, l_cs_value, r_cs_value, beta_value,
                            component_r0, weights,
                            mu_l=1.0, mu_r=20.0, tau=0.8,
                            exit_criterion_epsilon=1e-3, max_iteration=100,
                            is_degradation_calculated=True,
                            max_degradation=None, min_degradation=None,
                            verbose=True):

        ti = time()
        try:
            objective_values = self._calculate_objective(mu_l, mu_r, tau,
                l_cs_value, r_cs_value, beta_value, weights,
                sum_components=False)
            if verbose:
                print('starting at {:.3f}'.format(
                        np.sum(objective_values)), objective_values)
            improvement = np.inf
            old_objective_value = np.sum(objective_values)
            iteration = 0
            f1_last = objective_values[0]

            left_matric_minimization = LeftMatrixMinimization(
                self._power_signals_d, self._rank_k, weights, tau, mu_l,
                solver_type=self._solver_type)
            right_matric_minimization = RightMatrixMinimization(
                self._power_signals_d, self._rank_k, weights, tau, mu_r,
                component_r0,
                is_degradation_calculated=is_degradation_calculated,
                max_degradation=max_degradation,
                min_degradation=min_degradation,
                solver_type=self._solver_type)

            while improvement >= exit_criterion_epsilon:
                self._store_minimization_state_data(mu_l, mu_r, tau,
                    l_cs_value, r_cs_value, beta_value, component_r0)

                if verbose:
                    print('Miminizing left L matrix')
                l_cs_value, r_cs_value, beta_value\
                    = left_matric_minimization.minimize(l_cs_value, r_cs_value,
                                                        beta_value)

                if verbose:
                    print('Miminizing right R matrix')
                l_cs_value, r_cs_value, beta_value\
                    = right_matric_minimization.minimize(l_cs_value,
                                                         r_cs_value, beta_value)

                component_r0 = r_cs_value[0, :]

                objective_values = self._calculate_objective(mu_l, mu_r, tau,
                    l_cs_value, r_cs_value, beta_value, weights,
                    sum_components=False)
                new_objective_value = np.sum(objective_values)
                improvement = ((old_objective_value - new_objective_value)
                    * 1. / old_objective_value)
                old_objective_value = new_objective_value
                iteration += 1
                if verbose:
                    print('iteration {}: {:.3f}'.format(
                        iteration, new_objective_value),
                        np.round(objective_values, 3))
                if objective_values[0] > f1_last:
                    self._state_data.f1_increase = True
                    if verbose:
                        print('Caution: residuals increased')
                if improvement < 0:
                    if verbose:
                        print('Caution: objective increased.')
                    self._state_data.obj_increase = True
                    improvement *= -1
                if iteration >= max_iteration:
                    if verbose:
                        print('Reached iteration limit. Previous improvement: {:.2f}%'.format(improvement * 100))
                    improvement = 0.

                self._store_minimization_state_data(mu_l, mu_r, tau,
                    l_cs_value, r_cs_value, beta_value, component_r0)

        except cvx.SolverError:
            if verbose:
                print('solver failed!')
            self._state_data.is_solver_error = True
        except ProblemStatusError as e:
            if verbose:
                print(e)
            self._state_data.is_problem_status_error = True
        else:
            tf = time()
            if verbose:
                print('Minimization complete in {:.2f} minutes'.format(
                      (tf - ti) / 60.))
            self._analyze_residuals(l_cs_value, r_cs_value, weights)
            self._make_result_variables_accessible(l_cs_value, r_cs_value,
                                                   beta_value)

    def _calculate_objective(self, mu_l, mu_r, tau, l_cs_value, r_cs_value,
                             beta_value, weights, sum_components=True):
        weights_w1 = np.diag(weights)
        # Note: Not using cvx.sum and cvx.abs as in following caused
        # an error at * weights_w1:
        # ValueError: operands could not be broadcast together with shapes
        # (288,1300) (1300,1300)
        # term_f1 = sum((0.5 * abs(
        #     self._power_signals_d - l_cs_value.dot(r_cs_value))
        #     + (tau - 0.5)
        #     * (self._power_signals_d - l_cs_value.dot(r_cs_value)))
        #     * weights_w1)
        term_f1 = (cvx.sum((0.5 * cvx.abs(
                    self._power_signals_d - l_cs_value.dot(r_cs_value))
                    + (tau - 0.5) * (self._power_signals_d - l_cs_value.dot(
                        r_cs_value))) * weights_w1)).value
        weights_w2 = np.eye(self._rank_k)
        term_f2 = mu_l * norm((l_cs_value[:-2, :] - 2 * l_cs_value[1:-1, :] +
                               l_cs_value[2:, :]).dot(weights_w2), 'fro')
        term_f3 = mu_r * norm(r_cs_value[:, :-2] - 2 * r_cs_value[:, 1:-1] +
                               r_cs_value[:, 2:], 'fro')
        if r_cs_value.shape[1] < 365 + 2:
            term_f4 = 0
        else:
            # Note: it was cvx.norm. Check if this modification makes a
            # difference:
            term_f4 = (mu_r * norm(r_cs_value[1:, :-365] - r_cs_value[1:, 365:],
                                 'fro'))
        components = [term_f1, term_f2, term_f3, term_f4]
        objective = sum(components)
        if sum_components:
            return objective
        else:
            return components

    def _handle_time_shift(self, power_signals_d, auto_fix_time_shifts):
        self._fixed_time_stamps = False
        if auto_fix_time_shifts:
            power_signals_d_fix = fix_time_shifts(power_signals_d)
            if np.alltrue(np.isclose(power_signals_d, power_signals_d_fix)):
                del power_signals_d_fix
            else:
                power_signals_d = power_signals_d_fix
                self._fixed_time_stamps = True
        return power_signals_d

    def _adjust_low_rank_matrices(self, left_low_rank_matrix_u,
                                  right_low_rank_matrix_v):

        if np.sum(left_low_rank_matrix_u[:, 0]) < 0:
            left_low_rank_matrix_u[:, 0] *= -1
            right_low_rank_matrix_v[0] *= -1

        return left_low_rank_matrix_u, right_low_rank_matrix_v

    # def _use_stored_date_if_any(self, mu_l, mu_r, tau):
    #     if self._state_data.mu_l is not None:
    #         mu_l = self._state_data.mu_l
    #     if self._state_data.mu_r is not None:
    #         mu_r = self._state_data.mu_r
    #     if self._state_data.tau is not None:
    #         tau = self._state_data.tau
    #     return mu_l, mu_r, tau

    def _obtain_initial_values(self):
        if self._state_data.l_value.size > 0:
            l_cs_value = self._state_data.l_value
        else:
            l_cs_value = self._left_low_rank_matrix_u[:, :self._rank_k]
        if self._state_data.r_value.size > 0:
            r_cs_value = self._state_data.r_value
        else:
            r_cs_value = np.diag(self._singular_values_sigma[
                                 :self._rank_k]).dot(
                                 self._right_low_rank_matrix_v[
                                 :self._rank_k, :])
        if self._state_data.beta_value != 0.0:
            beta_value = self._state_data.beta_value
        else:
            beta_value = 0.0
        self._make_result_variables_accessible(l_cs_value, r_cs_value,
                                               beta_value)
        return l_cs_value, r_cs_value, beta_value

    def _obtain_initial_component_r0(self, verbose=True):
        if verbose:
            print('obtaining initial value of component r0')
        if self._state_data.component_r0.size > 0:
            component_r0 = self._state_data.component_r0
        else:
            component_r0 = self._linearization_helper.obtain_component_r0(
                self._power_signals_d, self._left_low_rank_matrix_u,
                self._singular_values_sigma, self._right_low_rank_matrix_v,
                rank_k=self._rank_k)
        return component_r0

    def _obtain_weights(self, verbose=True):
        if verbose:
            print('obtaining weights')
        if self._state_data.weights.size > 0:
            weights = self._state_data.weights
        else:
            weights = self._weight_setting.obtain_weights(self._power_signals_d)
            if self._test_days is not None:
                weights[self._test_days] = 0
        return weights

    def _set_testdays(self, power_signals_d, reserve_test_data):
        if reserve_test_data:
            m, n = power_signals_d.shape
            day_indices = np.arange(n)
            num = int(n * reserve_test_data)
            self._test_days = np.sort(np.random.choice(day_indices, num,
                                                       replace=False))
        else:
            self._test_days = None

    def _set_residuals(self):
        if self._state_data.residuals_median is not None:
            self._residuals_median = self._state_data.residuals_median
        else:
            self._residuals_median = None
        if self._state_data.residuals_variance is not None:
            self._residuals_variance = self._state_data.residuals_variance
        else:
            self._residuals_variance = None
        if self._state_data.residual_l0_norm is not None:
            self._residual_l0_norm = self._state_data.residual_l0_norm
        else:
            self._residual_l0_norm = None

    def _analyze_residuals(self, l_cs_value, r_cs_value, weights):
        # Residual analysis
        weights_w1 = np.diag(weights)
        wres = np.dot(l_cs_value.dot(
                r_cs_value) - self._power_signals_d, weights_w1)
        use_days = np.logical_not(np.isclose(np.sum(wres, axis=0), 0))
        scaled_wres = wres[:, use_days] / np.average(
                self._power_signals_d[:, use_days])
        final_metric = scaled_wres[
                self._power_signals_d[:, use_days] > 1e-3]
        self._residuals_median = np.median(final_metric)
        self._residuals_variance = np.power(np.std(final_metric), 2)
        self._residual_l0_norm = np.linalg.norm(
                self._matrix_l0[:, 0] - l_cs_value[:, 0])

    def _make_result_variables_accessible(self, l_cs_value, r_cs_value,
                                          beta_value):
        self._l_cs_value = l_cs_value
        self._r_cs_value = r_cs_value
        self._beta_value = beta_value

    def _store_initial_state_data(self, auto_fix_time_shifts):
        self._state_data.auto_fix_time_shifts = auto_fix_time_shifts
        self._state_data.power_signals_d = self._power_signals_d
        self._state_data.rank_k = self._rank_k
        self._state_data.matrix_l0 = self._matrix_l0
        self._state_data.matrix_r0 = self._matrix_r0

    def _store_minimization_state_data(self, mu_l, mu_r, tau,
            l_cs_value, r_cs_value, beta_value, component_r0):
        self._state_data.mu_l = mu_l
        self._state_data.mu_r = mu_r
        self._state_data.tau = tau
        self._state_data.l_value = l_cs_value
        self._state_data.r_value = r_cs_value
        self._state_data.beta_value = beta_value
        self._state_data.component_r0 = component_r0

    def _store_final_state_data(self, weights):
        self._state_data.residuals_median = self._residuals_median
        self._state_data.residuals_variance = self._residuals_variance
        self._state_data.residual_l0_norm = self._residual_l0_norm
        self._state_data.weights = weights