"""Offline recall cache primitives.

The cache is deliberately a small, append-only JSONL contract.  It contains
only claims which have passed the existing identity and first-party gates;
unchecked work remains explicitly distinguishable from a checked absence.
"""

from .universe import CacheLookup, iter_cache_records, merge_cache_profile

__all__ = ["CacheLookup", "iter_cache_records", "merge_cache_profile"]
