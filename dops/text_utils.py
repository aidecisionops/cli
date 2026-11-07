from __future__ import annotations


def levenshtein_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, a_char in enumerate(a, start=1):
        diagonal = previous[0]
        previous[0] = i
        for j, b_char in enumerate(b, start=1):
            next_diagonal = previous[j]
            substitution_cost = 0 if a_char == b_char else 1
            previous[j] = min(previous[j] + 1, previous[j - 1] + 1, diagonal + substitution_cost)
            diagonal = next_diagonal
    return previous[-1]
