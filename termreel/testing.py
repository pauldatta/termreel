"""
High-speed parallel / asynchronous test runner for TermReel.
Discovers and executes unittest suites concurrently across worker threads or processes.
"""

import concurrent.futures
import os
import re
import sys
import time
import unittest
from typing import List, Tuple, Optional


def run_single_test(test_case: unittest.TestCase) -> Tuple[unittest.TestCase, unittest.TestResult, float]:
    """Execute an individual TestCase instance and measure runtime."""
    res = unittest.TestResult()
    start = time.time()
    test_case.run(res)
    duration = time.time() - start
    return test_case, res, duration


def discover_all_tests(start_dir: str = "tests", test_filter: Optional[str] = None, fast: bool = False) -> List[unittest.TestCase]:
    """Recursively discover and flatten all TestCase instances, applying optional filters."""
    suite = unittest.defaultTestLoader.discover(start_dir)
    tests = []

    def _flatten(node):
        if isinstance(node, unittest.TestSuite):
            for child in node:
                _flatten(child)
        elif isinstance(node, unittest.TestCase):
            name = str(node)
            if fast and ("pure_interactive" in name or "slow" in name):
                return
            if test_filter:
                if not re.search(test_filter, name, re.IGNORECASE):
                    return
            tests.append(node)

    _flatten(suite)
    # Sort so potentially slow integration tests start earliest in the worker pool
    tests.sort(key=lambda t: 0 if ("integration" in str(t).lower() or "audit" in str(t).lower()) else 1)
    return tests


def run_parallel_tests(
    start_dir: str = "tests",
    max_workers: Optional[int] = None,
    verbose: bool = True,
    test_filter: Optional[str] = None,
    fast: bool = False,
    show_durations: int = 5,
) -> int:
    """Run all discovered tests concurrently with aggregated results reporting."""
    if max_workers is None or max_workers <= 0:
        max_workers = min(16, os.cpu_count() or 8)

    tests = discover_all_tests(start_dir, test_filter=test_filter, fast=fast)
    total_tests = len(tests)
    if total_tests == 0:
        print("No tests found matching filter criteria.")
        return 0

    if verbose:
        filter_str = f" [filter: '{test_filter}']" if test_filter else ""
        fast_str = " [fast mode: skipping slow E2E]" if fast else ""
        print(f"🚀 Running {total_tests} tests concurrently across {max_workers} async workers{filter_str}{fast_str}...\n")

    start_time = time.time()
    passed = 0
    failures: List[Tuple[unittest.TestCase, str]] = []
    errors: List[Tuple[unittest.TestCase, str]] = []
    durations: List[Tuple[str, float]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(run_single_test, t): t for t in tests}
        for future in concurrent.futures.as_completed(future_map):
            test, res, duration = future.result()
            durations.append((str(test), duration))
            if res.wasSuccessful():
                passed += 1
                if verbose:
                    sys.stdout.write(".")
                    sys.stdout.flush()
            elif res.failures:
                failures.append((test, res.failures[0][1]))
                if verbose:
                    sys.stdout.write("F")
                    sys.stdout.flush()
            elif res.errors:
                errors.append((test, res.errors[0][1]))
                if verbose:
                    sys.stdout.write("E")
                    sys.stdout.flush()

    total_time = time.time() - start_time
    if verbose:
        print("\n")

    if failures:
        print("=" * 70)
        print("FAILURES:")
        for test, err in failures:
            print(f"\nFAIL: {test}\n{'-' * 70}\n{err}")

    if errors:
        print("=" * 70)
        print("ERRORS:")
        for test, err in errors:
            print(f"\nERROR: {test}\n{'-' * 70}\n{err}")

    durations.sort(key=lambda x: x[1], reverse=True)
    if show_durations > 0 and durations and durations[0][1] > 1.0:
        print("\n⏱️  Slowest tests:")
        for name, dur in durations[:show_durations]:
            print(f"   {dur:.2f}s - {name}")

    print("=" * 70)
    print(f"Ran {total_tests} tests in {total_time:.2f}s ({max_workers} workers)")
    if failures or errors:
        print(f"❌ FAILED (failures={len(failures)}, errors={len(errors)}, passed={passed})")
        sys.stdout.flush()
        return 1
    else:
        print(f"✅ OK ({passed} passed, 0 failures, 0 errors)")
        sys.stdout.flush()
        return 0


if __name__ == "__main__":
    workers = None
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        workers = int(sys.argv[1])
    sys.exit(run_parallel_tests(max_workers=workers))

