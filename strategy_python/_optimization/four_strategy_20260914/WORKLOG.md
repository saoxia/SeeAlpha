# Execution checkpoint

User goal: independently optimize the four members of `mean_reversion_long_trend_both_pullback_3x.json`, preserve original strategy files, create independent new strategy packages. Goal already exists and is active. No user risk constraints received after async question; protocol uses unchanged sizing and validation drawdown guard.

## Completed

- All original 4 packages hashed in original_hashes.json; no original package edits.
- Original behavior tests: 284 passed. Optimization selection/equity tests: 5 passed.
- Local minute source used to build isolated derived_5m.parquet; 4-day aggregation parity audit passed.
- 40 first-round 5m developer trials completed; 8 second-round 5m trials completed (total 48 distinct configs).
- First-round native PBS/TFS train+validation completed. TFS extend_4pct passed; PBS first round none passed.
- TFL native train complete, validation in progress. MRL native first round queued.
- Streaming low-memory runner added using installed engine's documented streaming API, independent of original files. Pilot parity in May 2022 tests active trades for all 4. Three done, MRL pending as of checkpoint. See check_streaming.log / streaming_parity.json.

## Active processes and how they finish

- review_queue.py (log review_queue.log): handles first-round PBS -> TFL -> TFS (cached from other worker) -> MRL, 4 candidates each, train and validation. End marker ALL_REVIEWED.
- review_tfs.py: completed, marker SELECTED in review_tfs.log.
- round2_review.py: waits first TFS done, then best second-round PBS -> TFL -> TFS native train/validation. End marker ALL_REVIEWED.
- MRL_round2_early_review.log: streaming native train then validation for expire24_deeper, best MRL second-round screen; saves normal runs paths. Lock avoids duplicate work with freeze_queue later.
- freeze_queue.py: waits first-round all complete, ensures best MRL second-round native train/validation, waits other round2 reviews, RECOMPUTES ALL selections using latest select.py criteria, then calls materialize.py to create packages/tests/new manifest. End marker FROZEN. This recomputation matters: original review_queue process imported the initial (superseded) annual profitability guard; final rule instead requires two development years improving relative to baseline by >0.01 USDT, documented before validation. Do not rely on intermediate *_selection.json until FROZEN.
- check_streaming.py: parity checks normal-vs-streaming May 2022, produces streaming_parity.json.

## Next required work

1. Monitor logs, keep user informed. Resolve failures if any. Don't touch holdout/full yet; freeze first.
2. When FROZEN, inspect generated packages/configs. materialize.py copies all original domain modules with new package and class names, applies chosen defaults (PBS includes .015 risk/3x cap), generates independent backtest.py and copied behavior tests.
3. Run new behavior tests and existing full strategy suite; copied default assertions may need adjustment only to selected defaults. Run lint/format on new files only. materialize.py generates thin factories; test factory/config parameter snapshots agree. New portfolio wrapper portfolio_optimized_backtest.py registers new keys lazily, preserves original portfolio.py.
4. Final evaluations must exercise DELIVERED classes' defaults: streaming.run(key, 'holdout'/'full', selected_name, optimized=True) uses no parameter overrides for optimized=True. Baselines use original classes/base params. Streaming outputs runs/{key}/{period}/baseline and frozen_runs/{key}/{period}/{selected}; documented 1m engine with continuous state between 180-day batches. Run at most 2-3 simultaneously considering 8GB RAM; streaming avoids loading 3 million Bar objects.
5. Keep selected parameters fixed even if holdout is worse. Report failures honestly; cannot call historical reused holdout wholly unseen OOS.
6. write_report.py creates comparison.csv/json, RESULTS.md, 4-panel equity_comparison.png from final metrics. Add specific trading hypotheses/new rule descriptions, actual holdout conclusions, cost stress/annual results and data gaps. Update each new README with selected rules and report links. Reference tested ranges, not global/future optimality.
7. Smoke-test new optimized portfolio assembly/backtest (short historical interval), without claiming independent PnLs sum to shared-account portfolio PnL. No need to alter/run full original portfolio; goal is independent improvements.
8. Verify original hashes unchanged, all required checks pass, inspect chart, final concise Chinese results with clickable absolute paths. Mark goal complete only after required work is done.

