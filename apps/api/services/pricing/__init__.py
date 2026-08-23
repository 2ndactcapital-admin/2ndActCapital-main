"""Pricing services.

Sprint 31 lands the SSVI volatility-surface engine:

* ``ssvi_surface`` — the calibration module, copied from
  ``backend_reference/ssvi_surface.py``. The math is not ours to change; see
  the module docstring and ``NOTES FOR THE IMPLEMENTER`` in the sprint prompt.
* ``memory_guard`` — preemptive container-memory checks. Render SIGKILLs an OOM
  container and the process cannot catch that after the fact, so the guard runs
  *before* the heavy imports and the chain fetch.

Nothing in this package imports numpy, scipy, pandas or yfinance at package
import time — ``ssvi_surface`` pulls numpy/scipy at module import, so the router
imports it inside the request handler, not at module scope.
"""
