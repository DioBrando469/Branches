import argparse
import ast
import configparser
import sys
import urllib.request
import urllib.error
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Any, List, Optional

# TOML parsing (Standard Library in Python 3.11+)
try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        print("Error: 'tomli' module is required for Python < 3.11.", file=sys.stderr)
        sys.exit(1)

# --- Stage 1: Configuration (reused) ---
@dataclass
class Config:
    package_name: str
    repo_url_or_path: str
    repo_mode: str
    output_image_name: str
    max_depth: int
    filter_substring: str

def validate_config(config_data: Dict[str, Any]) -> Config:
    required_keys = ["package_name", "repo_url_or_path", "repo_mode", "output_image_name", "max_depth"]
    for key in required_keys:
        if key not in config_data:
            raise ValueError(f"Missing required configuration parameter: '{key}'")
    
    if config_data["repo_mode"] not in ["url", "local_file"]:
        raise ValueError("'repo_mode' must be either 'url' or 'local_file'.")
    if not isinstance(config_data["max_depth"], int) or config_data["max_depth"] <= 0:
        raise ValueError("'max_depth' must be a positive integer.")
    
    return Config(
        package_name=config_data["package_name"],
        repo_url_or_path=config_data["repo_url_or_path"],
        repo_mode=config_data["repo_mode"],
        output_image_name=config_data["output_image_name"],
        max_depth=config_data["max_depth"],
        filter_substring=config_data.get("filter_substring", "")
    )

def load_config(file_path: Path) -> Config:
    if not file_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {file_path}")
    with open(file_path, "rb") as f:
        return validate_config(tomllib.load(f))

# --- Stage 2: Data Collection ---
def fetch_content_from_github(repo_url: str, filename: str) -> Optional[str]:
    """Attempts to fetch a file from GitHub's raw content endpoint."""
    # Basic heuristic: only works for standard GitHub URLs
    if "github.com" not in repo_url:
        return None
    
    # Try both 'main' and 'master' branches
    for branch in ["main", "master"]:
        raw_url = f"{repo_url.replace('github.com', 'raw.githubusercontent.com')}/{branch}/{filename}"
        try:
            req = urllib.request.Request(raw_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    return response.read().decode('utf-8')
        except urllib.error.HTTPError:
            continue
    return None

def get_repo_file(config: Config, filename: str) -> Optional[str]:
    """Fetches a file from the repository, either locally or via URL."""
    if config.repo_mode == "local_file":
        path = Path(config.repo_url_or_path) / filename
        if path.exists():
            return path.read_text(encoding='utf-8')
    else:
        # For this stage, we implement GitHub fetching.
        # Other Git providers would require their own logic.
        return fetch_content_from_github(config.repo_url_or_path, filename)
    return None

def parse_pyproject_toml(content: str) -> List[str]:
    """Extracts dependencies from pyproject.toml (PEP 621 or Poetry)."""
    deps = []
    try:
        data = tomllib.loads(content)
    except Exception:
        return deps
    
    # PEP 621 standard
    if "project" in data and "dependencies" in data["project"]:
        deps.extend(data["project"]["dependencies"])
    
    # Poetry style
    if "tool" in data and "poetry" in data["tool"] and "dependencies" in data["tool"]["poetry"]:
        for name, version in data["tool"]["poetry"]["dependencies"].items():
            if name.lower() != "python":  # Skip the Python version constraint
                deps.append(f"{name}{version}")
    return deps

def parse_setup_py(content: str) -> List[str]:
    """Extracts install_requires from setup.py using AST (safe parsing)."""
    deps = []
    try:
        tree = ast.parse(content)
    except Exception:
        return deps
    
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func_name = ""
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr
            
            if func_name == "setup":
                for keyword in node.keywords:
                    if keyword.arg == "install_requires":
                        # Try to evaluate literal list of strings
                        if isinstance(keyword.value, ast.List):
                            for elt in keyword.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    deps.append(elt.value)
    return deps

def parse_setup_cfg(content: str) -> List[str]:
    """Extracts install_requires from setup.cfg."""
    deps = []
    parser = configparser.ConfigParser()
    parser.read_string(content)
    if parser.has_option("options", "install_requires"):
        raw = parser.get("options", "install_requires")
        # setup.cfg typically lists dependencies on new lines
        deps = [line.strip() for line in raw.splitlines() if line.strip()]
    return deps

def parse_requirements_txt(content: str) -> List[str]:
    """Parses a requirements.txt file."""
    deps = []
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            # Strip inline comments
            if " #" in line:
                line = line.split(" #")[0].strip()
            deps.append(line)
    return deps

def extract_direct_dependencies(config: Config) -> List[str]:
    """Fetches the repository and extracts direct dependencies."""
    # Order of preference: modern standard -> legacy -> simple requirements
    files_to_try = [
        ("pyproject.toml", parse_pyproject_toml),
        ("setup.cfg", parse_setup_cfg),
        ("setup.py", parse_setup_py),
        ("requirements.txt", parse_requirements_txt)
    ]
    
    for filename, parser in files_to_try:
        content = get_repo_file(config, filename)
        if content:
            deps = parser(content)
            if deps: # Return as soon as we find dependencies
                return deps
    
    return []

def main():
    parser = argparse.ArgumentParser(description="Dependency Graph Visualizer - Stage 2")
    parser.add_argument("--config", type=str, default="config.toml", help="Path to TOML config")
    args = parser.parse_args()

    try:
        config = load_config(Path(args.config))
    except Exception as e:
        print(f"Configuration Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Analyzing package: {config.package_name}")
    print(f"Repository: {config.repo_url_or_path}")

    try:
        deps = extract_direct_dependencies(config)
        
        if deps:
            print("\n--- Direct Dependencies ---")
            for dep in deps:
                print(dep)
            print("---------------------------")
        else:
            print("\nNo direct dependencies found or file not accessible.")
            
    except Exception as e:
        print(f"Data Collection Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()