"""
Operator Arithmetic.
"""

import numpy as np

import pycsou.abc.operator as pyco
import pycsou.util.ptype as pyct


def add(lhs: pyct.OpT, rhs: pyct.OpT) -> pyct.OpT:
    pass


def scale(op: pyct.OpT, cst: pyct.Real) -> pyct.OpT:
    from pycsou.operator.linop import HomothetyOp, NullFunc, NullOp

    if np.isclose(cst, 0):
        return NullOp(shape=op.shape) if (op.codim > 1) else NullFunc()
    elif np.isclose(cst, 1):
        return op
    else:
        h = HomothetyOp(cst, dim=op.codim)
        return compose(h, op)


def compose(lhs: pyct.OpT, rhs: pyct.OpT) -> pyct.OpT:
    pass


def pow(op: pyct.OpT, k: pyct.Integer) -> pyct.OpT:
    # check square
    pass


def argscale(op: pyct.OpT, cst: pyct.Real) -> pyct.OpT:
    pass


def argshift(op: pyct.OpT, cst: pyct.NDArray) -> pyct.OpT:
    pass
