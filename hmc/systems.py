"""Classes to represent Hamiltonian systems of various types."""

import logging
import numpy as np
import scipy.linalg as sla
from hmc.states import cache_in_state, multi_cache_in_state

AUTOGRAD_AVAILABLE = True
try:
    from autograd import make_vjp
    from hmc.autograd_extensions import (
        grad_and_value, jacobian_and_value, hessian_grad_and_value,
        mhp_jacobian_and_value, mtp_hessian_grad_and_value)
except ImportError:
    AUTOGRAD_AVAILABLE = False

logger = logging.getLogger(__name__)


def _autograd_fallback(diff_func, func, diff_op, name):
    if diff_func is not None:
        return diff_func
    elif AUTOGRAD_AVAILABLE:
        return diff_op(func)
    elif not AUTOGRAD_AVAILABLE:
        raise ValueError(
            f'Autograd not available therefore {name} must be provided.')


class HamiltonianSystem(object):
    """Base class for Hamiltonian systems."""

    def __init__(self, pot_energy, grad_pot_energy=None):
        self._pot_energy = pot_energy
        self._grad_pot_energy = _autograd_fallback(
            grad_pot_energy, pot_energy, grad_and_value, 'grad_pot_energy')

    @cache_in_state('pos')
    def pot_energy(self, state):
        return self._pot_energy(state.pos)

    @multi_cache_in_state(['pos'], ['grad_pot_energy', 'pot_energy'])
    def grad_pot_energy(self, state):
        return self._grad_pot_energy(state.pos)

    def h(self, state):
        raise NotImplementedError()

    def dh_dpos(self, state):
        raise NotImplementedError()

    def dh_dmom(self, state):
        raise NotImplementedError()

    def sample_momentum(self, state, rng):
        raise NotImplementedError()


class SeparableHamiltonianSystem(HamiltonianSystem):
    """Base class for separable Hamiltonian systems.

    Here separable means that the Hamiltonian can be expressed as the sum of
    a term depending only on the position (target) variables, typically denoted
    the potential energy, and a second term depending only on the momentum
    variables, typically denoted the kinetic energy.
    """

    @cache_in_state('mom')
    def kin_energy(self, state):
        return self._kin_energy(state.mom)

    @cache_in_state('mom')
    def grad_kin_energy(self, state):
        return self._grad_kin_energy(state.mom)

    def h(self, state):
        return self.pot_energy(state) + self.kin_energy(state)

    def dh_dpos(self, state):
        return self.grad_pot_energy(state)

    def dh_dmom(self, state):
        return self.grad_kin_energy(state)

    def _kin_energy(self, mom):
        raise NotImplementedError()

    def _grad_kin_energy(self, mom):
        raise NotImplementedError()

    def sample_momentum(self, state, rng):
        raise NotImplementedError()


class BaseEuclideanMetricSystem(SeparableHamiltonianSystem):

    def __init__(self, pot_energy, metric=None, grad_pot_energy=None):
        super().__init__(pot_energy, grad_pot_energy)
        self.metric = metric

    def mult_inv_metric(self, rhs):
        raise NotImplementedError()

    def mult_metric(self, rhs):
        raise NotImplementedError()


class IsotropicEuclideanMetricSystem(BaseEuclideanMetricSystem):
    """Euclidean-Gaussian Hamiltonian system with isotropic metric.

    The momenta are taken to be independent of the position variables and with
    a isotropic covariance zero-mean Gaussian marginal distribution.
    """

    def __init__(self, pot_energy, grad_pot_energy=None, **kwargs):
        super().__init__(pot_energy, 1, grad_pot_energy)

    def _kin_energy(self, mom):
        return 0.5 * np.sum(mom**2)

    def _grad_kin_energy(self, mom):
        return mom

    def sample_momentum(self, state, rng):
        return rng.normal(size=state.pos.shape)

    def mult_inv_metric(self, rhs):
        return rhs

    def mult_metric(self, rhs):
        return rhs


