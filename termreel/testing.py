"""
High-speed parallel / asynchronous test runner for TermReel.
Discovers and executes unittest suites concurrently across worker threads or processes.
"""

import concurrent.futures
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


def discover_all_tests(start_dir: str = "tests") -> List[unittest.TestCase]:
    """Recursively discover and flatten all TestCase instances in the test directory."""
    suite = unittest.defaultTestLoader.discover(start_dir)
    tests = []

    def _flatten(node):
        if isinstance(node, unittest.TestSuite):
            for child in node:
                _flatten(child)
        elif isinstance(node, unittest.TestCase):
            tests.append(node)

    _flatten(suite)
    return tests


def run_parallel_tests(
    start_dir: str = "tests",
    max_workers: int = 8,
    verbose: bool = True,
) -> int:
    """Run all discovered tests concurrently with aggregated results reporting."""
    tests = discover_all_tests(start_dir)
    total_tests = len(tests)
    if total_tests == 0:
        print("No tests found.")
        return 0

    if verbose:
        print(f"🚀 Running {total_tests} tests concurrently across {max_workers} async workers...\n")

    start_time = time.time()
    passed = 0
    failures: List[Tuple[unittest.TestCase, str]] = []
    errors: List[Tuple[unittest.TestCase, str]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(run_single_test, t): t for t in tests}
        for future in concurrent.futures.as_completed(future_map):
            test, res, duration = future.result()
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

    print("=" * 70)
    print(f"Ran {total_tests} tests in {total_time:.2f}s ({max_workers} workers)")
    if failures or errors:
        print(f"❌ FAILED (failures={len(failures)}, errors={len(errors)}, passed={passed})")
        return 1
    else:
        print(f"✅ OK ({passed} passed, 0 failures, 0 errors)")
        return 0


if __name__ == "__main__":
    workers = 8
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        workers = int(sys.argv[1])
    sys.exit(run_parallel_tests(max_workers=workers))
