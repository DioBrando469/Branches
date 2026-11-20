"""Run the dependency graph visualization tool."""
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
		print(
			"Error: 'tomli' module required. "
			"Run: pip install tomli"
		)
		sys.exit(1)

try:
	from PIL import Image, ImageDraw, ImageFont
except ImportError:
	print(
		"Error: 'Pillow' module required. "
		"Run: pip install Pillow"
	)
	sys.exit(1)

DEFAULT_TIMEOUT = 5
NODE_WIDTH = 140
NODE_HEIGHT = 40
X_SPACING = 50
Y_SPACING = 80
FONT_SIZE = 14
START_X_OFFSET = 50
IMG_PADDING = 50
LINE_WIDTH = 2


@dataclass
class Config:
	"""Store configuration settings for the application."""

	package_name: str
	repo_url_or_path: str
	repo_mode: str
	output_image_name: str
	max_depth: int
	filter_substring: str
	show_download_order: bool = False
	plantuml_output_file: str = "graph.puml"


def validate_config(
	config_data: Dict[str, Any]
) -> Config:
	"""Validate the TOML data and return a Config object."""
	required = [
		"package_name",
		"repo_url_or_path",
		"repo_mode",
		"output_image_name",
		"max_depth",
	]
	for key in required:
		if key not in config_data:
			raise ValueError(
				f"Missing required parameter: '{key}'"
			)

	valid_modes = ["url", "local_file", "test"]
	if config_data["repo_mode"] not in valid_modes:
		raise ValueError(
			"'repo_mode' must be 'url', "
			"'local_file', or 'test'."
		)

	depth = config_data["max_depth"]
	if not isinstance(depth, int) or depth <= 0:
		raise ValueError(
			"'max_depth' must be a positive integer."
		)

	filter_str = config_data.get("filter_substring", "")
	show_order = config_data.get("show_download_order", False)
	puml_file = config_data.get(
		"plantuml_output_file", "graph.puml"
	)

	return Config(
		package_name=config_data["package_name"],
		repo_url_or_path=config_data["repo_url_or_path"],
		repo_mode=config_data["repo_mode"],
		output_image_name=config_data["output_image_name"],
		max_depth=depth,
		filter_substring=filter_str,
		show_download_order=show_order,
		plantuml_output_file=puml_file,
	)


def load_config(file_path: Path) -> Config:
	"""Load the configuration from a TOML file."""
	if not file_path.exists():
		raise FileNotFoundError(
			f"Config file not found: {file_path}"
		)
	with open(file_path, "rb") as f:
		return validate_config(tomllib.load(f))


def fetch_github_file(
	repo_url: str, filename: str
) -> Optional[str]:
	"""Fetch raw file content from a GitHub repository."""
	if "github.com" not in repo_url:
		return None

	for branch in ["main", "master"]:
		raw_base = repo_url.replace(
			"github.com", "raw.githubusercontent.com"
		)
		raw_url = f"{raw_base}/{branch}/{filename}"

		try:
			req = urllib.request.Request(
				raw_url,
				headers={"User-Agent": "Mozilla/5.0"},
			)
			with urllib.request.urlopen(
				req, timeout=DEFAULT_TIMEOUT
			) as response:
				if response.status == 200:
					return response.read().decode("utf-8")
		except Exception:
			continue
	return None


def parse_pyproject_toml(content: str) -> List[str]:
	"""Parse dependencies from pyproject.toml."""
	deps = []
	try:
		data = tomllib.loads(content)
	except Exception:
		return deps

	proj = data.get("project", {})
	if "dependencies" in proj:
		deps.extend(proj["dependencies"])

	poetry = data.get("tool", {}).get("poetry", {})
	poetry_deps = poetry.get("dependencies", {})

	for name in poetry_deps.keys():
		if name.lower() != "python":
			deps.append(name)
	return deps


def parse_setup_py(content: str) -> List[str]:
	"""Parse dependencies from setup.py."""
	deps = []
	try:
		tree = ast.parse(content)
	except Exception:
		return deps

	for node in ast.walk(tree):
		if not isinstance(node, ast.Call):
			continue
		if getattr(node.func, "id", "") != "setup":
			continue

		for kw in node.keywords:
			if kw.arg != "install_requires":
				continue
			if not isinstance(kw.value, ast.List):
				continue

			for elt in kw.value.elts:
				if not isinstance(elt, ast.Constant):
					continue
				if isinstance(elt.value, str):
					deps.append(elt.value)
	return deps


def parse_setup_cfg(content: str) -> List[str]:
	"""Parse dependencies from setup.cfg."""
	parser = configparser.ConfigParser()
	try:
		parser.read_string(content)
	except Exception:
		return []

	if parser.has_option("options", "install_requires"):
		raw = parser.get("options", "install_requires")
		result = []
		for line in raw.splitlines():
			if line.strip():
				result.append(line.strip())
		return result
	return []


