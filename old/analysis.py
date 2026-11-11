import subprocess
import sys
from pathlib import Path


def main() -> None:
	project_dir = Path(__file__).resolve().parent
	analysis_scripts = sorted(project_dir.glob("analysis_*.py"))

	for script in analysis_scripts:
		print(f"\nEjecutando {script.name}...", flush=True)
		subprocess.run([sys.executable, str(script)], cwd=project_dir, check=True)


if __name__ == "__main__":
	main()