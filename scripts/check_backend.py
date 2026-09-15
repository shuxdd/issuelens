import subprocess


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


run(["pytest"])
run(["ruff", "check", "."])
run(["mypy"])