def parse_requirements_txt(content: str) -> List[str]:
	"""Parse dependencies from requirements.txt."""
	result = []
	for line in content.splitlines():
		line = line.strip()
		if line and not line.startswith("#"):
			clean = line.split(" #")[0].strip()
			result.append(clean)
	return result


def get_pypi_repo(pkg_name: str) -> Optional[str]:
	"""Find a GitHub URL via the PyPI API."""
	try:
		url = f"https://pypi.org/pypi/{pkg_name}/json"
		req = urllib.request.Request(
			url, headers={"User-Agent": "Mozilla/5.0"}
		)
		with urllib.request.urlopen(
			req, timeout=DEFAULT_TIMEOUT
		) as response:
			data = json.loads(response.read().decode("utf-8"))
			urls = data.get("info", {}).get("project_urls", {})
			keys = ["Source", "Homepage", "Repository"]

			for key in keys:
				if key in urls:
					if "github.com" in urls[key]:
						return urls[key]
	except Exception:
		pass
	return None


def parse_repo_files(repo_url: str) -> List[str]:
	"""Fetch and parse known dependency files."""
	parsers = [
		("pyproject.toml", parse_pyproject_toml),
		("setup.py", parse_setup_py),
		("setup.cfg", parse_setup_cfg),
	]
	for filename, parser_func in parsers:
		content = fetch_github_file(repo_url, filename)
		if content:
			return parser_func(content)
	return []


def get_package_deps(
	pkg_name: str,
	config: Config,
	test_data: Optional[Dict[str, List[str]]],
) -> List[str]:
	"""Determine direct dependencies based on mode."""
	if config.repo_mode == "test" and test_data:
		return test_data.get(pkg_name, [])

	if config.repo_mode == "url":
		is_root = pkg_name.lower() == config.package_name.lower()
		if is_root:
			repo_url = config.repo_url_or_path
		else:
			repo_url = get_pypi_repo(pkg_name)

		if repo_url:
			return parse_repo_files(repo_url)
	return []


def build_graph_dfs(
	pkg_name: str,
	depth: int,
	config: Config,
	test_data: Optional[Dict[str, List[str]]],
	graph: Dict[str, List[str]],
	visited: Set[str],
) -> None:
	"""Recursively build the dependency graph."""
	if depth > config.max_depth or pkg_name in visited:
		return

	visited.add(pkg_name)
	if pkg_name not in graph:
		graph[pkg_name] = []

	deps = get_package_deps(pkg_name, config, test_data)
	clean_deps = []
	for dep in deps:
		clean = dep.split(">")[0].split("<")[0]
		clean = clean.split("=")[0].split("!")[0]
		clean_deps.append(clean.strip())

	filter_str = config.filter_substring.lower()
	for dep in clean_deps:
		if filter_str and filter_str in dep.lower():
			continue

		graph[pkg_name].append(dep)
		build_graph_dfs(
			dep,
			depth + 1,
			config,
			test_data,
			graph,
			visited,
		)


def load_test_data(file_path: Path) -> Dict[str, List[str]]:
	"""Load mock data from a text file."""
	data = {}
	if not file_path.exists():
		raise FileNotFoundError(
			f"Test file not found: {file_path}"
		)

	with open(file_path, "r", encoding="utf-8") as f:
		for line in f:
			if ":" in line:
				pkg, deps_str = line.split(":", 1)
				deps_list = []
				for d in deps_str.split(","):
					if d.strip():
						deps_list.append(d.strip().upper())
				data[pkg.strip().upper()] = deps_list
	return data


def topo_dfs(
	node: str,
	graph: Dict[str, List[str]],
	visited: Set[str],
	order: List[str],
) -> None:
	"""Perform a recursive DFS for topological sorting."""
	if node in visited:
		return

	visited.add(node)
	for dep in graph.get(node, []):
		topo_dfs(dep, graph, visited, order)
	order.append(node)


def get_download_order(
	graph: Dict[str, List[str]]
) -> List[str]:
	"""Calculate the installation order."""
	visited: Set[str] = set()
	order: List[str] = []

	for node in graph:
		topo_dfs(node, graph, visited, order)
	return order


def generate_plantuml(
	graph: Dict[str, List[str]], filename: str
) -> None:
	"""Generate a PlantUML text file."""
	lines = [
		"@startuml",
		"skinparam linetype ortho",
		"left to right direction",
	]
	for pkg, deps in graph.items():
		for dep in deps:
			lines.append(f'"{pkg}" --> "{dep}"')
	lines.append("@enduml")

	with open(filename, "w", encoding="utf-8") as f:
		f.write("\n".join(lines))
	print(f"[+] PlantUML saved to: {filename}")


