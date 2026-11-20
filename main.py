import argparse
import ast
import configparser
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Set

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        print("Error: 'tomli' module required.", file=sys.stderr)
        sys.exit(1)

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
    
    if config_data["repo_mode"] not in ["url", "local_file", "test"]:
        raise ValueError("'repo_mode' must be 'url', 'local_file', or 'test'.")
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

# --- Stage 2: Data Collection Helpers ---
def fetch_content_from_github(repo_url: str, filename: str) -> Optional[str]:
    if "github.com" not in repo_url: return None
    for branch in ["main", "master"]:
        raw_url = f"{repo_url.replace('github.com', 'raw.githubusercontent.com')}/{branch}/{filename}"
        try:
            req = urllib.request.Request(raw_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    return response.read().decode('utf-8')
        except urllib.error.HTTPError:
            continue
    return None

def parse_pyproject_toml(content: str) -> List[str]:
    deps = []
    try: data = tomllib.loads(content)
    except Exception: return deps
    
    if "project" in data and "dependencies" in data["project"]:
        deps.extend(data["project"]["dependencies"])
    if "tool" in data and "poetry" in data["tool"] and "dependencies" in data["tool"]["poetry"]:
        for name, version in data["tool"]["poetry"]["dependencies"].items():
            if name.lower() != "python": deps.append(name)
    return deps

def parse_setup_py(content: str) -> List[str]:
    deps = []
    try: tree = ast.parse(content)
    except Exception: return deps
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "setup":
            for kw in node.keywords:
                if kw.arg == "install_requires" and isinstance(kw.value, ast.List):
                    for elt in kw.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            deps.append(elt.value)
    return deps

def parse_setup_cfg(content: str) -> List[str]:
    parser = configparser.ConfigParser()
    parser.read_string(content)
    if parser.has_option("options", "install_requires"):
        return [line.strip() for line in parser.get("options", "install_requires").splitlines() if line.strip()]
    return []

def parse_requirements_txt(content: str) -> List[str]:
    return [line.split(" #")[0].strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]

# --- Stage 3: Recursive Resolution & Graph Building ---

def get_dependencies_for_package(pkg_name: str, config: Config, test_data: Optional[Dict[str, List[str]]]) -> List[str]:
    """Abstracts how we get dependencies based on the mode."""
    
    # TEST MODE (Requirement 5)
    if config.repo_mode == "test" and test_data is not None:
        return test_data.get(pkg_name, [])

    # URL MODE (Resolving arbitrary packages requires PyPI API to find their repo)
    if config.repo_mode == "url":
        # 1. Find the repository URL via PyPI JSON API
        try:
            req = urllib.request.Request(f"https://pypi.org/pypi/{pkg_name}/json", headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                pypi_data = json.loads(response.read().decode('utf-8'))
                
                # Extract repo URL (looking for Source, Homepage, etc.)
                repo_url = None
                urls = pypi_data.get("info", {}).get("project_urls", {})
                for key in ["Source", "Source Code", "Homepage", "Repository"]:
                    if key in urls and "github.com" in urls[key]:
                        repo_url = urls[key]
                        break
                
                if repo_url:
                    # 2. Fetch repo files and parse (reuse Stage 2 logic)
                    for filename, parser in [("pyproject.toml", parse_pyproject_toml), ("setup.py", parse_setup_py), ("requirements.txt", parse_requirements_txt)]:
                        content = fetch_content_from_github(repo_url, filename)
                        if content:
                            return parser(content)
        except Exception:
            pass # Silently fail if PyPI or GitHub fails for a dependency
        return []

    return []

def build_graph_dfs(
    pkg_name: str, 
    current_depth: int, 
    config: Config, 
    test_data: Optional[Dict[str, List[str]]], 
    graph: Dict[str, List[str]], 
    visited: Set[str]
):
    """
    Requirement 1: Recursive DFS algorithm.
    Requirement 2: Respects max_depth.
    Requirement 4: Handles cycles correctly.
    """
    # Stop if max depth is reached
    if current_depth > config.max_depth:
        return

    # Stop if we've already processed this node (prevents infinite cycles)
    if pkg_name in visited:
        return
        
    visited.add(pkg_name)
    if pkg_name not in graph:
        graph[pkg_name] = []

    # Get direct dependencies
    deps = get_dependencies_for_package(pkg_name, config, test_data)

    # Clean dependency names (strip version specs like "requests >= 2.0" -> "requests")
    clean_deps = []
    for dep in deps:
        clean_name = dep.split(">")[0].split("<")[0].split("=")[0].split("!")[0].strip()
        clean_deps.append(clean_name)

    for dep in clean_deps:
        # Requirement 3: Filter packages by substring
        if config.filter_substring and config.filter_substring.lower() in dep.lower():
            continue
            
        graph[pkg_name].append(dep)
        
        # Recursive call
        build_graph_dfs(dep, current_depth + 1, config, test_data, graph, visited)

def load_test_data(file_path: str) -> Dict[str, List[str]]:
    """Parses the custom test file format (Requirement 5)."""
    data = {}
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Test file not found: {file_path}")
        
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if ":" in line:
                pkg, deps_str = line.split(":", 1)
                pkg = pkg.strip().upper()
                deps = [d.strip().upper() for d in deps_str.split(",") if d.strip()]
                data[pkg] = deps
    return data

def main():
    parser = argparse.ArgumentParser(description="Dependency Graph Visualizer - Stage 3")
    parser.add_argument("--config", type=str, default="config.toml", help="Path to TOML config")
    args = parser.parse_args()

    try:
        config = load_config(Path(args.config))
    except Exception as e:
        print(f"Configuration Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"--- Stage 3: Graph Building (Mode: {config.repo_mode}) ---")
    print(f"Root Package: {config.package_name}")
    print(f"Max Depth: {config.max_depth}")
    print(f"Filter Substring: '{config.filter_substring}'\n")

    test_data = None
    if config.repo_mode == "test":
        try:
            test_data = load_test_data(config.repo_url_or_path)
        except Exception as e:
            print(f"Error loading test data: {e}", file=sys.stderr)
            sys.exit(1)

    graph: Dict[str, List[str]] = {}
    visited: Set[str] = set()

    # Start DFS from the root package at depth 0
    build_graph_dfs(config.package_name, 0, config, test_data, graph, visited)

    print("--- Resulting Dependency Graph (Adjacency List) ---")
    for pkg, deps in graph.items():
        print(f"{pkg} -> {deps}")
        
if __name__ == "__main__":
    main()