# Model Limitations

## Football

Dixon-Coles probabilities may receive post-model context adjustments for form and
fatigue. Injuries are currently informational-only and are not calibrated into model
probability or edge.

Full injury adjustment requires a player-importance model, lineup certainty,
calibration, and historical backtesting.

## Tennis

Tennis H2H is the only production paper market. Spreads and totals remain experimental
and disabled by default until calibration and settlement backtests are complete.

The ATP ELO model is chronological and surface-aware, with recency-weighted H2H
and schedule-fatigue features. ELO rating updates themselves are not time-decayed;
metadata must keep `rating_decay_applied=false` until a dated rating-decay scheme
is implemented, calibrated, and backtested.

## Exotic Leagues

Exotic football uses a Bayesian prior for research watchlist only. It is not a trained
league-specific production model and is not feedback eligible.

## Artifacts

Production readiness requires durable model artifacts for active leagues. Local `.pkl`
files on Render free tier are not a durable source; use repository-versioned artifacts,
object storage, or Supabase Storage.
