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
    show_download_order: bool = False

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
        filter_substring=config_data.get("filter_substring", ""),
        show_download_order=config_data.get("show_download_order", False)
    )

def load_config(file_path: Path) -> Config:
    if not file_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {file_path}")
    with open(file_path, "rb") as f:
        return validate_config(tomllib.load(f))

# --- Stage 2 & 3: Data Collection & Graph Building ---
# (Assuming previous functions like fetch_content_from_github, parsers, build_graph_dfs are here)
# For brevity, they are condensed in this snippet.

def get_dependencies_for_package(pkg_name: str, config: Config, test_data: Optional[Dict[str, List[str]]]) -> List[str]:
    if config.repo_mode == "test" and test_data is not None:
        return test_data.get(pkg_name, [])
    # URL mode logic (Stage 3) would go here...
    return []

def build_graph_dfs(pkg_name: str, current_depth: int, config: Config, test_data: Optional[Dict[str, List[str]]], graph: Dict[str, List[str]], visited: Set[str]):
    if current_depth > config.max_depth: return
    if pkg_name in visited: return
    
    visited.add(pkg_name)
    if pkg_name not in graph: graph[pkg_name] = []

    deps = get_dependencies_for_package(pkg_name, config, test_data)
    clean_deps = [dep.split(">")[0].split("<")[0].split("=")[0].split("!")[0].strip() for dep in deps]

    for dep in clean_deps:
        if config.filter_substring and config.filter_substring.lower() in dep.lower(): continue
        graph[pkg_name].append(dep)
        build_graph_dfs(dep, current_depth + 1, config, test_data, graph, visited)

def load_test_data(file_path: str) -> Dict[str, List[str]]:
    data = {}
    path = Path(file_path)
    if not path.exists(): raise FileNotFoundError(f"Test file not found: {file_path}")
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if ":" in line:
                pkg, deps_str = line.split(":", 1)
                data[pkg.strip().upper()] = [d.strip().upper() for d in deps_str.split(",") if d.strip()]
    return data

# --- Stage 4: Topological Sort (Download Order) ---
def get_download_order(graph: Dict[str, List[str]]) -> List[str]:
    """
    Performs a DFS-based topological sort to determine installation order.
    Dependencies are added to the order list *before* the packages that require them.
    """
    visited = set()
    temp_visited = set() # For cycle detection
    order = []
    cycle_detected = False

    def dfs(node: str):
        nonlocal cycle_detected
        if node in temp_visited:
            cycle_detected = True
            return
        if node in visited:
            return

        temp_visited.add(node)
        for dep in graph.get(node, []):
            dfs(dep)
        
        temp_visited.remove(node)
        visited.add(node)
        order.append(node)

    for node in graph:
        dfs(node)

    if cycle_detected:
        print("  [Warning] Cycles detected in the graph. Topological sort may be incomplete or broken.")
    
    return order

def main():
    parser = argparse.ArgumentParser(description="Dependency Graph Visualizer - Stage 4")
    parser.add_argument("--config", type=str, default="config.toml")
    args = parser.parse_args()

    try:
        config = load_config(Path(args.config))
    except Exception as e:
        print(f"Configuration Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"--- Stage 4: Download Order (Mode: {config.repo_mode}) ---")
    print(f"Root Package: {config.package_name}\n")

    test_data = None
    if config.repo_mode == "test":
        try:
            test_data = load_test_data(config.repo_url_or_path)
        except Exception as e:
            print(f"Error loading test data: {e}", file=sys.stderr)
            sys.exit(1)

    graph: Dict[str, List[str]] = {}
    visited: Set[str] = set()

    build_graph_dfs(config.package_name, 0, config, test_data, graph, visited)

    print("--- Dependency Graph (Adjacency List) ---")
    for pkg, deps in graph.items():
        print(f"{pkg} -> {deps}")

    if config.show_download_order:
        print("\n--- Calculated Download Order (Install Dependencies First) ---")
        order = get_download_order(graph)
        print(" -> ".join(order))

if __name__ == "__main__":
    main()