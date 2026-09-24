# Benchmark results — <YYYY-MM-DD>

Protocol: `docs/bench/protocol.md` @ `<git sha>` · Code: `<git sha>` · Gold: `gold_sha <…>`
(n = <…>, in <…> / out <…> / borderline <…>, drifted excluded <…>)

## Headline (Phase-1 recall on D, 5 repeats, T=0)

| Model | Recall (D) | 95 % CI | Precision (D) | F1 | Unanimous % | Fleiss κ | $/run |
|---|---|---|---|---|---|---|---|
| Haiku 4.5 | | | | | | | |
| Sonnet 4.6 | | | | | | | |
| Δ (Sonnet − Haiku) | | [ , ] | | | | | |

H1: <held / not held>. McNemar p = <…> on <b+c> discordant pages.

## Bounds

| Metric | D | borderline→in | borderline→out |
|---|---|---|---|
| Recall Haiku | | | |
| Recall Sonnet 4.6 | | | |
| Precision Haiku | | | |
| Precision Sonnet 4.6 | | | |

## Phase 2 (crossed, paired on identical Phase-1 keeps)

| P1 → P2 | FP removed | TP wrongly dropped | Net | Drop precision | E2E recall | E2E precision |
|---|---|---|---|---|---|---|
| haiku → haiku | | | | | | |
| haiku → sonnet46 | | | | | | |
| sonnet46 → haiku | | | | | | |
| sonnet46 → sonnet46 | | | | | | |

H3 / H6: <…>

## Stability

Flip pages (0 < keeps < 5): Haiku <…>, Sonnet 4.6 <…> (list in `stability.csv`). Score SD mean
<…>; share > 0.15 <…>. Band stability <…>.

## Calibration

| Score band | n | gold-in share (D) | →in bound | →out bound |
|---|---|---|---|---|
| 0 | | | | |
| 0.1–0.3 | | | | |
| 0.4–0.6 | | | | |
| 0.7–1.0 | | | | |

H5: <monotone / not>. Brier <…>.

## Grounding

| Check | Haiku | Sonnet 4.6 |
|---|---|---|
| G1 parsed | | |
| G2 keep ↔ score | | |
| G3 evidence verbatim | | |
| G4 where_hit valid | | |
| G5 primary_topic sane | | |
| G6 score ∈ [0,1] | | |
| G7 not truncated | | |

## Cost & latency

| Model | Phase | $/run | ms median | ms p90 | $ per correct decision | $ per gold-in recalled |
|---|---|---|---|---|---|---|

## Decisions

- Phase-1 model: <…>
- Phase-2 model: <…>
- Pin temperature in production: <…> (ADR <…>)

## Deviations from protocol

<none / list with git SHAs>

## Files

`per_page.csv`, `per_run.csv`, `stability.csv`, `grounding.csv`, `paired.csv`, `runs.json`
