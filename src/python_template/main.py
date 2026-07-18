from .config.main import get_config
from .config.types import Config


def run(config: Config) -> None:
    print("Hello from python-template!")
    print(f"The default model is {config.default_model}.")


def main() -> None:
    run(get_config())


if __name__ == "__main__":
    main()
