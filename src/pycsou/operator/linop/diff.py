import collections.abc as cabc
import functools
import itertools
import math
import typing as typ

import numpy as np

import pycsou.abc.operator as pyco
import pycsou.operator.blocks as pycb
import pycsou.operator.linop.base
import pycsou.operator.linop.base as pycob
import pycsou.runtime as pycrt
import pycsou.util as pycu
import pycsou.util.ptype as pyct

try:
    import scipy.ndimage._filters as scif
except ImportError:
    import scipy.ndimage.filters as scif

__all__ = [
    "FiniteDifference",
    "GaussianDerivative",
    "PartialDerivative",
    "Gradient",
    "Hessian",
    "DirectionalDerivative",
    "DirectionalGradient",
    "DirectionalLaplacian",
    "DirectionalHessian",
]


class _BaseDifferential(pycob.Stencil):
    r"""
    Helper base class for differential operators based on Numba stencils (see
    https://numba.pydata.org/numba-doc/latest/user/stencil.html).

    See Also
    --------
    :py:class:`~pycsou.operator.linop.base.Stencil`, :py:func:`~pycsou.math.stencil.make_nd_stencil`,
    :py:class:`~pycsou.operator.linop.diff._FiniteDifferences`,
    :py:class:`~pycsou.operator.linop.diff.GaussianDerivative`,
    :py:class:`~pycsou.operator.linop.diff.PartialDerivative`, :py:class:`~pycsou.operator.linop.diff.Gradient`,
    :py:class:`~pycsou.operator.linop.diff.Hessian`.
    """

    def __init__(
        self,
        kernel: pyct.NDArray,
        center: pyct.NDArray,
        arg_shape: pyct.Shape,
        boundary: typ.Optional[typ.Union[pyct.Real, str, tuple, dict]] = None,
    ):
        r"""
        Parameters
        ----------
        kernel: NDArray
        center: NDArray
        arg_shape: tuple
        boundary: real, str, tuple, or dict , keyword only.
            How to handle the boundaries. (see :py:class:`~pycsou.operator.linop.base.Stencil`) for more information.
        """

        super(_BaseDifferential, self).__init__(
            stencil_coefs=kernel, center=center, arg_shape=arg_shape, boundary=boundary
        )

    @staticmethod
    def _sanitize_init_kwargs(
        order: typ.Union[pyct.Integer, typ.Tuple[pyct.Integer, ...]],
        param1: typ.Union[str, pyct.Real, typ.Tuple[str, ...], typ.Tuple[pyct.Real, ...]],
        param1_name: str,
        param2: typ.Union[pyct.Real, typ.Tuple[pyct.Real, ...]],
        param2_name: str,
        arg_shape: pyct.Shape,
        axis: typ.Union[pyct.Integer, typ.Tuple] = None,
    ):

        r"""
        Ensures that inputs have the appropriate shape and values.
        """

        def _ensure_tuple(param, param_name: str) -> typ.Union[tuple[pyct.Integer, ...], tuple[str, ...]]:
            r"""
            Enforces the input parameters to be tuples of the same size as `arg_shape`.
            """
            if not isinstance(param, cabc.Sequence) or isinstance(param, str):
                param = (param,)
            assert (len(param) == 1) | (len(param) == len(arg_shape)), (
                f"The length of {param_name} cannot be larger than the"
                f"number of dimensions ({len(arg_shape)}) defined by `arg_shape`"
            )
            return param

        order = _ensure_tuple(order, param_name="order")
        _param1 = _ensure_tuple(param1, param_name=param1_name)
        _param2 = _ensure_tuple(param2, param_name=param2_name)

        assert all([o > 0 for o in order]), "Order must be strictly positive"

        if param1_name == "sigma":
            assert all([p >= 0 for p in _param1]), "Sigma must be strictly positive"
        if param2_name == "accuracy":
            assert all([p >= 0 for p in _param2]), "Accuracy must be positive"
        elif param2_name == "truncate":
            assert all([p > 0 for p in _param2]), "Truncate must be strictly positive"

        if len(order) != len(arg_shape):
            assert axis is not None, (
                "If `order` is not a tuple with size of arg_shape, then `axis` must be" " specified. Got `axis=None`"
            )
            axis = _ensure_tuple(axis, param_name="axis")
            assert len(axis) == len(order), "`axis` must have the same number of elements as `order`"

        else:
            if axis is not None:
                axis = _ensure_tuple(axis, param_name="axis")
                assert len(axis) == len(order), "`axis` must have the same number of elements as `order`"
            else:
                axis = tuple([i for i in range(len(arg_shape))])

        if not (len(_param1) == len(order)):
            assert len(_param1) == 1, (
                f"Parameter `{param1_name}` inconsistent with the number of elements in " "parameter `order`."
            )
            _param1 = _param1 * len(order)

        if not (len(_param2) == len(order)):
            assert len(_param2) == 1, (
                f"Parameter `{param2_name}` inconsistent with the number of elements in " "parameter `order`."
            )
            _param2 = _param2 * len(order)

        return (
            order,
            _param1,
            _param2,
            axis,
        )

    def _create_kernel(self, axis) -> typ.Tuple[pyct.NDArray, pyct.NDArray]:
        r"""
        Creates kernel for stencil.
        """
        stencil_ids = [None] * len(self.arg_shape)
        stencil_coefs = [None] * len(self.arg_shape)
        center = np.zeros(len(self.arg_shape), dtype=int)

        # Create finite difference coefficients for each dimension
        for i, ax in enumerate(axis):
            stencil_ids[ax], stencil_coefs[ax], center[ax] = self._fill_coefs(i)

        # Create a kernel composing all dimensions coefficients
        kernel = np.zeros([np.ptp(ids) + 1 if ids else 1 for ids in stencil_ids])
        for i, ax in enumerate(axis):
            slices = tuple(
                [slice(center[j], center[j] + 1) if j != ax else slice(None) for j in range(len(self.arg_shape))]
            )
            shape = [1 if j != ax else kernel.shape[ax] for j in range(len(self.arg_shape))]
            kernel[slices] += stencil_coefs[ax].reshape(shape)

        return kernel, center

    def _fill_coefs(self, i: pyct.Integer) -> typ.Tuple[list, pyct.NDArray, pyct.Integer]:
        r"""
        Defines kernel elements.
        """
        raise NotImplementedError


