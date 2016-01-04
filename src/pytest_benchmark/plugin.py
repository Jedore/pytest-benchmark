from __future__ import division
from __future__ import print_function

import argparse
import operator
import platform
import sys
import traceback
from collections import defaultdict
from datetime import datetime

import pytest

from . import __version__
from .fixture import BenchmarkFixture
from .session import BenchmarkSession
from .timers import default_timer
from .utils import NameWrapper
from .utils import format_dict
from .utils import get_commit_info
from .utils import get_current_time
from .utils import get_tag
from .utils import parse_columns
from .utils import parse_compare_fail
from .utils import parse_rounds
from .utils import parse_save
from .utils import parse_seconds
from .utils import parse_sort
from .utils import parse_timer
from .utils import parse_warmup

try:
    import statistics
except (ImportError, SyntaxError):
    statistics = False
    statistics_error = traceback.format_exc()
else:
    from .stats import Stats



class FixtureAlreadyUsed(Exception):
    pass


def pytest_report_header(config):
    bs = config._benchmarksession

    return ("benchmark: {version} (defaults:"
            " timer={timer}"
            " disable_gc={0[disable_gc]}"
            " min_rounds={0[min_rounds]}"
            " min_time={0[min_time]}"
            " max_time={0[max_time]}"
            " calibration_precision={0[calibration_precision]}"
            " warmup={0[warmup]}"
            " warmup_iterations={0[warmup_iterations]}"
            ")").format(
        bs.options,
        version=__version__,
        timer=bs.options.get("timer"),
    )


def add_display_options(addoption):
    addoption(
        "--benchmark-sort",
        metavar="COL", type=parse_sort, default="min",
        help="Column to sort on. Can be one of: 'min', 'max', 'mean', 'stddev', "
             "'name', 'fullname'. Default: %(default)r"
    )
    addoption(
        "--benchmark-group-by",
        metavar="LABEL", default="group",
        help="How to group tests. Can be one of: 'group', 'name', 'fullname', 'func', 'fullfunc', "
             "'param' or 'param:NAME', where NAME is the name passed to @pytest.parametrize."
             " Default: %(default)r"
    )
    addoption(
        "--benchmark-columns",
        metavar="LABELS", type=parse_columns,
        default="min, max, mean, stddev, median, iqr, outliers, rounds, iterations",
        help='Comma-separated list of columns to show in the result table. Default: %(default)r'
    )
    prefix = "benchmark_%s" % get_current_time()
    addoption(
        "--benchmark-histogram",
        action='append', metavar="FILENAME-PREFIX", nargs="?", default=[], const=prefix,
        help="Plot graphs of min/max/avg/stddev over time in FILENAME-PREFIX-test_name.svg. If FILENAME-PREFIX contains"
             " slashes ('/') then directories will be created. Default: %r" % prefix
    )


def add_global_options(addoption):
    addoption(
        "--benchmark-storage",
        metavar="STORAGE-PATH", default="./.benchmarks",
        help="Specify a different path to store the runs (when --benchmark-save or --benchmark-autosave are used). "
             "Default: %(default)r",
    )
    addoption(
        "--benchmark-verbose",
        action="store_true", default=False,
        help="Dump diagnostic and progress information."
    )


