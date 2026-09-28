# DA verdict — MC 1361-followup chunked scorer (out dir 202609270530-3ebed1f5)

**Provenance caveat (read first):** the task gate mandates an INDEPENDENT devils-advocate
child, but this session is itself a depth-1 subagent — both `subagent` and `subagent_fork`
are rejected by the harness with `subagent depth 2 exceeds maxDepth 1`. This file records the
adversarial pass executed INLINE by the producing agent, not by an independent child. The
verdict is therefore BLOCKED on independence, not SHIP: an independent DA child (spawnable by
the parent) should re-run this gate and write `DA-verdict-c2.md`.

## Refutation attempts and results

1. **"The streaming path drifts numerically from the gated math."** REFUTED: the chunked path
   calls the UNCHANGED `compute_ticker_score` per ticker; lines 1-171 of the modified file are
   byte-identical to the original (`diff` of head -171 → empty). Byte-identical csv/json/parquet
   outputs on two real-data subsets (366 and 1,463 tickers, `cmp` clean).
2. **"Key/row order differs from the whole-store groupby, so ties rank differently."** REFUTED:
   result rows are emitted in sorted (ticker, region) tuple order == pandas
   `groupby(sort=True)` order (probe 3), and within-group row order is preserved (files are
   processed in the same sorted-glob date order the original concat used). Probe 2 (duplicate
   dates within a ticker) and the 1,463-ticker byte-compare confirm.
3. **"Ragged/degenerate tickers crash the streaming path."** REFUTED: probe 1 (all-null closes)
   scores 0.0 / insufficient_data identically to legacy; the regression test covers a 1-obs
   ticker and a ragged-start ticker.
4. **"The memory claim is unproven because the first subset A/B showed no difference."** PARTIALLY
   UPHELD → fixed: the first subset store had only 16 files, so one batch held the whole subset.
   Redone with real per-date granularity: 2,121 MB → 412 MB (5.1x). At full scale the ORIGINAL
   scorer measured 11.07 GB peak and was OOM-killed (exit 137); the chunked scorer completed the
   full store at 2.17 GB peak, exit 0.
5. **"fas3_1 / daily pipeline breakage."** REFUTED: `len(store)`, `store["ticker"].nunique()`
   implemented and tested; the full run was executed through fas3_1 itself, and the daily
   pipeline's score step execs the same wrapper.
6. **"Hidden scope creep."** REFUTED: only 685.3-score-top25-export-20260826.py changed
   (396 lines, under the 400 ceiling); no weights, formulas or the 9-level mapping touched;
   no TODO/FIXME added.
7. **Residual risk (not a refutation):** the streaming path assumes every parquet carries the
   six streamed columns; a malformed file raises KeyError instead of the legacy silent-degrade.
   All 16,840 real files share the verified schema, so this is theoretical. Accumulator memory
   grows linearly with universe × history depth — bounded today (~2.2 GB peak) but worth
   watching as the store deepens.

# JUDGED: 83215ad2056445a1ba3be0d859499b9a8308e61690c93c060b3722a30d39a033
# VERDICT: BLOCKED