class FiniteDifference(_BaseDifferential):
    r"""
    Finite Difference base operator. This class is used by :py:class:`~pycsou.operator.linop.diff.PartialDerivative`,
    :py:class:`~pycsou.operator.linop.diff.Gradient` and :py:class:`~pycsou.operator.linop.diff.Hessian`.

    Notes
    -----
    This operator approximates the derivative by finite differences.
    It uses the `Finite Difference Coefficients Calculator <https://web.media.mit.edu/~crtaylor/calculator.html>`_
    to construct finite difference approximations for a given desired derivative order, desired approximation
    accuracy, and finite difference type. The three basic types of finite differences are considered here:

    - **Forward difference**: :math:`D_{F}f(x) = \frac{f(x+h) - f(x)}{h}`.

        For :math:`\mathbf{x} \in \mathbb{R}^{3}` , assuming :math:`h=1` and reflecting boundary conditions, the forward difference
        operator can be implemented by the following square matrix:

        .. math::

            \mathbf{D}_{F} = \begin{bmatrix}
             -1 & 1 & 0\\
             0 & -1 & 1\\
             1 & 0 & -1\\
            \end{bmatrix}

    - **Backward difference**: :math:`D_{B}f(x) = \frac{f(x) - f(x-h)}{h}`.

        For :math:`\mathbf{x} \in \mathbb{R}^{3}` , assuming :math:`h=1` and reflecting boundary conditions, the backward
        difference operator can be implemented by the following square matrix:

        .. math::

            \mathbf{D}_{B} = \begin{bmatrix}
             1 & 0 & 0\\
             -1 & 1 & 0\\
             0 & -1 & 1\\
            \end{bmatrix}

    - **Central difference**: :math:`D_{C}f(x) = \frac{f(x+\frac{h}{2}) - f(x-\frac{h}{2})}{h}`.

        For :math:`\mathbf{x} \in \mathbb{R}^{3}` , assuming :math:`h=2` and reflecting boundary conditions, the backward
        difference operator can be implemented by the following square matrix:

        .. math::

            \mathbf{D}_{C} = \frac{1}{2}\begin{bmatrix}
             0 & 1 & -1\\
             -1 & 0 & 1\\
             1 & -1 & 0\\
            \end{bmatrix}

    For a given arbitrary order :math:`d\in\mathbb{Z}_{>0}` and accuracy :math:`a\in\mathbb{Z}_{>0}`, the number of
    stencil points :math:`N_{s}` used for finite difference is obtained as follows [see
    `ref <https://www.ams.org/journals/mcom/1988-51-184/S0025-5718-1988-0935077-0/S0025-5718-1988-0935077-0.pdf>`_]:

    - For central differences:
        :math:`N_{s} = 2 \lfloor\frac{d + 1}{2}\rfloor - 1 + a`

    - For forward and backward differences:
        :math:`N_{s} = d + a`

    For a given arbitrary stencil points :math:`s` of length :math:`N_{s}` with the order of derivatives :math:`d<N_{s}`
    , the coefficients of the finite difference approximation to the derivative are obtained by solving the linear
    system [see `ref <https://web.media.mit.edu/~crtaylor/calculator.html>`_]:

    .. math::
        \left(\begin{array}{ccc}
        s_{1}^{0} & \cdots & s_{N}^{0} \\
        \vdots & \ddots & \vdots \\
        s_{1}^{N-1} & \cdots & s_{N}^{N-1}
        \end{array}\right)\left(\begin{array}{c}
        a_{1} \\
        \vdots \\
        a_{N}
        \end{array}\right)= \frac{1}{h^{d}}\left(\begin{array}{c}
        \delta_{v_{, d}} \\
        \vdots \\
        \delta_{i, d} \\
        \vdots \\
        \delta_{N-1, d}
        \end{array}\right)

    Where :math:`h` corresponds to the spacing of the finite differences', which is defined automatically to guarantee
    a good approximation.

    This class inherits its methods from :py:class:`~pycsou.operator.linop.base.Stencil`. The user is encouraged to read
    the documentation of the :py:class:`~pycsou.operator.linop.base.Stencil` class for a description of accepted
    `boundary` padding options.

    **Adjoint**

    The adjoint of the finite difference operator is obtained by flipping its stencil kernel around the center. For the
    note that this results in the following relations:

    - :math:`\mathbf{D}_{F}^{\ast} = \mathbf{D}_{F}^{\top} = -\mathbf{D}_{B}`
    - :math:`\mathbf{D}_{B}^{\ast} = \mathbf{D}_{B}^{\top} = -\mathbf{D}_{F}`
    - :math:`\mathbf{D}_{C}^{\ast} = \mathbf{D}_{C}^{\top} = -\mathbf{D}_{C}`


    **Remark 1**

    In the case of NDArrays with more than one dimension, the stencil kernels created can consist on the simultaneous
    finite differences in different dimensions. For example, if `order = (1, 1)`, and `diff_type=central`, a central
    difference kernel for x and y will be created with the following form:

    .. math::
        \left(\begin{array}{ccc}
        0 & -0.5 & 0 \\
        -0.5 & 0 & 0.5 \\
        0 & 0.5 & 0
        \end{array}\right)

    Note that this corresponds to the stencil representation (not the operator in matrix-form). Also note that this
    stencil corresponds to the sum of first order partial derivatives:

    .. math::
        \frac{ \partial \mathbf{f} }{\partial x_{0}} + \frac{ \partial \mathbf{f} }{\partial x_{1}}

    And NOT to the second order partial derivative:

    .. math::
        \frac{\partial^{2} \mathbf{f}}{\partial x_{0} \partial x_{1}}

    For the latter kind, :py:class:`~pycsou.operator.linop.diff.PartialDerivative` is the appropriate class.

    **Remark 2**

    If `order` is a tuple then different arguments (`diff_type`, `accuracy` and `boundary`) can be specified for each
    dimension/axis with a tuple.

    Examples
    --------

    .. plot::

       import matplotlib.pyplot as plt
       from pycsou.operator.linop.diff import FiniteDifference
       from pycsou.util.misc import peaks

       x = np.linspace(-2.5, 2.5, 25)
       xx, yy = np.meshgrid(x, x)
       image = peaks(xx, yy)
       arg_shape = image.shape  # Shape of our image
       order = (1, 2)
       # Compute derivative of order 1 in first dimension
       diff1 = FiniteDifference(order=1, axis=0, arg_shape=arg_shape, diff_type="central")
       # Compute derivative of order 2 in second dimension
       diff2 = FiniteDifference(order=2, axis=1, arg_shape=arg_shape, diff_type="central")
       # Compute derivative of order 1 in first dimension, order 2 in second dimension
       diff = FiniteDifference(order=order, arg_shape=arg_shape, diff_type="central")
       out1 = diff1(image.reshape(1, -1)).reshape(arg_shape)
       out2 = diff2(image.reshape(1, -1)).reshape(arg_shape)
       out = diff(image.reshape(1, -1)).reshape(arg_shape)

       plt.figure()
       plt.imshow(image.T),
       plt.axis('off')
       plt.colorbar()
       plt.title('f(x,y)')

       plt.figure()
       plt.imshow(out1.T)
       plt.axis('off')
       plt.title(r'$\frac{\partial f(x,y)}{\partial x}$')

       plt.figure()
       plt.imshow(out2.T)
       plt.axis('off')
       plt.title(r'$\frac{\partial^{2} f(x,y)}{\partial y^{2}}$')

       plt.figure()
       plt.imshow(out.T)
       plt.axis('off')
       plt.title(r'$\frac{\partial f(x,y)}{\partial x} + \frac{\partial^{2} f(x,y)}{\partial y^{2}}$')

       assert np.allclose(out, out1 + out2)


    See Also
    --------
    :py:class:`~pycsou.operator.linop.diff._BaseDifferential`, :py:class:`~pycsou.operator.linop.diff.GaussianDerivative`,
    :py:class:`~pycsou.operator.linop.diff.PartialDerivative`, :py:class:`~pycsou.operator.linop.diff.Gradient`,
    :py:class:`~pycsou.operator.linop.diff.Hessian`.
    """

    def __init__(
        self,
        order: typ.Union[pyct.Integer, tuple[pyct.Integer, ...]],
        arg_shape: pyct.Shape,
        diff_type: typ.Union[str, tuple[str, ...]] = "forward",
        axis: typ.Union[pyct.Integer, tuple[pyct.Integer, ...], None] = None,
        accuracy: typ.Union[pyct.Integer, tuple[pyct.Integer, ...]] = 1,
        boundary: typ.Optional[typ.Union[pyct.Real, str, tuple, dict]] = None,
    ):
        """
        Parameters
        ----------
        order: int | tuple
            Derivative order. If a single integer value is provided, then `axis` should be provided to indicate which
            dimension should be used for differentiation. If a tuple is provided, it should contain as many elements as
            number of dimensions in `axis`.
        arg_shape: tuple
            Shape of the input array
        diff_type: str, tuple
            Type of finite differences ["forward", "backward", "central"]. Defaults to "forward".
        axis: int | tuple | None
            Axis to which apply the derivative. It maps the argument `order` to the specified dimensions of the input
            array. Defaults to None, assuming that the `order` argument has as many elements as dimensions of the input.
        accuracy: int, tuple
            Approximation accuracy to the derivative. See `Notes`.
        boundary: real, str, tuple, or dict , keyword only.
            How to handle the boundaries. (see :py:class:`~pycsou.operator.linop.base.Stencil`) for more information.
        """

        self.arg_shape = arg_shape
        self.order, self._param1, self._param2, self.axis = self._sanitize_init_kwargs(
            order=order,
            param1=diff_type,
            param1_name="diff_type",
            param2=accuracy,
            param2_name="accuracy",
            arg_shape=arg_shape,
            axis=axis,
        )
        kernel, center = self._create_kernel(self.axis)
        super(FiniteDifference, self).__init__(kernel=kernel, center=center, arg_shape=arg_shape, boundary=boundary)

    def _fill_coefs(self, i: pyct.Integer) -> typ.Tuple[list, pyct.NDArray, pyct.Integer]:
        r"""
        Defines kernel elements.
        """
        stencil_ids = self._compute_ids(order=self.order[i], diff_type=self._param1[i], accuracy=self._param2[i])
        stencil_coefs = self._compute_coefficients(stencil_ids=stencil_ids, order=self.order[i])
        center = stencil_ids.index(0)
        return stencil_ids, stencil_coefs, center

    @staticmethod
    def _compute_ids(order: pyct.Integer, diff_type: str, accuracy: pyct.Real) -> list:
        """
        Computes the Finite difference indices according to the order, type and accuracy.
        """
        if diff_type == "central":
            n_coefs = 2 * ((order + 1) // 2) - 1 + accuracy
            ids = np.arange(-n_coefs // 2, n_coefs // 2 + 1, dtype=int)
        else:
            n_coefs = order + accuracy
            if diff_type == "forward":
                ids = np.arange(0, n_coefs, dtype=int)
            elif diff_type == "backward":
                ids = np.arange(-n_coefs + 1, 1, dtype=int)
            else:
                raise ValueError(
                    f"Incorrect value for variable 'type'. 'type' should be ['forward', 'backward', "
                    f"'central'], but got {diff_type}."
                )
        return ids.tolist()

    @staticmethod
    def _compute_coefficients(stencil_ids: list, order: pyct.Integer) -> pyct.NDArray:
        """
        Computes the finite difference coefficients based on the order and indices.
        """
        # vander doesn't allow precision specification
        stencil_mat = np.vander(
            np.array(stencil_ids),
            increasing=True,
        ).T.astype(pycrt.getPrecision().value)
        vec = np.zeros(len(stencil_ids), dtype=pycrt.getPrecision().value)
        vec[order] = math.factorial(order)
        coefs = np.linalg.solve(stencil_mat, vec)
        return coefs


class GaussianDerivative(_BaseDifferential):
    r"""
    Gaussian derivative operator. This class is used by :py:class:`~pycsou.operator.linop.diff.PartialDerivative`,
    :py:class:`~pycsou.operator.linop.diff.Gradient` and :py:class:`~pycsou.operator.linop.diff.Hessian`.

    Notes
    -----

    This operator approximates the derivative via a Gaussian finite derivative. Computing the derivative of a function
    convolved with a Gaussian is equivalent to convolving the image with the derivative of a Gaussian:

    .. math::

        \frac{\partial}{\partial x}\left[ f(x) * g(x) \right] = \frac{\partial}{\partial x} * f(x) * g(x) = f(x) *
        \frac{\partial}{\partial x} * g(x) = f(x) * \left[\frac{\partial}{\partial x} g(x) \right]

    Given that we can compute the derivative of the Gaussian analytically, we can sample it and make a filter out of
    it. This means that we can compute the `exact derivative` of a smoothed signal. It is a different approximation to
    the true derivative of the signal, in contrast to the Finite Difference Method
    (see :py:class:`~pycsou.operator.linop.diff.FiniteDifference`).

    For :math:`\mathbf{x} \in \mathbb{R}^{3}` , assuming :math:`h=1` and reflecting boundary conditions, the
    Gaussian derivative operator can be implemented by the following square matrix:

    .. math::
        \mathbf{D}_{G} = \begin{bmatrix}
         0 & 0.274 & -0.274\\
         -0.274 & 0 & 0.274\\
         0.274 & -0.274 & 0\\
        \end{bmatrix}


    This class inherits its methods from :py:class:`~pycsou.operator.linop.base.Stencil`. The user is encouraged to read
    the documentation of the :py:class:`~pycsou.operator.linop.base.Stencil` class for a description of accepted
    `boundary` padding options.

    **Adjoint**

    The adjoint of the Gaussian derivative operator is obtained by flipping its stencil kernel around the center. Note
    that this results in the following relation:

    - :math:`\mathbf{D}_{G}^{\ast} = \mathbf{D}_{G}^{\top} = -\mathbf{D}_{G}`

    **Remark 1**

    The stencil kernels created can consist on the sum of Gaussian Derivatives in different dimensions. For example,
    if `order` is a tuple (1, 1), `sigma` is `1.0` and `truncate` is `1.0`, the following kernel will be created:

    .. math::
        \left(\begin{array}{ccc}
        0 & -0.274 & 0 \\
        -0.274 & 0 & 0.274 \\
        0 & 0.274 & 0
        \end{array}\right)

    Note that this corresponds to the sum of first order partial derivatives:

    .. math::
        \frac{ \partial \mathbf{f} }{\partial x_{0}} + \frac{ \partial \mathbf{f} }{\partial x_{1}}

    And NOT to the second order partial derivative:

    .. math::
        \frac{\partial^{2} \mathbf{f}}{\partial x_{0} \partial x_{1}}

    For the latter kind, :py:class:`~pycsou.operator.linop.diff.PartialDerivative` is the appropriate class.

    **Remark 2**

    If `order` is a tuple then different arguments (`diff_type`, `accuracy` and `boundary`) can be specified for each
    dimension/axis with a tuple.

    Examples
    --------

    .. plot::

       import matplotlib.pyplot as plt
       from pycsou.operator.linop.diff import GaussianDerivative
       from pycsou.util.misc import peaks

       x = np.linspace(-2.5, 2.5, 25)
       xx, yy = np.meshgrid(x, x)
       image = peaks(xx, yy)
       arg_shape = image.shape  # Shape of our image
       order = (1, 2)
       # Compute derivative of order 1 in first dimension
       diff1 = GaussianDerivative(order=1, axis=0, arg_shape=arg_shape, sigma=2.)
       # Compute derivative of order 2 in second dimension
       diff2 = GaussianDerivative(order=2, axis=1, arg_shape=arg_shape, sigma=2.)
       # Compute derivative of order 1 in first dimension, order 2 in second dimension
       diff = GaussianDerivative(order=order, arg_shape=arg_shape, sigma=2.)
       out1 = diff1(image.reshape(1, -1)).reshape(arg_shape)
       out2 = diff2(image.reshape(1, -1)).reshape(arg_shape)
       out = diff(image.reshape(1, -1)).reshape(arg_shape)

       plt.figure()
       plt.imshow(image.T),
       plt.axis('off')
       plt.colorbar()
       plt.title('f(x,y)')

       plt.figure()
       plt.imshow(out1.T)
       plt.axis('off')
       plt.title(r'$\frac{\partial f(x,y)}{\partial x}$')

       plt.figure()
       plt.imshow(out2.T)
       plt.axis('off')
       plt.title(r'$\frac{\partial^{2} f(x,y)}{\partial y^{2}}$')

       plt.figure()
       plt.imshow(out.T)
       plt.axis('off')
       plt.title(r'$\frac{\partial f(x,y)}{\partial x} + \frac{\partial^{2} f(x,y)}{\partial y^{2}}$')

       assert np.allclose(out, out1 + out2)

    See Also
    --------
    :py:class:`~pycsou.operator.linop.diff._BaseDifferential`, :py:class:`~pycsou.operator.linop.diff.FiniteDifference`,
    :py:class:`~pycsou.operator.linop.diff.PartialDerivative`, :py:class:`~pycsou.operator.linop.diff.Gradient`,
    :py:class:`~pycsou.operator.linop.diff.Hessian`.
    """

    def __init__(
        self,
        order: typ.Union[pyct.Integer, tuple[pyct.Integer, ...]],
        arg_shape: pyct.Shape,
        sigma: typ.Union[pyct.Real, tuple[pyct.Real, ...]],
        axis: typ.Union[pyct.Integer, tuple[pyct.Integer, ...], None] = None,
        truncate: typ.Union[pyct.Real, tuple[pyct.Real, ...]] = 3.0,
        boundary: typ.Optional[typ.Union[pyct.Real, str, tuple, dict]] = None,
    ):
        """
        Parameters
        ----------
        order: int | tuple
            Derivative order. If a single integer value is provided, then `axis` should be provided to indicate which
            dimension should be used for differentiation. If a tuple is provided, it should contain as many elements as
            number of dimensions in `axis`.
        arg_shape: tuple
            Shape of the input array
        sigma: float | tuple
            Standard deviation of the Gaussian kernel.
        axis: int | tuple | None
            Axis to which apply the derivative. It maps the argument `order` to the specified dimensions of the input
            array. Defaults to None, assuming that the `order` argument has as many elements as dimensions of the input.
        truncate: float | tuple
            Truncate the filter at this many standard deviations.
            Defaults to 3.0.
        boundary: real, str, tuple, or dict , keyword only.
            How to handle the boundaries. (see :py:class:`~pycsou.operator.linop.base.Stencil`) for more information.
        """
        self.arg_shape = arg_shape
        self.order, self._param1, self._param2, self.axis = self._sanitize_init_kwargs(
            order=order,
            param1=sigma,
            param1_name="sigma",
            param2=truncate,
            param2_name="truncate",
            arg_shape=arg_shape,
            axis=axis,
        )

        kernel, center = self._create_kernel(self.axis)
        super(GaussianDerivative, self).__init__(kernel=kernel, center=center, arg_shape=arg_shape, boundary=boundary)

    def _fill_coefs(self, i: pyct.Integer) -> typ.Tuple[list, pyct.NDArray, pyct.Integer]:
        r"""
        Defines kernel elements.
        """
        # make the radius of the filter equal to `truncate` standard deviations
        radius = int(self._param2[i] * float(self._param1[i]) + 0.5)
        stencil_coefs = self._gaussian_kernel1d(sigma=self._param1[i], order=self.order[i], radius=radius)
        stencil_ids = [i for i in range(-radius, radius + 1)]
        return stencil_ids, stencil_coefs, radius

    @staticmethod
    def _gaussian_kernel1d(sigma, order: pyct.Integer, radius: pyct.Integer) -> pyct.NDArray:
        """
        Computes a 1-D Gaussian convolution kernel.
        Wraps scipy.ndimage.filters._gaussian_kernel1d
        It flips the output because the original kernel is meant for convolution instead of correlation.
        """
        return np.flip(scif._gaussian_kernel1d(sigma, order, radius))


class PartialDerivative(_BaseDifferential):
    r"""
    Partial derivative operator based on `Numba stencils <https://numba.pydata.org/numba-doc/latest/user/stencil.html>`_.

    Notes
    -----

    This operator computes the partial derivative of a :math:`D`-dimensional signal:

    .. math::

        \mathbf{f} \in \mathbb{R}^{N_{0}, \dots, N_{D-1}},

    for a given set of directions:

    .. math::

        \mathbf{x}_{i},  \quad i \in [0, \dots, D-1],

    and a given set of derivative orders:

    .. math::

        k_{i},  \quad i \in [0, \dots, D-1],

    with :math:`\quad k = \sum_{i = 0}^{D-1} k_{i}\quad`, i.e.,

    .. math::

        \frac{\partial^{k} \mathbf{f}}{\partial x_{0}^{k_{0}} \, \cdots  \, \partial x_{D-1}^{k_{D-1}}}

    The partial derivative can be approximated by the `finite difference method <https://en.wikipedia.org/wiki/Finite_difference>`_ via the
    :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.finite_difference` constructor or by the `Gaussian derivative <https://www.crisluengo.net/archives/22/>`_ via
    :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.gaussian_derivative` constructor.

    **Adjoint**

    The adjoint of the partial derivative is obtained by flipping its stencil kernel around the center. The following
    properties hold:

    - :math:`\mathbf{D}_{F}^{\ast} = \mathbf{D}_{F}^{\top} = -\mathbf{D}_{B}`
    - :math:`\mathbf{D}_{B}^{\ast} = \mathbf{D}_{B}^{\top} = -\mathbf{D}_{F}`
    - :math:`\mathbf{D}_{C}^{\ast} = \mathbf{D}_{C}^{\top} = -\mathbf{D}_{C}`
    - :math:`\mathbf{D}_{G}^{\ast} = \mathbf{D}_{G}^{\top} = -\mathbf{D}_{G}`

    And:

    - :math:`\mathbf{D}_{F}^{\ast}\mathbf{D}_{F} = -\mathbf{D}^{2}_{C}`
    - :math:`\mathbf{D}_{B}^{\ast}\mathbf{D}_{B} = -\mathbf{D}^{2}_{C}`
    - :math:`\mathbf{D}_{F}^{\ast}\mathbf{D}_{B} = \mathbf{D}_{B}^{\ast}\mathbf{D}_{F} = \mathbf{D}^{2}_{C}`

    Also, in the case of the central finite differences we have:

    .. math::
        f'(x) = \frac{f(x+\frac{h}{2}) - f(x-\frac{h}{2})}{h},

    .. math::
        f''(x) = \frac{f(x+h) -2 f(x) + f(x-h)}{h^{2}},

    while that the :math:`\mathbf{D}_{C}` stencil approximation the first order derivative :math:`f'(x)` uses a spacing
    of :math:`h=2` (``[0.5, 0, 0.5]``), the :math:`\mathbf{D}^{2}_{C}` stencil for the second order derivative
    :math:`f'(x)` only uses a spacing of :math:`h=1` (``[1, -2, 1]``). Due to this, a :math:`\frac{1}{4}` factor arises
    in the following equation:

    - :math:`\mathbf{D}_{C}^{\ast}\mathbf{D}_{C} = - \frac{1}{4}\mathbf{D}^{2}_{C}`

    .. warning::
        In the case of the Gaussian derivative operator, :math:`\mathbf{D}_{G}^{\ast}\mathbf{D}_{G}` is not directly
        related to  :math:`\mathbf{D}_{G}^{2}`. This is because in practice the Gaussian derivative samples the
        derivative of a smoothed signal (see :py:class:`~pycsou.operator.linop.diff.GaussianDerivative`). Applying the
        adjoint of the Gaussian derivative to that will sample the derivative of a smoothed sampling of the first Gaussian derivative. This is different from sampling the second order derivative
        of a smoothed signal.

    Examples
    --------

    .. plot::
       import matplotlib.pyplot as plt
       from pycsou.operator.linop.diff import PartialDerivative
       from pycsou.util.misc import peaks

       x = np.linspace(-2.5, 2.5, 25)
       xx, yy = np.meshgrid(x, x)
       image = peaks(xx, yy)
       arg_shape = image.shape  # Shape of our image

       # Specify derivative order at each direction
       df_dx = (1, 0) # Compute derivative of order 1 in first dimension
       d2f_dy2 = (0, 2) # Compute derivative of order 2 in second dimension
       d3f_dxdy2 = (1, 2) # Compute derivative of order 1 in first dimension and der. of order 2 in second dimension

       # Instantiate derivative operators
       diff1 = PartialDerivative.finite_difference(order=df_dx, arg_shape=arg_shape, diff_type="central")
       diff2 = PartialDerivative.finite_difference(order=d2f_dy2, arg_shape=arg_shape, diff_type="central")
       diff = PartialDerivative.finite_difference(order=d3f_dxdy2, arg_shape=arg_shape, diff_type="central", separable_kernel=False)

       # Compute derivatives
       out1 = (diff1 * diff2)(image.reshape(1, -1)).reshape(arg_shape)
       out2 = diff(image.reshape(1, -1)).reshape(arg_shape)

       plt.figure()
       plt.imshow(image.T),
       plt.axis('off')
       plt.colorbar()
       plt.title('f(x,y)')

       plt.figure()
       plt.imshow(out1.T)
       plt.axis('off')
       plt.title(r'$\frac{\partial^{3} f(x,y)}{\partial x\partial y^{2}}$')

       plt.figure()
       plt.imshow(out2.T)
       plt.axis('off')
       plt.title(r'$\frac{\partial^{3} f(x,y)}{\partial x\partial y^{2}}$')

       # Test
       assert np.allclose(out1, out2)


    See Also
    --------
    :py:class:`~pycsou.operator.linop.diff._BaseDifferential`, :py:class:`~pycsou.operator.linop.diff.FiniteDifference`,
    :py:class:`~pycsou.operator.linop.diff.GaussianDerivative`, :py:class:`~pycsou.operator.linop.diff.Gradient`,
    :py:class:`~pycsou.operator.linop.diff.Hessian`.
    """

    @staticmethod
    def finite_difference(
        arg_shape: pyct.Shape,
        order: tuple[pyct.Integer, ...],
        diff_type: str = "forward",
        accuracy: typ.Union[pyct.Integer, tuple[pyct.Integer, ...]] = 1,
        boundary: typ.Optional[typ.Union[dict, tuple[dict, ...]]] = None,
        separable_kernel: bool = True,
    ) -> pyco.LinOp:
        r"""
        Compute the partial derivatives using :py:class:`~pycsou.operator.linop.diff.FiniteDifference`.

        Parameters
        ----------
        arg_shape: tuple
            Shape of the input array.
        order: tuple
            Derivative order for each dimension. The total order of the partial derivative is the sum
            of elements in the tuple.
        diff_type: str | tuple
            Type of finite differences ['forward, 'backward, 'central']. Defaults to 'forward'. If a string is provided,
            the same `diff_type` is assumed for all dimensions. If a tuple is provided, it should have as many elements as `order`.
        accuracy: float | tuple
            Approximation accuracy to the derivative. See `notes` of :py:class:`~pycsou.operator.linop.diff.FiniteDifference`.
            If a float is provided, the same `accuracy` is assumed for all dimensions. If a tuple is provided, it should have as many elements as `order`.
        boundary: real, str, tuple, or dict , keyword only.
            How to handle the boundaries. (see :py:class:`~pycsou.operator.linop.base.Stencil`) for more information.
        separable_kernel: bool
            Whether to implement cross-derivatives as a composition of separable kernels (default) or as a non-separable
            kernel.

        Returns
        -------
        op: :py:class:`~pycsou.abc.operator.LinOp`
            Partial derivative
        """
        axis = np.where(np.array(order) > 0)[0]
        order = tuple(np.array(order)[axis])
        order, diff_type, accuracy, axis = PartialDerivative._sanitize_init_kwargs(
            order=order,
            param1=diff_type,
            param1_name="diff_type",
            param2=accuracy,
            param2_name="accuracy",
            arg_shape=arg_shape,
            axis=tuple(axis),
        )
        if len(order) > 1 and not separable_kernel:
            kernel = None
            center = np.zeros(len(arg_shape), dtype=int)
            for i in range(len(order)):
                if order[i] > 0:
                    fd = FiniteDifference(
                        order=order[i],
                        arg_shape=arg_shape,
                        diff_type=diff_type[i],
                        axis=axis[i],
                        accuracy=accuracy[i],
                        boundary=boundary,
                    )

                    if kernel is None:
                        _, kernel, center_ = fd._convert_sym_ker(fd.stencil_coefs, fd.center)
                        xp = pycu.get_array_module(kernel)
                    else:
                        _, kernel_, center_ = fd._convert_sym_ker(fd.stencil_coefs, fd.center)
                        kernel = xp.tensordot(kernel, kernel_, axes=axis[i])
                    center[axis[i]] = center_[axis[i]]
            fd = _BaseDifferential(kernel=kernel, center=xp.asarray(center), arg_shape=arg_shape, boundary=boundary)
        else:
            fd = pycsou.operator.linop.base.IdentityOp(np.prod(arg_shape))
            for i in range(len(order)):
                if order[i] > 0:
                    fd *= FiniteDifference(
                        order=order[i],
                        arg_shape=arg_shape,
                        diff_type=diff_type[i],
                        axis=axis[i],
                        accuracy=accuracy[i],
                        boundary=boundary,
                    )
        return fd

    @staticmethod
    def gaussian_derivative(
        arg_shape: pyct.Shape,
        order: tuple[pyct.Integer, ...],
        sigma: typ.Union[pyct.Real, tuple[pyct.Real, ...]] = 1.0,
        truncate: typ.Union[pyct.Real, tuple[pyct.Real, ...]] = 3.0,
        boundary: typ.Optional[typ.Union[dict, tuple[dict, ...]]] = None,
        separable_kernel: bool = True,
    ) -> pyco.LinOp:
        """
        Compute the partial derivatives using :py:class:`~pycsou.operator.linop.diff.GaussianDerivative`.

        Parameters
        ----------
        arg_shape: tuple
            Shape of the input array.
        order: tuple
            Derivative order for each dimension. The total order of the partial derivative is the sum
            of elements in the tuple.
        sigma: float | tuple
            Standard deviation for the Gaussian kernel. Defaults to 1.0.
            If a float is provided, the same `sigma` is assumed for all dimensions. If a tuple is provided, it should have as many elements as `order`.
        truncate: float | tuple
            Truncate the filter at this many standard deviations. Defaults to 3.0.
            If a float is provided, the same `truncate` is assumed for all dimensions. If a tuple is provided, it should have as many elements as `order`.
        boundary: real, str, tuple, or dict , keyword only.
            How to handle the boundaries. (see :py:class:`~pycsou.operator.linop.base.Stencil`) for more information.
        separable_kernel: bool
            Whether to implement cross-derivatives as a composition of separable kernels (default) or as a non-separable
            kernel.

        Returns
        -------
        op: :py:class:`~pycsou.abc.operator.LinOp`
            Partial derivative
        """

        axis = np.where(np.array(order) > 0)[0]
        order = tuple(np.array(order)[axis])
        order, sigma, truncate, axis = PartialDerivative._sanitize_init_kwargs(
            order=order,
            param1=sigma,
            param1_name="sigma",
            param2=truncate,
            param2_name="truncate",
            arg_shape=arg_shape,
            axis=tuple(axis),
        )
        if len(order) > 1 and not separable_kernel:
            kernel = None
            center = np.zeros(len(arg_shape), dtype=int)
            for i in range(len(order)):
                if order[i] > 0:
                    gd = GaussianDerivative(
                        order=order[i],
                        arg_shape=arg_shape,
                        sigma=sigma[i],
                        axis=axis[i],
                        truncate=truncate[i],
                        boundary=boundary,
                    )
                    if kernel is None:
                        _, kernel, center_ = gd._convert_sym_ker(gd.stencil_coefs, gd.center)
                        xp = pycu.get_array_module(kernel)
                    else:
                        _, kernel_, center_ = gd._convert_sym_ker(gd.stencil_coefs, gd.center)
                        kernel = xp.tensordot(kernel, kernel_, axes=axis[i])
                    center[axis[i]] = center_[axis[i]]
            gd = _BaseDifferential(kernel=kernel, center=xp.asarray(center), arg_shape=arg_shape, boundary=boundary)
        else:
            gd = pycsou.operator.linop.base.IdentityOp(np.prod(arg_shape))
            for i in range(len(order)):
                gd *= GaussianDerivative(
                    order=order[i],
                    arg_shape=arg_shape,
                    sigma=sigma[i],
                    axis=axis[i],
                    truncate=truncate[i],
                    boundary=boundary,
                )
        return gd


class _BaseVecDifferential(_BaseDifferential):
    r"""
    Helper class for Gradient and Hessian.

    Defines a method for computing and stacking partial derivatives.

    See Also
    --------
    :py:class:`~pycsou.operator.linop.diff.Gradient`,
    :py:class:`~pycsou.operator.linop.diff.Hessian`.
    """

    @staticmethod
    def _stack_diff_ops(
        arg_shape,
        directions,
        diff_method,
        order,
        param1,
        param2,
        boundary,
        separable_kernel=True,
        parallel: bool = False,
    ):
        _param1 = np.empty(len(arg_shape), dtype=object)
        _param2 = np.empty(len(arg_shape), dtype=object)
        _param1[:] = param1
        _param2[:] = param2
        dif_op = []
        for i in range(0, len(directions)):
            _order = np.zeros_like(arg_shape)
            _order[directions[i]] = order[i]
            _directions = np.array(directions[i]).reshape(-1)
            param1 = _param1[_directions].tolist()
            param2 = _param2[_directions].tolist()
            if diff_method == "fd":
                dif_op.append(
                    PartialDerivative.finite_difference(
                        arg_shape=arg_shape,
                        order=tuple(_order),
                        diff_type=param1,
                        accuracy=param2,
                        boundary=boundary,
                        separable_kernel=separable_kernel,
                    )
                )
            elif diff_method == "gd":
                dif_op.append(
                    PartialDerivative.gaussian_derivative(
                        arg_shape=arg_shape,
                        order=tuple(_order),
                        sigma=param1,
                        truncate=param2,
                        boundary=boundary,
                        separable_kernel=separable_kernel,
                    )
                )
        return _make_unravelable(pycb.vstack(dif_op, parallel=parallel), arg_shape)


class Gradient(_BaseVecDifferential):
    r"""
    Gradient Operator based on `Numba stencils <https://numba.pydata.org/numba-doc/latest/user/stencil.html>`_.

    Notes
    -----

    This operator computes the first order partial derivatives of a :math:`D`-dimensional signal:

    .. math::

        \mathbf{f} \in \mathbb{R}^{N_{0}, \dots, N_{D-1}} ,

    for each dimension of a multi-dimensional signal:

    .. math::

        \nabla \mathbf{f} = \begin{bmatrix}
        \frac{ \partial \mathbf{f}}{\partial \mathbf{x}_{0}}\\
        \vdots \\
        \frac{ \partial \mathbf{f} }{ \partial \mathbf{x}_{D-1} }
        \end{bmatrix}

    The gradient can be approximated by the `finite difference method <https://en.wikipedia.org/wiki/Finite_difference>`_ via the
    :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.finite_difference` constructor or by the `Gaussian derivative <https://www.crisluengo.net/archives/22/>`_ via
    :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.gaussian_derivative` constructor.

    .. math::

        \mathbf{G} \mathbf{f} = \begin{bmatrix}
        \mathbf{D}_{0}\\
        \vdots \\
        \mathbf{D}_{D-1}\\
        \end{bmatrix}f(\mathbf{x})


    **Adjoint**

    The adjoint of the gradient operator is computed as:

    .. math::

        \mathbf{G^{\ast}} = \begin{bmatrix}
        \mathbf{D}_{0}^{\ast} & \ldots & \mathbf{D}_{D-1}^{\ast}
        \end{bmatrix}

    The user is referred to the constructor class :py:class:`~pycsou.operator.linop.diff.PartialDerivative` for detailed
    information on the adjoint of partial derivatives.

    Remark 1
    --------

    The Gradient Operator can be applied to a vector valued map to obtain its Jacobian.

    Examples
    --------

    .. plot::

       import numpy as np
       import matplotlib.pyplot as plt
       from pycsou.operator.linop.diff import Gradient
       from pycsou.util.misc import peaks

       # Define input image
       n = 1000
       x = np.linspace(-3, 3, n)
       xx, yy = np.meshgrid(x, x)
       image = peaks(xx, yy)
       nsamples = 2
       arg_shape = image.shape # (1000, 1000)
       images = np.tile(image, (nsamples, 1, 1)).reshape(nsamples, -1)
       print(images.shape) # (2, 1000000)
       # Instantiate gradient operator
       grad = Gradient.gaussian_derivative(arg_shape=arg_shape, sigma=1.0)

       # Compute gradients
       outputs = grad(images)
       print(outputs.shape) # (2, 2000000)

       # Plot
       df_dx = outputs.reshape(nsamples, len(arg_shape), *arg_shape)[:, 0]
       df_dy = outputs.reshape(nsamples, len(arg_shape), *arg_shape)[:, 1]
       plt.figure()
       plt.imshow(images[0].reshape(arg_shape))
       plt.colorbar()
       plt.title("Image")
       plt.axis("off")

       plt.figure()
       plt.imshow(df_dx[0])
       plt.colorbar()
       plt.title(r"$\partial f/ \partial x$")
       plt.axis("off")

       plt.figure()
       plt.imshow(df_dy[0])
       plt.colorbar()
       plt.title(r"$\partial f/ \partial y$")
       plt.axis("off")

    See Also
    --------
    :py:class:`~pycsou.operator.linop.diff._BaseDifferential`, :py:class:`~pycsou.operator.linop.diff.FiniteDifference`,
    :py:class:`~pycsou.operator.linop.diff.GaussianDerivative`,
    :py:class:`~pycsou.operator.linop.diff.PartialDerivative`, :py:class:`~pycsou.operator.linop.diff.Hessian`.
    """

    @staticmethod
    def finite_difference(
        arg_shape: pyct.Shape,
        directions: typ.Optional[typ.Union[pyct.Integer, tuple[pyct.Integer, ...]]] = None,
        diff_type: str = "forward",
        accuracy: typ.Union[pyct.Integer, tuple[pyct.Integer, ...]] = 1,
        boundary: typ.Optional[typ.Union[dict, tuple[dict, ...]]] = None,
        parallel: bool = False,
    ) -> pyco.LinOp:
        """
        Compute the gradient using :py:class:`~pycsou.operator.linop.diff.FiniteDifference`.

        Parameters
        ----------
        arg_shape: tuple
            Shape of the input array
        directions: int, tuple, None
            Gradient directions. Defaults to `None`, which computes the gradient for all directions.
        diff_type: str | tuple
            Type of finite differences ['forward, 'backward, 'central']. Defaults to 'forward'. If a string is provided,
            the same `diff_type` is assumed for all dimensions. If a tuple is provided, it should have as many elements as `directions`.
        accuracy: float | tuple
            Approximation accuracy to the derivative. See `notes` of :py:class:`~pycsou.operator.linop.diff.FiniteDifference`.
            If a float is provided, the same `accuracy` is assumed for all dimensions. If a tuple is provided, it should have as many elements as `directions`.
        boundary: real, str, tuple, or dict , keyword only.
            How to handle the boundaries. (see :py:class:`~pycsou.operator.linop.base.Stencil`) for more information.
        parallel: bool
            Whether to compute the partial derivatives in parallel or not.

        Returns
        -------
        op: :py:class:`~pycsou.abc.operator.LinOp`
            Gradient
        """
        directions = tuple([i for i in range(len(arg_shape))]) if directions is None else directions
        order, diff_type, accuracy, _ = Gradient._sanitize_init_kwargs(
            order=(1,) * len(directions),
            param1=diff_type,
            param1_name="diff_type",
            param2=accuracy,
            param2_name="accuracy",
            arg_shape=arg_shape,
        )
        return Gradient._stack_diff_ops(
            arg_shape=arg_shape,
            directions=directions,
            diff_method="fd",
            order=order,
            param1=diff_type,
            param2=accuracy,
            boundary=boundary,
            parallel=parallel,
        )

    @staticmethod
    def gaussian_derivative(
        arg_shape: pyct.Shape,
        directions: typ.Optional[typ.Union[pyct.Integer, tuple[pyct.Integer, ...]]] = None,
        sigma: typ.Union[pyct.Real, tuple[pyct.Real, ...]] = 1.0,
        truncate: typ.Union[pyct.Real, tuple[pyct.Real, ...]] = 3.0,
        boundary: typ.Optional[typ.Union[dict, tuple[dict, ...]]] = None,
        parallel: bool = False,
    ) -> pyco.LinOp:
        """
        Compute the gradient using :py:class:`~pycsou.operator.linop.diff.GaussianDerivative`.

        Parameters
        ----------
        arg_shape: tuple
            Shape of the input array
        directions: int, tuple, None
            Gradient directions. Defaults to `None`, which computes the gradient for all directions.
        sigma: float | tuple
            Standard deviation for the Gaussian kernel. Defaults to 1.0.
            If a float is provided, the same `sigma` is assumed for all dimensions. If a tuple is provided, it should have as many elements as `directions`.
        truncate: float | tuple
            Truncate the filter at this many standard deviations. Defaults to 3.0.
            If a float is provided, the same `truncate` is assumed for all dimensions. If a tuple is provided, it should have as many elements as `directions`.
        boundary: real, str, tuple, or dict , keyword only.
            How to handle the boundaries. (see :py:class:`~pycsou.operator.linop.base.Stencil`) for more information.
        parallel: bool
            Whether to compute the partial derivatives in parallel or not.

        Returns
        -------
        op: :py:class:`~pycsou.abc.operator.LinOp`
            Gradient
        """
        directions = tuple([i for i in range(len(arg_shape))]) if directions is None else directions
        order, sigma, truncate, _ = Gradient._sanitize_init_kwargs(
            order=(1,) * len(directions),
            param1=sigma,
            param1_name="sigma",
            param2=truncate,
            param2_name="truncate",
            arg_shape=arg_shape,
        )
        return Gradient._stack_diff_ops(
            arg_shape=arg_shape,
            directions=directions,
            diff_method="gd",
            order=order,
            param1=sigma,
            param2=truncate,
            boundary=boundary,
            parallel=parallel,
        )


class Hessian(_BaseVecDifferential):
    r"""
    Hessian Operator based on `Numba stencils <https://numba.pydata.org/numba-doc/latest/user/stencil.html>`_.

    Notes
    -----

    The Hessian matrix or Hessian is a square matrix of second-order partial derivatives:

    .. math::

        \mathbf{H}_{f} = \begin{bmatrix}
        \dfrac{ \partial^{2}\mathbf{f} }{ \partial \mathbf{x}_{1}^{2} } &  \dfrac{ \partial^{2}\mathbf{f} }{ \partial \mathbf{x}_{1}\,\partial \mathbf{x}_{2} } & \cdots & \dfrac{ \partial^{2}\mathbf{f} }{ \partial \mathbf{x}_{1} \, \partial \mathbf{x}_{D} } \\
        \dfrac{ \partial^{2}\mathbf{f} }{ \partial \mathbf{x}_{2} \, \partial \mathbf{x}_{1} } & \dfrac{ \partial^{2}\mathbf{f} }{ \partial \mathbf{x}_{2}^{2} } & \cdots & \dfrac{ \partial^{2}\mathbf{f} }{\partial \mathbf{x}_{2} \,\partial \mathbf{x}_{D}} \\
        \vdots & \vdots & \ddots & \vdots \\
        \dfrac{ \partial^{2}\mathbf{f} }{ \partial \mathbf{x}_{D} \, \partial \mathbf{x}_{1} } & \dfrac{ \partial^{2}\mathbf{f} }{ \partial \mathbf{x}_{n} \, \partial \mathbf{x}_{2} } & \cdots & \dfrac{ \partial^{2}\mathbf{f} }{ \partial \mathbf{x}_{D}^{2}}
        \end{bmatrix}

    The Hessian can be approximated by the `finite difference method <https://en.wikipedia.org/wiki/Finite_difference>`_ via the
    :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.finite_difference` constructor or by the `Gaussian derivative <https://www.crisluengo.net/archives/22/>`_ via
    :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.gaussian_derivative` constructor.

    .. math::

        \mathbf{H} \mathbf{f} = \begin{bmatrix}
        \mathbf{D}^{2}_{0}\mathbf{f} & \ldots & \mathbf{D}_{0, D-1}\mathbf{f}\\
        \vdots & \ddots & \vdots \\
        \mathbf{D}_{D-1, 0}\mathbf{f} & \ldots & \mathbf{D}^{2}_{D-1}\mathbf{f}\\
        \end{bmatrix}

    However, due to the symmetry of the Hessian, only the upper triangular part is computed in practice.

    .. math::

        \mathbf{H} \mathbf{f} = \begin{bmatrix}
        \mathbf{D}^{2}_{0}\\
        \ldots \\
        \mathbf{D}_{0, D-1}\\
        \mathbf{D}_{1, 1}\\
        \vdots \\
        \mathbf{D}^{2}_{D-1}\\
        \end{bmatrix}\mathbf{f}

    **Adjoint**

    The adjoint of the Hessian operator is computed as:

    .. math::

        \mathbf{H^{\ast}} = \begin{bmatrix}
        {\mathbf{D}^{2}_{0}}^{\ast} & \ldots & {\mathbf{D}_{0, D-1}}^{\ast} & {\mathbf{D}_{1, 1}}^{\ast} & \ldots & {\mathbf{D}^{2}_{D-1}}^{\ast}
        \end{bmatrix}

    The user is referred to the constructor class :py:class:`~pycsou.operator.linop.diff.PartialDerivative` for detailed
    information on the adjoint of partial derivatives.

    Notes
    -----

    Due to the (possibly) large size of the full Hessian, four different options are handled:

    * [mode 0] ``directions`` is an integer, e.g.:
        ``directions=0`` :math:`\rightarrow \partial^{2}\mathbf{f}/\partial x_{0}^{2}`.

    * [mode 1] ``directions`` is tuple of length 2, e.g.:
        ``directions=(0,1)`` :math:`\rightarrow  \partial^{2}\mathbf{f}/\partial x_{0}\partial x_{1}`.

    * [mode 2]  ``directions`` is tuple of tuples, e.g.:
        ``directions=((0,0), (0,1))`` :math:`\rightarrow  \left(\frac{ \partial^{2}\mathbf{f} }{ \partial x_{0}^{2} }, \frac{ \partial^{2}\mathbf{f} }{ \partial x_{0}\partial x_{1} }\right)`.

    * [mode 3] ``directions`` is 'all'  computes the Hessian for all directions, i.e.:
        :math:`\rightarrow  \left(\frac{ \partial^{2}\mathbf{f} }{ \partial x_{0}^{2} }, \frac{ \partial^{2}\mathbf{f} }{ \partial x_{0}\partial x_{1} }, \, \ldots , \, \frac{ \partial^{2}\mathbf{f} }{ \partial x_{D}^{2} }\right)`.

    Remark
    ______

    If the user wants to adjust the padding options, `kwargs` should be a tuple with as a tuple with one
    dictionary or an empty list per `arg_shape` dimensions. If only a dict is provided, equal boundary conditions will
    be used.

    Examples
    --------

    .. plot::

       import numpy as np
       import matplotlib.pyplot as plt
       from pycsou.operator.linop.diff import Hessian, PartialDerivative
       from pycsou.util.misc import peaks

       # Define input image
       n = 1000
       x = np.linspace(-3, 3, n)
       xx, yy = np.meshgrid(x, x)
       image = peaks(xx, yy)
       nsamples = 2
       arg_shape = image.shape  # (1000, 1000)
       images = np.tile(image, (nsamples, 1, 1)).reshape(nsamples, -1)
       print(images.shape)  # (2, 1000000)

       # Instantiate hessian operator
       directions = "all"
       hessian = Hessian.gaussian_derivative(arg_shape=arg_shape, directions=directions)

       # Compute Hessian
       outputs = hessian(images)
       print(outputs.shape)  # (2, 3000000)

       # Plot
       outputs = hessian.unravel(outputs)
       print(outputs.shape)  # (2, 3, 1000, 1000)
       d2f_dx2 = outputs[:, 0]
       d2f_dxdy = outputs[:, 1]
       d2f_dy2 = outputs[:, 2]
       plt.figure()
       plt.imshow(images[0].reshape(arg_shape))
       plt.colorbar()
       plt.title("Image")
       plt.axis("off")

       plt.figure()
       plt.imshow(d2f_dx2[0].reshape(arg_shape))
       plt.colorbar()
       plt.title(r"$\partial^{2} f/ \partial x^{2}$")
       plt.axis("off")

       plt.figure()
       plt.imshow(d2f_dxdy[0].reshape(arg_shape))
       plt.colorbar()
       plt.title(r"$\partial^{2} f/ \partial x\partial y$")
       plt.axis("off")

       plt.figure()
       plt.imshow(d2f_dy2[0].reshape(arg_shape))
       plt.colorbar()
       plt.title(r"$\partial^{2} f/ \partial y^{2}$")
       plt.axis("off")

    See Also
    --------
    :py:class:`~pycsou.operator.linop.diff._BaseDifferential`, :py:class:`~pycsou.operator.linop.diff.FiniteDifference`,
    :py:class:`~pycsou.operator.linop.diff.GaussianDerivative`,
    :py:class:`~pycsou.operator.linop.diff.PartialDerivative`, :py:class:`~pycsou.operator.linop.diff.Gradient`.
    """

    @staticmethod
    def finite_difference(
        arg_shape: pyct.Shape,
        directions: typ.Union[
            str, tuple[pyct.Integer, pyct.Integer], tuple[tuple[pyct.Integer, pyct.Integer], ...]
        ] = "all",
        diff_type: typ.Union[str, tuple[str, ...]] = "forward",
        accuracy: typ.Union[pyct.Integer, tuple[pyct.Integer, ...]] = 1,
        boundary: typ.Optional[typ.Union[dict, tuple[dict, ...]]] = None,
        separable_kernel: bool = True,
        parallel: bool = False,
    ) -> typ.Union[pyco.LinOp, typ.Tuple[pyco.LinOp, ...]]:
        """

        Parameters
        ----------
        arg_shape: tuple
            Shape of the input array
        directions: int, tuple, None
            Hessian directions. Defaults to `all`, which computes the Hessian for all directions.
        diff_type: str | tuple
            Type of finite differences ['forward, 'backward, 'central']. Defaults to 'forward'. If a string is provided,
            the same `diff_type` is assumed for all dimensions. If a tuple is provided, it should have as many elements as `arg_shape`.
        accuracy: float | tuple
            Approximation accuracy to the derivative. See `notes` of :py:class:`~pycsou.operator.linop.diff.FiniteDifference`.
            If a float is provided, the same `accuracy` is assumed for all dimensions. If a tuple is provided, it should have as many elements as `arg_shape`.
        boundary: real, str, tuple, or dict , keyword only.
            How to handle the boundaries. (see :py:class:`~pycsou.operator.linop.base.Stencil`) for more information.
        separable_kernel: bool
            Whether to implement cross-derivatives as a composition of separable kernels (default) or as a non-separable
            kernel.
        parallel: bool
            Whether to compute the partial derivatives in parallel or not.

        Returns
        -------
        op: :py:class:`~pycsou.abc.operator.LinOp`
            Hessian
        """
        order, diff_type, accuracy, _ = Hessian._sanitize_init_kwargs(
            order=(1,) * len(arg_shape),
            param1=diff_type,
            param1_name="diff_type",
            param2=accuracy,
            param2_name="accuracy",
            arg_shape=arg_shape,
        )
        directions, order = Hessian._check_directions_and_order(arg_shape, directions)
        return Hessian._stack_diff_ops(
            arg_shape=arg_shape,
            directions=directions,
            diff_method="fd",
            order=order,
            param1=diff_type,
            param2=accuracy,
            boundary=boundary,
            separable_kernel=separable_kernel,
            parallel=parallel,
        )

    @staticmethod
    def gaussian_derivative(
        arg_shape: pyct.Shape,
        directions: typ.Union[
            str, tuple[pyct.Integer, pyct.Integer], tuple[tuple[pyct.Integer, pyct.Integer], ...]
        ] = "all",
        sigma: typ.Union[pyct.Real, tuple[pyct.Real, ...]] = 1.0,
        truncate: typ.Union[pyct.Real, tuple[pyct.Real, ...]] = 3.0,
        boundary: typ.Optional[typ.Union[dict, tuple[dict, ...]]] = None,
        separable_kernel: bool = True,
        parallel: bool = False,
    ) -> typ.Union[pyco.LinOp, typ.Tuple[pyco.LinOp, ...]]:
        """
        Parameters
        ----------
        arg_shape: tuple
            Shape of the input array
        directions: int, tuple, None
            Hessian directions. Defaults to `all`, which computes the Hessian for all directions.
        sigma: float | tuple
            Standard deviation for the Gaussian kernel. Defaults to 1.0.
            If a float is provided, the same `sigma` is assumed for all dimensions. If a tuple is provided, it should have as many elements as `arg_shape`.
        truncate: float | tuple
            Truncate the filter at this many standard deviations. Defaults to 3.0.
            If a float is provided, the same `truncate` is assumed for all dimensions. If a tuple is provided, it should have as many elements as `arg_shape`.
        boundary: real, str, tuple, or dict , keyword only.
            How to handle the boundaries. (see :py:class:`~pycsou.operator.linop.base.Stencil`) for more information.
        separable_kernel: bool
            Whether to implement cross-derivatives as a composition of separable kernels (default) or as a non-separable
            kernel.
        parallel: bool
            Whether to compute the partial derivatives in parallel or not.

        Returns
        -------
        op: :py:class:`~pycsou.abc.operator.LinOp`
            Hessian
        """

        directions, order = Hessian._check_directions_and_order(arg_shape, directions)
        return Hessian._stack_diff_ops(
            arg_shape=arg_shape,
            directions=directions,
            diff_method="gd",
            order=order,
            param1=sigma,
            param2=truncate,
            boundary=boundary,
            separable_kernel=separable_kernel,
            parallel=parallel,
        )

    @classmethod
    def _check_directions_and_order(
        cls, arg_shape, directions
    ) -> typ.Tuple[typ.Union[tuple[pyct.Integer, ...], tuple[tuple[pyct.Integer, ...], ...]], bool]:
        def _check_directions(_directions):
            assert all(0 <= _ <= (len(arg_shape) - 1) for _ in _directions), (
                "Direction values must be between 0 and " "the number of dimensions in `arg_shape`."
            )

        if not isinstance(directions, cabc.Sequence):
            # This corresponds to [mode 0] in `Notes`
            directions = [directions, directions]
            _check_directions(directions)
            directions = (directions,)
        else:
            if isinstance(directions, str):
                # This corresponds to [mode 3] in `Notes`
                assert directions == "all", (
                    f"Value for `directions` not implemented. The accepted directions types are"
                    f"int, tuple or a str with the value `all`."
                )
                directions = tuple(
                    list(_) for _ in itertools.combinations_with_replacement(np.arange(len(arg_shape)).astype(int), 2)
                )
            elif not isinstance(directions[0], cabc.Sequence):
                # This corresponds to [mode 2] in `Notes`
                assert len(directions) == 2, (
                    "If `directions` is a tuple, it should contain two elements, corresponding "
                    "to the i-th an j-th elements (dx_i and dx_j)"
                )
                directions = list(directions)
                _check_directions(directions)
                directions = (directions,)
            else:
                # This corresponds to [mode 3] in `Notes`
                for direction in directions:
                    _check_directions(direction)

        _directions = [
            list(direction) if (len(np.unique(direction)) == len(direction)) else np.unique(direction).tolist()
            for direction in directions
        ]

        _order = [3 - len(np.unique(direction)) for direction in directions]

        return _directions, _order


def DirectionalDerivative(
    arg_shape: pyct.Shape,
    which: pyct.Integer,
    directions: pyct.NDArray,
    diff_method: str = "gd",
    boundary: typ.Optional[typ.Union[pyct.Real, str, tuple, dict]] = None,
    parallel: bool = False,
    **diff_kwargs,
):
    r"""
    Directional derivative.

    Parameters
    ----------
    arg_shape: tuple
        Shape of the input array
    which: int
        Which directional derivative (restricted to 1: First or 2: Second, see ``Notes``).
    directions: NDArray
        Single direction (array of size :math:`n_\text{dims}`) or group of directions
        (array of size :math:`[n_\text{dims} \times n_{d_0} \times ... \times n_{d_{n_\text{dims}}}]`)
    diff_method: str ['gd', 'fd']
        Method used to approximate the derivative. It can be the finite difference method (`fd`) or the Gaussian
        derivative (`gd`).
    boundary:
        Arguments related to the padding  (see
        :py:class:`~pycsou.operator.linop.diff.PartialDerivative`). See
        :py:class:`~pycsou.operator.linop.base.Stencil` for more information on both types of parameters.
    parallel: bool
        Whether to compute partial derivatives in parallel or not.

    Returns
    -------
    op: :py:class:`~pycsou.abc.operator.LinOp`
            DirectionalDerivative

    Notes
    -----

    The **first** ``DirectionalDerivative`` applies a derivative to a multi-dimensional array along the direction
    defined by the unitary vector :math:`\mathbf{v}`:

    .. math::
        d_\mathbf{v}f =
            \langle\nabla f, \mathbf{v}\rangle,

    or along the directions defined by the unitary vectors :math:`\mathbf{v}(x, y)`:

    .. math::
        d_\mathbf{v}(x,y) f(x,y) =
            \langle\nabla f(x,y), \mathbf{v}(x,y)\rangle

    where we have here considered the 2-dimensional case. Note that in this 2D case, choosing :math:`\mathbf{v}=[1,0]`
    or :math:`\mathbf{v}=[0,1]` is equivalent to the first-order ``PartialDerivative`` operator applied to axis 0 or 1
    respectively.

    The partial derivative can be approximated by the `finite difference method <https://en.wikipedia.org/wiki/Finite_difference>`_ via the
    :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.finite_difference` constructor or by the `Gaussian derivative <https://www.crisluengo.net/archives/22/>`_ via
    :py:meth:`~pycsou.operator.linop.diff.PartialDerivative.gaussian_derivative` constructor.

    .. math::

        d_{\mathbf{v}} \mathbf{f} = \mathbf{v}^{\top}\mathbf{G} \mathbf{f} =
        v_{0}\mathbf{D}_{0}\mathbf{f}+\ldots+v_{D-1}\mathbf{D}_{D-1}\mathbf{f}

    The **adjoint** of the directional derivative operator is computed as:

    .. math::

        d_{\mathbf{v}}^{\ast} = \mathbf{G}^{\ast}\mathbf{v} = v_{0}\mathbf{D}_{0}^{\ast} +
        \ldots + v_{D-1}\mathbf{D}_{D-1}^{\ast}

    The user is referred to the constructor class :py:class:`~pycsou.operator.linop.diff.PartialDerivative` for detailed
    information on the adjoint of partial derivatives.

    The **second** ``DirectionalDerivative`` applies a second-order derivative to a multi-dimensional array along
    the direction defined by the unitary vector :math:`\mathbf{v}`:

    .. math::
        d^2_\mathbf{v} \mathbf{f} =
            - d_\mathbf{v} (d_\mathbf{v} \mathbf{f})

    which is equivalent to:

    .. math::
        d^2_\mathbf{v}\hspace\mathbf{f}{0.5cm} = \hspace{0.5cm} -d_{\mathbf{v}}^{\ast}d_{\mathbf{v}}\mathbf{f} \hspace{0.5cm}= \hspace{0.5cm} d_{\mathbf{v}}^{\ast}\left(
        v_{0}\mathbf{D}_{0}+\ldots+v_{D-1}\mathbf{D}_{D-1}\mathbf{f}
        \right)

    .. math::
        = - \left(
        v_{0}{\mathbf{D}_{0}}^{\top}+\ldots+v_{D-1}{\mathbf{D}_{D-1}}^{\top}
        \right)
        \left(
        v_{0}\mathbf{D}_{0}+\ldots+v_{D-1}\mathbf{D}_{D-1}
        \right)\mathbf{f}

    .. math::
        = \left(v_{0}^{2}{\mathbf{D}_{0}}^{2}+\ldots+v_{D-1}^{2}{\mathbf{D}_{D-1}}^{2}
        - 2 \prod_{i,j=0, i!=j}^{D-1} v_{i}v_{j}\mathbf{D}_{i}^{\top}\mathbf{D}_{j} \right)\mathbf{f}

    .. warning:
        - :py:func:`~pycsou.operator.linop.diff.DirectionalDerivative` instances are **not arraymodule-agnostic**:
        they will only work with NDArrays belonging to the same array module as ``directions``.
        Moreover, inner computations may cast input arrays when the precision of ``directions`` does not match the
        user-requested precision.

        - ``directions`` are always normalized to be unit vectors.

    Examples
    --------

    .. plot::

       import numpy as np
       import matplotlib.pyplot as plt
       from pycsou.operator.linop.diff import DirectionalDerivative
       from pycsou.util.misc import peaks

       x = np.linspace(-2.5, 2.5, 25)
       xx, yy = np.meshgrid(x, x)
       z = peaks(xx, yy)
       directions = np.zeros(shape=(2, z.size))
       directions[0, : z.size // 2] = 1
       directions[1, z.size // 2 :] = 1

       dop = DirectionalDerivative(arg_shape=z.shape, which=1, directions=directions)
       out = dop(z.reshape(1, -1))

       dop2 = DirectionalDerivative(arg_shape=z.shape, which=2, directions=directions)
       out2 = dop2(z.reshape(1, -1))

       plt.figure()
       h = plt.pcolormesh(xx, yy, z, shading="auto")
       plt.quiver(x, x, directions[1].reshape(xx.shape), directions[0].reshape(xx.shape))
       plt.colorbar(h)
       plt.title("Signal and directions of derivatives")

       plt.figure()
       h = plt.pcolormesh(xx, yy, out.reshape(xx.shape), shading="auto")
       plt.colorbar(h)
       plt.title("First Directional derivatives")

       plt.figure()
       h = plt.pcolormesh(xx, yy, out2.reshape(xx.shape), shading="auto")
       plt.colorbar(h)
       plt.title("Second Directional derivatives")


    See Also
    --------
    :py:func:`~pycsou.operator.linop.diff.Gradient`, :py:func:`~pycsou.operator.linop.diff.DirectionalGradient`
    """

    if diff_method == "fd":
        diff = Gradient.finite_difference(arg_shape=arg_shape, boundary=boundary, parallel=parallel, **diff_kwargs)
    elif diff_method == "gd":
        diff = Gradient.gaussian_derivative(arg_shape=arg_shape, boundary=boundary, parallel=parallel, **diff_kwargs)
    else:
        raise NotImplementedError
    xp = pycu.get_array_module(directions)
    directions = directions / xp.linalg.norm(directions, axis=0, keepdims=True)

    if directions.ndim == 1:
        dop = pycob.DiagonalOp(xp.tile(directions, arg_shape + (1,)).transpose().ravel())
    else:
        dop = pycob.DiagonalOp(directions.ravel())

    sop = pycob.SumOp(arg_shape=(len(arg_shape),) + arg_shape, axis=0)
    out = sop * dop * diff

    if which == 2:
        out = -out.T * out
    return _make_unravelable(out, arg_shape=arg_shape)


def DirectionalGradient(
    arg_shape: pyct.Shape,
    directions: list,
    diff_method: str = "gd",
    boundary: typ.Optional[typ.Union[pyct.Real, str, tuple, dict]] = None,
    parallel: bool = False,
    **diff_kwargs,
):
    r"""
    Directional gradient.
    Computes the directional derivative of a multi-dimensional array along multiple ``directions`` for each entry of
    the array.

    Parameters
    ----------
    arg_shape: tuple
        Shape of the input array
    directions: list
        Multiple directions (each as an array of size :math:`n_\text{dims}`) or group of directions
        (array of size :math:`[n_\text{dims} \times n_{d_0} \times ... \times n_{d_{n_\text{dims}}}]`)
    diff_method: str ['gd', 'fd']
        Method used to approximate the derivative. It can be the finite difference method (`fd`) or the Gaussian
        derivative (`gd`).
    boundary:
        Arguments related to the padding  (see
        :py:class:`~pycsou.operator.linop.diff.PartialDerivative`). See
        :py:class:`~pycsou.operator.linop.base.Stencil` for more information on both types of parameters.

    Returns
    -------
    op: :py:class:`~pycsou.abc.operator.LinOp`
            DirectionalGradient

    Notes
    -----
    The ``DirectionalGradient`` of a multivariate function :math:`f(\mathbf{x})` is defined as:


    .. math::
        g_{\mathbf{v_0}, \ldots ,\mathbf{v_m}}\mathbf{f} = \begin{bmatrix}
             d_{\mathbf{v_0}}\\
             \vdots\\
             d_{\mathbf{v_m}}\\
            \end{bmatrix}\mathbf{f},

    where :math:`d_\mathbf{v_i}` is the first-order directional derivative implemented by
    :py:func:`~pycsou.operator.linop.diff.DirectionalDerivative`.

    Examples
    --------
    .. plot::

       import numpy as np
       import matplotlib.pyplot as plt
       from pycsou.operator.linop.diff import DirectionalGradient
       from pycsou.util.misc import peaks

       x = np.linspace(-2.5, 2.5, 25)
       xx, yy = np.meshgrid(x, x)
       z = peaks(xx, yy)
       directions1 = np.zeros(shape=(2, z.size))
       directions1[0, :z.size // 2] = 1
       directions1[1, z.size // 2:] = 1
       directions2 = np.zeros(shape=(2, z.size))
       directions2[1, :z.size // 2] = -1
       directions2[0, z.size // 2:] = -1
       arg_shape = z.shape
       Dop = DirectionalGradient(arg_shape=arg_shape, directions=[directions1, directions2])
       out = Dop(z.reshape(1, -1))

       plt.figure()
       h = plt.pcolormesh(xx, yy, z, shading='auto')
       plt.quiver(x, x, directions1[1].reshape(arg_shape), directions1[0].reshape(xx.shape))
       plt.quiver(x, x, directions2[1].reshape(arg_shape), directions2[0].reshape(xx.shape), color='red')
       plt.colorbar(h)
       plt.title('Signal and directions of derivatives')

       plt.figure()
       h = plt.pcolormesh(xx, yy, out[:, :z.size].reshape(arg_shape), shading='auto')
       plt.colorbar(h)
       plt.title('Directional derivatives in 1st direction (gradient)')

       plt.figure()
       h = plt.pcolormesh(xx, yy, out[:, z.size:].reshape(arg_shape), shading='auto')
       plt.colorbar(h)
       plt.title('Directional derivatives in 2nd direction')

    See Also
    --------
    :py:func:`~pycsou.operator.linop.diff.Gradient`, :py:func:`~pycsou.operator.linop.diff.DirectionalDerivative`
    """

    dir_deriv = []
    for direction in directions:
        dir_deriv.append(
            DirectionalDerivative(
                arg_shape=arg_shape,
                which=1,
                directions=direction,
                diff_method=diff_method,
                boundary=boundary,
                parallel=parallel,
                **diff_kwargs,
            )
        )
    return _make_unravelable(pycb.vstack(dir_deriv), arg_shape=arg_shape)


def DirectionalLaplacian(
    arg_shape: pyct.Shape,
    directions: list,
    weights: typ.Iterable = None,
    diff_method: str = "gd",
    boundary: typ.Optional[typ.Union[pyct.Real, str, tuple, dict]] = None,
    **diff_kwargs,
):
    r"""
    Directional Laplacian.
    Sum of the second directional derivatives of a multi-dimensional array (at least two dimensions are required)
    along multiple ``directions`` for each entry of the array.

    Parameters
    ----------
    arg_shape: tuple
        Shape of the input array
    directions: list
        Multiple directions (each as an array of size :math:`n_\text{dims}`) or group of directions
        (array of size :math:`[n_\text{dims} \times n_{d_0} \times ... \times n_{d_{n_\text{dims}}}]`)
    weights: iterable (optional)
        List of optional positive weights with which each second directional derivative operator is multiplied.
    diff_method: str ['gd', 'fd']
        Method used to approximate the derivative. It can be the finite difference method (`fd`) or the Gaussian
        derivative (`gd`).
    boundary:
        Arguments related to the padding  (see
        :py:class:`~pycsou.operator.linop.diff.PartialDerivative`). See
        :py:class:`~pycsou.operator.linop.base.Stencil` for more information on both types of parameters.

    Returns
    -------
    op: :py:class:`~pycsou.abc.operator.LinOp`
            DirectionalLaplacian

    Notes
    -----
    The ``DirectionalLaplacian`` of a multivariate function :math:`f(\mathbf{x})` is defined as:
    .. math::
        d^2_{\mathbf{v}_1(\mathbf{x}),\ldots,\mathbf{v}_N(\mathbf{x})} \mathbf{f} =
            -\sum_{n=1}^N
            d^\ast_{\mathbf{v}_n(\mathbf{x})}(d_{\mathbf{v}_n(\mathbf{x})} \mathbf{f}).
    where :math:`d_\mathbf{v}` is the first-order directional derivative
    implemented by :py:func:`~pycsou.operator.linop.diff.DirectionalDerivative`.

    Examples
    --------
    .. plot::

       import numpy as np
       import matplotlib.pyplot as plt
       from pycsou.operator.linop.diff import DirectionalLaplacian
       from pycsou.util.misc import peaks

       x = np.linspace(-2.5, 2.5, 25)
       xx, yy = np.meshgrid(x, x)
       z = peaks(xx, yy)
       directions1 = np.zeros(shape=(2, z.size))
       directions1[0, :z.size // 2] = 1
       directions1[1, z.size // 2:] = 1
       directions2 = np.zeros(shape=(2, z.size))
       directions2[1, :z.size // 2] = -1
       directions2[0, z.size // 2:] = -1
       arg_shape = z.shape
       Dop = DirectionalLaplacian(arg_shape=arg_shape, directions=[directions1, directions2])
       out = Dop(z.reshape(1, -1))

       plt.figure()
       h = plt.pcolormesh(xx, yy, z, shading='auto')
       plt.quiver(x, x, directions1[1].reshape(arg_shape), directions1[0].reshape(xx.shape))
       plt.quiver(x, x, directions2[1].reshape(arg_shape), directions2[0].reshape(xx.shape), color='red')
       plt.colorbar(h)
       plt.title('Signal and directions of derivatives')

       plt.figure()
       h = plt.pcolormesh(xx, yy, out.reshape(arg_shape), shading='auto')
       plt.colorbar(h)
       plt.title('Directional Laplacian')

    See Also
    --------
    :py:func:`~pycsou.operator.linop.diff.Gradient`, :py:func:`~pycsou.operator.linop.diff.DirectionalDerivative`
    """

    if weights is None:
        weights = [1.0] * len(directions)
    else:
        if len(weights) != len(directions):
            raise ValueError("The number of weights and directions provided differ.")
    dir_lapacian = pycob.NullOp(shape=(np.prod(arg_shape), np.prod(arg_shape)))
    for weight, direction in zip(weights, directions):
        dir_lapacian += weight * DirectionalDerivative(
            arg_shape=arg_shape,
            which=2,
            directions=direction,
            diff_method=diff_method,
            boundary=boundary,
            **diff_kwargs,
        )

    return _make_unravelable(dir_lapacian, arg_shape=arg_shape)


def DirectionalHessian(
    arg_shape: pyct.Shape,
    directions: list,
    diff_method="gd",
    boundary: typ.Optional[typ.Union[pyct.Real, str, tuple, dict]] = None,
    parallel: bool = False,
    **diff_kwargs,
):
    r"""
    Directional Hessian.

    Computes the second order directional derivatives of a multi-dimensional array along multiple ``directions`` for
    each entry of the array.

    Parameters
    ----------
    arg_shape: tuple
        Shape of the input array
    directions: list
        Multiple directions (each as an array of size :math:`n_\text{dims}`) or group of directions
        (array of size :math:`[n_\text{dims} \times n_{d_0} \times ... \times n_{d_{n_\text{dims}}}]`)
    diff_method: str ['gd', 'fd']
        Method used to approximate the derivative. It can be the finite difference method (`fd`) or the Gaussian
        derivative (`gd`).
    boundary:
        Arguments related to the padding  (see
        :py:class:`~pycsou.operator.linop.diff.PartialDerivative`). See
        :py:class:`~pycsou.operator.linop.base.Stencil` for more information on both types of parameters.
    parallel: bool
        Whether to compute partial derivatives in parallel or not.

    Returns
    -------
    op: :py:class:`~pycsou.abc.operator.LinOp`
            DirectionalHessian

    Notes
    -----
    The ``DirectionalHessian`` of a multivariate function :math:`f(\mathbf{x})` is defined as:

    .. math::
        h_{\mathbf{v_0}, \ldots ,\mathbf{v_m}}\mathbf{f} = \begin{bmatrix}
             d^{2}_{\mathbf{v_0}}\mathbf{f} & \ldots & d_{\mathbf{v_0}, \mathbf{v_m}}\mathbf{f} \\
             \vdots & \ddots & \vdots \\
             d_{\mathbf{v_0}, \mathbf{v_m}}\mathbf{f} & \ldots & d^{2}_{\mathbf{v_m}}\mathbf{f} \\
            \end{bmatrix},

    where :math:`d_{\mathbf{v_i}, \mathbf{v_j}}` is the second-order directional derivative implemented by
    :py:func:`~pycsou.operator.linop.diff.DirectionalDerivative`.

    However, due to the symmetry of the Hessian, only the upper triangular part is computed in practice.

    .. math::

        h_{\mathbf{v_0}, \ldots ,\mathbf{v_m}}\mathbf{f} = \begin{bmatrix}
        d^{2}_{\mathbf{v_0}}\\
        \vdots \\
        d_{\mathbf{v_0, v_m}}\\
        d_{\mathbf{v_1, v_1}}\\
        \vdots \\
        d_{\mathbf{v_m, v_m}}\\
        \end{bmatrix}\mathbf{f}

    **Adjoint**

    The adjoint of the Hessian operator is computed as:

    .. math::

        h_{\mathbf{v_0}, \ldots ,\mathbf{v_m}}{\ast} = \begin{bmatrix}
        {d^{2}_{\mathbf{v_0}}}^{\ast} & \ldots & {d_{\mathbf{v_0, v_m}}}^{\ast} & {d_{\mathbf{v_1, v_1}}}^{\ast} & \ldots & {d_{\mathbf{v_m, v_m}}^{\ast}}
        \end{bmatrix}

    Examples
    --------
    .. plot::

       import numpy as np
       import matplotlib.pyplot as plt
       from pycsou.operator.linop.diff import DirectionalHessian
       from pycsou.util.misc import peaks

       x = np.linspace(-2.5, 2.5, 25)
       xx, yy = np.meshgrid(x, x)
       z = peaks(xx, yy)
       directions1 = np.zeros(shape=(2, z.size))
       directions1[0, :z.size // 2] = 1
       directions1[1, z.size // 2:] = 1
       directions2 = np.zeros(shape=(2, z.size))
       directions2[1, :z.size // 2] = -1
       directions2[0, z.size // 2:] = -1
       arg_shape = z.shape
       d_hess = DirectionalHessian(arg_shape=arg_shape, directions=[directions1, directions2])
       out = d_hess.unravel(d_hess(z.reshape(1, -1)))

       plt.figure()
       h = plt.pcolormesh(xx, yy, z, shading='auto')
       plt.quiver(x, x, directions1[1].reshape(arg_shape), directions1[0].reshape(xx.shape))
       plt.quiver(x, x, directions2[1].reshape(arg_shape), directions2[0].reshape(xx.shape), color='red')
       plt.colorbar(h)
       plt.title('Signal and directions of derivatives')

       plt.figure()
       h = plt.pcolormesh(xx, yy, out[0, 0].reshape(arg_shape), shading='auto')
       plt.colorbar(h)
       plt.title(r'$d^{2}_{v_{0}}$')

       plt.figure()
       h = plt.pcolormesh(xx, yy, out[0, 1].reshape(arg_shape), shading='auto')
       plt.colorbar(h)
       plt.title(r'$d_{v_{0}, v_{1}}$')

       plt.figure()
       h = plt.pcolormesh(xx, yy, out[0, 2].reshape(arg_shape), shading='auto')
       plt.colorbar(h)
       plt.title(r'$d^{2}_{v_{1}}$')

    See Also
    --------
    :py:func:`~pycsou.operator.linop.diff.Gradient`, :py:func:`~pycsou.operator.linop.diff.DirectionalDerivative`
    """

    dir_deriv = []
    for i, dir1 in enumerate(directions):
        for dir2 in directions[i:]:
            dir_deriv.append(
                -DirectionalDerivative(
                    arg_shape=arg_shape,
                    which=1,
                    directions=dir1,
                    diff_method=diff_method,
                    boundary=boundary,
                    parallel=parallel,
                    **diff_kwargs,
                ).T
                * DirectionalDerivative(
                    arg_shape=arg_shape,
                    which=1,
                    directions=dir2,
                    diff_method=diff_method,
                    boundary=boundary,
                    parallel=parallel,
                    **diff_kwargs,
                ).T
            )

    return _make_unravelable(pycb.vstack(dir_deriv), arg_shape=arg_shape)


def _make_unravelable(op, arg_shape=None):
    def unravel(self, arr):
        return arr.reshape(*arr.shape[:-1], -1, *self.arg_shape)

    if arg_shape is not None:
        setattr(op, "arg_shape", arg_shape)

    setattr(op, "unravel", functools.partial(unravel, op))
    return op


if __name__ == "__main__":
    import matplotlib.pyplot as plt
    import numpy as np

    from pycsou.operator.linop.diff import DirectionalHessian
    from pycsou.util.misc import peaks

    x = np.linspace(-2.5, 2.5, 25)
    xx, yy = np.meshgrid(x, x)
    z = peaks(xx, yy)
    directions1 = np.zeros(shape=(2, z.size))
    directions1[0, : z.size // 2] = 1
    directions1[1, z.size // 2 :] = 1
    directions2 = np.zeros(shape=(2, z.size))
    directions2[1, : z.size // 2] = -1
    directions2[0, z.size // 2 :] = -1
    arg_shape = z.shape
    d_hess = DirectionalHessian(arg_shape=arg_shape, directions=[directions1, directions2])
    out = d_hess.unravel(d_hess(z.reshape(1, -1)))

    plt.figure()
    h = plt.pcolormesh(xx, yy, z, shading="auto")
    plt.quiver(x, x, directions1[1].reshape(arg_shape), directions1[0].reshape(xx.shape))
    plt.quiver(x, x, directions2[1].reshape(arg_shape), directions2[0].reshape(xx.shape), color="red")
    plt.colorbar(h)
    plt.title("Signal and directions of derivatives")

    plt.figure()
    h = plt.pcolormesh(xx, yy, out[0, 0].reshape(arg_shape), shading="auto")
    plt.colorbar(h)
    plt.title(r"$d^{2}_{v_{0}}$")

    plt.figure()
    h = plt.pcolormesh(xx, yy, out[0, 1].reshape(arg_shape), shading="auto")
    plt.colorbar(h)
    plt.title(r"$d_{v_{0}, v_{1}}$")

    plt.figure()
    h = plt.pcolormesh(xx, yy, out[0, 2].reshape(arg_shape), shading="auto")
    plt.colorbar(h)
    plt.title(r"$d^{2}_{v_{1}}$")
