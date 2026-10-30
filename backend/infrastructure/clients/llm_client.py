"""
LLM Client - 大模型调用客户端
支持多种 LLM 提供商：OpenAI, Anthropic, 通义千问
API Key 从数据库配置中获取，不再依赖环境变量
"""
import logging
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class LLMResult:
    """LLM 调用结果"""
    def __init__(
        self,
        content: str,
        prompt_tokens: int,
        completion_tokens: int,
        generation_time: int,
    ):
        self.content = content
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.generation_time = generation_time


class LLMError(Exception):
    """LLM 调用错误"""
    pass


@dataclass
class LLMStreamChunk:
    """流式响应片段及模型结束原因。"""

    content: str = ""
    finish_reason: str | None = None


class BaseLLMClient:
    """LLM 客户端基类"""

    def __init__(self, api_key: str, api_base: str | None = None):
        self.api_key = api_key
        self.api_base = api_base

    async def generate(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResult:
        raise NotImplementedError

    async def generate_stream(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        raise NotImplementedError


class OpenAIClient(BaseLLMClient):
    """OpenAI API 客户端"""

    async def generate(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResult:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.api_base or "https://api.openai.com/v1"
            )

            start_time = time.time()
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            generation_time = int(time.time() - start_time)

            # glm-5.2 等推理模型：content 可能为空，实际内容在 reasoning_content
            msg = response.choices[0].message
            content = msg.content or ""
            if not content and hasattr(msg, "reasoning_content") and msg.reasoning_content:
                content = msg.reasoning_content
                logger.info("LLM content empty, using reasoning_content as fallback")

            return LLMResult(
                content=content,
                prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
                completion_tokens=response.usage.completion_tokens if response.usage else 0,
                generation_time=generation_time,
            )
        except Exception as e:
            logger.error(f"LLM API call failed: {type(e).__name__}: {e}")
            raise LLMError(f"LLM API 调用失败（{type(e).__name__}: {e}）") from e

    async def generate_stream(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ):
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.api_base or "https://api.openai.com/v1"
            )

            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )

            async for chunk in response:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta and delta.content:
                    yield LLMStreamChunk(content=delta.content)
                if choice.finish_reason:
                    yield LLMStreamChunk(finish_reason=choice.finish_reason)
        except Exception as e:
            logger.error(f"LLM streaming failed: {type(e).__name__}")
            raise LLMError("LLM 流式调用失败")


class AnthropicClient(BaseLLMClient):
    """Anthropic API 客户端"""

    async def generate(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResult:
        try:
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(
                api_key=self.api_key,
                base_url=self.api_base or "https://api.anthropic.com"
            )

            start_time = time.time()

            # Anthropic 的 API 格式不同
            system_prompt = ""
            user_messages = []
            for msg in messages:
                if msg["role"] == "system":
                    system_prompt = msg["content"]
                else:
                    user_messages.append(msg)

            response = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=user_messages,
            )
            generation_time = int(time.time() - start_time)

            return LLMResult(
                content=response.content[0].text if response.content else "",
                prompt_tokens=response.usage.input_tokens if response.usage else 0,
                completion_tokens=response.usage.output_tokens if response.usage else 0,
                generation_time=generation_time,
            )
        except Exception as e:
            logger.error(f"LLM API call failed: {type(e).__name__}: {e}")
            raise LLMError(f"LLM API 调用失败（{type(e).__name__}: {e}）") from e

    async def generate_stream(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ):
        try:
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(
                api_key=self.api_key,
                base_url=self.api_base or "https://api.anthropic.com"
            )

            system_prompt = ""
            user_messages = []
            for msg in messages:
                if msg["role"] == "system":
                    system_prompt = msg["content"]
                else:
                    user_messages.append(msg)

            async with client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=user_messages,
                temperature=temperature,
            ) as stream:
                async for text in stream.text_stream:
                    yield LLMStreamChunk(content=text)
                final_message = await stream.get_final_message()
                yield LLMStreamChunk(finish_reason=final_message.stop_reason)
        except Exception as e:
            logger.error(f"LLM streaming failed: {type(e).__name__}")
            raise LLMError("LLM 流式调用失败")


class QwenClient(BaseLLMClient):
    """通义千问 API 客户端（兼容 OpenAI 格式）"""

    async def generate(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResult:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.api_base or "https://dashscope.aliyuncs.com/compatible-mode/v1"
            )

            start_time = time.time()
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            generation_time = int(time.time() - start_time)

            return LLMResult(
                content=response.choices[0].message.content or "",
                prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
                completion_tokens=response.usage.completion_tokens if response.usage else 0,
                generation_time=generation_time,
            )
        except Exception as e:
            logger.error(f"LLM API call failed: {type(e).__name__}")
            raise LLMError("LLM API 调用失败")

    async def generate_stream(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ):
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.api_base or "https://dashscope.aliyuncs.com/compatible-mode/v1"
            )

            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )

            async for chunk in response:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta and delta.content:
                    yield LLMStreamChunk(content=delta.content)
                if choice.finish_reason:
                    yield LLMStreamChunk(finish_reason=choice.finish_reason)
        except Exception as e:
            logger.error(f"LLM streaming failed: {type(e).__name__}")
            raise LLMError("LLM 流式调用失败")


# 提供商对应的客户端类
PROVIDER_CLIENTS = {
    "openai": OpenAIClient,
    # DeepSeek exposes an OpenAI-compatible API. Keep the provider name for
    # configuration/model routing, while reusing the same transport client.
    "deepseek": OpenAIClient,
    "anthropic": AnthropicClient,
    "qwen": QwenClient,
    "zhipu": OpenAIClient,
}


def create_client(provider: str, api_key: str, api_base: str | None = None) -> BaseLLMClient:
    """
    创建 LLM 客户端实例

    Args:
        provider: 提供商名称 (openai/deepseek/anthropic/qwen/zhipu)
        api_key: API Key
        api_base: API 基础 URL（可选）

    Returns:
        LLM 客户端实例
    """
    if provider not in PROVIDER_CLIENTS:
        raise LLMError(f"Unsupported provider: {provider}. Available: {list(PROVIDER_CLIENTS.keys())}")

    client_class = PROVIDER_CLIENTS[provider]
    return client_class(api_key=api_key, api_base=api_base)


class LLMClient:
    """LLM 调用客户端（统一入口）- 支持动态传入配置"""

    async def generate(
        self,
        provider: str,
        model: str,
        api_key: str,
        api_base: str | None = None,
        system_prompt: str = "",
        user_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResult:
        """
        调用大模型生成内容

        Args:
            provider: LLM 提供商 (openai/deepseek/anthropic/qwen/zhipu)
            model: 模型名称
            api_key: API Key
            api_base: API 基础 URL（可选）
            system_prompt: 系统提示词
            user_prompt: 用户提示词
            temperature: 温度参数
            max_tokens: 最大 token 数

        Returns:
            LLMResult 对象
        """
        if not api_key:
            raise LLMError(f"API Key not configured for provider: {provider}")

        client = create_client(provider, api_key, api_base)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        return await client.generate(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def generate_stream(
        self,
        provider: str,
        model: str,
        api_key: str,
        api_base: str | None = None,
        system_prompt: str = "",
        user_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        messages: list[dict] | None = None,
    ):
        if not api_key:
            raise LLMError(f"API Key not configured for provider: {provider}")

        client = create_client(provider, api_key, api_base)

        if messages is None:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

        async for chunk in client.generate_stream(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield chunk

    def get_supported_providers(self) -> list[str]:
        """获取支持的 LLM 提供商列表"""
        return list(PROVIDER_CLIENTS.keys())
