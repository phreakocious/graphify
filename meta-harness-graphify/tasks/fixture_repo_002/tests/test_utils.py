from mylib.utils import compute_score


def test_compute_score_returns_median_skewed_low():
    # [1, 2, 100]: median=2.0, mean≈34.33
    assert compute_score([1.0, 2.0, 100.0]) == 2.0


def test_compute_score_returns_median_skewed_high():
    # [1, 1, 1, 5]: median=1.0, mean=2.0
    assert compute_score([1.0, 1.0, 1.0, 5.0]) == 1.0


def test_compute_score_returns_median_even_length_avg_of_middle():
    # [1, 2, 8, 100]: median=(2+8)/2=5.0, mean≈27.75
    assert compute_score([1.0, 2.0, 8.0, 100.0]) == 5.0


def test_compute_score_empty_list_returns_zero():
    assert compute_score([]) == 0.0
