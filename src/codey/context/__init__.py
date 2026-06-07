"""Context management for the agent loop.

A 4-step proactive compaction pipeline plus a reactive retry path. The
pipeline runs at the top of every model round inside Agent.run() and is
designed so cheap steps are no-ops on small histories.

Steps (in order):
  1. tool_result_budget — persist >200kb tool results to disk
  2. snip_compact       — trim middle of conversation past 50 messages
  3. micro_compact      — placeholder old tool results, keep last 5 bodies
  4. llm_compact_history — single API call summary (only past headroom)

Failure path:
  reactive_compact      — runs on PromptTooLongError, ≤1 retry per turn

See docs/2026-06-07-context-management-design.md for the full spec.
"""
from __future__ import annotations
