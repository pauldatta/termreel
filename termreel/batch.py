"""
Concurrent batch execution engine and report generator for TermReel.
Runs scenario recordings in parallel with worker thread pools and aggregates results.
"""

import concurrent.futures
from dataclasses import dataclass, asdict, field
import glob
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import List, Optional, Dict, Any, Union

from termreel.scenario.schema import ScenarioManifest
from termreel.scenario.runner import ScenarioRunner


@dataclass
class BatchScenarioResult:
    """Execution result for an individual scenario within a batch run."""
    file: str
    status: str  # "pass" or "fail"
    duration: float
    frames: int
    output: str
    poster: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BatchReport:
    """Aggregated batch execution report."""
    total: int
    passed: int
    failed: int
    elapsed_seconds: float
    scenarios: List[BatchScenarioResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "scenarios": [s.to_dict() for s in self.scenarios],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_markdown(self) -> str:
        lines = [
            "# 🎬 TermReel Batch Execution Report",
            "",
            f"- **Total Scenarios**: {self.total}",
            f"- **Passed**: {self.passed}",
            f"- **Failed**: {self.failed}",
            f"- **Total Elapsed Time**: {self.elapsed_seconds:.2f}s",
            "",
            "| Status | Scenario | Duration | Frames | Output Video | Poster | Error |",
            "| :---: | :--- | :---: | :---: | :--- | :--- | :--- |",
        ]
        for s in self.scenarios:
            status_badge = "✅ PASS" if s.status == "pass" else "❌ FAIL"
            dur_str = f"{s.duration:.2f}s"
            poster_str = f"`{os.path.basename(s.poster)}`" if s.poster else "-"
            err_str = f"`{s.error}`" if s.error else "-"
            lines.append(
                f"| {status_badge} | `{s.file}` | {dur_str} | {s.frames} | `{s.output}` | {poster_str} | {err_str} |"
            )
        lines.append("")
        return "\n".join(lines)

    def save(self, path: str):
        """Save report to disk as JSON or Markdown based on file extension."""
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        content = self.to_json() if path.endswith(".json") else self.to_markdown()
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


class BatchOrchestrator:
    """
    Orchestrates high-concurrency batch execution of TermReel scenario manifests.
    Executes scenarios in parallel using ThreadPoolExecutor and renders clean terminal progress.
    """

    def __init__(
        self,
        scenarios: Union[str, List[str]],
        concurrency: int = 4,
        output_dir: Optional[str] = None,
        generate_posters: bool = True,
        poster_time: float = 0.5,
        report_file: Optional[str] = None,
        theme_override: Optional[str] = None,
        fps_override: Optional[int] = None,
        quiet: bool = False,
    ):
        if isinstance(scenarios, str):
            self.scenario_patterns = [scenarios]
        else:
            self.scenario_patterns = list(scenarios)

        self.concurrency = max(1, int(concurrency))
        self.output_dir = output_dir
        self.generate_posters = generate_posters
        self.poster_time = float(poster_time)
        self.report_file = report_file
        self.theme_override = theme_override
        self.fps_override = fps_override
        self.quiet = quiet

        self._lock = threading.Lock()
        self._completed_count = 0

    def resolve_scenario_files(self) -> List[str]:
        """Expand glob patterns and resolve unique scenario YAML manifest files."""
        matched: List[str] = []
        for pat in self.scenario_patterns:
            if any(char in pat for char in ("*", "?", "[")):
                globbed = glob.glob(pat, recursive=True)
                if globbed:
                    matched.extend(sorted(globbed))
                else:
                    matched.append(pat)
            else:
                matched.append(pat)

        # Deduplicate preserving order
        seen = set()
        unique_files = []
        for path in matched:
            abs_p = os.path.abspath(path)
            if abs_p not in seen:
                seen.add(abs_p)
                unique_files.append(path)

        return unique_files

    def _extract_poster(self, video_path: str, poster_path: str) -> bool:
        """Extract a single high-quality PNG poster thumbnail from the rendered video."""
        if not os.path.exists(video_path):
            return False
        os.makedirs(os.path.dirname(os.path.abspath(poster_path)), exist_ok=True)
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", str(self.poster_time),
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            poster_path,
        ]
        res = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True)
        return res.returncode == 0 and os.path.exists(poster_path)

    def _run_single_scenario(self, scenario_file: str) -> BatchScenarioResult:
        """Execute an individual scenario manifest and record output metadata."""
        t0 = time.time()

        if not os.path.exists(scenario_file):
            return BatchScenarioResult(
                file=scenario_file,
                status="fail",
                duration=0.0,
                frames=0,
                output="",
                poster=None,
                error=f"Scenario file not found: {scenario_file}",
            )

        stem = os.path.splitext(os.path.basename(scenario_file))[0]

        try:
            manifest = ScenarioManifest.from_yaml_file(scenario_file)

            # Determine target output path
            if self.output_dir:
                os.makedirs(self.output_dir, exist_ok=True)
                out_path = os.path.abspath(os.path.join(self.output_dir, f"{stem}.mp4"))
            elif manifest.metadata.output:
                out_path = os.path.abspath(manifest.metadata.output)
            else:
                out_path = os.path.abspath(f"output/{stem}.mp4")

            # Determine target poster path
            poster_path = None
            if self.generate_posters:
                if self.output_dir:
                    poster_path = os.path.abspath(os.path.join(self.output_dir, f"{stem}.png"))
                elif manifest.metadata.poster_output:
                    poster_path = os.path.abspath(manifest.metadata.poster_output)
                else:
                    poster_path = os.path.splitext(out_path)[0] + ".png"

            # Execute scenario runner with worker-isolated logging
            runner = ScenarioRunner(
                manifest=manifest,
                output_override=out_path,
                fps_override=self.fps_override,
                theme_override=self.theme_override,
                verbose=False,
            )
            report = runner.run()

            duration = round(time.time() - t0, 2)

            if report.status == "pass":
                actual_poster = None
                if self.generate_posters and poster_path:
                    if self._extract_poster(out_path, poster_path):
                        actual_poster = poster_path

                return BatchScenarioResult(
                    file=scenario_file,
                    status="pass",
                    duration=duration,
                    frames=report.frame_count,
                    output=out_path,
                    poster=actual_poster,
                    error=None,
                )
            else:
                return BatchScenarioResult(
                    file=scenario_file,
                    status="fail",
                    duration=duration,
                    frames=report.frame_count,
                    output=out_path,
                    poster=None,
                    error=report.error_message or "Scenario failed without error details",
                )

        except Exception as e:
            duration = round(time.time() - t0, 2)
            out_fallback = (
                os.path.abspath(os.path.join(self.output_dir, f"{stem}.mp4"))
                if self.output_dir
                else os.path.abspath(f"output/{stem}.mp4")
            )
            return BatchScenarioResult(
                file=scenario_file,
                status="fail",
                duration=duration,
                frames=0,
                output=out_fallback,
                poster=None,
                error=str(e),
            )

    def run(self) -> BatchReport:
        """Execute all resolved scenarios concurrently and generate aggregate report."""
        files = self.resolve_scenario_files()
        total_scenarios = len(files)
        start_time = time.time()

        if total_scenarios == 0:
            if not self.quiet:
                print("No scenario files matched the specified patterns.")
            report = BatchReport(total=0, passed=0, failed=0, elapsed_seconds=0.0, scenarios=[])
            if self.report_file:
                report.save(self.report_file)
            return report

        if not self.quiet:
            print(f"🚀 Launching batch execution for {total_scenarios} scenario(s) across {self.concurrency} worker(s)...\n")

        results: List[BatchScenarioResult] = []
        self._completed_count = 0

        # Execute scenarios across thread pool
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            future_to_file = {executor.submit(self._run_single_scenario, f): f for f in files}

            for future in concurrent.futures.as_completed(future_to_file):
                res = future.result()
                results.append(res)

                with self._lock:
                    self._completed_count += 1
                    idx = self._completed_count

                if not self.quiet:
                    if res.status == "pass":
                        print(f"[{idx}/{total_scenarios}] ✅ {res.file} -> {res.output} ({res.duration:.1f}s)")
                    else:
                        print(f"[{idx}/{total_scenarios}] ❌ {res.file} -> FAILED: {res.error} ({res.duration:.1f}s)")

        elapsed = time.time() - start_time
        passed_count = sum(1 for r in results if r.status == "pass")
        failed_count = total_scenarios - passed_count

        # Maintain original scenario ordering in final report
        file_order = {f: i for i, f in enumerate(files)}
        sorted_results = sorted(results, key=lambda r: file_order.get(r.file, 999999))

        report = BatchReport(
            total=total_scenarios,
            passed=passed_count,
            failed=failed_count,
            elapsed_seconds=elapsed,
            scenarios=sorted_results,
        )

        if not self.quiet:
            print("\n" + "=" * 70)
            print(f"✨ Batch finished in {elapsed:.2f}s: {passed_count}/{total_scenarios} passed, {failed_count} failed.")
            print("=" * 70)

        if self.report_file:
            report.save(self.report_file)
            if not self.quiet:
                print(f"📄 Saved batch report to: {self.report_file}")

        return report
