from __future__ import annotations

from src.services.job_guard import job_guard


def test_job_guard_allows_single_runner() -> None:
    with job_guard("unit_job") as acquired:
        assert acquired is True


def test_job_guard_skips_parallel_runner() -> None:
    with job_guard("parallel_job") as first:
        with job_guard("parallel_job") as second:
            assert first is True
            assert second is False


def test_job_guard_releases_after_exception() -> None:
    try:
        with job_guard("exception_job") as acquired:
            assert acquired is True
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with job_guard("exception_job") as acquired_again:
        assert acquired_again is True
