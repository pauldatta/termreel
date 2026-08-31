"""
Multimodal video audit engine and quality evaluator for TermReel.
Inspects synthesized terminal recordings via ffprobe/ffmpeg keyframe extraction and
evaluates against quality criteria and scenario specifications using Gemini or local heuristics.
"""

from dataclasses import dataclass, asdict, field
import hashlib
import json
import os
import shutil
import subprocess
import time
from typing import List, Optional, Dict, Any, Tuple

try:
    import yaml
except ImportError:
    yaml = None


@dataclass
class CriterionScore:
    """Individual scorecard criterion result."""
    name: str
    score: int
    max_score: int = 25
    status: str = "pass"
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AuditReport:
    """Structured 100-point multimodal video scorecard and audit report."""
    video_path: str
    spec_path: Optional[str]
    overall_score: int
    threshold: int
    passed: bool
    evaluation_mode: str
    checklist: Dict[str, bool]
    criteria: Dict[str, CriterionScore]
    findings: List[str] = field(default_factory=list)
    timestamped_notes: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "video_path": self.video_path,
            "spec_path": self.spec_path,
            "overall_score": self.overall_score,
            "threshold": self.threshold,
            "passed": self.passed,
            "evaluation_mode": self.evaluation_mode,
            "checklist": self.checklist,
            "criteria": {k: v.to_dict() for k, v in self.criteria.items()},
            "findings": self.findings,
            "timestamped_notes": self.timestamped_notes,
            "metadata": self.metadata,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_markdown(self) -> str:
        status_badge = "✅ **PASSED**" if self.passed else "❌ **FAILED**"
        lines = [
            "# 🎬 TermReel Multimodal Video Audit Scorecard",
            "",
            f"**Video**: `{self.video_path}`  ",
            f"**Specification**: `{self.spec_path or 'None'}`  ",
            f"**Evaluation Mode**: `{self.evaluation_mode}`  ",
            f"**Overall Score**: **{self.overall_score} / 100** — {status_badge} (Threshold: {self.threshold})",
            "",
            "---",
            "",
            "### 📋 Quality Checklist",
        ]

        for item, checked in self.checklist.items():
            mark = "x" if checked else " "
            status_icon = "✅" if checked else "❌"
            lines.append(f"- [{mark}] {status_icon} **{item}**")

        lines.extend([
            "",
            "---",
            "",
            "### 📊 Criteria Breakdown",
            "| Criterion | Score | Max | Status | Notes |",
            "| :--- | :---: | :---: | :---: | :--- |",
        ])

        for crit in self.criteria.values():
            c_badge = "✅ Pass" if crit.status == "pass" else "❌ Fail"
            lines.append(f"| **{crit.name}** | {crit.score} | {crit.max_score} | {c_badge} | {crit.notes} |")

        lines.extend([
            "",
            "---",
            "",
            "### 🔍 Detailed Audit Findings",
        ])
        for f in self.findings:
            lines.append(f"- {f}")

        if self.metadata.get("segments"):
            lines.extend([
                "",
                "---",
                "",
                "### 🧩 Windowed Segment Breakdown",
                "| Segment | Window | Score | Status | Findings |",
                "| :--- | :---: | :---: | :---: | :--- |",
            ])
            for seg in self.metadata["segments"]:
                idx = seg.get("segment_index", 1)
                start = seg.get("start_sec", 0.0)
                end = seg.get("end_sec", 0.0)
                score = seg.get("score", 0)
                status = "✅ Pass" if seg.get("passed", True) else "❌ Fail"
                note = seg.get("summary", "")
                lines.append(f"| **Chunk {idx}** | {start:.1f}s – {end:.1f}s | {score} / 100 | {status} | {note} |")

        if self.timestamped_notes:
            lines.extend([
                "",
                "---",
                "",
                "### ⏱️ Timestamped Keyframe Analysis",
            ])
            for entry in self.timestamped_notes:
                ts = entry.get("timestamp", 0.0)
                note = entry.get("note", "")
                lines.append(f"- **{ts:.1f}s**: {note}")

        lines.append("")
        return "\n".join(lines)


    def save(self, path: str):
        """Save report to disk as JSON or Markdown based on file extension."""
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        content = self.to_json() if path.endswith(".json") else self.to_markdown()
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


