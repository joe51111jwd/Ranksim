# libgomp.so.1 for the Vercel deployment only

LightGBM's Linux wheel links the GNU OpenMP runtime (`libgomp.so.1`). Vercel's Python 3.12 runtime image does not
ship it, so a deployed app failed with `OSError: libgomp.so.1: cannot open shared object file` the first time the
engine trained. `app.py` (the Vercel entry) preloads this copy with `ctypes.CDLL(..., RTLD_GLOBAL)` when the system has
none, which satisfies LightGBM's `DT_NEEDED libgomp.so.1`. Local runs (`python -m aeo.ranksim serve`) never load it.

Provenance: extracted from the scikit-learn 1.7.2 wheel `scikit_learn-1.7.2-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl`
(PyPI), file `scikit_learn.libs/libgomp-a34b3233.so.1.0.0`, built by the manylinux2014 toolchain (needs glibc 2.17 or
newer). The only change: `patchelf --set-soname libgomp.so.1`. SHA-256 after the change:
`dd8eca019c43c3d7da57c197e32cefe53bda8e4a5e699ab54e4234772a1d5ed4`.

License: libgomp is part of GCC, GPL-3.0 with the GCC Runtime Library Exception, which allows redistribution with
programs that use it.
