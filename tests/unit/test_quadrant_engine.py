"""Verify score and review-ratio quadrant classification."""
import pytest

from contributor_board.quadrant_engine import (
    QuadrantPoint,
    calculate_quadrants,
    determine_quadrant,
)


@pytest.mark.parametrize(
    ("score", "review_ratio", "quadrant"),
    [
        (80, 0.8, "INCENTIVE"),
        (80, 0.1, "IMPROVE"),
        (20, 0.8, "ENCOURAGE"),
        (20, 0.1, "REPLACE"),
        (50, 0.8, "INCENTIVE"),
        (80, 0.5, "INCENTIVE"),
    ],
)
def test_determine_quadrant(score, review_ratio, quadrant):
    assert determine_quadrant(score, review_ratio, 50, 0.5) == quadrant


def test_empty_list():
    assert calculate_quadrants([]) == ([], 0.0, 0.0)


def test_single_contributor_preserves_metadata_and_legacy_score_key():
    points, score, ratio = calculate_quadrants([
        {
            "login": "solo",
            "avatar_url": "https://example.com/avatar.png",
            "contribution_score": 75,
            "review_ratio": 0.4,
            "merged_pr_count": 10,
            "is_community": True,
        }
    ])
    assert (score, ratio) == (75, 0.4)
    assert points == [
        QuadrantPoint("solo", "https://example.com/avatar.png", 75, 0.4,
                      "INCENTIVE", 10, True, 0.4)
    ]


def test_four_contributors_land_in_distinct_quadrants():
    points, score, ratio = calculate_quadrants([
        {"login": "a", "total_score": 90, "review_ratio": 0.75},
        {"login": "b", "total_score": 90, "review_ratio": 0.25},
        {"login": "c", "total_score": 10, "review_ratio": 0.75},
        {"login": "d", "total_score": 10, "review_ratio": 0.25},
    ])
    assert (score, ratio) == (50, 0.5)
    assert {point.login: point.quadrant for point in points} == {
        "a": "INCENTIVE",
        "b": "IMPROVE",
        "c": "ENCOURAGE",
        "d": "REPLACE",
    }


@pytest.mark.parametrize(
    ("values", "expected_score", "expected_ratio"),
    [
        ([(1, 0.1), (2, 0.2), (3, 0.3), (4, 0.4), (5, 0.5)], 3, 0.3),
        ([(0, 0), (10, 0.2), (20, 0.4), (30, 0.6)], 15, 0.3),
        ([(0, 0), (0, 0)], 0, 0),
        ([(100, 0.8), (1, 0.2)], 50.5, 0.5),
        ([(50, 0.3)] * 5, 50, 0.3),
    ],
)
def test_thresholds_follow_the_median(values, expected_score, expected_ratio):
    points, score, ratio = calculate_quadrants([
        {"login": str(index), "total_score": score, "review_ratio": ratio}
        for index, (score, ratio) in enumerate(values)
    ])
    assert len(points) == len(values)
    assert (score, ratio) == pytest.approx((expected_score, expected_ratio))


def test_missing_optional_fields_default_to_zero_or_empty():
    points, _, _ = calculate_quadrants([{"login": "minimal", "total_score": 80}])
    assert points == [QuadrantPoint("minimal", None, 80, 0, "INCENTIVE", 0, False)]