def calc_layout(graph: Dict[str, List[str]]) -> Dict:
	"""Calculate coordinates for all nodes."""
	pos = {}
	all_deps = {dep for deps in graph.values() for dep in deps}
	roots = [node for node in graph if node not in all_deps]
	root = roots[0] if roots else list(graph.keys())[0]

	def assign(node: str, level: int, x: float) -> float:
		if node in pos:
			return x

		pos[node] = (x, level * Y_SPACING)
		curr_x = x

		for dep in graph.get(node, []):
			curr_x = assign(dep, level + 1, curr_x)

		return curr_x + X_SPACING + NODE_WIDTH

	assign(root, 0, START_X_OFFSET)
	return pos


def draw_edges(draw, pos: Dict, graph: Dict) -> None:
	"""Draw connecting lines between nodes."""
	for pkg, deps in graph.items():
		if pkg not in pos:
			continue
		x1, y1 = pos[pkg]

		for dep in deps:
			if dep not in pos:
				continue
			x2, y2 = pos[dep]

			st_x = x1 + NODE_WIDTH // 2
			st_y = y1 + NODE_HEIGHT
			en_x = x2 + NODE_WIDTH // 2

			draw.line(
				[(st_x, st_y), (en_x, y2)],
				fill="gray",
				width=LINE_WIDTH,
			)


def draw_nodes(draw, pos: Dict, font) -> None:
	"""Draw boxes and text for each node."""
	for node, (x, y) in pos.items():
		box = [x, y, x + NODE_WIDTH, y + NODE_HEIGHT]
		draw.rectangle(
			box,
			fill="lightblue",
			outline="black",
			width=LINE_WIDTH,
		)

		bbox = draw.textbbox((0, 0), node, font=font)
		text_w = bbox[2] - bbox[0]
		text_h = bbox[3] - bbox[1]

		text_x = x + (NODE_WIDTH - text_w) / 2
		text_y = y + (NODE_HEIGHT - text_h) / 2

		draw.text(
			(text_x, text_y),
			node,
			fill="black",
			font=font,
		)


def generate_png(
	graph: Dict[str, List[str]], filename: str
) -> None:
	"""Generate the visual PNG image."""
	if not graph:
		print("[-] Graph is empty. Skipping PNG.")
		return

	pos = calc_layout(graph)
	max_x = max(p[0] for p in pos.values())
	max_y = max(p[1] for p in pos.values())

	img_x = max_x + NODE_WIDTH + IMG_PADDING
	img_y = max_y + NODE_HEIGHT + IMG_PADDING

	img = Image.new("RGB", (img_x, img_y), "white")
	draw = ImageDraw.Draw(img)

	try:
		font = ImageFont.truetype("arial.ttf", FONT_SIZE)
	except IOError:
		font = ImageFont.load_default()

	draw_edges(draw, pos, graph)
	draw_nodes(draw, pos, font)

	img.save(filename)
	print(f"[+] PNG saved to: {filename}")


def main() -> None:
	"""Run the main execution logic."""
	parser = argparse.ArgumentParser()
	parser.add_argument(
		"--config",
		type=str,
		default="tests/config.toml",
	)
	args = parser.parse_args()

	conf_path = Path(args.config)
	if not conf_path.exists():
		script_dir = Path(__file__).resolve().parent
		root = script_dir.parent
		root_conf = root / args.config
		test_conf = root / "tests" / conf_path.name

		if root_conf.exists():
			conf_path = root_conf
		elif test_conf.exists():
			conf_path = test_conf

	try:
		config = load_config(conf_path)
	except Exception as e:
		print(f"Config Error: {e}", file=sys.stderr)
		abs_path = conf_path.absolute()
		print(f"Looked at: {abs_path}", file=sys.stderr)
		sys.exit(1)

	test_data = None
	if config.repo_mode == "test":
		file_path = conf_path.parent
		file_path = file_path / config.repo_url_or_path
		test_data = load_test_data(file_path)

	graph: Dict[str, List[str]] = {}
	visited: Set[str] = set()

	print(f"--- Mode: {config.repo_mode} ---")
	print(f"Analyzing {config.package_name}...\n")

	build_graph_dfs(
		config.package_name,
		0,
		config,
		test_data,
		graph,
		visited,
	)

	print("--- Graph Data ---")
	for pkg, deps in graph.items():
		print(f"{pkg} -> {deps}")

	if config.show_download_order:
		print("\n--- Download Order ---")
		order = get_download_order(graph)
		print(" -> ".join(order))

	print("\n--- Visualizations ---")
	generate_plantuml(
		graph, config.plantuml_output_file
	)
	generate_png(graph, config.output_image_name)


if __name__ == "__main__":
	main()