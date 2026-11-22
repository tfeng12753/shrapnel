import os, pytest, sys
from sitecustomize import write


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    os.environ["BR_ITEM"] = item.nodeid
    try:
        yield
    finally:
        os.environ.pop("BR_ITEM", None)


def pytest_runtest_logreport(report):
    if report.when == "call":
        write("status", [report.nodeid, report.outcome])
    elif report.failed or report.skipped:
        write("uncertain", report.nodeid)