class DiagonalEuclideanMetricSystem(BaseEuclideanMetricSystem):
    """Euclidean-Gaussian Hamiltonian system with diagonal metric.

    The momenta are taken to be independent of the position variables and with
    a zero-mean Gaussian marginal distribution with diagonal covariance matrix.
    """

    def __init__(self, pot_energy, metric, grad_pot_energy=None):
        super().__init__(pot_energy, metric, grad_pot_energy)
        if hasattr(metric, 'ndim') and metric.ndim == 2:
            logger.warning(
                f'Off-diagonal metric values ignored for '
                f'{type(self).__name__}.')
            self.metric_diagonal = metric.diagonal()
        else:
            self.metric_diagonal = metric

    def _kin_energy(self, mom):
        return 0.5 * np.sum(mom**2 / self.metric_diagonal)

    def _grad_kin_energy(self, mom):
        return mom / self.metric_diagonal

    def sample_momentum(self, state, rng):
        return self.metric_diagonal**0.5 * rng.normal(size=state.pos.shape)

    def mult_inv_metric(self, rhs):
        return (rhs.T / self.metric_diagonal).T

    def mult_metric(self, rhs):
        return (rhs.T * self.metric_diagonal).T


class DenseEuclideanMetricSystem(BaseEuclideanMetricSystem):
    """Euclidean-Gaussian Hamiltonian system with dense metric.

    The momenta are taken to be independent of the position variables and with
    a zero-mean Gaussian marginal distribution with dense covariance matrix.
    """

    def __init__(self, pot_energy, metric, grad_pot_energy=None):
        super().__init__(pot_energy, metric, grad_pot_energy)
        self.chol_metric = sla.cholesky(metric, lower=True)

    def _kin_energy(self, mom):
        return 0.5 * mom @ self._grad_kin_energy(mom)

    def _grad_kin_energy(self, mom):
        return sla.cho_solve((self.chol_metric, True), mom)

    def sample_momentum(self, state, rng):
        return self.chol_metric @ rng.normal(size=state.pos.shape)

    def mult_inv_metric(self, rhs):
        return sla.cho_solve((self.chol_metric, True), rhs)

    def mult_metric(self, rhs):
        return self.metric @ rhs


class BaseRiemannianMetricSystem(HamiltonianSystem):

    def sqrt_metric(self, state):
        raise NotImplementedError()

    def log_det_sqrt_metric(self, state):
        raise NotImplementedError()

    def grad_log_det_sqrt_metric(self, state):
        raise NotImplementedError()

    def grad_mom_inv_metric_mom(self, state):
        raise NotImplementedError()

    def inv_metric_mom(self, state):
        raise NotImplementedError()

    def h(self, state):
        return self.h1(state) + self.h2(state)

    def h1(self, state):
        return self.pot_energy(state) + self.log_det_sqrt_metric(state)

    def h2(self, state):
        return 0.5 * state.mom @ self.inv_metric_mom(state)

    def dh1_dpos(self, state):
        return (
            self.grad_pot_energy(state) +
            self.grad_log_det_sqrt_metric(state))

    def dh2_dpos(self, state):
        return 0.5 * self.grad_mom_inv_metric_mom(state)

    def dh_dpos(self, state):
        return self.dh1_dpos(state) + self.dh2_dpos(state)

    def dh_dmom(self, state):
        return self.inv_metric_mom(state)

    def sample_momentum(self, state, rng):
        sqrt_metric = self.sqrt_metric(state)
        return sqrt_metric @ rng.normal(size=state.pos.shape)


class BaseCholeskyRiemannianMetricSystem(BaseRiemannianMetricSystem):

    def chol_metric(self, state):
        raise NotImplementedError()

    @cache_in_state('pos')
    def log_det_sqrt_metric(self, state):
        chol_metric = self.chol_metric(state)
        return np.log(chol_metric.diagonal()).sum()

    @cache_in_state('pos', 'mom')
    def inv_metric_mom(self, state):
        chol_metric = self.chol_metric(state)
        return sla.cho_solve((chol_metric, True), state.mom)

    def sqrt_metric(self, state):
        return self.chol_metric(state)


