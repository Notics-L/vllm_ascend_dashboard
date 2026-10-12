"""Tests for contributor_board.quadrant_engine."""
from contributor_board.quadrant_engine import (
    QuadrantPoint,
    calculate_quadrants,
    determine_quadrant,
)


class TestDetermineQuadrant:
    """Tests for determine_quadrant() — quadrant classification logic."""

    def test_incentive_quadrant(self):
        """High contribution, low error -> INCENTIVE."""
        result = determine_quadrant(
            contribution_score=80,
            error_score=5,
            quad_x=50,
            quad_y=20,
        )
        assert result == "INCENTIVE"

    def test_improve_quadrant(self):
        """High contribution, high error -> IMPROVE."""
        result = determine_quadrant(
            contribution_score=80,
            error_score=30,
            quad_x=50,
            quad_y=20,
        )
        assert result == "IMPROVE"

    def test_encourage_quadrant(self):
        """Low contribution, low error -> ENCOURAGE."""
        result = determine_quadrant(
            contribution_score=20,
            error_score=5,
            quad_x=50,
            quad_y=20,
        )
        assert result == "ENCOURAGE"

    def test_replace_quadrant(self):
        """Low contribution, high error -> REPLACE."""
        result = determine_quadrant(
            contribution_score=20,
            error_score=30,
            quad_x=50,
            quad_y=20,
        )
        assert result == "REPLACE"

    def test_boundary_x_exact_median(self):
        """Contribution score equals median (>= check) -> high contribution."""
        result = determine_quadrant(
            contribution_score=50,
            error_score=5,
            quad_x=50,
            quad_y=20,
        )
        assert result == "INCENTIVE"

    def test_boundary_y_exact_median(self):
        """Error score equals median (< check) -> not low error, treated as high."""
        result = determine_quadrant(
            contribution_score=80,
            error_score=20,
            quad_x=50,
            quad_y=20,
        )
        assert result == "IMPROVE"


class TestCalculateQuadrantsEmpty:
    """Tests for calculate_quadrants() — empty list."""

    def test_empty_list(self):
        """Empty list returns empty result with zero medians."""
        points, quad_x, quad_y = calculate_quadrants([])
        assert points == []
        assert quad_x == 0.0
        assert quad_y == 0.0


class TestCalculateQuadrantsSingleContributor:
    """Tests for calculate_quadrants() — single contributor."""

    def test_single_contributor(self):
        """Single contributor is always placed in ENCOURAGE quadrant."""
        metrics = [
            {
                "login": "solo-dev",
                "avatar_url": "https://example.com/avatar.png",
                "contribution_score": 100,
                "error_score": 0,
                "merged_pr_count": 10,
                "is_community": True,
            }
        ]
        points, quad_x, quad_y = calculate_quadrants(metrics)

        assert len(points) == 1
        p = points[0]
        assert isinstance(p, QuadrantPoint)
        assert p.login == "solo-dev"
        assert p.avatar_url == "https://example.com/avatar.png"
        assert p.contribution_score == 100
        assert p.error_score == 0
        assert p.quadrant == "ENCOURAGE"
        assert p.merged_pr_count == 10
        assert p.is_community is True
        assert quad_x == 100.0
        assert quad_y == 0.0


class TestCalculateQuadrantsBasicFourQuadrants:
    """Tests for calculate_quadrants() — four contributors in four quadrants."""

    def test_basic_four_quadrants(self):
        """Four contributors with distinct scores land in all four quadrants."""
        metrics = [
            {"login": "user-a", "contribution_score": 90, "error_score": 5},
            {"login": "user-b", "contribution_score": 90, "error_score": 35},
            {"login": "user-c", "contribution_score": 10, "error_score": 5},
            {"login": "user-d", "contribution_score": 10, "error_score": 35},
        ]
        points, quad_x, quad_y = calculate_quadrants(metrics)

        assert len(points) == 4

        # Contribution scores: [90, 90, 10, 10] -> median = 50.0
        # Error scores: [5, 35, 5, 35] -> median = 20.0
        assert quad_x == 50.0
        assert quad_y == 20.0

        quad_map = {p.login: p.quadrant for p in points}
        assert quad_map["user-a"] == "INCENTIVE"
        assert quad_map["user-b"] == "IMPROVE"
        assert quad_map["user-c"] == "ENCOURAGE"
        assert quad_map["user-d"] == "REPLACE"


class TestCalculateQuadrantsMedianCalculation:
    """Tests for calculate_quadrants() — median calculation accuracy."""

    def test_median_calculation(self):
        """Median is computed correctly for odd and even number of contributors."""
        # Odd count: 5 contributors
        metrics_odd = [
            {"login": f"user-{i}", "contribution_score": float(i + 1), "error_score": float(i + 1)}
            for i in range(5)
        ]
        # contribution_scores = [1, 2, 3, 4, 5] -> median = 3.0
        # error_scores = [1, 2, 3, 4, 5] -> median = 3.0
        points_odd, quad_x_odd, quad_y_odd = calculate_quadrants(metrics_odd)
        assert quad_x_odd == 3.0
        assert quad_y_odd == 3.0
        assert len(points_odd) == 5

        # Even count: 4 contributors
        metrics_even = [
            {"login": f"even-{i}", "contribution_score": float(i * 10), "error_score": float(i * 10)}
            for i in range(4)
        ]
        # contribution_scores = [0, 10, 20, 30] -> median = 15.0
        # error_scores = [0, 10, 20, 30] -> median = 15.0
        points_even, quad_x_even, quad_y_even = calculate_quadrants(metrics_even)
        assert quad_x_even == 15.0
        assert quad_y_even == 15.0
        assert len(points_even) == 4


