import dask.array as da
import dask.distributed as dad
import numpy as np

import pycsou.operator.linop.nufft as nufft
import pycsou.runtime as pycrt
import pycsou.util as pycu


def NUFFT3_array(x, z, isign) -> np.ndarray:
    return np.exp(1j * np.sign(isign) * z @ x.T)


if __name__ == "__main__":
    use_dask = True
    real = False

    rng = np.random.default_rng(0)
    D, M, N = 3, 200, 50
    x = rng.normal(size=(M, D)) + 2000
    z = rng.normal(size=(N, D))
    if use_dask:
        client = dad.Client(processes=False)  # processes=True yields a serialization error.
        x = da.from_array(x)
        z = da.from_array(z)

    with pycrt.Precision(pycrt.Width.SINGLE):
        N_trans, isign = 60, -1
        A = nufft.NUFFT.type3(x, z, n_trans=N_trans, isign=isign, eps=1e-5, real=real)
        B = NUFFT3_array(x, z, isign)

        arr = rng.normal(size=(34, N_trans, M))
        if not real:
            arr = arr + 1j * rng.normal(size=arr.shape)
        if use_dask:
            arr = da.from_array(arr)

        A_out_fw = pycu.view_as_complex(A.apply(pycu.view_as_real(arr)))
        B_out_fw = np.tensordot(arr, B, axes=[[2], [1]])

        A_out_bw = A.adjoint(pycu.view_as_real(A_out_fw))
        if not real:
            A_out_bw = pycu.view_as_complex(A_out_bw)
        B_out_bw = np.tensordot(B_out_fw, B.conj().T, axes=[[2], [1]])
        if real:
            B_out_bw = B_out_bw.real

        res_fw = (np.linalg.norm(A_out_fw - B_out_fw, axis=-1) / np.linalg.norm(B_out_fw, axis=-1)).max()
        res_bw = (np.linalg.norm(A_out_bw - B_out_bw, axis=-1) / np.linalg.norm(B_out_bw, axis=-1)).max()
        if use_dask:
            res_fw, res_bw = pycu.compute(res_fw, res_bw, scheduler="multiprocessing")
        print(res_fw)
        print(res_bw)
