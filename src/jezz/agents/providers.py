"""Multi-provider Pydantic AI model instantiation with SecretStr injection and TLS retry."""

import asyncio
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Annotated, Final

from pydantic import Field, SecretStr
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, infer_model
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from jezz.guards import IdentifierStr, InstanceGuard, jezz_callable
from jezz.secrets import GuardedSecretStr

type GuardedProviderModel = Annotated[Model, InstanceGuard(Model)]
type _AuthModelBuilder = Callable[[str, SecretStr], Model]


def _infer_google_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.google import GoogleProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: GoogleProvider(api_key=api_key.get_secret_value()),
    )


def _infer_google_cloud_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.google_cloud import GoogleCloudProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: GoogleCloudProvider(api_key=api_key.get_secret_value()),
    )


def _infer_openai_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.openai import OpenAIProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: OpenAIProvider(api_key=api_key.get_secret_value()),
    )


def _infer_anthropic_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.anthropic import AnthropicProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: AnthropicProvider(api_key=api_key.get_secret_value()),
    )


def _infer_openrouter_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.openrouter import OpenRouterProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: OpenRouterProvider(api_key=api_key.get_secret_value()),
    )


def _infer_groq_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.groq import GroqProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: GroqProvider(api_key=api_key.get_secret_value()),
    )


def _infer_mistral_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.mistral import MistralProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: MistralProvider(api_key=api_key.get_secret_value()),
    )


def _infer_cohere_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.cohere import CohereProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: CohereProvider(api_key=api_key.get_secret_value()),
    )


def _infer_deepseek_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.deepseek import DeepSeekProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: DeepSeekProvider(api_key=api_key.get_secret_value()),
    )


def _infer_xai_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.xai import XaiProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: XaiProvider(api_key=api_key.get_secret_value()),
    )


def _infer_cerebras_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.cerebras import CerebrasProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: CerebrasProvider(api_key=api_key.get_secret_value()),
    )


def _infer_fireworks_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.fireworks import FireworksProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: FireworksProvider(api_key=api_key.get_secret_value()),
    )


def _infer_together_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.together import TogetherProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: TogetherProvider(api_key=api_key.get_secret_value()),
    )


_AUTH_MODEL_BUILDERS: Final[Mapping[str, _AuthModelBuilder]] = MappingProxyType(
    {
        "google": _infer_google_model,
        "google-decisions": _infer_google_model,
        "google-cloud": _infer_google_cloud_model,
        "openai": _infer_openai_model,
        "openai-chat": _infer_openai_model,
        "openai-responses": _infer_openai_model,
        "openai-decisions": _infer_openai_model,
        "anthropic": _infer_anthropic_model,
        "openrouter": _infer_openrouter_model,
        "groq": _infer_groq_model,
        "mistral": _infer_mistral_model,
        "cohere": _infer_cohere_model,
        "deepseek": _infer_deepseek_model,
        "xai": _infer_xai_model,
        "cerebras": _infer_cerebras_model,
        "fireworks": _infer_fireworks_model,
        "together": _infer_together_model,
    }
)


@jezz_callable
def infer_authenticated_model(
    model_name: IdentifierStr,
    *,
    api_key: GuardedSecretStr | None = None,
) -> GuardedProviderModel:
    """Instantiate a Pydantic AI ``Model`` without mutating process ``os.environ``."""
    if api_key is None or model_name == "test":
        return infer_model(model_name)
    active_key: SecretStr = api_key
    if ":" not in model_name:
        msg = (
            f"Expected 'provider:model' identifier when injecting a vault API key: '{model_name}'."
        )
        raise UserError(msg)
    provider_prefix = model_name.split(":", maxsplit=1)[0]
    if provider_prefix.startswith("gateway/"):
        from pydantic_ai.providers.gateway import gateway_provider

        upstream = provider_prefix.removeprefix("gateway/")
        return infer_model(
            model_name,
            provider_factory=lambda _: gateway_provider(
                upstream, api_key=active_key.get_secret_value()
            ),
        )
    builder = _AUTH_MODEL_BUILDERS.get(provider_prefix)
    if builder is None:
        msg = (
            f"Provider '{provider_prefix}' does not support direct SecretStr API-key "
            "injection without environment mutation."
        )
        raise UserError(msg)
    return builder(model_name, active_key)


class TlsResilientModel(WrapperModel):
    """Per-request fresh HTTP client wrapper retrying transient SSL/socket drops."""

    @jezz_callable
    def __init__(
        self,
        model_name: IdentifierStr,
        *,
        api_key: GuardedSecretStr | None = None,
        max_attempts: Annotated[int, Field(ge=1, le=8)] = 4,
    ) -> None:
        super().__init__(infer_authenticated_model(model_name, api_key=api_key))
        self._model_name = model_name
        self._api_key = api_key
        self._max_attempts = max_attempts

    @property
    def configured_model_id(self) -> str:
        """Return the exact ``provider:model`` string supplied at instantiation."""
        return self._model_name

    @jezz_callable
    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """Execute one request inside a fresh client context, retrying transient OSErrors."""
        last_err: OSError | None = None
        for attempt in range(self._max_attempts):
            try:
                fresh = infer_authenticated_model(self._model_name, api_key=self._api_key)
                async with fresh as active_model:
                    return await active_model.request(
                        messages,
                        model_settings,
                        model_request_parameters,
                    )
            except OSError as exc:
                last_err = exc
                if attempt + 1 < self._max_attempts:
                    await asyncio.sleep(float(attempt + 1))
        if last_err is not None:
            raise last_err
        msg = "Unreachable retry exhaustion without captured OSError."
        raise RuntimeError(msg)