class DenseRiemannianMetricSystem(BaseCholeskyRiemannianMetricSystem):

    def __init__(self, pot_energy, metric, grad_pot_energy=None,
                 vjp_metric=None):
        super().__init__(pot_energy, grad_pot_energy)
        self._metric = metric
        self._vjp_metric = _autograd_fallback(
            vjp_metric, metric, make_vjp, 'vjp_metric')

    @cache_in_state('pos')
    def grad_log_det_sqrt_metric(self, state):
        inv_metric = self.inv_metric(state)
        return 0.5 * self.vjp_metric(state)(inv_metric)

    @cache_in_state('pos', 'mom')
    def grad_mom_inv_metric_mom(self, state):
        inv_metric_mom = self.inv_metric_mom(state)
        inv_metric_mom_outer = np.outer(inv_metric_mom, inv_metric_mom)
        return -self.vjp_metric(state)(inv_metric_mom_outer)

    @cache_in_state('pos')
    def metric(self, state):
        return self._metric(state.pos)

    @cache_in_state('pos')
    def chol_metric(self, state):
        return sla.cholesky(self.metric(state), True)

    @cache_in_state('pos')
    def inv_metric(self, state):
        chol_metric = self.chol_metric(state)
        return sla.cho_solve((chol_metric, True), np.eye(state.n_dim))

    @multi_cache_in_state(['pos'], ['vjp_metric', 'metric'])
    def vjp_metric(self, state):
        return self._vjp_metric(state.pos)


class FactoredRiemannianMetricSystem(BaseCholeskyRiemannianMetricSystem):

    def __init__(self, pot_energy, chol_metric, grad_pot_energy=None,
                 vjp_chol_metric=None):
        super().__init__(pot_energy, grad_pot_energy)
        self._chol_metric = chol_metric
        self._vjp_chol_metric = _autograd_fallback(
            vjp_chol_metric, chol_metric, make_vjp, 'vjp_chol_metric')

    @cache_in_state('pos')
    def grad_log_det_sqrt_metric(self, state):
        inv_chol_metric = self.inv_chol_metric(state)
        return self.vjp_chol_metric(state)(inv_chol_metric.T)

    @cache_in_state('pos', 'mom')
    def grad_mom_inv_metric_mom(self, state):
        chol_metric = self.chol_metric(state)
        inv_chol_metric_mom = sla.solve_triangular(
            chol_metric, state.mom, lower=True)
        inv_metric_mom = self.inv_metric_mom(state)
        inv_metric_mom_outer = np.outer(inv_metric_mom, inv_chol_metric_mom)
        return -2 * self.vjp_chol_metric(state)(inv_metric_mom_outer)

    @cache_in_state('pos')
    def chol_metric(self, state):
        return self._chol_metric(state.pos)

    @cache_in_state('pos')
    def inv_chol_metric(self, state):
        chol_metric = self.chol_metric(state)
        return sla.solve_triangular(
            chol_metric, np.eye(state.n_dim), lower=True)

    @multi_cache_in_state(['pos'], ['vjp_metric', 'metric'])
    def vjp_chol_metric(self, state):
        return self._vjp_chol_metric(state.pos)


