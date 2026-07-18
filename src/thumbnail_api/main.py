from .config.main import get_config
from .config.types import Config


def run(config: Config) -> None:
    print(f"Hello from thumbnail-api! The environment is {config.environment}.")


def main() -> None:
    run(get_config())


if __name__ == "__main__":
    main()
