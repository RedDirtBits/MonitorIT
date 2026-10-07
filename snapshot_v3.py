import os
from pathlib import Path
from typing import List, Set
from datetime import datetime

# Output Markdown bundle file name
OUTPUT_FILE = "snapshot.md"

# File extensions to include automatically in the code dump
ALLOWED_EXTENSIONS: Set[str] = {
    ".py",
    ".html",
    ".css",
    ".js",
    ".md",
    ".env",
    ".sh",
    ".ps1",      # PowerShell scripts (launch.ps1, setup_project.ps1)
    ".bat",      # Windows Batch scripts (start.bat)
    ".json",
    ".txt",
    ".ini",      # Vertiv UPS / System configs
    ".service",
    ".yaml",
    ".yml",
    ".jinja",
    ".j2",
    ".sql",
}

# Explicit hidden or extensionless files to always include
EXPLICIT_FILES: Set[str] = {
    ".env",
    ".gitignore",
    "README.md",
    "requirements.txt",
    "Dockerfile",
}

# Directories to ignore completely (prevents scanning virtualenvs, binaries, and git history)
IGNORE_DIRS: Set[str] = {
    ".git",
    "__pycache__",
    "python_env",  # Critical for Portable Embedded Python
    ".venv",
    "venv",
    "env",
    ".idea",
    ".vscode",
    "node_modules",
    "backups",
    ".pytest_cache",
    ".mypy_cache",
}

# Ignore database files, binary dumps, and snapshot output dynamically by extension
IGNORE_EXTENSIONS: Set[str] = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".db-journal",
    ".db-wal",
    ".db-shm",
    ".zip",
    ".tar",
    ".gz",
    ".pyc",
    ".exe",
    ".dll",
    ".png",
    ".jpg",
    ".ico",
}

IGNORE_FILES: Set[str] = {
    OUTPUT_FILE,
}

def generate_directory_tree(dir_path: Path, prefix: str = "") -> str:
    """
    Recursively generates an ASCII visual tree structure of the project.
    Filters out runtime databases, backup zip exports, and virtual environments.
    """
    tree_str = ""
    items = sorted(
        [
            p for p in dir_path.iterdir()
            if p.name not in IGNORE_DIRS 
            and p.name not in IGNORE_FILES
            and p.suffix.lower() not in IGNORE_EXTENSIONS
            and not (p.name.startswith(".") and p.name not in EXPLICIT_FILES)
        ],
        key=lambda s: (s.is_file(), s.name.lower())
    )

    count = len(items)
    for index, item in enumerate(items):
        is_last = (index == count - 1)
        connector = "└── " if is_last else "├── "

        tree_str += f"{prefix}{connector}{item.name}\n"

        if item.is_dir():
            extension = "    " if is_last else "│   "
            tree_str += generate_directory_tree(item, prefix=prefix + extension)

    return tree_str

def collect_source_files(root_dir: Path) -> List[Path]:
    """
    Walks the project directory and returns a sorted list of source code,
    template, static, and configuration files.
    """
    source_files: List[Path] = []
    for path in sorted(root_dir.rglob("*")):
        if path.is_file():
            # Skip ignored directories
            if any(part in IGNORE_DIRS for part in path.parts):
                continue
            # Skip specific files or binary/database extensions
            if path.name in IGNORE_FILES or path.suffix.lower() in IGNORE_EXTENSIONS:
                continue

            if path.suffix.lower() in ALLOWED_EXTENSIONS or path.name in EXPLICIT_FILES:
                source_files.append(path)

    return source_files

def bundle_codebase() -> None:
    """
    Combines project directory tree and source code files
    into a single formatted Markdown snapshot document.
    """
    root = Path(__file__).resolve().parent
    output_path = root / OUTPUT_FILE
    source_files = collect_source_files(root)
    project_name = root.name

    with open(output_path, "w", encoding="utf-8") as out:
        # Header & Metadata
        out.write(f"# {project_name} Codebase Snapshot\n\n")
        out.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        out.write(f"**Total Captured Files:** {len(source_files)}\n\n")
        out.write("---\n\n")

        # 1. Directory Tree
        out.write("## 1. Project Directory Structure\n\n")
        out.write("```text\n")
        out.write(f"{project_name}/\n")
        out.write(generate_directory_tree(root))
        out.write("```\n\n")
        out.write("---\n\n")

        # 2. File Contents
        out.write("## 2. Source Code Files\n\n")

        syntax_map = {
            "py": "python",
            "js": "javascript",
            "css": "css",
            "html": "html",
            "sh": "bash",
            "ps1": "powershell",
            "bat": "cmd",
            "json": "json",
            "env": "text",
            "ini": "ini",
            "service": "ini",
            "md": "markdown",
            "yaml": "yaml",
            "yml": "yaml",
            "sql": "sql",
            "jinja": "jinja2",
            "j2": "jinja2",
        }

        for file_path in source_files:
            relative_path = file_path.relative_to(root)
            out.write(f"### File: `{relative_path}`\n\n")

            ext = file_path.suffix.lstrip('.').lower()
            lang = syntax_map.get(ext, "text")

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                out.write(f"```{lang}\n")
                out.write(content)
                out.write("\n```\n\n")
            except Exception as e:
                out.write(f"<!-- Error reading file `{relative_path}`: {e} -->\n\n")

    print(f"[+] Snapshot successfully generated: {output_path}")

if __name__ == "__main__":
    bundle_codebase()