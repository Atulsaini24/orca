"""ORCA: optimized real-time coordination and assignment."""
import os as _os

# The dispatcher calls its models many small times per second. OpenMP / BLAS
# worker threads cost far more than they save at that size and spin against the
# simulation loop, so run them single-threaded. Must be set before numpy,
# scipy or scikit-learn load their native thread pools.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    _os.environ.setdefault(_var, "1")

__version__ = "0.2.0"
