import itertools

import numpy as np
import pytest

import pyxu.abc as pxa
import pyxu.info.deps as pxd
import pyxu.info.ptype as pxt
import pyxu.math.linalg as pxlg
import pyxu.runtime as pxrt
import pyxu_tests.operator.conftest as conftest


class SquaredL2Norm(pxa.DiffFunc):
    # f: \bR^{M} -> \bR
    #      x     -> \norm{x}{2}^{2}
    def __init__(self, M: int):
        super().__init__(shape=(1, M))
        self.lipschitz = np.inf
        self.diff_lipschitz = 2

    @pxrt.enforce_precision(i="arr")
    def apply(self, arr):
        y = pxlg.norm(arr, axis=-1, keepdims=True)
        y **= 2
        return y

    @pxrt.enforce_precision(i="arr")
    def grad(self, arr):
        return 2 * arr

    def asloss(self, data: pxt.NDArray = None) -> pxt.OpT:
        from pyxu.operator.func.loss import shift_loss

        op = shift_loss(op=self, data=data)
        return op


class TestSquaredL2Norm(conftest.DiffFuncT):
    @pytest.fixture(
        params=itertools.product(
            ((4, SquaredL2Norm(M=4)),),  # dim, op
            pxd.NDArrayInfo,
            pxrt.Width,
        )
    )
    def _spec(self, request):
        return request.param

    @pytest.fixture
    def spec(self, _spec):
        return _spec[0][1], _spec[1], _spec[2]

    @pytest.fixture
    def dim(self, _spec):
        return _spec[0][0]

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