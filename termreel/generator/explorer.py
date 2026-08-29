"""
CLI exploration and capability discovery engine.
Probes target CLI tools via --help and subcommands to extract metadata and infer workflows.
"""

from dataclasses import dataclass, field
import os
import re
import shutil
import subprocess
from typing import List, Dict, Optional, Tuple, Any


@dataclass
class SubcommandInfo:
    name: str
    description: str


@dataclass
class CLISpec:
    name: str
    executable_path: str
    version: str
    summary: str
    usage: str
    subcommands: List[SubcommandInfo] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    category: str = "general"  # 'agent', 'vcs', 'cloud', 'repl', 'package', 'general'
    inferred_prompts: List[Dict[str, Any]] = field(default_factory=list)
    suggested_setup_commands: List[str] = field(default_factory=list)
    recommended_permissions: List[str] = field(default_factory=list)


class CLIExplorer:
    """
    Explores and analyzes local CLI binaries by probing help text, version, and subcommands.
    """

    def __init__(self, cli_name: str):
        self.cli_name = cli_name.strip()
        self.executable_path = shutil.which(self.cli_name)

    def is_installed(self) -> bool:
        return self.executable_path is not None

    def probe(self) -> CLISpec:
        """Probe the CLI tool to construct a comprehensive specification."""
        if not self.executable_path:
            raise FileNotFoundError(f"CLI binary '{self.cli_name}' not found on PATH.")

        version = self._probe_version()
        help_text = self._probe_help()
        subcommands = self._extract_subcommands(help_text)
        flags = self._extract_flags(help_text)
        category = self._infer_category(help_text)
        summary = self._extract_summary(help_text)
        usage = self._extract_usage(help_text)

        suggested_setup, suggested_perms, inferred_prompts = self._generate_suggestions(category, subcommands)

        return CLISpec(
            name=self.cli_name,
            executable_path=self.executable_path,
            version=version,
            summary=summary,
            usage=usage,
            subcommands=subcommands,
            flags=flags,
            category=category,
            inferred_prompts=inferred_prompts,
            suggested_setup_commands=suggested_setup,
            recommended_permissions=suggested_perms,
        )

    def _probe_version(self) -> str:
        """Attempt to extract version string from binary."""
        for flag in ["--version", "-v", "version"]:
            try:
                res = subprocess.run([self.cli_name, flag], capture_output=True, text=True, timeout=3.0)
                if res.returncode == 0 and res.stdout.strip():
                    lines = res.stdout.strip().split("\n")
                    return lines[0].strip()
            except Exception:
                pass
        return "unknown"

    def _probe_help(self) -> str:
        """Extract primary help documentation."""
        for flag in ["--help", "-h", "help"]:
            try:
                res = subprocess.run([self.cli_name, flag], capture_output=True, text=True, timeout=3.0)
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
                elif res.stderr.strip():
                    return res.stderr.strip()
            except Exception:
                pass
        return ""

    def _extract_summary(self, help_text: str) -> str:
        if not help_text:
            return f"Interactive CLI utility for {self.cli_name}"
        lines = [line.strip() for line in help_text.split("\n") if line.strip()]
        for line in lines[:5]:
            if not line.lower().startswith("usage") and not line.startswith("-") and not line.startswith("[") and len(line) > 10:
                return line
        return f"{self.cli_name} command line interface"

    def _extract_usage(self, help_text: str) -> str:
        m = re.search(r"(?i)usage:\s*([^\n]+)", help_text)
        return m.group(1).strip() if m else f"{self.cli_name} [options] [command]"

    def _extract_subcommands(self, help_text: str) -> List[SubcommandInfo]:
        subcommands = []
        if not help_text:
            return subcommands

        # Look for section headers: Commands:, Available commands:, Subcommands:
        sections = re.split(r"(?i)\n(?:available\s+)?(?:sub)?commands:\s*\n", help_text)
        if len(sections) > 1:
            cmd_block = sections[1].split("\n\n")[0]
            for line in cmd_block.split("\n"):
                line = line.strip()
                if not line or line.startswith("-"):
                    continue
                parts = re.split(r"\s{2,}|\t+", line, maxsplit=1)
                cmd_name = parts[0].strip().split()[0] if parts[0].strip() else ""
                desc = parts[1].strip() if len(parts) > 1 else ""
                if cmd_name and len(cmd_name) < 25 and not cmd_name.startswith("-"):
                    subcommands.append(SubcommandInfo(name=cmd_name, description=desc))

        # Fallback regex search for lines like "  init    Initialize something"
        if not subcommands:
            matches = re.findall(r"^\s{2,}([a-z0-9_\-]+)\s{2,}([^\n]+)", help_text, re.MULTILINE)
            for name, desc in matches[:15]:
                if not name.startswith("-") and len(name) < 20:
                    subcommands.append(SubcommandInfo(name=name, description=desc.strip()))

        return subcommands[:15]

    def _extract_flags(self, help_text: str) -> List[str]:
        flags = re.findall(r"(--[a-zA-Z0-9_\-]+|-[a-zA-Z0-9])", help_text)
        seen = set()
        unique = []
        for f in flags:
            if f not in seen:
                seen.add(f)
                unique.append(f)
        return unique[:20]

    def _infer_category(self, help_text: str) -> str:
        name_lower = self.cli_name.lower()
        if name_lower in ["git", "hg", "jj", "svn"]:
            return "vcs"
        if name_lower in ["agy", "gemini", "claude", "chatgpt", "aider", "copilot"]:
            return "agent"
        if name_lower in ["gcloud", "kubectl", "aws", "az", "terraform"]:
            return "cloud"
        if name_lower in ["python", "python3", "node", "ipython", "ruby", "lua"]:
            return "repl"
        if name_lower in ["npm", "pip", "uv", "cargo", "brew", "yarn", "pnpm"]:
            return "package"

        text_lower = (self.cli_name + " " + help_text).lower()
        if any(w in text_lower for w in ["antigravity", "ai assistant", "llm agent", "coding agent"]):
            return "agent"
        elif any(w in text_lower for w in ["version control", "commit", "working tree", "repository"]):
            return "vcs"
        elif any(w in text_lower for w in ["cloud platform", "kubernetes", "cloud sdk", "compute engine"]):
            return "cloud"
        elif any(w in text_lower for w in ["interactive interpreter", "interactive console", "repl"]):
            return "repl"
        elif any(w in text_lower for w in ["package manager", "dependency manager"]):
            return "package"
        return "general"

    def _generate_suggestions(self, category: str, subcommands: List[SubcommandInfo]) -> Tuple[List[str], List[str], List[Dict[str, Any]]]:
        setup_cmds = []
        perms = []
        steps = []
        sub_names = [s.name for s in subcommands]

        if category == "agent":
            setup_cmds = [
                "git init",
                "git config user.name 'Developer'",
                "git config user.email 'dev@example.com'",
                "echo '# Sample Project' > README.md",
                "echo 'def process_data(): return True' > main.py",
                "git add . && git commit -m 'Initial commit'",
            ]
            perms = ["python3", "git", "pytest", "npm", "cat", "ls"]
            steps = [
                {"title": "Explore Repository", "prompt": "What files exist in this project and what does main.py do?"},
                {"title": "Refactor Code", "prompt": "Refactor main.py to add robust error handling and type hints"},
            ]
        elif category == "vcs":
            setup_cmds = [
                "git init",
                "git config user.name 'Developer'",
                "git config user.email 'dev@example.com'",
                "echo 'hello world' > file.txt",
            ]
            perms = ["git"]
            steps = [
                {"title": "Status Inspection", "command": f"{self.cli_name} status"},
                {"title": "Staging Changes", "command": f"{self.cli_name} add file.txt"},
                {"title": "Creating Commit", "command": f"{self.cli_name} commit -m 'feat: Initial commit'"},
                {"title": "Branch History", "command": f"{self.cli_name} log --oneline"},
            ]
        elif category == "cloud":
            perms = [self.cli_name, "curl", "kubectl"]
            steps = [
                {"title": "Version & Info", "command": f"{self.cli_name} version"},
                {"title": "Configuration Check", "command": f"{self.cli_name} config list"},
            ]
        elif category == "repl":
            steps = [
                {"title": "Expression Evaluation", "input": "2 + 2"},
                {"title": "Data Structures", "input": "numbers = [x * 2 for x in range(5)]; print(numbers)"},
                {"title": "Exit Session", "input": "exit()"},
            ]
        else:
            if sub_names:
                for sub in sub_names[:3]:
                    steps.append({"title": f"Run {sub}", "command": f"{self.cli_name} {sub} --help"})
            else:
                steps = [
                    {"title": "Version Check", "command": f"{self.cli_name} --version"},
                    {"title": "Help Exploration", "command": f"{self.cli_name} --help"},
                ]

        return setup_cmds, perms, steps
