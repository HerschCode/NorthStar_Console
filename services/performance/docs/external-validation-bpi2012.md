# External validation on a different event log (BPI Challenge 2012)

Reproduce: `python -m scripts.external_validation_bpi2012` (downloads the file on demand and
verifies its MD5; the data is **not** committed — it is governed by the
[4TU General Terms of Use](https://data.4tu.nl/articles/dataset/BPI_Challenge_2012/12689204)).

**Why:** every number elsewhere in this repo comes from BPI 2019 with labels defined by this
project. This checks whether the two main findings hold on a public log this project did not
prepare, with a label it did not choose (cycle time above the *training window's* p50 / p75;
threshold from the first 80% of cases by start time, last 20% held out in time order).

Data as parsed: 13,087 cases, 262,200 events (matches the published size); median cycle time 19.4 h,
max 137 days. Activity label = `concept:name + lifecycle:transition`.

## Feature sets

| Set | Information used |
|---|---|
| creation-time only | requested amount, start hour / weekday / month, first activity |
| first k events | the above + elapsed time at event k, unique activities and repeats so far, the k-th activity |
| full case | event count, unique activities, rework, last activity (aggregates over the finished case) |

For first-k rows, cases with fewer than k events are dropped **and so are cases whose elapsed time
at event k already exceeds the threshold** — their label is already determined, so scoring them is a
look-up rather than a prediction. (A first version of the script did not do this and showed a base rate
of 0.92 at k=10; that was caught from the output and fixed before anything was reported.)

## Results (held-out last 20% by start time; RF / LR; base rate = share labelled "long")

| Target | Feature set | Test n | Base rate | ROC-AUC RF / LR | PR-AUC RF / LR |
|---|---|---|---|---|---|
| p50 (18.8 h) | creation-time only | 2618 | 0.512 | 0.649 / 0.622 | 0.643 / 0.605 |
| | first 3 events | 2618 | 0.512 | 0.920 / 0.910 | 0.905 / 0.882 |
| | first 5 events | 1641 | 0.672 | 0.861 / 0.851 | 0.897 / 0.885 |
| | first 10 events | 847 | 0.865 | 0.939 / 0.936 | 0.988 / 0.987 |
| | full case | 2618 | 0.512 | 0.995 / 0.993 | 0.994 / 0.993 |
| p75 (358 h) | creation-time only | 2618 | 0.187 | 0.615 / 0.618 | 0.247 / 0.246 |
| | first 3 events | 2618 | 0.187 | 0.771 / 0.772 | 0.356 / 0.363 |
| | first 5 events | 1879 | 0.260 | 0.673 / 0.677 | 0.368 / 0.373 |
| | first 10 events | 1391 | 0.346 | 0.631 / 0.608 | 0.456 / 0.449 |
| | full case | 2618 | 0.187 | 0.933 / 0.931 | 0.757 / 0.748 |

## What it supports

1. **The BPI 2019 finding replicates.** Features aggregated over the finished case give
   0.93–0.995 ROC-AUC; creation-time features alone give 0.62–0.65 (BPI 2019: 0.51–0.65). Most of the
   "skill" of a full-case model is information about how long the case already was.
2. **Early warning is feasible in this process.** With only the first three events, ROC-AUC is 0.92
   (p50) and 0.77 (p75) against ~0.62 at creation. The k-th activity carries routing information (e.g. a
   quick decline vs. continued processing), which is legitimately known at that moment.

## What it does not support

- **Rows with different k are not comparable**: each evaluates a different surviving population
  (base rate moves from 0.51 to 0.87 at p50). Don't read the k=10 rows as "better than k=3".
- **No transfer claim to BPI 2019.** The prefix approach has not been run on P1's own data; a loan
  process's early events may carry more routing information than a P2P process's.
- One time-ordered split, one seed, no confidence intervals; RF vs LR gaps here are within noise.
