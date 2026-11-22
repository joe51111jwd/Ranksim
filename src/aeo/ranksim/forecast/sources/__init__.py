"""Loaders for public and paid datasets that feed the forecast models.

Every value a loader yields is REAL data copied from a dated source, with that
date kept beside it. Nothing here predicts. The exact layouts are in
docs/plan/IMPLEMENTATION_SPEC.md section 8.
"""

from __future__ import annotations
