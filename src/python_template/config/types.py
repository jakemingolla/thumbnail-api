from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Configuration for the project.

    All configuration values are loaded from the .env file or environment variable overrides.
    """

    model_config = SettingsConfigDict(env_file=".env")

    openai_api_key: SecretStr = Field(description="The API key for the OpenAI API.")
    default_model: str = Field(
        default="gpt-4o-mini",
        description="The default model to use for the OpenAI API.",
    )
