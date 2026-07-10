"""Reusable skewed-distribution math, shared by the products and orders generators."""


def pareto_weighted_split(total, n, alpha=1.5, rng=None):
    """
    Splits `total` into `n` non-negative integers using a pareto-shaped
    weighting - a few buckets get much more than most (e.g. "a handful of
    power sellers own hundreds of listings, most sellers own a handful").

    Lower `alpha` = more extreme skew, higher `alpha` = closer to even.
    Returns a list of exactly `n` integers that sum to exactly `total`
    (largest-remainder rounding, so the totals always add up precisely
    rather than drifting from floating-point rounding).
    """
    import random as _random

    rng = rng or _random

    if n <= 0:
        return []
    if total <= 0:
        return [0] * n

    raw_weights = [rng.paretovariate(alpha) for _ in range(n)]
    weight_sum = sum(raw_weights)
    shares = [w / weight_sum * total for w in raw_weights]

    floors = [int(s) for s in shares]
    remainder = total - sum(floors)
    # Give the leftover units to whichever buckets had the largest fractional part.
    order = sorted(range(n), key=lambda i: shares[i] - floors[i], reverse=True)
    for i in order[:remainder]:
        floors[i] += 1

    return floors
