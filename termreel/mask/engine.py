"""
Screen Masking, Secret Redaction, and Realistic Value Substitution Engine.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Union, Pattern, Any, Callable
import copy
import json
import os
import re
import sys

from termreel.emulator.colors import RGBColor
from termreel.emulator.state import CharCell, TerminalState

# Default patterns for sensitive cloud tokens, API keys, and credentials
DEFAULT_SECRET_PATTERNS: List[Pattern] = [
    # Google OAuth 2.0 access token
    re.compile(r"ya29\.[a-zA-Z0-9_\-]+"),
    # Google API Key
    re.compile(r"AIza[0-9A-Za-z\-_]{25,45}"),
    # GitHub Personal Access Token (classic & fine-grained)
    re.compile(r"ghp_[a-zA-Z0-9]{30,45}"),
    re.compile(r"github_pat_[a-zA-Z0-9_]{60,90}"),
    # GitHub OAuth, user-to-server, server-to-server and refresh tokens
    re.compile(r"gh[ousr]_[a-zA-Z0-9]{30,}"),
    # Anthropic API/admin keys (sk-ant-api03-..., sk-ant-admin01-...)
    re.compile(r"sk-ant-[a-z]+[0-9]*-[A-Za-z0-9_\-]{20,}"),
    # OpenAI project / service-account keys (sk-proj-..., sk-svcacct-...)
    re.compile(r"sk-(?:proj|svcacct|admin)-[A-Za-z0-9_\-]{20,}"),
    # OpenAI API Key
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    # Stripe secret and restricted keys
    re.compile(r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"),
    # Slack bot/app/user/refresh/legacy tokens
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    # GitLab personal access token
    re.compile(r"glpat-[A-Za-z0-9_\-]{20,}"),
    # AWS Access Key ID
    re.compile(r"AKIA[0-9A-Z]{16}"),
    # Generic JWT Token
    re.compile(r"eyJ[a-zA-Z0-9_\-]{8,}\.eyJ[a-zA-Z0-9_\-]{8,}\.[a-zA-Z0-9_\-]+"),
    # Bearer Authorization header
    re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{16,}"),
    # Private Key block headers
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----"),
]

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


@dataclass
class ValueRule:
    """
    Literal exact string substitution rule.
    Matches exact strings and substitutes them with realistic fake values.
    """
    match: str
    replace: str
    match_count: int = 0
    _compiled: Pattern = field(init=False, repr=False)

    def __post_init__(self):
        self._compiled = re.compile(re.escape(self.match))


@dataclass
class PatternRule:
    """
    Regex pattern redaction/substitution rule.
    """
    pattern_str: str
    replace: Optional[str] = None
    match_count: int = 0
    is_default: bool = False
    _compiled: Pattern = field(init=False, repr=False)

    def __post_init__(self):
        if isinstance(self.pattern_str, Pattern):
            self._compiled = self.pattern_str
            self.pattern_str = self.pattern_str.pattern
        else:
            try:
                self._compiled = re.compile(self.pattern_str)
            except (re.error, TypeError) as exc:
                from termreel.exceptions import MaskConfigError
                raise MaskConfigError(
                    f"Invalid mask pattern {self.pattern_str!r}: {exc}"
                ) from exc


@dataclass
class AnchorRule:
    """
    Contextual landmark matching rule.
    Matches text anchored relative to specific landmarks (e.g. `after: "project = "`).
    """
    after: Optional[str] = None
    before: Optional[str] = None
    match: Optional[str] = None
    span: Optional[str] = None  # e.g. "rest_of_line", "word"
    replace: Optional[str] = None
    match_count: int = 0
    _compiled: Pattern = field(init=False, repr=False)
    _target_group: int = field(init=False, default=2, repr=False)

    def __post_init__(self):
        try:
            self._compile_anchor()
        except re.error as exc:
            from termreel.exceptions import MaskConfigError
            raise MaskConfigError(
                f"Invalid mask anchor match {self.match!r}: {exc}"
            ) from exc

    def _compile_anchor(self):
        after_pat = re.escape(self.after) if self.after else None
        before_pat = re.escape(self.before) if self.before else None

        if after_pat and before_pat:
            target = self.match if self.match else r".*?"
            if not self.match:
                pattern_str = (
                    rf"({after_pat}(?:\x1b\[[0-9;?]*[a-zA-Z])*\s*[\"']?)"
                    rf"({target})"
                    rf"([\"']?(?:\x1b\[[0-9;?]*[a-zA-Z])*\s*{before_pat})"
                )
            else:
                pattern_str = (
                    rf"({after_pat}(?:\x1b\[[0-9;?]*[a-zA-Z])*\s*)"
                    rf"({target})"
                    rf"((?:\x1b\[[0-9;?]*[a-zA-Z])*\s*{before_pat})"
                )
            self._compiled = re.compile(pattern_str)
            self._target_group = 2
        elif after_pat:
            if self.span == "rest_of_line":
                pattern_str = rf"({after_pat}(?:\x1b\[[0-9;?]*[a-zA-Z])*\s*)([^\r\n]+)"
            elif self.match:
                pattern_str = rf"({after_pat}(?:\x1b\[[0-9;?]*[a-zA-Z])*\s*)({self.match})"
            else:
                # Tolerates unquoted or quoted target tokens (e.g. project = "elevate-security-2026")
                pattern_str = rf"({after_pat}(?:\x1b\[[0-9;?]*[a-zA-Z])*\s*[\"']?)([^\s,;\"'\x1b]+)([\"']?)"
            self._compiled = re.compile(pattern_str)
            self._target_group = 2
        elif before_pat:
            if self.match:
                pattern_str = rf"({self.match})((?:\x1b\[[0-9;?]*[a-zA-Z])*\s*{before_pat})"
                self._compiled = re.compile(pattern_str)
                self._target_group = 1
            else:
                pattern_str = rf"([\"']?)([^\s,;\"'\x1b]+)([\"']?(?:\x1b\[[0-9;?]*[a-zA-Z])*\s*{before_pat})"
                self._compiled = re.compile(pattern_str)
                self._target_group = 2
        else:
            target = self.match if self.match else r".+"
            self._compiled = re.compile(rf"({target})")
            self._target_group = 1


def get_global_config_path() -> str:
    """Return the path to ~/.termreel/config.yaml or $TERMREEL_CONFIG."""
    if os.environ.get("TERMREEL_CONFIG"):
        return os.path.expanduser(os.environ["TERMREEL_CONFIG"])
    return os.path.join(os.path.expanduser("~"), ".termreel", "config.yaml")


def load_mask_config(path: Optional[str] = None) -> Dict[str, Any]:
    """
    Read mask and redaction rules from global configuration file.
    Merges both 'mask' and 'redactions' sections if both are present.

    Returns an empty dict only when the file does not exist or is empty.
    A file that exists but cannot be read or parsed raises MaskConfigError:
    silently dropping the rules would record the secrets they protect.
    """
    from termreel.exceptions import MaskConfigError

    target = path if path is not None else get_global_config_path()
    if not os.path.exists(target):
        return {}
    if not os.path.isfile(target):
        raise MaskConfigError(f"Mask config {target} is not a regular file")
    try:
        import yaml

        with open(target, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        raise MaskConfigError(f"Cannot read mask config {target}: {exc}") from exc

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise MaskConfigError(
            f"Mask config {target} must be a mapping with 'mask' and/or 'redactions' keys, "
            f"got {type(data).__name__}"
        )

    combined: Dict[str, Any] = {}

    redact_sec = data.get("redactions")
    if redact_sec is not None:
        if isinstance(redact_sec, list):
            combined["patterns"] = list(redact_sec)
        elif isinstance(redact_sec, dict):
            combined.update(redact_sec)

    mask_sec = data.get("mask")
    if mask_sec is not None:
        if isinstance(mask_sec, dict):
            for k, v in mask_sec.items():
                if k == "patterns" and "patterns" in combined:
                    existing = combined["patterns"]
                    v_list = v if isinstance(v, list) else [v]
                    combined["patterns"] = (existing if isinstance(existing, list) else [existing]) + v_list
                elif k == "values" and "values" in combined:
                    if isinstance(combined["values"], dict) and isinstance(v, dict):
                        combined["values"].update(v)
                    elif isinstance(combined["values"], list) and isinstance(v, list):
                        combined["values"].extend(v)
                    else:
                        combined["values"] = v
                else:
                    combined[k] = v
        elif isinstance(mask_sec, list):
            if "patterns" in combined:
                combined["patterns"].extend(mask_sec)
            else:
                combined["rules"] = mask_sec

    return combined


class MaskEngine:
    """
    High-performance screen masking and secret redaction engine.
    Supports:
      1. Exact value substitutions with realistic fake data
      2. Contextual landmark anchor matching
      3. Regular expression patterns with custom or bullet masks
      4. 2D terminal grid cell manipulation with style/color preservation
      5. Rule match telemetry and typo verification
    """

    def __init__(
        self,
        custom_patterns: Optional[List[Union[str, Pattern]]] = None,
        patterns: Optional[List[Union[str, Pattern, Dict[str, Any]]]] = None,
        values: Optional[Union[Dict[str, str], List[Dict[str, Any]]]] = None,
        anchors: Optional[List[Dict[str, Any]]] = None,
        use_default_patterns: bool = True,
        mask_char: str = "•",
        load_global_config: bool = False,
        global_config_path: Optional[str] = None,
    ):
        self.mask_char = mask_char
        self.use_default_patterns = use_default_patterns
        self.values: List[ValueRule] = []
        self.patterns: List[PatternRule] = []
        self.anchors: List[AnchorRule] = []

        # Load global configuration if requested
        if load_global_config:
            cfg = load_mask_config(global_config_path)
            if cfg:
                self._add_from_config(cfg)

        # Register default patterns
        if use_default_patterns:
            for pat in DEFAULT_SECRET_PATTERNS:
                self.patterns.append(PatternRule(pattern_str=pat, replace=None, is_default=True))

        # Register provided rules
        if custom_patterns:
            for cp in custom_patterns:
                self.add_pattern(cp)
        if patterns:
            for p in patterns:
                self.add_pattern(p)
        if values:
            self.add_values(values)
        if anchors:
            self.add_anchors(anchors)

    @classmethod
    def create(
        cls,
        scenario_rules: Optional[Any] = None,
        load_global: bool = True,
        use_default_patterns: bool = True,
        mask_char: str = "•",
        config_path: Optional[str] = None,
        redactions: Optional[Any] = None,
        mask: Optional[Any] = None,
    ) -> "MaskEngine":
        """
        Factory method to construct a MaskEngine merging global configuration
        with optional scenario-specific rules.
        """
        engine = cls(
            use_default_patterns=use_default_patterns,
            mask_char=mask_char,
            load_global_config=load_global,
            global_config_path=config_path,
        )
        if redactions:
            engine._add_from_config(redactions)
        if mask:
            engine._add_from_config(mask)
        if scenario_rules:
            engine._add_from_config(scenario_rules)
        return engine

    def _add_from_config(self, config: Any) -> None:
        """Parse and register rules from a dictionary or list representation."""
        if not config:
            return

        if isinstance(config, list):
            for item in config:
                if isinstance(item, str):
                    self.add_pattern(item)
                elif isinstance(item, ValueRule):
                    self.values.append(item)
                elif isinstance(item, PatternRule):
                    self.patterns.append(item)
                elif isinstance(item, AnchorRule):
                    self.anchors.append(item)
                elif isinstance(item, dict):
                    if "after" in item or "before" in item:
                        self.add_anchor(item)
                    elif "match" in item and "replace" in item:
                        self.add_value(item["match"], item["replace"])
                    elif "pattern" in item or "regex" in item:
                        self.add_pattern(item)
                    elif len(item) == 1:
                        # Shorthand { "match_string": "replace_string" }
                        for k, v in item.items():
                            self.add_value(str(k), str(v))
        elif isinstance(config, dict):
            # Check if this dict itself is a single rule:
            if ("after" in config or "before" in config) and not any(k in config for k in ("values", "patterns", "anchors")):
                self.add_anchor(config)
                return
            if "match" in config and "replace" in config and not any(k in config for k in ("values", "patterns", "anchors")):
                self.add_value(config["match"], config["replace"])
                return

            has_subkeys = any(k in config for k in ("values", "patterns", "anchors", "rules", "redactions"))
            if has_subkeys:
                if "values" in config:
                    self.add_values(config["values"])
                if "patterns" in config:
                    raw_pats = config["patterns"]
                    if isinstance(raw_pats, list):
                        for p in raw_pats:
                            self.add_pattern(p)
                    elif isinstance(raw_pats, (dict, str, Pattern, PatternRule)):
                        self.add_pattern(raw_pats)
                if "anchors" in config:
                    self.add_anchors(config["anchors"])
                if "rules" in config:
                    self._add_from_config(config["rules"])
                if "redactions" in config:
                    raw_redact = config["redactions"]
                    if isinstance(raw_redact, (list, dict)):
                        self._add_from_config(raw_redact)
            else:
                # Shorthand dictionary mapping match -> replace
                for k, v in config.items():
                    if isinstance(v, (str, int, float, bool)):
                        self.add_value(str(k), str(v))
                    elif isinstance(v, dict):
                        # Nested dict definition
                        if "after" in v or "before" in v:
                            self.add_anchor(v)
                        elif "replace" in v:
                            self.add_value(k, v["replace"])

    def add_value(self, match: str, replace: str) -> None:
        """Register an exact string replacement rule."""
        if not match:
            return
        # Override if already exists
        for idx, rule in enumerate(self.values):
            if rule.match == match:
                self.values[idx] = ValueRule(match=match, replace=str(replace))
                return
        self.values.append(ValueRule(match=match, replace=str(replace)))

    def add_values(self, values: Union[Dict[str, Any], List[Any]]) -> None:
        """Register multiple exact string replacement rules."""
        if isinstance(values, dict):
            # Check if this dict itself is a single rule: {"match": "...", "replace": "..."}
            if "match" in values and "replace" in values and len(values) <= 3:
                self.add_value(str(values["match"]), str(values["replace"]))
                return
            for k, v in values.items():
                if isinstance(v, dict) and "replace" in v:
                    self.add_value(str(k), str(v["replace"]))
                else:
                    self.add_value(str(k), str(v))
        elif isinstance(values, list):
            for item in values:
                if isinstance(item, ValueRule):
                    self.values.append(item)
                elif isinstance(item, dict):
                    if "match" in item and "replace" in item:
                        self.add_value(str(item["match"]), str(item["replace"]))
                    elif len(item) == 1:
                        for k, v in item.items():
                            self.add_value(str(k), str(v))
                elif isinstance(item, str):
                    self.add_value(item, self.mask_char * len(item))

    def add_pattern(self, pattern: Union[str, Pattern, Dict[str, Any], PatternRule]) -> None:
        """Register a regex pattern redaction/substitution rule."""
        if isinstance(pattern, PatternRule):
            self.patterns.append(pattern)
            return
        if isinstance(pattern, dict):
            pat = pattern.get("pattern") or pattern.get("regex") or pattern.get("match")
            repl = pattern.get("replace")
            if pat:
                self.patterns.append(PatternRule(pattern_str=pat, replace=repl))
        elif isinstance(pattern, (str, Pattern)):
            self.patterns.append(PatternRule(pattern_str=pattern, replace=None))

    def add_anchor(self, anchor: Union[Dict[str, Any], AnchorRule]) -> None:
        """Register a landmark anchor rule."""
        if isinstance(anchor, AnchorRule):
            self.anchors.append(anchor)
            return
        if not isinstance(anchor, dict):
            return
        rule = AnchorRule(
            after=anchor.get("after"),
            before=anchor.get("before"),
            match=anchor.get("match"),
            span=anchor.get("span"),
            replace=anchor.get("replace"),
        )
        self.anchors.append(rule)

    def add_anchors(self, anchors: Union[List[Union[Dict[str, Any], AnchorRule]], Dict[str, Any], AnchorRule]) -> None:
        """Register multiple landmark anchor rules."""
        if isinstance(anchors, list):
            for a in anchors:
                self.add_anchor(a)
        elif isinstance(anchors, (dict, AnchorRule)):
            self.add_anchor(anchors)

    @property
    def _sorted_values(self) -> List[ValueRule]:
        """Values sorted by match length descending to prioritize specific matches."""
        return sorted(self.values, key=lambda v: len(v.match), reverse=True)

    def redact_text(self, text: str) -> str:
        """
        Redact sensitive strings, anchors, and patterns from plain or ANSI text.
        """
        if not text:
            return text

        result = text

        # 1. Anchors first (landmarks intact before contents change)
        for anchor in self.anchors:
            result = self._apply_anchor_to_text(anchor, result)

        # 2. Values next (sorted by length descending)
        for val in self._sorted_values:
            result = self._apply_value_to_text(val, result)

        # 3. Custom and default patterns
        for pat in self.patterns:
            result = self._apply_pattern_to_text(pat, result)

        return result

    def _apply_anchor_to_text(self, anchor: AnchorRule, text: str) -> str:
        def _repl(match):
            anchor.match_count += 1
            full = match.group(0)
            target = match.group(anchor._target_group)
            replacement = anchor.replace if anchor.replace is not None else (self.mask_char * len(target))
            t_start = match.start(anchor._target_group) - match.start(0)
            t_end = match.end(anchor._target_group) - match.start(0)
            return full[:t_start] + replacement + full[t_end:]

        return anchor._compiled.sub(_repl, text)

    def _apply_value_to_text(self, rule: ValueRule, text: str) -> str:
        def _repl(match):
            rule.match_count += 1
            return rule.replace if rule.replace is not None else (self.mask_char * (match.end() - match.start()))

        return rule._compiled.sub(_repl, text)

    def _apply_pattern_to_text(self, rule: PatternRule, text: str) -> str:
        def _repl(match):
            rule.match_count += 1
            return rule.replace if rule.replace is not None else (self.mask_char * (match.end() - match.start()))

        return rule._compiled.sub(_repl, text)

    def redacted_snapshot(self, state: TerminalState) -> TerminalState:
        """
        Return a masked copy of ``state`` for rendering or telemetry.

        The live grid is never modified. Earlier versions rewrote the live
        grid in place every frame, so a replacement containing the original
        text grew on every frame (``paul`` -> ``paul_demo`` -> ``paul_demo_demo``)
        and a replacement of a different length shifted columns under the
        application's cursor.

        Matching runs on logical lines (rows joined across soft wraps), so a
        token that wraps onto the next row is masked in full.
        """
        snap = state.snapshot(copy_inactive=False)
        self.apply_to_terminal_state(snap, active_only=True)
        return snap

    def apply_to_terminal_state(self, state: TerminalState, active_only: bool = False) -> None:
        """
        Mask ``state`` in place.

        Only call this on a copy (see :meth:`redacted_snapshot`). Applying it
        repeatedly to a live grid re-masks already masked text. Rule
        ``match_count`` values are not changed here: they count occurrences
        in text passed to :meth:`redact_text`, not frames.

        ``active_only`` masks just the visible buffer (the hidden buffer of a
        ``snapshot(copy_inactive=False)`` is shared with the live state).
        """
        with state._lock:
            cursor = state.cursor
            active_is_alt = state.in_alt_buffer
            for grid, is_active in ((state.primary_grid, not active_is_alt), (state.alt_grid, active_is_alt)):
                if active_only and not is_active:
                    continue
                self._apply_to_grid(
                    grid, state.rows, state.cols, state.default_fg, state.default_bg,
                    cursor=cursor if is_active else None,
                )

    def _collect_matches(self, text: str, count: bool = False) -> List[Tuple[int, int, str]]:
        """
        Non-overlapping (start, end, replacement) spans over ``text``.

        With ``count=True`` each chosen span increments its rule's
        ``match_count`` (used for text that is written out once, such as a
        cast stream). Grid masking runs every frame and does not count.
        """
        matches: List[Tuple[int, int, str, Any]] = []
        for anchor in self.anchors:
            for m in anchor._compiled.finditer(text):
                start = m.start(anchor._target_group)
                end = m.end(anchor._target_group)
                if anchor.span == "rest_of_line":
                    stripped = text[start:end].rstrip(" ")
                    end = start + len(stripped) if stripped else start
                repl = anchor.replace if anchor.replace is not None else (self.mask_char * (end - start))
                if end > start:
                    matches.append((start, end, repl, anchor))
        for val in self._sorted_values:
            for m in val._compiled.finditer(text):
                start, end = m.span()
                repl = val.replace if val.replace is not None else (self.mask_char * (end - start))
                matches.append((start, end, repl, val))
        for pat in self.patterns:
            for m in pat._compiled.finditer(text):
                start, end = m.span()
                if end <= start:
                    continue
                repl = pat.replace if pat.replace is not None else (self.mask_char * (end - start))
                matches.append((start, end, repl, pat))
        if not matches:
            return []
        # Earliest start wins; for equal starts the longest span wins.
        matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))
        chosen: List[Tuple[int, int, str]] = []
        last_end = -1
        for start, end, repl, rule in matches:
            if start >= last_end:
                chosen.append((start, end, repl))
                last_end = end
                if count:
                    rule.match_count += 1
        return chosen

    def _apply_to_grid(
        self,
        grid: List[List[CharCell]],
        rows: int,
        cols: int,
        default_fg: RGBColor,
        default_bg: RGBColor,
        cursor: Any = None,
    ) -> None:
        from termreel.emulator.state import Row, char_width

        nrows = min(rows, len(grid))
        r = 0
        while r < nrows:
            # A logical line: rows chained by the soft-wrap flag.
            first = r
            while r < nrows - 1 and getattr(grid[r], "wrapped", False):
                r += 1
            last = r
            r += 1

            cells: List[CharCell] = []
            for rr in range(first, last + 1):
                row = grid[rr]
                base_cells = getattr(row, "_unmasked_cells", None)
                if base_cells is not None:
                    row_slice = [c.copy() for c in base_cells[:cols]]
                else:
                    row_slice = list(row[:cols])
                cells.extend(row_slice)
                cells.extend(CharCell(char=" ", fg=default_fg, bg=default_bg) for _ in range(cols - len(row_slice)))

            # Text with a map from text offset to cell index. Wide-character
            # placeholder cells ("") contribute no text.
            pieces: List[str] = []
            owner: List[int] = []
            for idx, cell in enumerate(cells):
                ch = cell.char
                if not ch:
                    continue
                pieces.append(ch)
                owner.extend([idx] * len(ch))
            text = "".join(pieces)
            if not text.strip():
                continue
            spans = self._collect_matches(text)
            if not spans:
                continue

            total = len(cells)
            cell_spans = []
            for start, end, repl in spans:
                c_start = owner[start]
                c_end = owner[end - 1] + 1
                while c_end < total and cells[c_end].char == "":
                    c_end += 1  # include the right half of a wide char
                cell_spans.append((c_start, c_end, repl))

            # Cursor as a linear offset within this logical line.
            cur_pos = None
            pending = False
            if cursor is not None and first <= cursor.row <= last:
                pending = cursor.col >= cols
                cur_pos = (cursor.row - first) * cols + min(cursor.col, cols)

            new_cells: List[CharCell] = []
            prev = 0
            shift = 0
            new_cur = cur_pos
            for c_start, c_end, repl in cell_spans:
                new_cells.extend(cells[prev:c_start])
                ref = cells[c_start]
                repl_cells: List[CharCell] = []
                for ch in repl:
                    w = char_width(ch)
                    if w <= 0 and repl_cells:
                        repl_cells[-1].char += ch
                        continue
                    base = ref.copy()
                    base.char = ch
                    repl_cells.append(base)
                    if w == 2:
                        filler = ref.copy()
                        filler.char = ""
                        repl_cells.append(filler)
                new_cells.extend(repl_cells)
                delta = len(repl_cells) - (c_end - c_start)
                if cur_pos is not None:
                    if cur_pos >= c_end:
                        new_cur = cur_pos + shift + delta
                    elif cur_pos > c_start:
                        new_cur = c_start + shift + min(cur_pos - c_start, len(repl_cells))
                shift += delta
                prev = c_end
            new_cells.extend(cells[prev:])
            if cur_pos is not None and new_cur == cur_pos and shift and cur_pos >= prev:
                new_cur = cur_pos + shift

            # Re-flow into the same rows; clip or pad.
            span_rows = last - first + 1
            capacity = span_rows * cols
            if len(new_cells) > capacity:
                new_cells = new_cells[:capacity]
                if new_cells and new_cells[-1].char and char_width(new_cells[-1].char[0]) == 2:
                    blank = new_cells[-1].copy()
                    blank.char = " "
                    new_cells[-1] = blank
            while len(new_cells) < capacity:
                new_cells.append(CharCell(char=" ", fg=default_fg, bg=default_bg))
            for i, rr in enumerate(range(first, last + 1)):
                old_row = grid[rr]
                wrapped = getattr(old_row, "wrapped", False)
                unmasked = getattr(old_row, "_unmasked_cells", None)
                if unmasked is None:
                    unmasked = [c.copy() for c in old_row[:cols]]
                new_row = Row(new_cells[i * cols:(i + 1) * cols], wrapped=wrapped)
                new_row._unmasked_cells = unmasked
                grid[rr] = new_row

            if cur_pos is not None and new_cur is not None and new_cur != cur_pos:
                new_cur = max(0, min(new_cur, capacity))
                if pending and new_cur % cols == 0 and new_cur > 0:
                    row_off, col = new_cur // cols - 1, cols
                else:
                    row_off, col = divmod(new_cur, cols)
                    if row_off >= span_rows:
                        row_off, col = span_rows - 1, cols - 1
                cursor.row = first + row_off
                cursor.col = col

    def get_verification_report(self) -> Dict[str, Any]:
        """
        Generate a structured telemetry report of all active rules,
        match counts, and 0-match rules for typo detection.
        """
        rules_summary = []
        unmatched = []
        total_matches = 0

        for v in self.values:
            total_matches += v.match_count
            item = {
                "type": "value",
                "target": v.match,
                "replace": v.replace,
                "match_count": v.match_count,
            }
            rules_summary.append(item)
            if v.match_count == 0:
                unmatched.append(item)

        for a in self.anchors:
            total_matches += a.match_count
            parts = []
            if a.after:
                parts.append(f"after: {a.after!r}")
            if a.before:
                parts.append(f"before: {a.before!r}")
            if a.span:
                parts.append(f"span: {a.span}")
            target_desc = " ".join(parts) if parts else (a.match or "anchor")
            item = {
                "type": "anchor",
                "target": target_desc,
                "replace": a.replace if a.replace is not None else (self.mask_char * 4),
                "match_count": a.match_count,
            }
            rules_summary.append(item)
            if a.match_count == 0:
                unmatched.append(item)

        for p in self.patterns:
            total_matches += p.match_count
            item = {
                "type": "pattern",
                "target": p.pattern_str,
                "replace": p.replace if p.replace is not None else (self.mask_char * 8),
                "match_count": p.match_count,
                "is_default": p.is_default,
            }
            rules_summary.append(item)
            if p.match_count == 0 and not p.is_default:
                unmatched.append(item)

        return {
            "total_matches": total_matches,
            "active_rules_count": len(rules_summary),
            "unmatched_count": len(unmatched),
            "rules": rules_summary,
            "unmatched": unmatched,
        }


# Backwards compatibility alias
Redactor = MaskEngine
