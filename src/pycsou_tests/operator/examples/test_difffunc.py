import numpy as np
import pytest

import pycsou.abc as pyca
import pycsou.operator.func as pycof
import pycsou.runtime as pycrt
import pycsou.util as pycu
import pycsou_tests.operator.conftest as conftest


class SquaredL2Norm(pyca.DiffFunc):
    # f: \bR^{M} -> \bR
    #      x     -> \norm{x}{2}^{2}
    def __init__(self, M: int = None):
        super().__init__(shape=(1, M))
        self._lipschitz = np.inf
        self._diff_lipschitz = 2

    @pycrt.enforce_precision(i="arr")
    def apply(self, arr):
        xp = pycu.get_array_module(arr)
        y = xp.linalg.norm(arr, axis=-1, keepdims=True)
        y2 = xp.power(y, 2, dtype=arr.dtype)
        return y2

    @pycrt.enforce_precision(i="arr")
    def grad(self, arr):
        return 2 * arr

    def asloss(self, data=None):
        return pycof.shift_loss(self, data)


class TestSquaredL2Norm(conftest.DiffFuncT):
    @pytest.fixture(params=[4, None])
    def dim(self, request):
        return request.param

    @pytest.fixture
    def op(self, dim):
        return SquaredL2Norm(M=dim)

    @pytest.fixture
    def data_shape(self, dim):
        return (1, dim)

    @pytest.fixture(
        params=[  # 2 evaluation points
            dict(
                in_=dict(arr=np.zeros((4,))),
                out=np.zeros((1,)),
            ),
            dict(
                in_=dict(arr=np.arange(-3, 1)),
                out=np.array([14]),
            ),
        ]
    )
    def data_apply(self, request):
        return request.param

    @pytest.fixture
    def data_math_lipschitz(self, dim):
        N_test, dim = 6, self._sanitize(dim, 3)
        return self._random_array((N_test, dim), seed=5)

    @pytest.fixture
    def data_math_diff_lipschitz(self, dim):
        N_test, dim = 6, self._sanitize(dim, 3)
        return self._random_array((N_test, dim), seed=6)

    @pytest.fixture(
        params=[  # 2 evaluation points
            dict(
                in_=dict(arr=np.zeros((4,))),
                out=np.zeros((4,)),
            ),
            dict(
                in_=dict(arr=np.arange(-3, 1)),
                out=2 * np.arange(-3, 1),
            ),
        ]
    )
    def data_grad(self, request):
        return request.param
