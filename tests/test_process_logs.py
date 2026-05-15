from runtime.processes import _LOG_FILES


def test_kinopub_log_is_routable() -> None:
    assert _LOG_FILES["kinopub"] == "sync_kinopub_log.txt"
