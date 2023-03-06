import collections.abc as cabc
import datetime as dt
import functools
import itertools
import operator

import numpy as np
import pytest

import pycsou.abc.operator as pyco
import pycsou.operator as pycop
import pycsou.operator.func as pycf
import pycsou.opt.solver as pycos
import pycsou.opt.stop as pycstop
import pycsou.util.ptype as pyct
import pycsou_tests.opt.solver.conftest as conftest


def generate_funcs_K(descr, N_term) -> cabc.Sequence[tuple[pyct.OpT]]:
    # Take description of many functionals, i.e. output of funcs(), and return a stream of
    # length-N_term tuples, where each of the first N_term - 1 terms of each tuple is a functional created by composing
    # and summing a subset of `descr`, and the last term is a tuple (func, op) that is an element of `descr`.
    #
    # Examples
    # --------
    # generate_funcs([(a, b), (c, d), (e, f)], 2)
    # -> [ (c*d + e*f, (a, b)), (a*b + e*f, (c, d)), (a*b + c*d, (e, f)), ]
    # generate_funcs([(a, b), (c, d), (e, f)], 3)
    # -> [ (c*d, e*f, (a, b)), (a*b, e*f, (c, d)),
    #      (e*f, c*d, (a, b)), (a*b, c*d, (e, f)),
    #      (e*f, a*b, (c, d)), (c*d, a*b, (e, f)) ]

    assert 2 <= N_term <= len(descr)  # Must have at least 2 terms because h cannot be a sum

    def chain(x, y):
        comp = x * y
        comp.diff_lipschitz()
        return comp

    stream = []
    for d in itertools.permutations(descr, N_term - 1):
        to_sum = list(set(descr) - set(d))
        p = functools.reduce(operator.add, [chain(*dd) for dd in to_sum])
        p.diff_lipschitz()
        stream.append([*d, p])  # The last subset is a func while the others are (func, op) tuples

    stream_K = []
    for part in stream:
        for n in range(N_term - 1):
            p = part.copy()
            del p[n]  # The n-th subset is used for the (h, K) pair
            p = [chain(*dd) for dd in p[:-1]]  # Compose all but the last subset (which is already a func)
            stream_K.append((*p, part[-1], part[n]))  # Append the last subset and the (h, K) tuple
    return stream_K


class MixinPDS(conftest.SolverT):
    @staticmethod
    def generate_init_kwargs(N: int, has_f: bool, has_g: bool, has_h: bool, has_K: bool) -> list[dict]:
        # Returns a stream of dictionaries for the init_kwargs fixture of the solver based on whether that solver has
        # arguments f, g, h and K. All possible combinations of the output of `funcs` are tested.

        funcs = conftest.funcs(N, seed=3)
        stream1 = conftest.generate_funcs(funcs, N_term=1)
        stream2 = conftest.generate_funcs(funcs, N_term=2)

        kwargs_init = []
        if has_f:
            kwargs_init.extend([dict(f=f) for (f, *_) in stream1])
        if has_g:
            kwargs_init.extend([dict(g=g) for (g, *_) in stream1])
            if has_f:
                kwargs_init.extend([dict(f=f, g=g) for (f, g) in stream2])
        if has_h:
            kwargs_init.extend([dict(h=h) for (h, *_) in stream1])
            if has_f:
                kwargs_init.extend([dict(f=f, h=h) for (f, h) in stream2])
            if has_g:
                kwargs_init.extend([dict(g=g, h=h) for (g, h) in stream2])
                if has_f:
                    stream3 = conftest.generate_funcs(funcs, N_term=3)
                    kwargs_init.extend([dict(f=f, g=g, h=h) for (f, g, h) in stream3])

        if has_K:
            stream2_K = generate_funcs_K(funcs, N_term=2)
            if has_f:
                kwargs_init.extend([dict(f=f, h=h, K=K) for (f, (h, K)) in stream2_K])
            if has_g:
                kwargs_init.extend([dict(g=g, h=h, K=K) for (g, (h, K)) in stream2_K])
                if has_f:
                    stream3_K = generate_funcs_K(funcs, N_term=3)
                    kwargs_init.extend([dict(f=f, g=g, h=h, K=K) for (f, g, (h, K)) in stream3_K])
        return kwargs_init

    @pytest.fixture
    def spec(self, klass, init_kwargs, fit_kwargs) -> tuple[pyct.SolverC, dict, dict]:
        return klass, init_kwargs, fit_kwargs

    @pytest.fixture(params=[1, 2, 3])
    def tuning_strategy(self, request) -> int:
        return request.param

    @pytest.fixture(params=["CV", "PD3O"])
    def base(self, request) -> pyct.SolverC:
        bases = {"CV": pycos.CV, "PD3O": pycos.PD3O}
        return bases[request.param]

    @pytest.fixture
    def N(self) -> int:
        return 5

    @pytest.fixture(params=["1d", "nd"])
    def x0(self, N, request) -> dict:
        # Multiple initial points
        return {"1d": np.full((N,), 3.0), "nd": np.full((2, N), 15.0)}[request.param]

    @pytest.fixture
    def klass(self) -> pyct.SolverC:
        return NotImplementedError

    @pytest.fixture
    def init_kwargs(self) -> dict:
        return NotImplementedError

    @pytest.fixture
    def fit_kwargs(self, x0, tuning_strategy) -> dict:
        # Overriden only for ADMM
        return dict(
            x0=x0,
            tuning_strategy=tuning_strategy,
        )

    @pytest.fixture
    def cost_function(self, N, init_kwargs) -> dict[str, pyct.OpT]:
        kwargs = [init_kwargs.get(k, pycf.NullFunc(dim=N)) for k in ("f", "g", "h")]
        func = kwargs[0] + kwargs[1]
        if init_kwargs.get("h") is not None:
            func += init_kwargs.get("h") * init_kwargs.get("K", pycop.IdentityOp(dim=N))
        return dict(x=func)


