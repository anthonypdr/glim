import difflib
import shlex
import subprocess
from pathlib import Path


PROJECT_ROOT = Path.cwd().resolve()


def _resolve_project_path(path: str) -> Path:
    path_obj = Path(path)

    if path_obj.is_absolute():
        resolved = path_obj.resolve()
    else:
        resolved = (PROJECT_ROOT / path_obj).resolve()

    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError:
        raise ValueError(
            "Access outside the current project directory is not allowed."
        )

    return resolved


def list_files(path="."):
    target = _resolve_project_path(path)

    if not target.exists():
        return {
            "ok": False,
            "error": f"Path does not exist: {path}",
        }

    if not target.is_dir():
        return {
            "ok": False,
            "error": f"Not a directory: {path}",
        }

    entries = []

    for item in sorted(target.iterdir()):
        relative = item.relative_to(PROJECT_ROOT)

        entries.append(
            {
                "path": str(relative),
                "type": "directory" if item.is_dir() else "file",
            }
        )

    return {
        "ok": True,
        "path": (
            str(target.relative_to(PROJECT_ROOT))
            if target != PROJECT_ROOT
            else "."
        ),
        "entries": entries,
    }


def read_file(path):
    target = _resolve_project_path(path)

    if not target.exists():
        return {
            "ok": False,
            "error": f"File does not exist: {path}",
        }

    if not target.is_file():
        return {
            "ok": False,
            "error": f"Not a file: {path}",
        }

    try:
        content = target.read_text(
            encoding="utf-8",
            errors="replace",
        )

        return {
            "ok": True,
            "path": str(target.relative_to(PROJECT_ROOT)),
            "content": content,
        }

    except Exception as error:
        return {
            "ok": False,
            "error": str(error),
        }


def write_file(path, content):
    target = _resolve_project_path(path)

    try:
        existed = target.exists()
        previous_content = (
            target.read_text(encoding="utf-8", errors="replace")
            if existed
            else ""
        )

        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        target.write_text(
            content,
            encoding="utf-8",
        )

        relative_path = str(target.relative_to(PROJECT_ROOT))
        diff = "".join(difflib.unified_diff(
            previous_content.splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile=(f"a/{relative_path}" if existed else "/dev/null"),
            tofile=f"b/{relative_path}",
        ))

        return {
            "ok": True,
            "path": relative_path,
            "action": "modified" if existed else "created",
            "bytes_written": len(
                content.encode("utf-8")
            ),
            # Presentation-only: Agent removes this before returning the tool
            # result to the model, so a large edit does not consume context.
            "diff": diff,
        }

    except Exception as error:
        return {
            "ok": False,
            "error": str(error),
        }


# Commands that are always blocked, even with approval.
BLOCKED_PATTERNS = [
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    "mkfs.",
    "dd if=",
    ":(){",
    "> /dev/sd",
    "shred /dev/",
]


# Only these commands may run automatically.
# Everything else asks the user first.
SAFE_COMMANDS = {
    "ls",
    "pwd",
    "cat",
    "head",
    "tail",
    "grep",
    "rg",
    "find",
    "tree",
    "wc",
    "file",
    "stat",
    "which",
    "whereis",
    "type",
    "echo",
    "printf",
    "git status",
    "git diff",
    "git log",
    "git branch",
}


def classify_command(command: str) -> str:
    normalized = command.strip()
    lowered = normalized.lower()

    for pattern in BLOCKED_PATTERNS:
        if pattern in lowered:
            return "blocked"

    try:
        parts = shlex.split(normalized)
    except ValueError:
        return "confirm"

    if not parts:
        return "confirm"

    # Compound shell commands should always require approval.
    shell_operators = [
        "&&",
        "||",
        ";",
        "|",
        ">",
        ">>",
        "<",
        "$(",
        "`",
    ]

    if any(operator in normalized for operator in shell_operators):
        return "confirm"

    executable = parts[0].lower()

    # Basic read-only commands.
    if executable in {
        "ls",
        "pwd",
        "cat",
        "head",
        "tail",
        "grep",
        "rg",
        "find",
        "tree",
        "wc",
        "file",
        "stat",
        "which",
        "whereis",
        "type",
        "echo",
        "printf",
    }:
        return "safe"

    # Only selected Git operations are automatic.
    if executable == "git" and len(parts) >= 2:
        git_command = f"git {parts[1].lower()}"

        if git_command in {
            "git status",
            "git diff",
            "git log",
            "git branch",
        }:
            return "safe"

    # Everything else requires explicit approval.
    return "confirm"


def run_command(command, timeout=120):
    classification = classify_command(command)

    if classification == "blocked":
        return {
            "ok": False,
            "blocked": True,
            "command": command,
            "error": "This command is blocked because it is highly destructive.",
        }

    if classification == "confirm":
        return {
            "ok": False,
            "requires_confirmation": True,
            "command": command,
        }

    return execute_command(
        command,
        timeout=timeout,
    )


def execute_command(command, timeout=120):
    try:
        process = subprocess.run(
            command,
            shell=True,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        return {
            "ok": process.returncode == 0,
            "command": command,
            "returncode": process.returncode,
            "stdout": process.stdout,
            "stderr": process.stderr,
        }

    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "command": command,
            "error": f"Command timed out after {timeout} seconds.",
        }

    except Exception as error:
        return {
            "ok": False,
            "command": command,
            "error": str(error),
        }
