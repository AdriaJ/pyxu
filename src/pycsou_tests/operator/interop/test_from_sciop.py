import itertools

import pytest
import scipy.sparse.linalg as spsl

import pycsou.operator.interop.scipy as isp
import pycsou.runtime as pycrt
import pycsou.util.deps as pycd
import pycsou.util.ptype as pyct
import pycsou_tests.operator.conftest as conftest


class FromSciOpMixin:
    # Fixtures ----------------------------------------------------------------
    @pytest.fixture
    def op_orig(self) -> pyct.OpT:
        # Override in inherited class with the operator used to create the sci_op.
        raise NotImplementedError

    @pytest.fixture(
        params=itertools.product(
            [
                pycd.NDArrayInfo.NUMPY,
                pycd.NDArrayInfo.CUPY,
                # DASK-based sci_ops unsupported
            ],
            pycrt.Width,
        )
    )
    def spec(self, op_orig, request) -> tuple[pyct.OpT, pycd.NDArrayInfo, pycrt.Width]:
        ndi, width = request.param
        if (xp := ndi.module()) is None:
            pytest.skip(f"{ndi} unsupported on this machine.")

        if ndi == pycd.NDArrayInfo.CUPY:
            import cupyx.scipy.sparse.linalg as xpl
        else:  # NUMPY
            xpl = spsl

        A = op_orig.asarray(xp=xp, dtype=width.value)
        B = spsl.aslinearoperator(A)
        op = isp.from_sciop(cls=self.base, sp_op=B)

        return op, ndi, width

    @pytest.fixture
    def data_shape(self, op_orig) -> pyct.OpShape:
        return op_orig.shape

    @pytest.fixture
    def data_apply(self, op_orig) -> conftest.DataLike:
        dim = op_orig.dim
        arr = self._random_array((dim,), seed=53)  # random seed for reproducibility
        out = op_orig.apply(arr)
        return dict(
            in_=dict(arr=arr),
            out=out,
        )

    @pytest.fixture
    def data_adjoint(self, op_orig) -> conftest.DataLike:
        codim = op_orig.codim
        arr = self._random_array((codim,), seed=54)  # random seed for reproducibility
        out = op_orig.adjoint(arr)
        return dict(
            in_=dict(arr=arr),
            out=out,
        )


class TestFromSciOpLinFunc(FromSciOpMixin, conftest.LinFuncT):
    @pytest.fixture
    def op_orig(self) -> pyct.OpT:
        import pycsou_tests.operator.examples.test_linfunc as tc

        return tc.ScaledSum(N=7)


class TestFromSciOpLinOp(FromSciOpMixin, conftest.LinOpT):
    @pytest.fixture
    def op_orig(self) -> pyct.OpT:
        import pycsou_tests.operator.examples.test_linop as tc

        return tc.Tile(N=3, M=4)


class TestFromSciOpSquareOp(FromSciOpMixin, conftest.SquareOpT):
    @pytest.fixture
    def op_orig(self) -> pyct.OpT:
        import pycsou_tests.operator.examples.test_squareop as tc

        return tc.CumSum(N=7)


class TestFromSciOpNormalOp(FromSciOpMixin, conftest.NormalOpT):
    @pytest.fixture
    def op_orig(self) -> pyct.OpT:
        import pycsou_tests.operator.examples.test_normalop as tc

        h = self._random_array((5,), seed=2)
        return tc.CircularConvolution(h=h)


class TestFromSciOpUnitOp(FromSciOpMixin, conftest.UnitOpT):
    @pytest.fixture
    def op_orig(self) -> pyct.OpT:
        import pycsou_tests.operator.examples.test_unitop as tc

        return tc.Permutation(N=7)


class TestFromSciOpSelfAdjointOp(FromSciOpMixin, conftest.SelfAdjointOpT):
    @pytest.fixture
    def op_orig(self) -> pyct.OpT:
        import pycsou_tests.operator.examples.test_selfadjointop as tc

        return tc.SelfAdjointConvolution(N=7)


class TestFromSciOpPosDefOp(FromSciOpMixin, conftest.PosDefOpT):
    @pytest.fixture
    def op_orig(self) -> pyct.OpT:
        import pycsou_tests.operator.examples.test_posdefop as tc

        return tc.PSDConvolution(N=7)


class TestFromSciOpProjOp(FromSciOpMixin, conftest.ProjOpT):
    @pytest.fixture
    def op_orig(self) -> pyct.OpT:
        import pycsou_tests.operator.examples.test_projop as tc

        return tc.Oblique(N=7, alpha=0.3)


class TestFromSciOpOrthProjOp(FromSciOpMixin, conftest.OrthProjOpT):
    @pytest.fixture
    def op_orig(self) -> pyct.OpT:
        import pycsou_tests.operator.examples.test_orthprojop as tc

        return tc.ScaleDown(N=7)
