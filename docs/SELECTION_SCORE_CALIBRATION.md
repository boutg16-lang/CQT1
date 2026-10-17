# Selection score and calibration

The pipeline has two scoring stages. The raw model `score` is only a candidate-ordering signal; it cannot outweigh the editorial and transcript-derived evidence.

## Candidate ordering

`scripts/performance_weights.py` supplies the default pre-sort weights:

| Component | Default | Meaning |
|---|---:|---|
| `virality` | 0.10 | Raw LLM self-score; hard-capped at 10% |
| `hook` | 0.20 | Hook estimate |
| `completeness` | 0.20 | Narrative completeness |
| `clarity` | 0.15 | Clarity |
| `novelty` | 0.15 | Novelty |
| `title` | 0.20 | Title quality |

The five editorial weights are normalized to the remaining 90%. Missing editorial fields use the neutral value 50; they do not inherit the model's virality score. Per-project performance learning can nudge editorial weights after at least three measured clips. It cannot raise the raw `virality` share above `MAX_VIRALITY_WEIGHT` (0.10).

## Final selection score

`scripts/clip_scoring.py` computes a separate score from ten factors. The default weights sum to 1.00:

| Factor | Weight | Signal |
|---|---:|---|
| `hook_strength` | 0.18 | LLM estimate when supplied; deterministic fallback otherwise |
| `standalone_context` | 0.14 | Transcript/window analysis |
| `narrative_completeness` | 0.13 | LLM estimate when supplied; deterministic fallback otherwise |
| `information_density` | 0.12 | Transcript analysis |
| `emotional_value` | 0.13 | Text heuristic |
| `transcript_alignment` | 0.10 | Transcript/window match |
| `boundary_quality` | 0.08 | Timing and sentence-boundary checks |
| `audio_quality` | 0.05 | Audio measurements/fallback |
| `visual_quality` | 0.03 | Visual measurements/fallback |
| `title_relevance` | 0.04 | Title-to-window text match |

The model-influenced factors (`hook_strength`, `standalone_context`, and `narrative_completeness`) have 45% combined raw default weight. Because `standalone_context` and `narrative_completeness` can blend model reports with transcript heuristics, the cap conservatively limits their entire combined final weight to 35%, including under `VIRALCUTTER_SELECTION_WEIGHTS` overrides; the remainder is redistributed across deterministic factors. The raw `score` field is not an input to this final formula. Missing final-score factors use the documented neutral value 80, while penalties are subtracted.

## Calibration

Set `VIRALCUTTER_SELECTION_WEIGHTS` to a JSON object keyed by the factor names above, for example:

```json
{"hook_strength": 0.20, "standalone_context": 0.16, "boundary_quality": 0.10}
```

Unknown keys are ignored, values are clamped to `[0, 1]`, and final positive weights are normalized before scoring. The 35% model-influenced-factor ceiling remains in force. `VIRALCUTTER_MIN_FINAL_SCORE` can set an optional final-score floor; its default is 0 (off). Weight or scoring-version changes alter the cache fingerprint so saved clip selections are regenerated.

The executable checks are in `tests/test_professional_selection.py`, `tests/test_v740_clip_quality.py`, and `tests/test_segment_cache_staleness.py`.
The scoring schema is now `7.52.2`; the cache fingerprint includes it and the title-validation schema, so cached selections made with the previous ranking formula are not silently reused. The application release remains `7.52.0-pro`.