class SoftAbsRiemannianMetricSystem(BaseRiemannianMetricSystem):

    def __init__(self, pot_energy, softabs_coeff=1.,
                 grad_pot_energy=None, hess_pot_energy=None,
                 mtp_pot_energy=None):
        super().__init__(pot_energy, grad_pot_energy)
        self.softabs_coeff = softabs_coeff
        self._hess_pot_energy = _autograd_fallback(
            hess_pot_energy, pot_energy, hessian_grad_and_value,
            'hess_pot_energy')
        self._mtp_pot_energy = _autograd_fallback(
            mtp_pot_energy, pot_energy, mtp_hessian_grad_and_value,
            'mtp_pot_energy')

    def softabs(self, x):
        return x / np.tanh(x * self.softabs_coeff)

    def grad_softabs(self, x):
        return (
            1. / np.tanh(self.softabs_coeff * x) -
            self.softabs_coeff * x / np.sinh(self.softabs_coeff * x)**2)

    @multi_cache_in_state(
        ['pos'], ['hess_pot_energy', 'grad_pot_energy', 'pot_energy'])
    def hess_pot_energy(self, state):
        return self._hess_pot_energy(state.pos)

    @multi_cache_in_state(
        ['pos'],
        ['mtp_pot_energy', 'hess_pot_energy', 'grad_pot_energy', 'pot_energy'])
    def mtp_pot_energy(self, state):
        return self._mtp_pot_energy(state.pos)

    @cache_in_state('pos')
    def eig_metric(self, state):
        hess = self.hess_pot_energy(state)
        hess_eigval, eigvec = sla.eigh(hess)
        metric_eigval = self.softabs(hess_eigval)
        return metric_eigval, hess_eigval, eigvec

    @cache_in_state('pos')
    def sqrt_metric(self, state):
        metric_eigval, hess_eigval, eigvec = self.eig_metric(state)
        return eigvec * metric_eigval**0.5

    @cache_in_state('pos')
    def log_det_sqrt_metric(self, state):
        metric_eigval, hess_eigval, eigvec = self.eig_metric(state)
        return 0.5 * np.log(metric_eigval).sum()

    @cache_in_state('pos')
    def grad_log_det_sqrt_metric(self, state):
        metric_eigval, hess_eigval, eigvec = self.eig_metric(state)
        return 0.5 * self.mtp_pot_energy(state)(
            eigvec * self.grad_softabs(hess_eigval) / metric_eigval @ eigvec.T)

    @cache_in_state('pos', 'mom')
    def inv_metric_mom(self, state):
        metric_eigval, hess_eigval, eigvec = self.eig_metric(state)
        return (eigvec / metric_eigval) @ (eigvec.T @ state.mom)

    @cache_in_state('pos', 'mom')
    def grad_mom_inv_metric_mom(self, state):
        metric_eigval, hess_eigval, eigvec = self.eig_metric(state)
        num_j_mtx = metric_eigval[:, None] - metric_eigval[None, :]
        num_j_mtx += np.diag(self.grad_softabs(hess_eigval))
        den_j_mtx = hess_eigval[:, None] - hess_eigval[None, :]
        np.fill_diagonal(den_j_mtx, 1)
        j_mtx = num_j_mtx / den_j_mtx
        eigvec_mom = (eigvec.T @ state.mom) / metric_eigval
        return -self.mtp_pot_energy(state)(
            eigvec @ (np.outer(eigvec_mom, eigvec_mom) * j_mtx) @ eigvec.T)


class BaseEuclideanMetricConstrainedSystem(BaseEuclideanMetricSystem):

    def constr(self, state):
        raise NotImplementedError()

    def jacob_constr(self, state):
        raise NotImplementedError()

    @cache_in_state('pos')
    def inv_metric_jacob_constr_t(self, state):
        jacob_constr = self.jacob_constr(state)
        return self.mult_inv_metric(jacob_constr.T)

    @cache_in_state('pos')
    def chol_gram(self, state):
        jacob_constr = self.jacob_constr(state)
        gram = jacob_constr @ self.inv_metric_jacob_constr_t(state)
        return sla.cholesky(gram, lower=True)

    def project_onto_tangent_space(self, mom, state):
        jacob_constr = self.jacob_constr(state)
        chol_gram = self.chol_gram(state)
        mom -= jacob_constr.T @ sla.cho_solve(
            (chol_gram, True), jacob_constr @ self.mult_inv_metric(mom))

    def solve_dh_dmom_for_mom(self, dpos_dt):
        return self.mult_metric(dpos_dt)

    def sample_momentum(self, state, rng):
        mom = super().sample_momentum(state, rng)
        self.project_onto_tangent_space(mom, state)
        return mom


class BaseEuclideanMetricNoJacobianDeterminantConstrainedSystem(
        BaseEuclideanMetricConstrainedSystem):

    def __init__(self, pot_energy, constr, metric=None,
                 grad_pot_energy=None, jacob_constr=None):
        super().__init__(pot_energy=pot_energy, metric=metric,
                         grad_pot_energy=grad_pot_energy)
        self._constr = constr
        self._jacob_constr = _autograd_fallback(
            jacob_constr, constr, jacobian_and_value, 'jacob_constr')

    @cache_in_state('pos')
    def constr(self, state):
        return self._constr(state.pos)

    @multi_cache_in_state(['pos'], ['jacob_constr', 'constr'])
    def jacob_constr(self, state):
        return self._jacob_constr(state.pos)


