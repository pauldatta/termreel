"""
Sensitive token, API key, and secret redaction manager.
"""

import re
from typing import List, Pattern, Union, Optional


DEFAULT_SECRET_PATTERNS: List[Pattern] = [
    # Google OAuth 2.0 access token
    re.compile(r"ya29\.[a-zA-Z0-9_\-]+"),
    # Google API Key
    re.compile(r"AIza[0-9A-Za-z\-_]{25,45}"),
    # GitHub Personal Access Token (classic & fine-grained)
    re.compile(r"ghp_[a-zA-Z0-9]{30,45}"),
    re.compile(r"github_pat_[a-zA-Z0-9_]{60,90}"),
    # OpenAI API Key
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    # AWS Access Key ID
    re.compile(r"AKIA[0-9A-Z]{16}"),
    # Generic JWT Token
    re.compile(r"eyJ[a-zA-Z0-9_\-]{8,}\.eyJ[a-zA-Z0-9_\-]{8,}\.[a-zA-Z0-9_\-]+"),
    # Bearer Authorization header
    re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{16,}"),
    # Private Key block headers
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----"),
]


class Redactor:
    """
    Manages sensitive data patterns and sanitizes output strings and terminal buffers.
    """

    def __init__(
        self,
        custom_patterns: Optional[List[Union[str, Pattern]]] = None,
        use_default_patterns: bool = True,
        mask_char: str = "•",
    ):
        self.mask_char = mask_char
        self.patterns: List[Pattern] = []

        if use_default_patterns:
            self.patterns.extend(DEFAULT_SECRET_PATTERNS)

        if custom_patterns:
            for p in custom_patterns:
                self.add_pattern(p)

    def add_pattern(self, pattern: Union[str, Pattern]):
        """Register an additional regex pattern to redact."""
        if isinstance(pattern, str):
            compiled = re.compile(pattern)
        else:
            compiled = pattern
        self.patterns.append(compiled)

    def redact_text(self, text: str) -> str:
        """Redact sensitive strings from plain text, replacing matching content."""
        result = text
        for pat in self.patterns:
            def _replace(match):
                span_len = match.end() - match.start()
                return self.mask_char * span_len
            result = pat.sub(_replace, result)
        return result

    def apply_to_terminal_state(self, state) -> None:
        """Sanitize characters in-place across the 2D TerminalState grid."""
        state.apply_redaction(self.patterns, mask_char=self.mask_char)
