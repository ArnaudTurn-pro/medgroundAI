"""Watches — persistent subscriptions that track new research over time.

A watch is (label, query, source, cadence). The executor pulls only the delta since the last
run using PubMed's `edat` (entrez date) filter, skips PMIDs already in the corpus, and updates
a cursor on success. Multiple watches run concurrently with a shared rate-limit semaphore so
NCBI guidelines hold even as the number of watches grows.
"""
