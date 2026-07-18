from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Configuration for the project.

    All configuration values are loaded from the .env file or environment variable overrides.
    """

    model_config = SettingsConfigDict(env_file=".env")

    environment: str = Field(description="The environment to run the project in.")