def pytest_addoption(parser):
    group = parser.getgroup("benchmark")
    group.addoption(
        "--benchmark-min-time",
        metavar="SECONDS", type=parse_seconds, default="0.000005",
        help="Minimum time per round in seconds. Default: %(default)r"
    )
    group.addoption(
        "--benchmark-max-time",
        metavar="SECONDS", type=parse_seconds, default="1.0",
        help="Maximum run time per test - it will be repeated until this total time is reached. It may be "
             "exceeded if test function is very slow or --benchmark-min-rounds is large (it takes precedence). "
             "Default: %(default)r"
    )
    group.addoption(
        "--benchmark-min-rounds",
        metavar="NUM", type=parse_rounds, default=5,
        help="Minimum rounds, even if total time would exceed `--max-time`. Default: %(default)r"
    )
    group.addoption(
        "--benchmark-timer",
        metavar="FUNC", type=parse_timer, default=str(NameWrapper(default_timer)),
        help="Timer to use when measuring time. Default: %(default)r"
    )
    group.addoption(
        "--benchmark-calibration-precision",
        metavar="NUM", type=int, default=10,
        help="Precision to use when calibrating number of iterations. Precision of 10 will make the timer look 10 times"
             " more accurate, at a cost of less precise measure of deviations. Default: %(default)r"
    )
    group.addoption(
        "--benchmark-warmup",
        metavar="KIND", nargs="?", default=parse_warmup("auto"), type=parse_warmup,
        help="Activates warmup. Will run the test function up to number of times in the calibration phase. "
             "See `--benchmark-warmup-iterations`. Note: Even the warmup phase obeys --benchmark-max-time. "
             "Available KIND: 'auto', 'off', 'on'. Default: 'auto' (automatically activate on PyPy)."
    )
    group.addoption(
        "--benchmark-warmup-iterations",
        metavar="NUM", type=int, default=100000,
        help="Max number of iterations to run in the warmup phase. Default: %(default)r"
    )
    group.addoption(
        "--benchmark-disable-gc",
        action="store_true", default=False,
        help="Disable GC during benchmarks."
    )
    group.addoption(
        "--benchmark-skip",
        action="store_true", default=False,
        help="Skip running any tests that contain benchmarks."
    )
    group.addoption(
        "--benchmark-disable",
        action="store_true", default=False,
        help="Disable benchmarks. Benchmarked functions are only ran once and no stats are reported. Use this is you "
             "want to run the test but don't do any benchmarking."
    )
    group.addoption(
        "--benchmark-only",
        action="store_true", default=False,
        help="Only run benchmarks."
    )
    group.addoption(
        "--benchmark-save",
        metavar="NAME", type=parse_save,
        help="Save the current run into 'STORAGE-PATH/counter_NAME.json'."
    )
    tag = get_tag()
    group.addoption(
        "--benchmark-autosave",
        action='store_const', const=tag,
        help="Autosave the current run into 'STORAGE-PATH/counter_%s.json" % tag,
    )
    group.addoption(
        "--benchmark-save-data",
        action="store_true",
        help="Use this to make --benchmark-save and --benchmark-autosave include all the timing data,"
             " not just the stats.",
    )
    group.addoption(
        "--benchmark-json",
        metavar="PATH", type=argparse.FileType('wb'),
        help="Dump a JSON report into PATH. "
             "Note that this will include the complete data (all the timings, not just the stats)."
    )
    group.addoption(
        "--benchmark-compare",
        metavar="NUM", nargs="?", default=[], const=True,
        help="Compare the current run against run NUM or the latest saved run if unspecified."
    )
    group.addoption(
        "--benchmark-compare-fail",
        metavar="EXPR", nargs="+", type=parse_compare_fail,
        help="Fail test if performance regresses according to given EXPR"
             " (eg: min:5%% or mean:0.001 for number of seconds). Can be used multiple times."
    )
    add_global_options(group.addoption)
    add_display_options(group.addoption)


def pytest_addhooks(pluginmanager):
    from . import hookspec

    method = getattr(pluginmanager, "add_hookspecs", None)
    if method is None:
        method = pluginmanager.addhooks
    method(hookspec)




def pytest_benchmark_compare_machine_info(config, benchmarksession, machine_info, compared_benchmark):
    if compared_benchmark["machine_info"] != machine_info:
        benchmarksession.logger.warn(
            "BENCHMARK-C6",
            "Benchmark machine_info is different. Current: %s VS saved: %s." % (
                format_dict(machine_info),
                format_dict(compared_benchmark["machine_info"]),
            ),
            fslocation=benchmarksession.storage.location
        )

if hasattr(pytest, 'hookimpl'):
    _hookwrapper = pytest.hookimpl(hookwrapper=True)
else:
    _hookwrapper = pytest.mark.hookwrapper


@_hookwrapper
def pytest_runtest_call(item):
    bs = item.config._benchmarksession
    fixure = hasattr(item, "funcargs") and item.funcargs.get("benchmark")
    if isinstance(fixure, BenchmarkFixture):
        if bs.skip:
            pytest.skip("Skipping benchmark (--benchmark-skip active).")
        else:
            yield
    else:
        if bs.only:
            pytest.skip("Skipping non-benchmark (--benchmark-only active).")
        else:
            yield


