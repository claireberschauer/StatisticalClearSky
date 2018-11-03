# -*- coding: utf-8 -*-
"""
This module contains the algorithm to statistically fit a clear sky signal.
"""

from clearsky.utilities import ProblemStatusError
import numpy as np
from numpy.linalg import norm
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import date, datetime
from time import time
import cvxpy as cvx

# Python 2.x, 3.x compatibility
try:
    xrange
except NameError:
    xrange = range


class IterativeClearSky(object):
    def __init__(self, D, k=8, reserve_test_data=False):
        self.D = D
        self.k = k
        self.L_cs = cvx.Variable(shape=(D.shape[0], k))
        self.R_cs = cvx.Variable(shape=(k, D.shape[1]))
        self.beta = cvx.Variable()
        self.good_fit = False
        U, Sigma, V = np.linalg.svd(D)
        if np.sum(U[:, 0]) < 0:
            U[:, 0] *= -1
            V[0] *= -1
        self.L0 = U[:, :k]
        self.R0 = np.diag(Sigma[:k]).dot(V[:k, :])
        self.L_cs.value = U[:, :k]
        self.R_cs.value = np.diag(Sigma[:k]).dot(V[:k, :])
        self.beta.value = 0.0
        r0 = self.R_cs.value[0]
        x = cvx.Variable(D.shape[1])
        obj = cvx.Minimize(
            cvx.sum(0.5 * cvx.abs(r0 - x) + (.9 - 0.5) * (r0 - x)) + 1e3 * cvx.norm(cvx.diff(x, k=2)))
        prob = cvx.Problem(obj)
        prob.solve(solver='MOSEK')
        self.r0 = x.value
        self.mu_L = 1.
        self.mu_R = 20.
        self.mu_C = 0.05
        self.mu_d = 1e-1
        self.tau = 0.8
        self.theta = 0.1
        tc = np.linalg.norm(D[:-2] - 2 * D[1:-1] + D[2:], ord=1, axis=0)
        tc = np.percentile(tc, 50) - tc
        tc /= np.max(tc)
        tc = np.clip(tc, 0, None)
        de = np.sum(D, axis=0)
        x = cvx.Variable(len(tc))
        obj = cvx.Minimize(
            cvx.sum(0.5 * cvx.abs(de - x) + (.9 - 0.5) * (de - x)) + 1e3 * cvx.norm(cvx.diff(x, k=2)))
        prob = cvx.Problem(obj)
        prob.solve(solver='MOSEK')
        de = np.clip(np.divide(de, x.value), 0, 1)
        th = .1
        self.weights = np.multiply(np.power(tc, th), np.power(de, 1.-th))
        self.weights[self.weights < 0.6] = 0.
        if reserve_test_data:
            m, n = D.shape
            day_indices = np.arange(n)
            num = int(n * reserve_test_data)
            self.test_days = np.sort(np.random.choice(day_indices, num, replace=False))
        else:
            self.test_days = None

    def calc_objective(self, sum_components=True):
        W1 = np.diag(self.weights)
        f1 = (cvx.sum((0.5 * cvx.abs(self.D - self.L_cs.value.dot(self.R_cs.value))
                              + (self.tau - 0.5) * (self.D - self.L_cs.value.dot(self.R_cs.value))) * W1)).value
        W2 = np.eye(self.k)
        f2 = self.mu_L * norm(((self.L_cs[:-2, :]).value -
                               2 * (self.L_cs[1:-1, :]).value +
                               (self.L_cs[2:, :]).value).dot(W2), 'fro')
        f3 = self.mu_R * norm((self.R_cs[:, :-2]).value -
                              2 * (self.R_cs[:, 1:-1]).value +
                              (self.R_cs[:, 2:]).value, 'fro')
        if self.R_cs.shape[1] < 365 + 2:
            f4 = 0
        else:
            f4 = (self.mu_R * cvx.norm(self.R_cs[1:, :-365] - self.R_cs[1:, 365:], 'fro')).value
        components = [f1, f2, f3, f4]
        objective = sum(components)
        if sum_components:
            return objective
        else:
            return components

    def minimize_objective(self, eps=1e-3, max_iter=100, calc_deg=True, max_deg=0., min_deg=-0.25):
        ti = time()
        print('starting at {:.3f}'.format(self.calc_objective()), self.calc_objective(False))
        improvement = np.inf
        old_obj = self.calc_objective()
        it = 0
        self.good_fit = True
        while improvement >= eps:
            if self.test_days is not None:
                self.weights[self.test_days] = 0
            self.min_L()
            self.min_R(calc_deg=calc_deg, max_deg=max_deg, min_deg=min_deg)
            new_obj = self.calc_objective()
            improvement = (old_obj - new_obj) * 1. / old_obj
            old_obj = new_obj
            it += 1
            print('iteration {}: {:.3f}'.format(it, new_obj), np.round(self.calc_objective(False), 3))
            if improvement < 0:
                print('Objective increased.')
                self.good_fit = False
                improvement *= -1
            if it >= max_iter:
                print('Reached iteration limit. Previous improvement: {:.2f}%'.format(improvement * 100))
                improvement = 0.
        tf = time()
        print('Minimization complete in {:.2f} minutes'.format((tf - ti) / 60.))

    def min_L(self):
        W1 = np.diag(self.weights)
        f1 = cvx.sum((0.5 * cvx.abs(self.D - self.L_cs * self.R_cs.value)
                              + (self.tau - 0.5) * (self.D - self.L_cs * self.R_cs.value)) * W1)
        W2 = np.eye(self.k)
        f2 = self.mu_L * cvx.norm((self.L_cs[:-2, :] - 2 * self.L_cs[1:-1, :] + self.L_cs[2:, :]) * W2, 'fro')
        objective = cvx.Minimize(f1 + f2)
        constraints = [
            self.L_cs * self.R_cs.value >= 0,
            self.L_cs[np.average(self.D, axis=1) <= 1e-5, :] == 0,
            cvx.sum(self.L_cs[:, 1:], axis=0) == 0
        ]
        problem = cvx.Problem(objective, constraints)
        problem.solve(solver='MOSEK')
        if problem.status != 'optimal':
            raise ProblemStatusError('Minimize R status: ' + problem.status)


    def min_R(self, calc_deg=True, max_deg=0., min_deg=-0.25):
        if self.R_cs.shape[1] < 365 + 2:
            n_tilde = 365 + 2 - self.R_cs.shape[1]
            R_tilde = cvx.hstack([self.R_cs, cvx.Variable(shape=(self.k, n_tilde))])
        else:
            R_tilde = self.R_cs
        W1 = np.diag(self.weights)
        f1 = cvx.sum((0.5 * cvx.abs(self.D - self.L_cs.value * self.R_cs)
                              + (self.tau - 0.5) * (self.D - self.L_cs.value * self.R_cs)) * W1)
        f2 = self.mu_R * cvx.norm(R_tilde[:, :-2] - 2 * R_tilde[:, 1:-1] + R_tilde[:, 2:], 'fro')
        constraints = [
            self.L_cs.value * self.R_cs >= 0
        ]
        if self.D.shape[1] > 365:
            r = self.R_cs[0, :].T
            if calc_deg:
                constraints.extend([
                    cvx.multiply(1./ self.r0[:-365], r[365:] - r[:-365]) == self.beta,
                    self.beta >= -.25
                ])
                if max_deg is not None:
                    constraints.append(self.beta <= max_deg)
                if min_deg is not None:
                    constraints.append(self.beta >= min_deg)
            else:
                constraints.append(cvx.multiply(1./ self.r0[:-365], r[365:] - r[:-365]) == 0)
            f3 = self.mu_R * cvx.norm(R_tilde[1:, :-365] - R_tilde[1:, 365:], 'fro')
        else:
            f3 = self.mu_R * cvx.norm(R_tilde[:, :-365] - R_tilde[:, 365:], 'fro')
        objective = cvx.Minimize(f1 + f2 + f3)
        problem = cvx.Problem(objective, constraints)
        problem.solve(solver='MOSEK')
        if problem.status != 'optimal':
            raise ProblemStatusError('Minimize R status: ' + problem.status)
        self.r0 = self.R_cs.value[0, :]

    def plot_LR(self, figsize=(14, 10), show_days=False):
        fig, ax = plt.subplots(nrows=2, ncols=2, figsize=figsize)
        ax[0, 1].plot(self.R_cs.value[0])
        ax[1, 1].plot(self.R_cs.value[1:].T)
        ax[0, 0].plot(self.L_cs.value[:, 0])
        ax[1, 0].plot(self.L_cs.value[:, 1:])
        ax[0, 0].legend(['$\\ell_1$'])
        ax[1, 0].legend(['$\\ell_{}$'.format(ix) for ix in range(2, self.R_cs.value.shape[0] + 1)])
        ax[0, 1].legend(['$r_{1}$'])
        ax[1, 1].legend(['$r_{}$'.format(ix) for ix in range(2, self.R_cs.value.shape[0] + 1)])
        if show_days:
            use_day = self.weights > 1e-1
            days = np.arange(self.D.shape[1])
            ax[0, 1].scatter(days[use_day], self.R_cs.value[0][use_day], color='orange', alpha=0.7)
        plt.tight_layout()
        return fig

    def plot_energy(self, figsize=(14, 6), show_days=True):
        plt.figure(figsize=figsize)
        plt.plot(np.sum(self.D, axis=0), linewidth=1)
        plt.plot(self.R_cs.value[0] * np.sum(self.L_cs.value[:, 0]), linewidth=1)
        if show_days:
            use_day = self.weights > 1e-1
            days = np.arange(self.D.shape[1])
            plt.scatter(days[use_day], np.sum(self.D, axis=0)[use_day], color='orange', alpha=0.7)
        fig = plt.gcf()
        return fig

    def plot_singular_vectors(self, k=4, figsize=(14, 4), show_days=False):
        fig, ax = plt.subplots(nrows=k, ncols=2, figsize=(figsize[0], 2*figsize[1]))
        for i in range(k):
            ax[i][0].plot(self.L0.T[i], linewidth=1)
            ax[i][0].set_xlim(0, 287)
            ax[i][0].set_ylabel('$\\ell_{}$'.format(i + 1))
            ax[i][1].plot(self.R0[i], linewidth=1)
            ax[i][1].set_xlim(0, self.D.shape[1])
            ax[i][1].set_ylabel('$r_{}$'.format(i + 1))
        ax[-1][0].set_xlabel('$i \\in 1, \\ldots, m$ (5-minute periods in one day)')
        ax[-1][1].set_xlabel('$j \\in 1, \\ldots, n$ (days)')
        if show_days:
            use_day = self.weights > 1e-1
            days = np.arange(self.D.shape[1])
            for i in range(k):
                ax[i, 1].scatter(days[use_day], self.R0[i][use_day], color='orange', alpha=0.7)
        plt.tight_layout()
        return fig

    def plot_D(self, figsize=(14, 6), show_days=False):
        with sns.axes_style("white"):
            fig, ax = plt.subplots(nrows=1, figsize=figsize, sharex=True)
            foo = ax.imshow(self.D, cmap='hot', interpolation='none', aspect='auto')
            ax.set_title('Measured power')
            plt.colorbar(foo, ax=ax, label='kW')
            ax.set_xlabel('Day number')
            ax.set_yticks([])
            ax.set_ylabel('Time of day')
            if show_days:
                xlim = ax.get_xlim()
                ylim = ax.get_ylim()
                use_day = self.weights > 1e-1
                days = np.arange(self.D.shape[1])
                y1 = np.ones_like(days[use_day]) * self.D.shape[0] * .99
                ax.scatter(days[use_day], y1, marker='|', color='yellow', s=2)
                ax.scatter(days[use_day], .995*y1, marker='|', color='yellow', s=2)
                ax.set_xlim(*xlim)
                ax.set_ylim(*ylim)
        return fig

    def plot_measured_clear(self, figsize=(12, 10), show_days=False):
        with sns.axes_style("white"):
            fig, ax = plt.subplots(nrows=2, figsize=figsize, sharex=True)
            foo = ax[0].imshow(self.D, cmap='hot', interpolation='none', aspect='auto')
            ax[0].set_title('Measured power')
            bar = ax[1].imshow(self.L_cs.value.dot(self.R_cs.value), cmap='hot',
                               vmin=0, vmax=np.max(self.D), interpolation='none', aspect='auto')
            ax[1].set_title('Estimated clear sky power')
            plt.colorbar(foo, ax=ax[0], label='kW')
            plt.colorbar(bar, ax=ax[1], label='kW')
            ax[1].set_xlabel('Day number')
            ax[0].set_yticks([])
            ax[0].set_ylabel('Time of day')
            ax[1].set_yticks([])
            ax[1].set_ylabel('Time of day')
            if show_days:
                xlim = ax[0].get_xlim()
                ylim = ax[0].get_ylim()
                use_day = self.weights > 1e-1
                days = np.arange(self.D.shape[1])
                y1 = np.ones_like(days[use_day]) * self.D.shape[0] * .99
                ax[0].scatter(days[use_day], y1, marker='|', color='yellow', s=2)
                ax[0].scatter(days[use_day], .995 * y1, marker='|', color='yellow', s=2)
                ax[0].set_xlim(*xlim)
                ax[0].set_ylim(*ylim)
            plt.tight_layout()
        return fig