class TestCalculateQuadrantsMultipleContributors:
    """Tests for calculate_quadrants() — multiple contributors distribution."""

    def test_multiple_contributors_distribution(self):
        """Multiple contributors are distributed across quadrants correctly."""
        metrics = [
            # INCENTIVE: high contribution, low error
            {"login": "dev1", "contribution_score": 80, "error_score": 5, "merged_pr_count": 15, "is_community": True},
            {"login": "dev2", "contribution_score": 70, "error_score": 3, "merged_pr_count": 8, "is_community": True},
            # IMPROVE: high contribution, high error
            {"login": "dev3", "contribution_score": 75, "error_score": 30, "merged_pr_count": 12, "is_community": False},
            {"login": "dev4", "contribution_score": 60, "error_score": 25, "merged_pr_count": 5, "is_community": False},
            # ENCOURAGE: low contribution, low error
            {"login": "dev5", "contribution_score": 20, "error_score": 2, "merged_pr_count": 2, "is_community": True},
            {"login": "dev6", "contribution_score": 15, "error_score": 1, "merged_pr_count": 1, "is_community": True},
            # REPLACE: low contribution, high error
            {"login": "dev7", "contribution_score": 10, "error_score": 40, "merged_pr_count": 0, "is_community": False},
            {"login": "dev8", "contribution_score": 5, "error_score": 35, "merged_pr_count": 1, "is_community": False},
        ]

        points, quad_x, quad_y = calculate_quadrants(metrics)

        assert len(points) == 8

        quad_map = {p.login: p.quadrant for p in points}
        assert quad_map["dev1"] == "INCENTIVE"
        assert quad_map["dev2"] == "INCENTIVE"
        assert quad_map["dev3"] == "IMPROVE"
        assert quad_map["dev4"] == "IMPROVE"
        assert quad_map["dev5"] == "ENCOURAGE"
        assert quad_map["dev6"] == "ENCOURAGE"
        assert quad_map["dev7"] == "REPLACE"
        assert quad_map["dev8"] == "REPLACE"

        # Verify QuadrantPoint fields are populated correctly
        dev1_point = [p for p in points if p.login == "dev1"][0]
        assert dev1_point.merged_pr_count == 15
        assert dev1_point.is_community is True

        dev3_point = [p for p in points if p.login == "dev3"][0]
        assert dev3_point.merged_pr_count == 12
        assert dev3_point.is_community is False


class TestCalculateQuadrantsZeroScores:
    """Tests for calculate_quadrants() — zero score edge case."""

    def test_zero_scores(self):
        """All zero scores produce valid quadrant assignment."""
        metrics = [
            {"login": "zero-user", "contribution_score": 0, "error_score": 0},
        ]
        points, quad_x, quad_y = calculate_quadrants(metrics)

        assert len(points) == 1
        assert points[0].quadrant == "ENCOURAGE"
        assert quad_x == 0.0
        assert quad_y == 0.0

        # Two contributors with zero scores
        metrics_two = [
            {"login": "zero-a", "contribution_score": 0, "error_score": 0},
            {"login": "zero-b", "contribution_score": 0, "error_score": 0},
        ]
        points_two, qx_two, qy_two = calculate_quadrants(metrics_two)

        assert len(points_two) == 2
        assert qx_two == 0.0
        assert qy_two == 0.0


class TestCalculateQuadrantsLargeContributionDifference:
    """Tests for calculate_quadrants() — large contribution score differences."""

    def test_large_contribution_difference(self):
        """Large score gaps still produce correct quadrant assignments."""
        metrics = [
            {"login": "star-contributor", "contribution_score": 10000, "error_score": 1},
            {"login": "minor-contributor", "contribution_score": 1, "error_score": 100},
        ]
        points, quad_x, quad_y = calculate_quadrants(metrics)

        assert len(points) == 2

        # median of [10000, 1] = 5000.5
        # median of [1, 100] = 50.5
        assert quad_x == 5000.5
        assert quad_y == 50.5

        quad_map = {p.login: p.quadrant for p in points}
        assert quad_map["star-contributor"] == "INCENTIVE"
        assert quad_map["minor-contributor"] == "REPLACE"


class TestCalculateQuadrantsAllSameScores:
    """Tests for calculate_quadrants() — all contributors have identical scores."""

    def test_all_same_scores(self):
        """All same scores -> all >= median (X) and not < median (Y) -> IMPROVE."""
        metrics = [
            {"login": f"same-{i}", "contribution_score": 50, "error_score": 30}
            for i in range(5)
        ]
        points, quad_x, quad_y = calculate_quadrants(metrics)

        assert len(points) == 5
        assert quad_x == 50.0
        assert quad_y == 30.0

        # All contribution_score >= median (50 >= 50 = True) -> high contribution
        # All error_score < median (30 < 30 = False) -> not low error
        # Result: all should be IMPROVE
        for p in points:
            assert p.quadrant == "IMPROVE"


class TestCalculateQuadrantsOptionalFields:
    """Tests for calculate_quadrants() — optional field defaults."""

    def test_missing_optional_fields(self):
        """Missing optional fields use correct defaults."""
        metrics = [
            {"login": "minimal", "contribution_score": 80, "error_score": 5},
            {"login": "minimal2", "contribution_score": 20, "error_score": 30},
        ]
        points, _, _ = calculate_quadrants(metrics)

        assert len(points) == 2

        for p in points:
            assert p.avatar_url is None
            assert p.merged_pr_count == 0
            assert p.is_community is False