def pytest_benchmark_group_stats(config, benchmarks, group_by):
    groups = defaultdict(list)
    for bench in benchmarks:
        if group_by == "group":
            groups[bench["group"]].append(bench)
        elif group_by == "name":
            groups[bench["name"]].append(bench)
        elif group_by == "func":
            groups[bench["name"].split("[")[0]].append(bench)
        elif group_by == "fullfunc":
            groups[bench["fullname"].split("[")[0]].append(bench)
        elif group_by == "fullname":
            groups[bench["fullname"]].append(bench)
        elif group_by == "param":
            groups[bench["param"]].append(bench)
        elif group_by.startswith("param:"):
            param_name = group_by[len("param:"):]
            param_value = bench["params"][param_name]
            groups[param_value].append(bench)
        else:
            raise NotImplementedError("Unsupported grouping %r." % group_by)
    #
    for grouped_benchmarks in groups.values():
        grouped_benchmarks.sort(key=operator.itemgetter("fullname" if "full" in group_by else "name"))
    return sorted(groups.items(), key=lambda pair: pair[0] or "")


@_hookwrapper
def pytest_sessionfinish(session, exitstatus):
    session.config._benchmarksession.finish()
    yield


def pytest_terminal_summary(terminalreporter):
    try:
        terminalreporter.config._benchmarksession.display(terminalreporter)
    except PerformanceRegression:
        raise
    except Exception:
        terminalreporter.config._benchmarksession.logger.error("\n%s" % traceback.format_exc())
        raise


def pytest_benchmark_generate_machine_info():
    python_implementation = platform.python_implementation()
    python_implementation_version = platform.python_version()
    if python_implementation == 'PyPy':
        python_implementation_version = '%d.%d.%d' % sys.pypy_version_info[:3]
        if sys.pypy_version_info.releaselevel != 'final':
            python_implementation_version += '-%s%d' % sys.pypy_version_info[3:]
    return {
        "node": platform.node(),
        "processor": platform.processor(),
        "machine": platform.machine(),
        "python_compiler": platform.python_compiler(),
        "python_implementation": python_implementation,
        "python_implementation_version": python_implementation_version,
        "python_version": platform.python_version(),
        "python_build": platform.python_build(),
        "release": platform.release(),
        "system": platform.system()
    }


def pytest_benchmark_generate_commit_info(config):
    return get_commit_info()


def pytest_benchmark_generate_json(config, benchmarks, include_data, machine_info, commit_info):
    benchmarks_json = []
    output_json = {
        "machine_info": machine_info,
        "commit_info": commit_info,
        "benchmarks": benchmarks_json,
        "datetime": datetime.utcnow().isoformat(),
        "version": __version__,
    }
    for bench in benchmarks:
        if not bench.has_error:
            benchmarks_json.append(bench.as_dict(include_data=include_data))
    return output_json


@pytest.fixture(scope="function")
def benchmark(request):
    bs = request.config._benchmarksession

    if bs.skip:
        pytest.skip("Benchmarks are skipped (--benchmark-skip was used).")
    else:
        node = request.node
        marker = node.get_marker("benchmark")
        options = marker.kwargs if marker else {}
        if "timer" in options:
            options["timer"] = NameWrapper(options["timer"])
        fixture = BenchmarkFixture(
            node,
            add_stats=bs.benchmarks.append,
            logger=bs.logger,
            warner=request.node.warn,
            disable=bs.disable,
            **dict(bs.options, **options)
        )
        request.addfinalizer(fixture._cleanup)
        return fixture


@pytest.fixture(scope="function")
def benchmark_weave(benchmark):
    return benchmark.weave


def pytest_runtest_setup(item):
    marker = item.get_marker("benchmark")
    if marker:
        if marker.args:
            raise ValueError("benchmark mark can't have positional arguments.")
        for name in marker.kwargs:
            if name not in (
                    "max_time", "min_rounds", "min_time", "timer", "group", "disable_gc", "warmup",
                    "warmup_iterations", "calibration_precision"):
                raise ValueError("benchmark mark can't have %r keyword argument." % name)


@pytest.mark.trylast  # force the other plugins to initialise, fixes issue with capture not being properly initialised
def pytest_configure(config):
    config.addinivalue_line("markers", "benchmark: mark a test with custom benchmark settings.")
    config._benchmarksession = BenchmarkSession(config)
    config.pluginmanager.register(config._benchmarksession, "pytest-benchmark")