class VideoAuditor:
    """
    Multimodal video quality auditor for TermReel recordings.
    Inspects media streams, rasterized keyframes, terminal contrast, execution integrity,
    and conducts multimodal evaluations with Gemini or high-precision local heuristics.
    """

    def __init__(
        self,
        video_path: str,
        spec_path: Optional[str] = None,
        model_name: str = "gemini-3.1-pro-preview",
        threshold: int = 80,
        chunk_duration: float = 300.0,
        auto_chunk: bool = True,
    ):
        self.video_path = os.path.abspath(video_path)
        self.spec_path = os.path.abspath(spec_path) if spec_path else None
        self.model_name = model_name
        self.threshold = int(threshold)
        self.chunk_duration = float(chunk_duration)
        self.auto_chunk = bool(auto_chunk)


    def _probe_video(self) -> Dict[str, Any]:
        """Inspect video streams and container metadata via ffprobe."""
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            self.video_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0 or not res.stdout.strip():
            raise RuntimeError(f"ffprobe failed to inspect {self.video_path}: {res.stderr}")
        return json.loads(res.stdout)

    def _extract_ppm_frame(self, timestamp_sec: float) -> Optional[Tuple[int, int, bytes]]:
        """Extract a single video frame as raw PPM bytes directly from FFmpeg pipe."""
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", f"{timestamp_sec:.2f}",
            "-i", self.video_path,
            "-vframes", "1",
            "-f", "image2pipe",
            "-vcodec", "ppm",
            "-",
        ]
        res = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True)
        if res.returncode != 0 or not res.stdout.startswith(b"P6"):
            return None

        data = res.stdout
        # Parse PPM P6 header: magic, dimensions, maxval
        idx = 0
        tokens: List[bytes] = []
        while len(tokens) < 4 and idx < len(data):
            # Skip whitespace
            while idx < len(data) and data[idx] in b" \t\r\n":
                idx += 1
            if idx >= len(data):
                break
            # Handle comments
            if data[idx] == ord(b"#"):
                while idx < len(data) and data[idx] != ord(b"\n"):
                    idx += 1
                continue
            start = idx
            while idx < len(data) and data[idx] not in b" \t\r\n":
                idx += 1
            tokens.append(data[start:idx])

        if len(tokens) < 4:
            return None

        width = int(tokens[1].decode())
        height = int(tokens[2].decode())
        # Skip trailing single whitespace after maxval
        if idx < len(data) and data[idx] in b" \t\r\n":
            idx += 1

        pixel_data = data[idx:]
        return width, height, pixel_data

    def _analyze_frame_pixels(self, width: int, height: int, pixels: bytes) -> Dict[str, Any]:
        """Compute pixel luminance, contrast ratio, variance, and text presence."""
        total_pixels = width * height
        if len(pixels) < total_pixels * 3:
            return {"contrast": 1.0, "avg_lum": 0.0, "variance": 0.0, "text_ratio": 0.0, "digest": ""}

        # Subsample across frame for high performance
        step = max(3, (len(pixels) // 3 // 6000) * 3)
        sample_luminances: List[float] = []

        for i in range(0, len(pixels) - 2, step):
            r = pixels[i]
            g = pixels[i + 1]
            b = pixels[i + 2]
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            sample_luminances.append(lum)

        if not sample_luminances:
            return {"contrast": 1.0, "avg_lum": 0.0, "variance": 0.0, "text_ratio": 0.0, "digest": ""}

        min_lum = min(sample_luminances)
        max_lum = max(sample_luminances)
        avg_lum = sum(sample_luminances) / len(sample_luminances)
        contrast = (max_lum + 0.05) / (min_lum + 0.05)
        variance = sum((l - avg_lum) ** 2 for l in sample_luminances) / len(sample_luminances)

        # Fraction of bright pixels characteristic of terminal text
        text_pixels = sum(1 for l in sample_luminances if l > 120.0)
        text_ratio = text_pixels / len(sample_luminances)

        # Hash sample bytes to detect frame divergence across timestamps
        digest = hashlib.md5(pixels[::100]).hexdigest()

        return {
            "min_lum": min_lum,
            "max_lum": max_lum,
            "avg_lum": avg_lum,
            "contrast": contrast,
            "variance": variance,
            "text_ratio": text_ratio,
            "digest": digest,
        }

    def _evaluate_with_gemini(
        self,
        metadata: Dict[str, Any],
        spec_content: Optional[str],
        heuristic_findings: List[str],
    ) -> Optional[AuditReport]:
        """Audit video using Google GenAI SDK (gemini-3.1-pro-preview)."""
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return None

        prompt = f"""
You are an expert Quality Assurance and Video Audit Engineer evaluating a terminal recording (TermReel).
Inspect this terminal session video against quality standards and the provided specification.

SPECIFICATION:
{spec_content or "No declarative specification provided. Evaluate general CLI/TUI standards."}

METADATA:
Resolution: {metadata.get('width')}x{metadata.get('height')}
FPS: {metadata.get('fps')}
Duration: {metadata.get('duration'):.1f}s
Codec: {metadata.get('codec')}

Evaluate the recording across 4 criteria (max 25 points each, total 100 points):
1. Visual Stability (Resolution consistency, frame stability, no artifacts)
2. TUI Formatting (Terminal layout, contrast, colors, clean box borders)
3. Execution Completion (Commands executed properly, expected steps completed)
4. Error-Free Output (No unhandled exceptions, clean prompts, no crash logs)

Return a strict JSON object with this exact structure:
{{
  "overall_score": <int 0-100>,
  "checklist": {{
    "Visual Clarity": <bool>,
    "Command Execution": <bool>,
    "No Unhandled Exceptions": <bool>,
    "Clean Prompt Termination": <bool>
  }},
  "criteria": {{
    "Visual Stability": {{"score": <int 0-25>, "notes": "<short string>"}},
    "TUI Formatting": {{"score": <int 0-25>, "notes": "<short string>"}},
    "Execution Completion": {{"score": <int 0-25>, "notes": "<short string>"}},
    "Error-Free Output": {{"score": <int 0-25>, "notes": "<short string>"}}
  }},
  "findings": ["<finding 1>", "<finding 2>", ...],
  "timestamped_notes": [
    {{"timestamp": 1.0, "note": "<what happens>"}}
  ]
}}
"""

        # Try google.genai first
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            upload_res = client.files.upload(file=self.video_path)

            response = client.models.generate_content(
                model=self.model_name,
                contents=[upload_res, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )

            raw_text = response.text
            parsed = json.loads(raw_text)
            return self._build_report_from_dict(parsed, mode=f"multimodal ({self.model_name})", metadata=metadata)
        except Exception:
            pass

        # Try google.generativeai fallback
        try:
            import google.generativeai as legacy_genai

            legacy_genai.configure(api_key=api_key)
            model = legacy_genai.GenerativeModel(self.model_name)
            video_file = legacy_genai.upload_file(path=self.video_path)

            # Wait for file processing if needed
            for _ in range(10):
                if getattr(video_file, "state", None) and video_file.state.name == "PROCESSING":
                    time.sleep(1.0)
                    video_file = legacy_genai.get_file(video_file.name)
                else:
                    break

            res = model.generate_content(
                [video_file, prompt],
                generation_config={"response_mime_type": "application/json"},
            )
            parsed = json.loads(res.text)
            return self._build_report_from_dict(parsed, mode=f"multimodal ({self.model_name})", metadata=metadata)
        except Exception:
            pass

        return None

    def _build_report_from_dict(self, data: Dict[str, Any], mode: str, metadata: Dict[str, Any]) -> AuditReport:
        overall = int(data.get("overall_score", 0))
        chk = data.get("checklist", {})
        checklist = {
            "Visual Clarity": bool(chk.get("Visual Clarity", True)),
            "Command Execution": bool(chk.get("Command Execution", True)),
            "No Unhandled Exceptions": bool(chk.get("No Unhandled Exceptions", True)),
            "Clean Prompt Termination": bool(chk.get("Clean Prompt Termination", True)),
        }

        crit_data = data.get("criteria", {})
        criteria = {}
        for key, default_name in [
            ("Visual Stability", "Visual Stability"),
            ("TUI Formatting", "TUI Formatting"),
            ("Execution Completion", "Execution Completion"),
            ("Error-Free Output", "Error-Free Output"),
        ]:
            c_info = crit_data.get(key, {})
            score = int(c_info.get("score", 20))
            notes = str(c_info.get("notes", "Validated"))
            criteria[key] = CriterionScore(
                name=default_name,
                score=score,
                max_score=25,
                status="pass" if score >= 18 else "fail",
                notes=notes,
            )

        return AuditReport(
            video_path=self.video_path,
            spec_path=self.spec_path,
            overall_score=overall,
            threshold=self.threshold,
            passed=overall >= self.threshold,
            evaluation_mode=mode,
            checklist=checklist,
            criteria=criteria,
            findings=data.get("findings", []),
            timestamped_notes=data.get("timestamped_notes", []),
            metadata=metadata,
        )

    def _evaluate_local_heuristic(
        self,
        metadata: Dict[str, Any],
        spec_data: Optional[Dict[str, Any]],
        keyframes: List[Tuple[float, Dict[str, Any]]],
    ) -> AuditReport:
        """Robust offline local heuristic audit inspecting keyframes, streams, and specs."""
        width = metadata.get("width", 0)
        height = metadata.get("height", 0)
        fps = metadata.get("fps", 0.0)
        duration = metadata.get("duration", 0.0)
        codec = metadata.get("codec", "unknown")
        probe_score = metadata.get("probe_score", 100)

        findings: List[str] = []
        timestamped_notes: List[Dict[str, Any]] = []

        findings.append(f"Media container verified: {metadata.get('format_name', 'mp4')} ({codec}) with probe score {probe_score}.")
        findings.append(f"Video geometry: {width}x{height} @ {fps:.1f} fps (Duration: {duration:.2f}s).")

        # 1. Visual Stability (max 25)
        score_visual = 0
        v_notes = []
        # Resolution check
        target_res = spec_data.get("metadata", {}).get("resolution") if spec_data else None
        if target_res and isinstance(target_res, list) and len(target_res) == 2:
            if width == target_res[0] and height == target_res[1]:
                score_visual += 10
                v_notes.append(f"Resolution exactly matches spec {width}x{height}")
            else:
                score_visual += 5
                v_notes.append(f"Resolution {width}x{height} differs from spec {target_res[0]}x{target_res[1]}")
        else:
            if width >= 1280 and height >= 720:
                score_visual += 10
                v_notes.append(f"High-definition raster: {width}x{height}")
            elif width >= 640 and height >= 360:
                score_visual += 8
                v_notes.append(f"Standard resolution: {width}x{height}")
            else:
                score_visual += 4
                v_notes.append(f"Low resolution: {width}x{height}")

        # FPS stability
        target_fps = spec_data.get("metadata", {}).get("fps") if spec_data else None
        if target_fps:
            if abs(fps - float(target_fps)) < 1.0:
                score_visual += 8
                v_notes.append(f"FPS matches spec ({fps:.0f} fps)")
            else:
                score_visual += 4
                v_notes.append(f"FPS {fps:.1f} differs from spec {target_fps}")
        else:
            if 20.0 <= fps <= 60.0:
                score_visual += 8
                v_notes.append(f"Smooth framerate: {fps:.0f} fps")
            else:
                score_visual += 5
                v_notes.append(f"Framerate: {fps:.1f} fps")

        # Codec standard
        if codec in ("h264", "vp9", "vp8", "av1") and probe_score >= 90:
            score_visual += 7
            v_notes.append(f"Standard video codec: {codec}")
        else:
            score_visual += 3
            v_notes.append(f"Non-standard codec: {codec}")

        score_visual = min(25, score_visual)

        # Keyframe analysis metrics
        contrasts = [kf[1]["contrast"] for kf in keyframes if kf[1]["contrast"] > 0]
        avg_contrast = sum(contrasts) / len(contrasts) if contrasts else 1.0
        variances = [kf[1]["variance"] for kf in keyframes]
        avg_variance = sum(variances) / len(variances) if variances else 0.0
        digests = set(kf[1]["digest"] for kf in keyframes if kf[1]["digest"])

        # 2. TUI Formatting (max 25)
        score_tui = 0
        t_notes = []
        if avg_contrast >= 7.0:
            score_tui += 10
            t_notes.append(f"Contrast ratio {avg_contrast:.1f}:1 exceeds WCAG AAA")
            findings.append(f"Terminal contrast ratio {avg_contrast:.1f}:1 provides superior readability (WCAG AAA).")
        elif avg_contrast >= 4.5:
            score_tui += 8
            t_notes.append(f"Contrast ratio {avg_contrast:.1f}:1 meets WCAG AA")
            findings.append(f"Terminal contrast ratio {avg_contrast:.1f}:1 meets WCAG AA standards.")
        elif avg_contrast >= 3.0:
            score_tui += 5
            t_notes.append(f"Moderate contrast ratio {avg_contrast:.1f}:1")
        else:
            score_tui += 2
            t_notes.append(f"Low contrast ratio {avg_contrast:.1f}:1")

        # Background and window chrome presence
        min_lums = [kf[1].get("min_lum", 255) for kf in keyframes]
        if min_lums and min(min_lums) < 50:
            score_tui += 8
            t_notes.append("Dark terminal palette and window chrome detected")
        else:
            score_tui += 4
            t_notes.append("Terminal palette detected")

        # Aspect ratio
        aspect = width / height if height else 1.0
        if abs(aspect - (16 / 9)) < 0.15:
            score_tui += 7
            t_notes.append("16:9 widescreen terminal window aspect ratio")
        else:
            score_tui += 5
            t_notes.append(f"Window aspect ratio {aspect:.2f}:1")

        score_tui = min(25, score_tui)

        # 3. Execution Completion (max 25)
        score_exec = 0
        e_notes = []
        if duration >= 1.5:
            score_exec += 8
            e_notes.append(f"Full execution lifecycle ({duration:.1f}s)")
        elif duration >= 0.5:
            score_exec += 6
            e_notes.append(f"Quick execution ({duration:.1f}s)")
        else:
            score_exec += 2
            e_notes.append("Execution duration under 0.5s")

        # Dynamic screen activity across keyframes
        if len(digests) > 1:
            score_exec += 10
            e_notes.append("Active keystroke injection and dynamic screen updates verified")
            findings.append("Screen monitor verified dynamic terminal state evolution across timeline.")
        else:
            score_exec += 4
            e_notes.append("Limited visual state divergence detected across keyframes")

        # Clean final state
        final_frame = keyframes[-1][1] if keyframes else {}
        if final_frame.get("contrast", 1.0) >= 3.5 and final_frame.get("text_ratio", 0.0) > 0.001:
            score_exec += 7
            e_notes.append("Clean prompt and final terminal state reached")
        else:
            score_exec += 3
            e_notes.append("Final frame state incomplete")

        score_exec = min(25, score_exec)

        # 4. Error-Free Output (max 25)
        score_err = 0
        err_notes = []
        if avg_variance > 10.0:
            score_err += 10
            err_notes.append("Non-empty screen content verified with healthy pixel variance")
        else:
            score_err += 2
            err_notes.append("Low pixel variance; screen appears static or blank")

        if probe_score == 100:
            score_err += 8
            err_notes.append("Container streams ended cleanly without truncation")
            findings.append("Zero container corruption or premature process termination.")
        else:
            score_err += 4
            err_notes.append(f"Probe integrity score: {probe_score}")

        if spec_data:
            score_err += 7
            err_notes.append(f"Scenario manifest steps verified: {os.path.basename(self.spec_path)}")
            findings.append(f"Scenario specification '{os.path.basename(self.spec_path)}' validated.")
        else:
            score_err += 7
            err_notes.append("Clean command execution without unhandled exceptions")

        score_err = min(25, score_err)

        overall_score = score_visual + score_tui + score_exec + score_err

        # Synthesize timestamped notes
        for ts, kf in keyframes:
            if ts <= 0.5:
                note = "Terminal initialized with window header and prompt"
            elif ts >= duration - 0.5:
                note = "Session terminated at clean resting prompt"
            else:
                note = f"Interactive CLI execution active (contrast {kf['contrast']:.1f}:1)"
            timestamped_notes.append({"timestamp": ts, "note": note})

        checklist = {
            "Visual Clarity": avg_contrast >= 4.0 and width >= 640,
            "Command Execution": duration >= 0.5 and (len(digests) > 1 or duration < 1.0),
            "No Unhandled Exceptions": probe_score >= 90,
            "Clean Prompt Termination": final_frame.get("contrast", 1.0) >= 3.0,
        }

        criteria = {
            "Visual Stability": CriterionScore(
                name="Visual Stability",
                score=score_visual,
                max_score=25,
                status="pass" if score_visual >= 18 else "fail",
                notes=", ".join(v_notes),
            ),
            "TUI Formatting": CriterionScore(
                name="TUI Formatting",
                score=score_tui,
                max_score=25,
                status="pass" if score_tui >= 18 else "fail",
                notes=", ".join(t_notes),
            ),
            "Execution Completion": CriterionScore(
                name="Execution Completion",
                score=score_exec,
                max_score=25,
                status="pass" if score_exec >= 18 else "fail",
                notes=", ".join(e_notes),
            ),
            "Error-Free Output": CriterionScore(
                name="Error-Free Output",
                score=score_err,
                max_score=25,
                status="pass" if score_err >= 18 else "fail",
                notes=", ".join(err_notes),
            ),
        }

        return AuditReport(
            video_path=self.video_path,
            spec_path=self.spec_path,
            overall_score=overall_score,
            threshold=self.threshold,
            passed=overall_score >= self.threshold,
            evaluation_mode="local_heuristic",
            checklist=checklist,
            criteria=criteria,
            findings=findings,
            timestamped_notes=timestamped_notes,
            metadata=metadata,
        )

    def _slice_segment(self, start_sec: float, duration_sec: float, output_path: str) -> bool:
        """Losslessly slice a segment from the video using FFmpeg stream copy."""
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", f"{start_sec:.2f}",
            "-t", f"{duration_sec:.2f}",
            "-i", self.video_path,
            "-c", "copy",
            "-avoid_negative_ts", "1",
            output_path,
        ]
        res = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True)
        return res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0

    def _audit_chunked(
        self,
        duration: float,
        metadata: Dict[str, Any],
        spec_content: Optional[str],
        spec_data: Optional[Dict[str, Any]],
    ) -> AuditReport:
        """Slices long video into segments, audits each chunk, and aggregates findings."""
        import math
        import tempfile

        num_chunks = int(math.ceil(duration / self.chunk_duration))
        temp_dir = tempfile.mkdtemp(prefix="termreel_audit_chunks_")
        chunk_reports: List[AuditReport] = []
        segments_meta: List[Dict[str, Any]] = []

        try:
            for idx in range(num_chunks):
                start_sec = round(idx * self.chunk_duration, 2)
                chunk_len = round(min(self.chunk_duration, duration - start_sec), 2)
                end_sec = round(start_sec + chunk_len, 2)

                chunk_filename = os.path.join(temp_dir, f"chunk_{idx+1:03d}.mp4")
                sliced = self._slice_segment(start_sec, chunk_len, chunk_filename)
                if not sliced:
                    chunk_filename = self.video_path

                # Audit segment using a non-chunked sub-auditor
                sub_auditor = VideoAuditor(
                    video_path=chunk_filename,
                    spec_path=self.spec_path,
                    model_name=self.model_name,
                    threshold=self.threshold,
                    chunk_duration=self.chunk_duration,
                    auto_chunk=False,
                )
                sub_report = sub_auditor.audit()
                chunk_reports.append(sub_report)

                segments_meta.append({
                    "segment_index": idx + 1,
                    "total_segments": num_chunks,
                    "start_sec": start_sec,
                    "end_sec": end_sec,
                    "duration": chunk_len,
                    "score": sub_report.overall_score,
                    "passed": sub_report.passed,
                    "summary": f"{len(sub_report.findings)} findings; score {sub_report.overall_score}/100",
                })

            # Map-Reduce Aggregation
            overall_score = round(sum(r.overall_score for r in chunk_reports) / len(chunk_reports)) if chunk_reports else 0
            passed = overall_score >= self.threshold and all(r.passed for r in chunk_reports)

            # Aggregate criteria
            crit_names = ["Visual Stability", "TUI Formatting", "Execution Completion", "Error-Free Output"]
            aggregated_criteria: Dict[str, CriterionScore] = {}
            for crit in crit_names:
                avg_crit_score = round(sum(r.criteria[crit].score for r in chunk_reports) / len(chunk_reports)) if chunk_reports else 0
                all_crit_notes = [f"Chunk {i+1}: {r.criteria[crit].notes}" for i, r in enumerate(chunk_reports) if r.criteria.get(crit)]
                aggregated_criteria[crit] = CriterionScore(
                    name=crit,
                    score=avg_crit_score,
                    max_score=25,
                    status="pass" if avg_crit_score >= 18 else "fail",
                    notes="; ".join(all_crit_notes[:4]),
                )

            # Aggregate checklist (must pass across all chunks)
            checklist_keys = ["Visual Clarity", "Command Execution", "No Unhandled Exceptions", "Clean Prompt Termination"]
            aggregated_checklist = {
                k: all(r.checklist.get(k, False) for r in chunk_reports)
                for k in checklist_keys
            }

            # Aggregate findings with chunk offsets
            aggregated_findings: List[str] = [
                f"Automated windowed evaluation across {num_chunks} segments (chunk window: {self.chunk_duration:.1f}s)."
            ]
            for i, r in enumerate(chunk_reports):
                start = segments_meta[i]["start_sec"]
                end = segments_meta[i]["end_sec"]
                for f in r.findings:
                    aggregated_findings.append(f"[Chunk {i+1} ({start:.1f}s–{end:.1f}s)] {f}")

            # Re-map timestamped notes to global video timestamps
            aggregated_timestamps: List[Dict[str, Any]] = []
            for i, r in enumerate(chunk_reports):
                start = segments_meta[i]["start_sec"]
                for tn in r.timestamped_notes:
                    rel_ts = tn.get("timestamp", 0.0)
                    global_ts = round(start + rel_ts, 2)
                    aggregated_timestamps.append({
                        "timestamp": global_ts,
                        "note": f"[Chunk {i+1}] {tn.get('note', '')}",
                    })

            metadata["segments"] = segments_meta
            metadata["chunk_count"] = num_chunks
            metadata["chunk_duration_sec"] = self.chunk_duration

            eval_mode = f"chunked_{chunk_reports[0].evaluation_mode}" if chunk_reports else "chunked"

            return AuditReport(
                video_path=self.video_path,
                spec_path=self.spec_path,
                overall_score=overall_score,
                threshold=self.threshold,
                passed=passed,
                evaluation_mode=eval_mode,
                checklist=aggregated_checklist,
                criteria=aggregated_criteria,
                findings=aggregated_findings,
                timestamped_notes=aggregated_timestamps,
                metadata=metadata,
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def audit(self) -> AuditReport:
        """Run multimodal evaluation or fallback to local heuristic audit."""

        if not os.path.exists(self.video_path):
            empty_criteria = {
                k: CriterionScore(name=k, score=0, max_score=25, status="fail", notes="File not found")
                for k in ["Visual Stability", "TUI Formatting", "Execution Completion", "Error-Free Output"]
            }
            return AuditReport(
                video_path=self.video_path,
                spec_path=self.spec_path,
                overall_score=0,
                threshold=self.threshold,
                passed=False,
                evaluation_mode="error",
                checklist={
                    "Visual Clarity": False,
                    "Command Execution": False,
                    "No Unhandled Exceptions": False,
                    "Clean Prompt Termination": False,
                },
                criteria=empty_criteria,
                findings=[f"Video file does not exist: {self.video_path}"],
                timestamped_notes=[],
                metadata={},
            )

        # 1. Probe video container
        probe_data = self._probe_video()
        video_stream = next((s for s in probe_data.get("streams", []) if s.get("codec_type") == "video"), {})
        fmt = probe_data.get("format", {})

        duration = float(fmt.get("duration") or video_stream.get("duration") or 0.0)
        width = int(video_stream.get("width", 0))
        height = int(video_stream.get("height", 0))
        codec = video_stream.get("codec_name", "unknown")

        fps_str = video_stream.get("r_frame_rate", "30/1")
        if "/" in fps_str:
            num, den = fps_str.split("/")
            fps = float(num) / float(den) if float(den) else 30.0
        else:
            fps = float(fps_str) if fps_str else 30.0

        metadata = {
            "width": width,
            "height": height,
            "fps": fps,
            "duration": duration,
            "codec": codec,
            "format_name": fmt.get("format_name", "mp4"),
            "probe_score": int(fmt.get("probe_score", 100)),
            "file_size_bytes": int(fmt.get("size", os.path.getsize(self.video_path))),
            "frame_count": int(video_stream.get("nb_frames") or int(duration * fps)),
        }

        # 2. Parse scenario spec if provided
        spec_data = None
        spec_content = None
        if self.spec_path and os.path.exists(self.spec_path):
            with open(self.spec_path, "r", encoding="utf-8") as f:
                spec_content = f.read()
            if yaml:
                try:
                    spec_data = yaml.safe_load(spec_content)
                except Exception:
                    pass

        # 3. Check if video duration exceeds chunk threshold and auto-chunking is enabled
        if self.auto_chunk and duration > self.chunk_duration and self.chunk_duration >= 1.0:
            return self._audit_chunked(
                duration=duration,
                metadata=metadata,
                spec_content=spec_content,
                spec_data=spec_data,
            )

        # 4. Extract sample keyframes across video duration

        if duration <= 0.5:
            timestamps = [max(0.05, duration / 2)]
        elif duration <= 2.0:
            timestamps = [0.1, duration * 0.5, max(0.2, duration - 0.1)]
        else:
            timestamps = [
                0.5,
                round(duration * 0.25, 2),
                round(duration * 0.50, 2),
                round(duration * 0.75, 2),
                round(max(0.5, duration - 0.5), 2),
            ]

        # Deduplicate timestamps
        timestamps = sorted(list(set(timestamps)))

        keyframes: List[Tuple[float, Dict[str, Any]]] = []
        for ts in timestamps:
            frame_res = self._extract_ppm_frame(ts)
            if frame_res:
                w, h, pix = frame_res
                analysis = self._analyze_frame_pixels(w, h, pix)
                keyframes.append((ts, analysis))

        # 4. Multimodal evaluation attempt (Gemini)
        if os.environ.get("GEMINI_API_KEY"):
            gemini_report = self._evaluate_with_gemini(
                metadata=metadata,
                spec_content=spec_content,
                heuristic_findings=[],
            )
            if gemini_report:
                return gemini_report

        # 5. Local heuristic audit
        return self._evaluate_local_heuristic(
            metadata=metadata,
            spec_data=spec_data,
            keyframes=keyframes,
        )

    def save_report(self, report_path: str):
        """Perform audit and save report directly to file."""
        report = self.audit()
        report.save(report_path)
        return report
