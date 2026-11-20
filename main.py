import argparse
import ast
import configparser
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Set, Tuple

# Standard library TOML parser
try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        print("Error: 'tomli' module required. Run: pip install tomli", file=sys.stderr)
        sys.exit(1)

# Import Pillow for image generation
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Error: 'Pillow' module required for PNG generation. Run: pip install Pillow", file=sys.stderr)
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
    plantuml_output_file: str = "graph.puml"

def validate_config(config_data: Dict[str, Any]) -> Config:
    required_keys = ["package_name", "repo_url_or_path", "repo_mode", "output_image_name", "max_depth"]
    for key in required_keys:
        if key not in config_data:
            raise ValueError(f"Missing required configuration parameter: '{key}'")
    
    if config_data["repo_mode"] not in ["url", "local_file", "test"]:
        raise ValueError("'repo_mode' must be 'url', 'local_file', or 'test'.")
    
    return Config(
        package_name=config_data["package_name"],
        repo_url_or_path=config_data["repo_url_or_path"],
        repo_mode=config_data["repo_mode"],
        output_image_name=config_data["output_image_name"],
        max_depth=config_data["max_depth"],
        filter_substring=config_data.get("filter_substring", ""),
        show_download_order=config_data.get("show_download_order", False),
        plantuml_output_file=config_data.get("plantuml_output_file", "graph.puml")
    )

def load_config(file_path: Path) -> Config:
    if not file_path.exists(): raise FileNotFoundError(f"Configuration file not found: {file_path}")
    with open(file_path, "rb") as f: return validate_config(tomllib.load(f))

# --- STAGE 2: DATA COLLECTION (Standard Library Only) ---

