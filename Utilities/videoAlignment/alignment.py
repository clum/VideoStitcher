"""Timestamp calculations for aligning two clips."""

from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class Alignment:
    offset: Fraction
    common_start: Fraction
    common_end: Fraction

    @property
    def duration(self) -> Fraction:
        return max(Fraction(0), self.common_end - self.common_start)

    @property
    def overlap_exists(self) -> bool:
        return self.duration > 0

    def trims(self, duration_a: Fraction, duration_b: Fraction) -> dict[str, Fraction]:
        if not self.overlap_exists:
            raise ValueError("The selected offset leaves no shared interval.")
        return {
            "a_start": self.common_start,
            "a_end": duration_a - self.common_end,
            "b_start": self.common_start - self.offset,
            "b_end": self.offset + duration_b - self.common_end,
        }


def calculate_alignment(
    duration_a: Fraction, duration_b: Fraction, offset: Fraction
) -> Alignment:
    """Return the shared interval on A's timeline for signed B offset."""
    common_start = max(Fraction(0), offset)
    common_end = min(duration_a, offset + duration_b)
    return Alignment(offset, common_start, max(common_start, common_end))