## Paths

- Research root: strategy_python/_optimization/four_strategy_20260914
- New packages (created at freeze): mean_reversion_long_optimized_5min, trend_following_long_optimized_5min, trend_following_short_optimized_5min, pullback_short_optimized_1h
- New manifest: strategy_python/_portfolio/four_strategies_optimized_20260914.json
- New combination entry: strategy_python/portfolio_optimized_backtest.py
- New tests copied under tests/strategy_python/<new package>, plus optimization/ tests.

Known data limitations: train missing 584 original minutes, validation missing 15. 5m screening loses 605 minutes in train due discarding incomplete buckets. Maker fee .0002, taker .0005. Funding not replayed; slippage 2/5/10bp stress subtracts fixed executed turnover, doesn't rerun trading. Equity marks include unrealized PnL at minute closes, not intraminute worst equity.

## Later checkpoint: frozen + final evaluation underway

All 4 choices frozen: MRL moderate_discount (BOLL std 2.25); TFL extend_3pct; TFS extend_4pct; PBS baseline (no candidate beat validation).
All new packages and tests exist. Fixed generated factories' literal warmup dependency. Adapted copied tests' explicit 2% fixtures and new default assertions; PBS registry test uses monkeypatch. Added environment.py to comply with architecture dependency constraints (spec equals all original backtest specs, verified). All 993 strategy tests PASS (all_strategy_tests.log). tests.ok exists, final worker started. Original 49 source/docs hashes unchanged. frozen_source_hashes.json covers new *.py; do not edit delivered Python files while final evaluations run. New READMEs are written with actual rules.

IMPORTANT semantic correction: MRL max_holding_hours only limits the ADDING window, NOT time liquidation. Communicated to user and documented in PROTOCOL + new MRL README/config comment/test. Final MRL selected std change retains 48-hour adding window.

Streaming parity passed all 4 with active May 2022 trades; streaming_parity.json. New portfolio smoke succeeded for March 2024; portfolio_smoke.json contains report path. It is integration only, no claim independent PnL sum represents portfolio results.

final_evaluate.py running 3 subprocess workers, emits final_evaluate.log. It executes 16 cases (old/new × full/holdout × 4), then write_report.py. Extra helper final_PBS_assist.log running PBS full/holdout old/new in parallel; per-trial file locks avoid duplicate execution, queue will use completed cache. Source for final evaluations is streaming.py; optimized=True uses actual new class defaults without overrides.

Completed final full results so far: MRL original net 563.33, new 660.57, DD 35.79% -> 26.03%; TFL original 119.35, new 179.57, DD 29.96% -> 33.35%; PBS original 247.83. CRITICAL: TFL new full annual 2025 and 2026 PnLs are negative, so holdout likely WORSE. Do not obscure holdout failures or alter choices after viewing. Full net gains alone do not establish successful forward improvement. New TFL cost stress 2bp is negative. Report must say this clearly.

write_report.py updated to verify original AND frozen new hashes, give development/validation/full/holdout tables, flag holdout losers, explain rules, provide 5bp cost stress, mention 48 screen/20 native configs, link READMEs, note all tests and integration. MPLCONFIGDIR uses project .local/cache/matplotlib; matplotlib available. Once final complete, inspect report/comparison/plot, add precise holdout conclusions to new READMEs if needed (docs can change; frozen *.py should not), ensure accurate data-gap info (full 618 minutes; train 584, validation 15, holdout likely 19). View generated chart, final source-hash check + lint on new files/scripts. Finish with Chinese concise result table and absolute links; acknowledge PBS unchanged and any holdout degradation. Only then update_goal complete.