class TestPD3O(MixinPDS):
    @pytest.fixture
    def klass(self) -> pyct.SolverC:
        return pycos.PD3O

    @pytest.fixture(params=MixinPDS.generate_init_kwargs(N=5, has_f=True, has_g=True, has_h=True, has_K=True))
    def init_kwargs(self, request) -> dict:
        return request.param


class TestCV(MixinPDS):
    @pytest.fixture
    def klass(self) -> pyct.SolverC:
        return pycos.CV

    @pytest.fixture(params=MixinPDS.generate_init_kwargs(N=5, has_f=True, has_g=True, has_h=True, has_K=True))
    def init_kwargs(self, request) -> dict:
        return request.param


class TestCP(MixinPDS):
    @pytest.fixture
    def klass(self) -> pyct.SolverC:
        return pycos.CP

    @pytest.fixture(params=MixinPDS.generate_init_kwargs(N=5, has_f=False, has_g=True, has_h=True, has_K=True))
    def init_kwargs(self, request, base) -> dict:
        kwargs = request.param
        kwargs.update({"base": base})
        return kwargs


class TestLV(MixinPDS):
    @pytest.fixture
    def klass(self) -> pyct.SolverC:
        return pycos.LV

    @pytest.fixture(params=MixinPDS.generate_init_kwargs(N=5, has_f=True, has_g=False, has_h=True, has_K=True))
    def init_kwargs(self, request) -> dict:
        return request.param


class TestDY(MixinPDS):
    @pytest.fixture
    def klass(self) -> pyct.SolverC:
        return pycos.DY

    @pytest.fixture(params=MixinPDS.generate_init_kwargs(N=5, has_f=True, has_g=True, has_h=True, has_K=False))
    def init_kwargs(self, request) -> dict:
        return request.param


class TestDR(MixinPDS):
    @pytest.fixture
    def klass(self) -> pyct.SolverC:
        return pycos.DR

    @pytest.fixture(params=MixinPDS.generate_init_kwargs(N=5, has_f=False, has_g=True, has_h=True, has_K=False))
    def init_kwargs(self, request, base) -> dict:
        kwargs = request.param
        kwargs.update({"base": base})
        return kwargs


class TestADMM(MixinPDS):
    @pytest.fixture
    def klass(self) -> pyct.SolverC:
        return pycos.ADMM

    @pytest.fixture(params=MixinPDS.generate_init_kwargs(N=5, has_f=True, has_g=False, has_h=True, has_K=True))
    def init_kwargs(self, request) -> dict:
        return request.param

    @pytest.fixture
    def spec(self, klass, init_kwargs, fit_kwargs) -> tuple[pyct.SolverC, dict, dict]:
        # Overriden from base class
        isNLCG = (init_kwargs.get("K") is not None) and (not isinstance(init_kwargs.get("f"), pyco.QuadraticFunc))
        if (fit_kwargs["x0"].squeeze().ndim > 1) and isNLCG:
            pytest.skip(f"NLCG scenario with multiple initial points not supported.")
        return klass, init_kwargs, fit_kwargs

    @pytest.fixture
    def fit_kwargs(self, x0, tuning_strategy) -> dict:
        # Overriden from base class
        return dict(
            x0=x0,
            tuning_strategy=tuning_strategy,
            solver_kwargs=dict(stop_crit=pycstop.AbsError(eps=1e-4, var="gradient") | pycstop.RelError(1e-4)),
            # Stopping criterion necessary for NLGC scenario (the default stopping criterion is sometimes never
            # satisfied)
        )


class TestFB(MixinPDS):
    @pytest.fixture
    def klass(self) -> pyct.SolverC:
        return pycos.FB

    @pytest.fixture(params=MixinPDS.generate_init_kwargs(N=5, has_f=True, has_g=True, has_h=False, has_K=False))
    def init_kwargs(self, request) -> dict:
        return request.param


class TestPP(MixinPDS):
    @pytest.fixture
    def klass(self) -> pyct.SolverC:
        return pycos.PP

    @pytest.fixture(params=MixinPDS.generate_init_kwargs(N=5, has_f=False, has_g=True, has_h=False, has_K=False))
    def init_kwargs(self, request, base) -> dict:
        kwargs = request.param
        kwargs.update({"base": base})
        return kwargs
