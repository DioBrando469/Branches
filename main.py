import argparse
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Any

# Handle TOML parsing based on Python version
try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # Fallback for older Python versions
    except ModuleNotFoundError:
        print("Error: 'tomli' module is required for Python < 3.11. Install it via 'pip install tomli'", file=sys.stderr)
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
    """Validates the TOML configuration and returns a Config object."""
    required_keys = [
        "package_name", "repo_url_or_path", "repo_mode", 
        "output_image_name", "max_depth"
    ]
    
    # 1. Check for missing required keys
    for key in required_keys:
        if key not in config_data:
            raise ValueError(f"Missing required configuration parameter: '{key}'")

    # 2. Extract and validate types
    package_name = config_data["package_name"]
    if not isinstance(package_name, str) or not package_name.strip():
        raise ValueError("'package_name' must be a non-empty string.")

    repo_url_or_path = config_data["repo_url_or_path"]
    if not isinstance(repo_url_or_path, str) or not repo_url_or_path.strip():
        raise ValueError("'repo_url_or_path' must be a non-empty string.")

    repo_mode = config_data["repo_mode"]
    if repo_mode not in ["url", "local_file"]:
        raise ValueError("'repo_mode' must be either 'url' or 'local_file'.")

    output_image_name = config_data["output_image_name"]
    if not isinstance(output_image_name, str) or not output_image_name.strip():
        raise ValueError("'output_image_name' must be a non-empty string.")

    max_depth = config_data["max_depth"]
    if not isinstance(max_depth, int) or max_depth <= 0:
        raise ValueError("'max_depth' must be a positive integer greater than 0.")

    # Optional parameter
    filter_substring = config_data.get("filter_substring", "")
    if not isinstance(filter_substring, str):
        raise ValueError("'filter_substring' must be a string.")

    return Config(
        package_name=package_name,
        repo_url_or_path=repo_url_or_path,
        repo_mode=repo_mode,
        output_image_name=output_image_name,
        max_depth=max_depth,
        filter_substring=filter_substring
    )

def load_config(file_path: Path) -> Config:
    """Reads and parses the TOML file."""
    if not file_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {file_path}")
    
    try:
        with open(file_path, "rb") as f:
            config_data = tomllib.load(f)
    except Exception as e:
        raise ValueError(f"Failed to parse TOML file: {e}")

    return validate_config(config_data)

def main():
    parser = argparse.ArgumentParser(description="Dependency Graph Visualizer - Stage 1")
    parser.add_argument(
        "--config", 
        type=str, 
        default="config.toml", 
        help="Path to the TOML configuration file (default: config.toml)"
    )
    
    args = parser.parse_args()
    config_path = Path(args.config)

    try:
        # Load and validate configuration
        config = load_config(config_path)
        
        # Output parameters in key-value format as required by Stage 1
        print("--- Application Configuration ---")
        print(f"package_name: {config.package_name}")
        print(f"repo_url_or_path: {config.repo_url_or_path}")
        print(f"repo_mode: {config.repo_mode}")
        print(f"output_image_name: {config.output_image_name}")
        print(f"max_depth: {config.max_depth}")
        print(f"filter_substring: {config.filter_substring}")
        print("---------------------------------")
        
    except (FileNotFoundError, ValueError) as e:
        # Error handling for all parameters
        print(f"Configuration Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()