class IsotropicMetricConstrainedSystem(
        BaseEuclideanMetricNoJacobianDeterminantConstrainedSystem,
        IsotropicEuclideanMetricSystem):
    """
    Isotropic Euclidean metric Hamiltonian system with position constraints.
    """


class DiagonalMetricConstrainedSystem(
        BaseEuclideanMetricNoJacobianDeterminantConstrainedSystem,
        DiagonalEuclideanMetricSystem):
    """
    Diagonal Euclidean metric Hamiltonian system with position constraints.
    """


class DenseMetricConstrainedSystem(
        BaseEuclideanMetricNoJacobianDeterminantConstrainedSystem,
        DenseEuclideanMetricSystem):
    """
    Dense Euclidean metric Hamiltonian system with position constraints.
    """


class BaseEuclideanMetricObservedGeneratorSystem(
        BaseEuclideanMetricConstrainedSystem):

    def __init__(self, neg_log_input_density, generator, obs_output,
                 metric=None, grad_neg_log_input_density=None,
                 jacob_generator=None, mhp_generator=None):
        self.neg_log_input_density = neg_log_input_density
        self._generator = generator
        self.obs_output = obs_output
        super().__init__(
            pot_energy=neg_log_input_density,
            grad_pot_energy=grad_neg_log_input_density, metric=metric)
        self._jacob_generator = _autograd_fallback(
            jacob_generator, generator, jacobian_and_value, 'jacob_generator')
        self._mhp_generator = _autograd_fallback(
            mhp_generator, generator, mhp_jacobian_and_value, 'mhp_generator')

    @cache_in_state('pos')
    def generator(self, state):
        return self._generator(state.pos)

    @multi_cache_in_state(['pos'], ['jacob_generator', 'generator'])
    def jacob_generator(self, state):
        return self._jacob_generator(state.pos)

    @multi_cache_in_state(
        ['pos'], ['mhp_generator', 'jacob_generator', 'generator'])
    def mhp_generator(self, state):
        return self._mhp_generator(state.pos)

    def constr(self, state):
        return self.generator(state) - self.obs_output

    def jacob_constr(self, state):
        return self.jacob_generator(state)

    @cache_in_state('pos')
    def log_det_sqrt_gram(self, state):
        chol_gram = self.chol_gram(state)
        return np.log(chol_gram.diagonal()).sum()

    @cache_in_state('pos')
    def grad_log_det_sqrt_gram(self, state):
        mhp_generator = self.mhp_generator(state)
        jacob_generator = self.jacob_generator(state)
        chol_gram = self.chol_gram(state)
        gram_inv_jacob_generator = sla.cho_solve(
            (chol_gram, True), jacob_generator)
        return self.mhp_generator(state)(gram_inv_jacob_generator)

    def h(self, state):
        return (
            self.pot_energy(state) + self.log_det_sqrt_gram(state) +
            self.kin_energy(state))

    def dh_dpos(self, state):
        return (
            self.grad_pot_energy(state) + self.grad_log_det_sqrt_gram(state))


class IsotropicEuclideanMetricObservedGeneratorSystem(
        BaseEuclideanMetricObservedGeneratorSystem,
        IsotropicEuclideanMetricSystem):
    """
    Isotropic Euclidean metric observed generator Hamiltonian system.
    """


class DiagonalEuclideanMetricObservedGeneratorSystem(
        BaseEuclideanMetricObservedGeneratorSystem,
        DiagonalEuclideanMetricSystem):
    """
    Diagonal Euclidean metric observed generator Hamiltonian system.
    """


class DenseEuclideanMetricObservedGeneratorSystem(
        BaseEuclideanMetricObservedGeneratorSystem,
        DenseEuclideanMetricSystem):
    """
    Dense Euclidean metric observed generator Hamiltonian system.
    """