def fetch_content_from_github(repo_url: str, filename: str) -> Optional[str]:
    if "github.com" not in repo_url: return None
    for branch in ["main", "master"]:
        raw_url = f"{repo_url.replace('github.com', 'raw.githubusercontent.com')}/{branch}/{filename}"
        try:
            req = urllib.request.Request(raw_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200: return response.read().decode('utf-8')
        except Exception: continue
    return None

def parse_pyproject_toml(content: str) -> List[str]:
    deps = []
    try: data = tomllib.loads(content)
    except Exception: return deps
    if "project" in data and "dependencies" in data["project"]:
        deps.extend(data["project"]["dependencies"])
    if "tool" in data and "poetry" in data["tool"] and "dependencies" in data["tool"]["poetry"]:
        for name in data["tool"]["poetry"]["dependencies"].keys():
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
    try: parser.read_string(content)
    except Exception: return []
    if parser.has_option("options", "install_requires"):
        return [line.strip() for line in parser.get("options", "install_requires").splitlines() if line.strip()]
    return []

def parse_requirements_txt(content: str) -> List[str]:
    return [line.split(" #")[0].strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]

# --- STAGE 3 & 4: GRAPH BUILDING & TOPOLOGICAL SORT ---

def get_dependencies_for_package(pkg_name: str, config: Config, test_data: Optional[Dict[str, List[str]]]) -> List[str]:
    if config.repo_mode == "test" and test_data is not None:
        return test_data.get(pkg_name, [])
    if config.repo_mode == "url":
        repo_url = config.repo_url_or_path if pkg_name.lower() == config.package_name.lower() else None
        if not repo_url:
            try:
                req = urllib.request.Request(f"https://pypi.org/pypi/{pkg_name}/json", headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=5) as response:
                    pypi_data = json.loads(response.read().decode('utf-8'))
                    urls = pypi_data.get("info", {}).get("project_urls", {})
                    for key in ["Source", "Source Code", "Homepage", "Repository"]:
                        if key in urls and "github.com" in urls[key]: repo_url = urls[key]; break
            except Exception: pass 
        if repo_url:
            for filename, parser in [("pyproject.toml", parse_pyproject_toml), ("setup.py", parse_setup_py), ("setup.cfg", parse_setup_cfg)]:
                content = fetch_content_from_github(repo_url, filename)
                if content: return parser(content)
    return []

def build_graph_dfs(pkg_name: str, current_depth: int, config: Config, test_data: Optional[Dict[str, List[str]]], graph: Dict[str, List[str]], visited: Set[str]):
    if current_depth > config.max_depth or pkg_name in visited: return
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
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            if ":" in line:
                pkg, deps_str = line.split(":", 1)
                data[pkg.strip().upper()] = [d.strip().upper() for d in deps_str.split(",") if d.strip()]
    return data

def get_download_order(graph: Dict[str, List[str]]) -> List[str]:
    visited, temp_visited, order = set(), set(), []
    def dfs(node):
        if node in temp_visited: return
        if node in visited: return
        temp_visited.add(node)
        for dep in graph.get(node, []): dfs(dep)
        temp_visited.remove(node); visited.add(node); order.append(node)
    for node in graph: dfs(node)
    return order

# --- STAGE 5: VISUALIZATION (Pillow + PlantUML) ---

def generate_plantuml(graph: Dict[str, List[str]], filename: str):
    lines = ["@startuml", "skinparam linetype ortho", "left to right direction"]
    for pkg, deps in graph.items():
        for dep in deps: lines.append(f'"{pkg}" --> "{dep}"')
    lines.append("@enduml")
    with open(filename, "w", encoding="utf-8") as f: f.write("\n".join(lines))
    print(f"[+] PlantUML text saved to: {filename}")

def generate_png(graph: Dict[str, List[str]], filename: str):
    """Generates a PNG image using Pillow with a simple hierarchical layout."""
    if not graph:
        print("[-] Graph is empty, skipping PNG generation.")
        return

    # Layout calculations
    node_width, node_height = 140, 40
    x_spacing, y_spacing = 50, 80
    
    # Calculate node positions (simple level-based layout)
    positions = {}
    level_y = {}
    
    # Root is the node with no incoming edges
    all_deps = {dep for deps in graph.values() for dep in deps}
    root = [node for node in graph if node not in all_deps][0] if [node for node in graph if node not in all_deps] else list(graph.keys())[0]
    
    def assign_pos(node, level, x_offset):
        if node in positions: return x_offset
        positions[node] = (x_offset, level * y_spacing)
        level_y[level] = max(level_y.get(level, 0), x_offset)
        current_x = x_offset
        for dep in graph.get(node, []):
            current_x = assign_pos(dep, level + 1, current_x)
        return current_x + x_spacing + node_width

    assign_pos(root, 0, 50)
    
    # Calculate image size
    max_x = max([p[0] for p in positions.values()]) + node_width + 50
    max_y = max([p[1] for p in positions.values()]) + node_height + 50
    
    img = Image.new("RGB", (max_x, max_y), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except IOError:
        font = ImageFont.load_default()

    # Draw edges
    for pkg, deps in graph.items():
        if pkg not in positions: continue
        x1, y1 = positions[pkg]
        for dep in deps:
            if dep not in positions: continue
            x2, y2 = positions[dep]
            # Draw line from bottom of pkg to top of dep
            draw.line([(x1 + node_width//2, y1 + node_height), (x2 + node_width//2, y2)], fill="gray", width=2)
            
    # Draw nodes
    for node, (x, y) in positions.items():
        draw.rectangle([x, y, x + node_width, y + node_height], fill="lightblue", outline="black", width=2)
        # Center text
        bbox = draw.textbbox((0, 0), node, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        draw.text((x + (node_width - text_w)/2, y + (node_height - text_h)/2), node, fill="black", font=font)

    img.save(filename)
    print(f"[+] PNG image saved to: {filename}")

# --- MAIN EXECUTION ---
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str, default="config.toml")
    args = parser.parse_args()

    try: config = load_config(Path(args.config))
    except Exception as e: print(f"Config Error: {e}", file=sys.stderr); sys.exit(1)

    test_data = load_test_data(config.repo_url_or_path) if config.repo_mode == "test" else None
    graph, visited = {}, set()

    print(f"Analyzing {config.package_name}...")
    build_graph_dfs(config.package_name, 0, config, test_data, graph, visited)

    print("\n--- Graph Data ---")
    for pkg, deps in graph.items(): print(f"{pkg} -> {deps}")

    if config.show_download_order:
        print("\n--- Download Order ---")
        print(" -> ".join(get_download_order(graph)))

    print("\n--- Generating Visualizations ---")
    generate_plantuml(graph, config.plantuml_output_file)
    generate_png(graph, config.output_image_name)

if __name__ == "__main__":
    main()