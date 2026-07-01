from __future__ import annotations

import base64
import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

import tiktoken

from services.account_service import account_service
from services.config import config
from services.image_storage_service import image_storage_service
from services.openai_backend_api import ImagePollTimeoutError, OpenAIBackendAPI
from utils.helper import IMAGE_MODELS, UpstreamHTTPError, extract_image_from_message_content
from utils.log import logger


class ImageGenerationError(Exception):
    def __init__(
        self,
        message: str,
        status_code: int = 502,
        error_type: str = "server_error",
        code: str | None = "upstream_error",
        param: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.code = code
        self.param = param

    def to_openai_error(self) -> dict[str, Any]:
        return {
            "error": {
                "message": str(self),
                "type": self.error_type,
                "param": self.param,
                "code": self.code,
            }
        }


class CodexRateLimitError(ImageGenerationError):
    """Codex API 返回 429 rate limit，应尝试其他账号重试。"""
    pass


def is_token_invalid_error(message: str) -> bool:
    text = str(message or "").lower()
    return (
        "token_invalidated" in text
        or "token_revoked" in text
        or "authentication token has been invalidated" in text
        or "invalidated oauth token" in text
    )


def image_stream_error_message(message: str) -> str:
    text = str(message or "")
    lower = text.lower()
    if is_token_invalid_error(text):
        return "image generation failed"
    if "curl: (35)" in lower or "tls connect error" in lower or "openssl_internal" in lower:
        return "upstream image connection failed, please retry later"
    return text or "image generation failed"


def encode_images(images: Iterable[tuple[bytes, str, str]]) -> list[str]:
    return [base64.b64encode(data).decode("ascii") for data, _, _ in images if data]


def save_image_bytes(image_data: bytes, base_url: str | None = None) -> str:
    return image_storage_service.save(image_data, base_url).url


def message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and str(item.get("type") or "") in {"text", "input_text", "output_text"}:
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return ""


def normalize_messages(messages: object, system: Any = None) -> list[dict[str, Any]]:
    normalized = []
    if config.global_system_prompt:
        normalized.append({"role": "system", "content": config.global_system_prompt})
    system_text = message_text(system)
    if system_text:
        normalized.append({"role": "system", "content": system_text})
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role", "user")
            content = message.get("content", "")
            text = message_text(content)
            images: list[tuple[bytes, str]] = []
            if role == "user":
                images.extend(extract_image_from_message_content(content))
                if isinstance(content, list):
                    for part in content:
                        if not isinstance(part, dict) or part.get("type") != "image":
                            continue
                        data = part.get("data")
                        if isinstance(data, (bytes, bytearray)):
                            images.append((bytes(data), str(part.get("mime") or "image/png")))
            if images:
                parts: list[Any] = []
                if text:
                    parts.append({"type": "text", "text": text})
                for data, mime in images:
                    parts.append({"type": "image", "data": data, "mime": mime})
                normalized.append({"role": role, "content": parts})
            else:
                normalized.append({"role": role, "content": text})
    return normalized


def prompt_with_global_system(prompt: str) -> str:
    return f"{config.global_system_prompt}\n\n{prompt}" if config.global_system_prompt else prompt


def assistant_history_text(messages: list[dict[str, Any]]) -> str:
    return "".join(str(item.get("content") or "") for item in messages if item.get("role") == "assistant")


def assistant_history_messages(messages: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("content") or "") for item in messages if item.get("role") == "assistant" and item.get("content")]


def resolve_image_size_and_quality(size: str | None, quality: str | None) -> tuple[str | None, str]:
    """
    将用户选择的比例和清晰度映射到 GPT Image 2 的 size 和 quality 参数。

    参数:
        size: 比例，如 "1:1", "16:9", "9:16", "4:3", "3:4"
        quality: 清晰度，如 "1k", "2k", "4k"

    返回:
        (resolved_size, resolved_quality) 元组
        - resolved_size: 像素尺寸，如 "1024x1024", "2048x2048", "3840x2160"
        - resolved_quality: 质量参数，如 "medium", "high"
    """
    # 默认值
    if not quality or quality not in {"1k", "2k", "4k"}:
        quality = "1k"

    # 如果没有指定比例，返回 None（让 API 自动选择）
    if not size:
        resolved_quality = "medium" if quality == "1k" else "high"
        return None, resolved_quality

    # 定义尺寸映射
    size_mapping = {
        # 1K 分辨率
        "1k": {
            "1:1": "1024x1024",
            "16:9": "1536x1024",
            "9:16": "1024x1536",
            "4:3": "1024x768",
            "3:4": "768x1024",
        },
        # 2K 分辨率
        "2k": {
            "1:1": "2048x2048",
            "16:9": "2048x1152",
            "9:16": "1152x2048",
            "4:3": "2048x1536",
            "3:4": "1536x2048",
        },
        # 4K 分辨率
        "4k": {
            "1:1": "2880x2880",  # 接近 4K 的正方形
            "16:9": "3840x2160",
            "9:16": "2160x3840",
            "4:3": "3072x2304",
            "3:4": "2304x3072",
        },
    }

    # 获取对应的像素尺寸
    resolved_size = size_mapping.get(quality, {}).get(size)

    # 如果没有找到映射，使用提示词方式（保持向后兼容）
    if not resolved_size:
        resolved_size = None

    # 设置 quality 参数
    resolved_quality = "medium" if quality == "1k" else "high"

    return resolved_size, resolved_quality


def build_image_prompt(prompt: str, size: str | None, quality: str | None = None) -> str:
    """
    构建图片生成的提示词。

    参数:
        prompt: 用户输入的提示词
        size: 比例或像素尺寸，如 "1:1", "2048x2048"
        quality: 清晰度，如 "1k", "2k", "4k" 或 "low", "medium", "high"
    """
    base_prompt = prompt.strip()

    # 如果 size 是像素尺寸格式（如 "2048x2048"），提取比例信息
    if size and "x" in size:
        try:
            width, height = map(int, size.split("x"))
            ratio = width / height
            if 0.95 <= ratio <= 1.05:
                size_hint = "正方形构图，主体居中"
            elif ratio > 1.5:
                size_hint = "横屏构图，适合宽画幅展示"
            elif ratio < 0.67:
                size_hint = "竖屏构图，适合竖版画幅展示"
            else:
                size_hint = f"宽高比为 {width}:{height}"

            # 根据像素数判断清晰度
            total_pixels = width * height
            if total_pixels >= 7000000:  # 4K 级别
                quality_hint = "，要求超高清晰度、丰富细节、4K 级别画质"
            elif total_pixels >= 3000000:  # 2K 级别
                quality_hint = "，要求高清晰度、丰富细节、2K 级别画质"
            else:
                quality_hint = ""

            return f"{base_prompt}\n\n输出为 {size_hint}{quality_hint}。"
        except (ValueError, ZeroDivisionError):
            pass

    # 如果 size 是比例格式（如 "1:1"）
    if not size:
        return base_prompt
    if size not in {"1:1", "16:9", "9:16", "4:3", "3:4"}:
        return f"{base_prompt}\n\n输出图片，宽高比为 {size}。"

    hint = {
        "1:1": "输出为 1:1 正方形构图，主体居中，适合正方形画幅",
        "16:9": "输出为 16:9 横屏构图，适合宽画幅展示",
        "9:16": "输出为 9:16 竖屏构图，适合竖版画幅展示",
        "4:3": "输出为 4:3 比例，兼顾宽度与高度，适合展示画面细节",
        "3:4": "输出为 3:4 比例，纵向构图，适合人物肖像或竖向场景",
    }[size]

    # 根据 quality 添加清晰度要求
    quality_hint = ""
    if quality in {"2k", "high"}:
        quality_hint = "，要求高清晰度、丰富细节、2K 级别画质"
    elif quality in {"4k"}:
        quality_hint = "，要求超高清晰度、丰富细节、4K 级别画质"

    return f"{base_prompt}\n\n{hint}{quality_hint}。"


def encoding_for_model(model: str):
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        try:
            return tiktoken.get_encoding("o200k_base")
        except KeyError:
            return tiktoken.get_encoding("cl100k_base")


def count_message_tokens(messages: list[dict[str, Any]], model: str) -> int:
    encoding = encoding_for_model(model)
    total = 0
    for message in messages:
        total += 3
        for key, value in message.items():
            if not isinstance(value, str):
                continue
            total += len(encoding.encode(value))
            if key == "name":
                total += 1
    return total + 3


def count_message_image_tokens(messages: list[dict[str, Any]], model: str) -> int:
    """Count image tokens from messages. Returns 0 if no images found."""
    total = 0
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "image_url":
                # Estimate image tokens based on detail level
                detail = item.get("image_url", {}).get("detail", "auto")
                if detail == "low":
                    total += 85
                else:
                    # High detail: base 85 + tiles * 170
                    total += 85 + 170
    return total


def count_message_text_tokens(messages: list[dict[str, Any]], model: str) -> int:
    """Count text tokens from messages, excluding image content."""
    encoding = encoding_for_model(model)
    total = 0
    for message in messages:
        total += 3
        content = message.get("content")
        if isinstance(content, str):
            total += len(encoding.encode(content))
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        total += len(encoding.encode(item.get("text", "")))
        for key, value in message.items():
            if key in ("content", "role"):
                continue
            if isinstance(value, str):
                total += len(encoding.encode(value))
                if key == "name":
                    total += 1
    return total + 3


def count_text_tokens(text: str, model: str) -> int:
    return len(encoding_for_model(model).encode(text))


def format_image_result(
    items: list[dict[str, Any]],
    prompt: str,
    response_format: str,
    base_url: str | None = None,
    created: int | None = None,
    message: str = "",
) -> dict[str, Any]:
    data: list[dict[str, Any]] = []
    for item in items:
        b64_json = str(item.get("b64_json") or "").strip()
        if not b64_json:
            continue
        revised_prompt = str(item.get("revised_prompt") or prompt).strip() or prompt
        if response_format == "b64_json":
            data.append({
                "b64_json": b64_json,
                "url": save_image_bytes(base64.b64decode(b64_json), base_url),
                "revised_prompt": revised_prompt,
            })
        else:
            data.append({
                "url": save_image_bytes(base64.b64decode(b64_json), base_url),
                "revised_prompt": revised_prompt,
            })
    result: dict[str, Any] = {"created": created or int(time.time()), "data": data}
    if message and not data:
        result["message"] = message
    return result


@dataclass
class ConversationRequest:
    model: str = "auto"
    prompt: str = ""
    messages: list[dict[str, Any]] | None = None
    thinking_effort: str = ""
    images: list[str] | None = None
    n: int = 1
    size: str | None = None
    quality: str | None = None
    use_official_api: bool = False
    response_format: str = "b64_json"
    base_url: str | None = None
    message_as_error: bool = False
    required_account_types: set[str] | None = None


@dataclass
class ConversationState:
    text: str = ""
    conversation_id: str = ""
    file_ids: list[str] = field(default_factory=list)
    sediment_ids: list[str] = field(default_factory=list)
    blocked: bool = False
    tool_invoked: bool | None = None
    turn_use_case: str = ""


@dataclass
class ImageOutput:
    kind: str
    model: str
    index: int
    total: int
    created: int = field(default_factory=lambda: int(time.time()))
    text: str = ""
    upstream_event_type: str = ""
    data: list[dict[str, Any]] = field(default_factory=list)

    def to_chunk(self) -> dict[str, Any]:
        chunk: dict[str, Any] = {
            "object": "image.generation.chunk",
            "created": self.created,
            "model": self.model,
            "index": self.index,
            "total": self.total,
            "progress_text": self.text,
            "upstream_event_type": self.upstream_event_type,
            "data": [],
        }
        if self.kind == "message":
            chunk.update({
                "object": "image.generation.message",
                "message": self.text,
            })
            chunk.pop("progress_text", None)
            chunk.pop("upstream_event_type", None)
        elif self.kind == "result":
            chunk.update({
                "object": "image.generation.result",
                "data": self.data,
            })
            chunk.pop("progress_text", None)
            chunk.pop("upstream_event_type", None)
        return chunk


def assistant_message_text(message: dict[str, Any]) -> str:
    content = message.get("content") or {}
    parts = content.get("parts") or []
    if not isinstance(parts, list):
        return ""
    return "".join(part for part in parts if isinstance(part, str))


def strip_history(text: str, history_text: str = "") -> str:
    text = str(text or "")
    history_text = str(history_text or "")
    while history_text and text.startswith(history_text):
        text = text[len(history_text):]
    return text


def assistant_text(event: dict[str, Any], current_text: str = "", history_text: str = "") -> str:
    for candidate in (event, event.get("v")):
        if not isinstance(candidate, dict):
            continue
        message = candidate.get("message")
        if not isinstance(message, dict):
            continue
        role = str((message.get("author") or {}).get("role") or "").strip().lower()
        if role != "assistant":
            continue
        text = assistant_message_text(message)
        if text:
            return strip_history(text, history_text)
    return apply_text_patch(event, current_text, history_text)


def event_assistant_text(event: dict[str, Any], history_text: str = "") -> str:
    for candidate in (event, event.get("v")):
        if not isinstance(candidate, dict):
            continue
        message = candidate.get("message")
        if isinstance(message, dict) and (message.get("author") or {}).get("role") == "assistant":
            return strip_history(assistant_message_text(message), history_text)
    return ""


def apply_text_patch(event: dict[str, Any], current_text: str = "", history_text: str = "") -> str:
    if event.get("p") == "/message/content/parts/0":
        return apply_patch_op(event, current_text, history_text)

    operations = event.get("v")
    if isinstance(operations, str) and current_text and not event.get("p") and not event.get("o"):
        return current_text + operations

    if event.get("o") == "patch" and isinstance(operations, list):
        text = current_text
        for item in operations:
            if isinstance(item, dict):
                text = apply_text_patch(item, text, history_text)
        return text

    if not isinstance(operations, list):
        return current_text

    text = current_text
    for item in operations:
        if isinstance(item, dict):
            text = apply_text_patch(item, text, history_text)
    return text


def apply_patch_op(operation: dict[str, Any], current_text: str, history_text: str = "") -> str:
    op = operation.get("o")
    value = str(operation.get("v") or "")
    if op == "append":
        return current_text + value
    if op == "replace":
        return strip_history(value, history_text)
    return current_text


def add_unique(values: list[str], candidates: list[str]) -> None:
    for candidate in candidates:
        if candidate and candidate not in values:
            values.append(candidate)


def extract_conversation_ids(payload: str) -> tuple[str, list[str], list[str]]:
    conversation_match = re.search(r'"conversation_id"\s*:\s*"([^"]+)"', payload)
    conversation_id = conversation_match.group(1) if conversation_match else ""
    # Negative lookahead excludes "file-service" (URI prefix, not a real id).
    file_ids = re.findall(r"(file[-_](?!service\b)[A-Za-z0-9]+)", payload)
    sediment_ids = re.findall(r"sediment://([A-Za-z0-9_-]+)", payload)
    return conversation_id, file_ids, sediment_ids


def is_image_tool_event(event: dict[str, Any]) -> bool:
    value = event.get("v")
    message = event.get("message") or (value.get("message") if isinstance(value, dict) else None)
    if not isinstance(message, dict):
        return False
    metadata = message.get("metadata") or {}
    author = message.get("author") or {}
    content = message.get("content") or {}
    if author.get("role") != "tool":
        return False
    if metadata.get("async_task_type") == "image_gen":
        return True
    if content.get("content_type") != "multimodal_text":
        return False
    return any(
        isinstance(part, dict) and (
                part.get("content_type") == "image_asset_pointer"
                or str(part.get("asset_pointer") or "").startswith(("file-service://", "sediment://"))
        )
        for part in content.get("parts") or []
    )


def update_conversation_state(state: ConversationState, payload: str, event: dict[str, Any] | None = None) -> None:
    conversation_id, file_ids, sediment_ids = extract_conversation_ids(payload)
    if conversation_id and not state.conversation_id:
        state.conversation_id = conversation_id
    # Accept file_id / sediment_id when any of:
    #   1) event is a complete image_gen tool message
    #   2) prior server_ste_metadata already flipped tool_invoked True (in an image_gen turn)
    #   3) patch event whose payload references asset_pointer / file-service://
    # User messages (type=conversation.message) never satisfy these, so attacker-controlled
    # substrings in user input cannot inject file ids into state.
    is_patch_event = isinstance(event, dict) and event.get("o") == "patch"
    image_context = (
        (isinstance(event, dict) and is_image_tool_event(event))
        or state.tool_invoked is True
        or (is_patch_event and ("asset_pointer" in payload or "file-service://" in payload))
    )
    if image_context:
        add_unique(state.file_ids, file_ids)
        add_unique(state.sediment_ids, sediment_ids)
    if not isinstance(event, dict):
        return
    state.conversation_id = str(event.get("conversation_id") or state.conversation_id)
    value = event.get("v")
    if isinstance(value, dict):
        state.conversation_id = str(value.get("conversation_id") or state.conversation_id)
    if event.get("type") == "moderation":
        moderation = event.get("moderation_response")
        if isinstance(moderation, dict) and moderation.get("blocked") is True:
            state.blocked = True
    if event.get("type") == "server_ste_metadata":
        metadata = event.get("metadata")
        if isinstance(metadata, dict):
            if isinstance(metadata.get("tool_invoked"), bool):
                state.tool_invoked = metadata["tool_invoked"]
            state.turn_use_case = str(metadata.get("turn_use_case") or state.turn_use_case)


def conversation_base_event(event_type: str, state: ConversationState, **extra: Any) -> dict[str, Any]:
    return {
        "type": event_type,
        "text": state.text,
        "conversation_id": state.conversation_id,
        "file_ids": list(state.file_ids),
        "sediment_ids": list(state.sediment_ids),
        "blocked": state.blocked,
        "tool_invoked": state.tool_invoked,
        "turn_use_case": state.turn_use_case,
        **extra,
    }


def iter_conversation_payloads(payloads: Iterator[str], history_text: str = "",
                               history_messages: list[str] | None = None) -> Iterator[dict[str, Any]]:
    state = ConversationState()
    history_messages = history_messages or []
    history_index = 0
    for payload in payloads:
        # print(f"[upstream_sse] {payload}", flush=True)
        if not payload:
            continue
        if payload == "[DONE]":
            yield conversation_base_event("conversation.done", state, done=True)
            break
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            update_conversation_state(state, payload)
            yield conversation_base_event("conversation.raw", state, payload=payload)
            continue
        if not isinstance(event, dict):
            yield conversation_base_event("conversation.event", state, raw=event)
            continue
        update_conversation_state(state, payload, event)
        if history_index < len(history_messages) and event_assistant_text(event, history_text) == history_messages[history_index]:
            history_index += 1
            state.text = ""
            continue
        next_text = assistant_text(event, state.text, history_text)
        if next_text != state.text:
            delta = next_text[len(state.text):] if next_text.startswith(state.text) else next_text
            state.text = next_text
            yield conversation_base_event("conversation.delta", state, raw=event, delta=delta)
            continue
        yield conversation_base_event("conversation.event", state, raw=event)


def conversation_events(
    backend: OpenAIBackendAPI,
    messages: list[dict[str, Any]] | None = None,
    model: str = "auto",
    prompt: str = "",
    images: list[str] | None = None,
    size: str | None = None,
    quality: str | None = None,
    thinking_effort: str = "",
) -> Iterator[dict[str, Any]]:
    normalized = normalize_messages(messages or ([{"role": "user", "content": prompt}] if prompt else []))
    image_model = str(model or "").strip() in IMAGE_MODELS
    history_text = "" if image_model else assistant_history_text(normalized)
    history_messages = [] if image_model else assistant_history_messages(normalized)
    final_prompt = prompt_with_global_system(build_image_prompt(prompt, size, quality)) if image_model else prompt
    payloads = backend.stream_conversation(
        messages=normalized,
        model=model,
        prompt=final_prompt,
        images=images if image_model else None,
        system_hints=["picture_v2"] if image_model else None,
        thinking_effort=thinking_effort if not image_model else "",
    )
    yield from iter_conversation_payloads(payloads, history_text, history_messages)


def text_backend() -> OpenAIBackendAPI:
    return OpenAIBackendAPI(access_token=account_service.get_text_access_token())


def stream_text_deltas(backend: OpenAIBackendAPI, request: ConversationRequest) -> Iterator[str]:
    attempted_tokens: set[str] = set()
    token = getattr(backend, "access_token", "")
    emitted = False
    while True:
        if token and token in attempted_tokens:
            raise RuntimeError("no available text account")
        if token:
            attempted_tokens.add(token)
        active_backend = None
        try:
            active_backend = OpenAIBackendAPI(access_token=token)
            for event in conversation_events(
                active_backend,
                messages=request.messages,
                model=request.model,
                prompt=request.prompt,
                thinking_effort=request.thinking_effort,
            ):
                if event.get("type") != "conversation.delta":
                    continue
                delta = str(event.get("delta") or "")
                if delta:
                    emitted = True
                    yield delta
            account_service.mark_text_used(token)
            return
        except Exception as exc:
            error_message = str(exc)
            if token and not emitted and is_token_invalid_error(error_message):
                account_service.remove_invalid_token(token, "text_stream")
                token = account_service.get_text_access_token(attempted_tokens)
                if token:
                    continue
            raise
        finally:
            if active_backend is not None:
                active_backend.close()


def collect_text(backend: OpenAIBackendAPI, request: ConversationRequest) -> str:
    return "".join(stream_text_deltas(backend, request))


def stream_codex_image_outputs(
        backend: OpenAIBackendAPI,
        request: ConversationRequest,
        index: int = 1,
        total: int = 1,
) -> Iterator[ImageOutput]:
    """使用 Codex API 生成图片，支持直接指定分辨率。"""
    collected_images = []  # 收集图片结果

    try:
        for payload in backend.stream_codex_image_generation(
                prompt=request.prompt,
                size=request.size or "auto",
                quality=request.quality or "medium",
                images=request.images or [],
        ):
            if not payload:
                continue

            # 解析 SSE 事件
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue

            event_type = data.get("type", "")

            # 处理部分图片事件
            if event_type == "response.image_generation_call.partial_image":
                yield ImageOutput(
                    kind="progress",
                    model=request.model,
                    index=index,
                    total=total,
                    upstream_event_type=event_type,
                )
                continue

            # 处理图片输出项完成事件 (关键!)
            if event_type == "response.output_item.done":
                item = data.get("item", {})
                if item.get("type") == "image_generation_call":
                    result_b64 = item.get("result", "")
                    if result_b64:
                        collected_images.append({"b64_json": result_b64})
                        logger.debug({
                            "event": "codex_image_collected",
                            "image_count": len(collected_images),
                        })
                continue

            # 处理完成事件
            if event_type == "response.completed":
                # 优先使用收集到的图片结果
                if collected_images:
                    logger.info({
                        "event": "codex_images_success",
                        "image_count": len(collected_images),
                    })
                    # 格式化为标准响应
                    formatted_data = format_image_result(
                        collected_images,
                        request.prompt,
                        request.response_format,
                        request.base_url,
                        data.get("response", {}).get("created_at", int(time.time())),
                    )["data"]

                    if formatted_data:
                        yield ImageOutput(
                            kind="result",
                            model=request.model,
                            index=index,
                            total=total,
                            data=formatted_data,
                        )
                        return

                # 如果没有收集到图片,尝试从 response.output 中提取(兼容旧格式)
                output_items = data.get("response", {}).get("output", [])
                image_results = []

                logger.debug({
                    "event": "codex_response_completed",
                    "collected_images": len(collected_images),
                    "output_items_count": len(output_items),
                    "output_types": [item.get("type") for item in output_items] if output_items else [],
                })

                for item in output_items:
                    if item.get("type") == "image_generation_call":
                        result_b64 = item.get("result", "")
                        if result_b64:
                            image_results.append({"b64_json": result_b64})

                if image_results:
                    # 格式化为标准响应
                    formatted_data = format_image_result(
                        image_results,
                        request.prompt,
                        request.response_format,
                        request.base_url,
                        data.get("response", {}).get("created_at", int(time.time())),
                    )["data"]

                    if formatted_data:
                        yield ImageOutput(
                            kind="result",
                            model=request.model,
                            index=index,
                            total=total,
                            data=formatted_data,
                        )
                        return

                # 如果没有图片结果，可能是被拒绝
                logger.warning({
                    "event": "codex_no_image_result",
                    "collected_images": len(collected_images),
                    "output_items": output_items,
                })
                yield ImageOutput(
                    kind="message",
                    model=request.model,
                    index=index,
                    total=total,
                    text="Image generation was rejected or failed",
                )
                return

    except UpstreamHTTPError as exc:
        # 捕获详细的HTTP错误信息
        logger.error({
            "event": "codex_image_http_error",
            "status_code": exc.status_code,
            "body": exc.body,
            "error": str(exc),
        })
        if exc.status_code == 429:
            raise CodexRateLimitError(f"Codex rate limit: {exc}", status_code=429) from exc
        raise ImageGenerationError(f"Codex image generation failed: {exc}") from exc
    except Exception as exc:
        logger.error({"event": "codex_image_generation_error", "error": str(exc)})
        raise ImageGenerationError(f"Codex image generation failed: {exc}") from exc


def _remove_image_conversation_later(backend: OpenAIBackendAPI, conversation_id: str) -> None:
    if not config.image_remove_conversation_after_result or not conversation_id:
        return

    def _run() -> None:
        try:
            backend.delete_conversation(conversation_id)
            logger.info({"event": "image_conversation_removed", "conversation_id": conversation_id})
        except Exception as exc:
            logger.warning({
                "event": "image_conversation_remove_failed",
                "conversation_id": conversation_id,
                "error": str(exc),
            })

    threading.Thread(target=_run, name=f"remove-image-conversation-{conversation_id}", daemon=True).start()


def stream_image_outputs(
        backend: OpenAIBackendAPI,
        request: ConversationRequest,
        index: int = 1,
        total: int = 1,
) -> Iterator[ImageOutput]:
    last: dict[str, Any] = {}
    for event in conversation_events(
            backend,
            prompt=request.prompt,
            model=request.model,
            images=request.images or [],
            size=request.size,
            quality=request.quality,
    ):
        last = event
        if event.get("type") == "conversation.delta":
            yield ImageOutput(
                kind="progress",
                model=request.model,
                index=index,
                total=total,
                text=str(event.get("delta") or ""),
                upstream_event_type="conversation.delta",
            )
            continue
        if event.get("type") == "conversation.event":
            raw = event.get("raw")
            raw_type = str(raw.get("type") or "") if isinstance(raw, dict) else ""
            yield ImageOutput(
                kind="progress",
                model=request.model,
                index=index,
                total=total,
                upstream_event_type=raw_type,
            )

    conversation_id = str(last.get("conversation_id") or "")
    file_ids = [str(item) for item in last.get("file_ids") or []]
    sediment_ids = [str(item) for item in last.get("sediment_ids") or []]
    message = str(last.get("text") or "").strip()
    logger.info({
        "event": "image_stream_resolve_start",
        "conversation_id": conversation_id,
        "file_ids": file_ids,
        "sediment_ids": sediment_ids,
        "tool_invoked": last.get("tool_invoked"),
        "turn_use_case": last.get("turn_use_case"),
    })
    if message and not file_ids and not sediment_ids and last.get("blocked"):
        yield ImageOutput(kind="message", model=request.model, index=index, total=total, text=message)
        return
    should_poll_for_image = bool(request.images) or last.get("turn_use_case") == "image gen"
    if message and not file_ids and not sediment_ids and not should_poll_for_image:
        yield ImageOutput(kind="message", model=request.model, index=index, total=total, text=message)
        return

    image_urls = backend.resolve_conversation_image_urls(conversation_id, file_ids, sediment_ids)
    if image_urls:
        image_items = [
            {"b64_json": base64.b64encode(image_data).decode("ascii")}
            for image_data in backend.download_image_bytes(image_urls)
        ]
        data = format_image_result(
            image_items,
            request.prompt,
            request.response_format,
            request.base_url,
            int(time.time()),
        )["data"]
        if data:
            _remove_image_conversation_later(backend, conversation_id)
            yield ImageOutput(kind="result", model=request.model, index=index, total=total, data=data, conversation_id=conversation_id)
        return

    if message:
        # 检测模型是否返回了文本描述（含 referenced_image_ids）而非实际生成图片
        # 这说明模型已发起图片生成工具调用，但 SSE 在工具完成前断开。
        # 此时应再尝试轮询图片结果，而不是直接把文本当作最终输出。
        # 当 is_text_reply 但 conversation_id 丢失时，尝试从最近对话列表恢复
        if is_text_reply and not conversation_id:
            try:
                import time as _time
                recovered_id = backend.find_conversation_by_prompt(
                    request.prompt, _time.time(), timeout_secs=5.0,
                )
                if recovered_id:
                    conversation_id = recovered_id
                    logger.info({
                        "event": "image_text_reply_conversation_id_recovered",
                        "conversation_id": conversation_id,
                        "message_preview": message[:200],
                    })
            except Exception as exc:
                logger.warning({
                    "event": "image_text_reply_conversation_id_recovery_failed",
                    "error": repr(exc)[:300],
                })
        if is_text_reply and conversation_id:
            logger.info({
                "event": "image_model_text_reply_retry_poll",
                "conversation_id": conversation_id,
                "message_preview": message[:200],
            })
            # 文本回复场景下，图片可能需要 4-5 分钟才能异步生成完成。
            # 使用 300s 超时并允许多次重试，避免因临时网络问题提前退出。
            retry_poll_timeout = max(config.image_poll_timeout_secs, 300)
            MAX_POLL_RETRIES = 3
            for poll_attempt in range(1, MAX_POLL_RETRIES + 1):
                try:
                    polled_file_ids, polled_sediment_ids = backend._poll_image_results(
                        conversation_id,
                        retry_poll_timeout,
                        file_ids,
                        sediment_ids,
                    )
                    file_ids.extend(item for item in polled_file_ids if item and item not in file_ids)
                    sediment_ids.extend(item for item in polled_sediment_ids if item and item not in sediment_ids)
                    break  # 轮询成功，退出重试循环
                except Exception as exc:
                    error_str = str(exc)
                    is_transient = (
                        isinstance(exc, ImagePollTimeoutError)
                        or is_tls_connection_error(error_str)
                        or "upstream" in error_str.lower()
                        or "connection" in error_str.lower()
                        or "timeout" in error_str.lower()
                    )
                    logger.warning({
                        "event": "image_model_text_reply_poll_failed",
                        "conversation_id": conversation_id,
                        "poll_attempt": poll_attempt,
                        "error": repr(exc)[:300],
                        "is_transient": is_transient,
                    })
                    # 如果还有重试次数且不是超时/内容违规错误，继续重试
                    if poll_attempt < MAX_POLL_RETRIES and not isinstance(exc, (ImagePollTimeoutError, ImageContentPolicyError)):
                        # 递增退避：30s, 60s, 90s
                        backoff = 30.0 * poll_attempt
                        logger.info({
                            "event": "image_model_text_reply_poll_retry",
                            "conversation_id": conversation_id,
                            "poll_attempt": poll_attempt,
                            "backoff_secs": backoff,
                        })
                        time.sleep(backoff)
                        continue
                    # 超时错误或重试次数用尽，停止重试
                    break

            if file_ids or sediment_ids:
                image_urls = backend.resolve_conversation_image_urls(
                    conversation_id, file_ids, sediment_ids, poll=False,
                )
                if image_urls:
                    if request.progress_callback:
                        request.progress_callback("receiving_image")
                    image_items = [
                        {"b64_json": base64.b64encode(image_data).decode("ascii")}
                        for image_data in backend.download_image_bytes(image_urls)
                    ]
                    data = format_image_result(
                        image_items,
                        request.prompt,
                        request.response_format,
                        request.base_url,
                        int(time.time()),
                    )["data"]
                    if data:
                        _remove_image_conversation_later(backend, conversation_id)
                        yield ImageOutput(kind="result", model=request.model, index=index, total=total, data=data, conversation_id=conversation_id)
                        return
        elif is_text_reply:
            logger.warning({
                "event": "image_model_text_reply_no_image",
                "conversation_id": conversation_id,
                "message_preview": message[:200],
            })
        yield ImageOutput(kind="message", model=request.model, index=index, total=total, text=message, conversation_id=conversation_id)
        return

    # 兜底：当 message 为空且图片 URL 解析失败时，先尝试一次短延迟重试轮询
    # 然后抛出明确错误而非让调用方得到 "upstream completed without generating images" 这种模糊报错
    logger.warning({
        "event": "image_stream_no_result_fallback",
        "conversation_id": conversation_id,
        "file_ids": file_ids,
        "sediment_ids": sediment_ids,
        "should_poll_for_image": should_poll_for_image,
    })
    # 当 should_poll_for_image 为 True 但 conversation_id 丢失时，尝试恢复
    if should_poll_for_image and not conversation_id:
        try:
            import time as _time
            recovered_id = backend.find_conversation_by_prompt(
                request.prompt, _time.time(), timeout_secs=5.0,
            )
            if recovered_id:
                conversation_id = recovered_id
                logger.info({
                    "event": "image_fallback_conversation_id_recovered",
                    "conversation_id": conversation_id,
                })
        except Exception as exc:
            logger.warning({
                "event": "image_fallback_conversation_id_recovery_failed",
                "error": repr(exc)[:300],
            })
    if should_poll_for_image and conversation_id:
        # 图片可能仍在异步处理中（上游 SSE 流在图片生成完成前就结束了）。
        # 使用 300s 超时并允许多次重试，避免因临时网络问题或图片尚未提交而提前退出。
        retry_poll_timeout = max(config.image_poll_timeout_secs, 300)
        MAX_FALLBACK_POLL_RETRIES = 3
        for poll_attempt in range(1, MAX_FALLBACK_POLL_RETRIES + 1):
            retry_wait_secs = min(30.0 * poll_attempt, config.image_poll_initial_wait_secs * poll_attempt)
            logger.info({
                "event": "image_stream_retry_poll_after_wait",
                "conversation_id": conversation_id,
                "retry_wait_secs": retry_wait_secs,
                "poll_attempt": poll_attempt,
            })
            time.sleep(retry_wait_secs)
            try:
                polled_file_ids, polled_sediment_ids = backend._poll_image_results(
                    conversation_id,
                    retry_poll_timeout,
                    file_ids,
                    sediment_ids,
                )
                file_ids.extend(item for item in polled_file_ids if item and item not in file_ids)
                sediment_ids.extend(item for item in polled_sediment_ids if item and item not in sediment_ids)
                break  # 轮询成功，退出重试循环
            except Exception as exc:
                error_str = str(exc)
                is_transient = (
                    isinstance(exc, ImagePollTimeoutError)
                    or is_tls_connection_error(error_str)
                    or "upstream" in error_str.lower()
                    or "connection" in error_str.lower()
                    or "timeout" in error_str.lower()
                )
                logger.warning({
                    "event": "image_stream_retry_poll_failed",
                    "conversation_id": conversation_id,
                    "poll_attempt": poll_attempt,
                    "error": repr(exc)[:300],
                    "is_transient": is_transient,
                })
                # 如果还有重试次数且不是超时/内容违规错误，继续重试
                if poll_attempt < MAX_FALLBACK_POLL_RETRIES and not isinstance(exc, (ImagePollTimeoutError, ImageContentPolicyError)):
                    # 递增退避：30s, 60s
                    backoff = 30.0 * poll_attempt
                    logger.info({
                        "event": "image_stream_retry_poll_retry",
                        "conversation_id": conversation_id,
                        "poll_attempt": poll_attempt,
                        "backoff_secs": backoff,
                    })
                    time.sleep(backoff)
                    continue
                # 超时错误或重试次数用尽，停止重试
                break
        
        if file_ids or sediment_ids:
            image_urls = backend.resolve_conversation_image_urls(
                conversation_id, file_ids, sediment_ids, poll=False,
            )
            if image_urls:
                if request.progress_callback:
                    request.progress_callback("receiving_image")
                image_items = [
                    {"b64_json": base64.b64encode(image_data).decode("ascii")}
                    for image_data in backend.download_image_bytes(image_urls)
                ]
                data = format_image_result(
                    image_items,
                    request.prompt,
                    request.response_format,
                    request.base_url,
                    int(time.time()),
                )["data"]
                if data:
                    _remove_image_conversation_later(backend, conversation_id)
                    yield ImageOutput(kind="result", model=request.model, index=index, total=total, data=data, conversation_id=conversation_id)
                    return
        
        # 重试后仍然失败，yield 错误消息
        yield ImageOutput(kind="message", model=request.model, index=index, total=total,
                          text="Image generation completed upstream but the result could not be retrieved. "
                               "The image may still be processing. Please try again in a moment.",
                          conversation_id=conversation_id)
    elif message:
        yield ImageOutput(kind="message", model=request.model, index=index, total=total, text=message, conversation_id=conversation_id)
    else:
        # conversation_id 也为空时（SSE 流极短、未捕获到会话 ID），
        # 仍然 yield 一条消息，避免 stream_image_outputs_with_pool 产生
        # "upstream completed without generating images" 模糊报错
        yield ImageOutput(kind="message", model=request.model, index=index, total=total,
                          text="Image generation started upstream but the response was incomplete. "
                               "Please try again.",
                          conversation_id=conversation_id)


def _codex_response_images(value: Any) -> list[str]:
    if isinstance(value, dict):
        if value.get("type") == "image_generation_call" and isinstance(value.get("result"), str):
            result = value["result"].strip()
            if result:
                return [result.split(",", 1)[1] if result.startswith("data:image/") else result]
        images: list[str] = []
        for item in value.values():
            images.extend(_codex_response_images(item))
        return images
    if isinstance(value, list):
        images: list[str] = []
        for item in value:
            images.extend(_codex_response_images(item))
        return images
    return []


def stream_codex_image_outputs(
        backend: OpenAIBackendAPI,
        request: ConversationRequest,
        index: int = 1,
        total: int = 1,
) -> Iterator[ImageOutput]:
    images = _codex_response_images(list(backend.iter_codex_image_response_events(
        prompt=request.prompt,
        images=request.images or [],
        size=request.size,
        quality=request.quality,
    )))
    if not images:
        raise ImageGenerationError("No image result found in response")
    data = format_image_result(
        [{"b64_json": item, "revised_prompt": request.prompt} for item in images],
        request.prompt,
        request.response_format,
        request.base_url,
        int(time.time()),
    )["data"]
    if data:
        yield ImageOutput(kind="result", model=request.model, index=index, total=total, data=data)
        return
    raise ImageGenerationError("No image result found in response")


def _generate_single_image(
        request: ConversationRequest,
        index: int,
        total: int,
) -> list[ImageOutput]:
    """为单张图片执行生成逻辑（含重试），返回结果列表。

    该函数在独立线程中运行，每个线程使用不同的账号，
    实现并行生图，避免串行超时阻塞。
    """
    # 模型返回文本而非图片的最大重试次数
    MAX_TEXT_REPLY_RETRIES = 3
    # TLS 连接错误最大重试次数
    MAX_TLS_RETRIES = 3
    # 连接超时错误最大重试次数（同账号短等待重试）
    MAX_CONN_TIMEOUT_RETRIES = 3
    # 轮询超时错误最大重试次数（换账号重试）
    MAX_POLL_TIMEOUT_RETRIES = 4

    text_reply_retry_count = 0
    tls_retry_count = 0
    conn_timeout_retry_count = 0
    poll_timeout_retry_count = 0
    account_email = ""

    while True:
        try:
            if request.progress_callback:
                request.progress_callback("getting_account")
            plan_type, _ = split_image_model(request.model)
            codex_model = is_codex_image_model(request.model)
            token = account_service.get_available_access_token(
                plan_type=plan_type,
                source_type="codex" if codex_model else None,
                plan_types=("plus", "team", "pro") if codex_model and not plan_type else None,
            )
        except RuntimeError as exc:
            raise ImageGenerationError(str(exc) or "image generation failed", account_email=account_email) from exc

        emitted_for_token = False
        returned_message = False
        returned_result = False
        account = account_service.get_account(token) or {}
        account_email = str(account.get("email") or "").strip()
        logger.debug({
            "event": "image_account_lookup",
            "token_prefix": token[:12] + "..." if len(token) > 12 else token,
            "account_email": account_email,
            "account_found": bool(account),
            "index": index,
        })
        backend = None
        try:
            backend = OpenAIBackendAPI(access_token=token)
            if request.progress_callback:
                backend.progress_callback = request.progress_callback
            stream_fn = stream_codex_image_outputs if is_codex_image_model(request.model) else stream_image_outputs
            outputs: list[ImageOutput] = []
            for output in stream_fn(backend, request, index, total):
                if account_email and not output.account_email:
                    output.account_email = account_email
                if output.kind == "message" and request.message_as_error:
                    raise ImageGenerationError(
                        output.text or "Image generation was rejected by upstream policy.",
                        status_code=400,
                        error_type="invalid_request_error",
                        code="content_policy_violation",
                        account_email=account_email,
                        conversation_id=output.conversation_id,
                    )
                emitted_for_token = True
                returned_message = output.kind == "message"
                returned_result = returned_result or output.kind == "result"
                outputs.append(output)
            if returned_message:
                account_service.mark_image_result(token, False)
                return outputs
            if not returned_result:
                account_service.mark_image_result(token, False)
                if emitted_for_token:
                    conv_id = outputs[-1].conversation_id if outputs else ""
                    raise ImageGenerationError(
                        "upstream completed without generating images",
                        status_code=400,
                        error_type="invalid_request_error",
                        code="no_image_generated",
                        account_email=account_email,
                        conversation_id=conv_id,
                    )
                return outputs
            account_service.mark_image_result(token, True)
            return outputs
        except ImagePollTimeoutError as exc:
            account_service.mark_image_result(token, False)
            if account_email:
                setattr(exc, "account_email", account_email)
            # 轮询超时：换账号重试
            if not emitted_for_token:
                poll_timeout_retry_count += 1
                if poll_timeout_retry_count <= MAX_POLL_TIMEOUT_RETRIES:
                    logger.warning({
                        "event": "image_poll_timeout_retry",
                        "request_token": token,
                        "account_email": account_email,
                        "retry_count": poll_timeout_retry_count,
                        "index": index,
                        "error": str(exc)[:200],
                    })
                    continue
                logger.warning({
                    "event": "image_poll_timeout_exhausted_retries",
                    "request_token": token,
                    "account_email": account_email,
                    "retry_count": poll_timeout_retry_count,
                    "index": index,
                })
                raise
            raise
        except ImageContentPolicyError as exc:
            account_service.mark_image_result(token, False)
            logger.warning({
                "event": "image_stream_content_policy_error",
                "request_token": token,
                "account_email": account_email,
                "error": str(exc),
                "index": index,
            })
            raise ImageGenerationError(
                str(exc) or "Image generation was rejected by upstream policy.",
                status_code=400,
                error_type="invalid_request_error",
                code="content_policy_violation",
                account_email=account_email,
                conversation_id=getattr(exc, "conversation_id", ""),
            ) from exc
        except ImageGenerationError as exc:
            account_service.mark_image_result(token, False)
            if account_email and not getattr(exc, "account_email", ""):
                exc.account_email = account_email
            error_text = str(exc)
            # 如果是模型返回文本而非图片，尝试换账号重试
            if is_model_text_reply_instead_of_image(error_text) and not emitted_for_token:
                text_reply_retry_count += 1
                if text_reply_retry_count <= MAX_TEXT_REPLY_RETRIES:
                    logger.warning({
                        "event": "image_model_text_reply_retry",
                        "request_token": token,
                        "account_email": account_email,
                        "retry_count": text_reply_retry_count,
                        "index": index,
                        "error": error_text[:200],
                    })
                    continue
                logger.warning({
                    "event": "image_model_text_reply_exhausted_retries",
                    "request_token": token,
                    "account_email": account_email,
                    "retry_count": text_reply_retry_count,
                    "index": index,
                })
                raise ImageGenerationError(
                    "Image generation failed: the upstream model returned a text description "
                    "instead of generating an image. Please try again later.",
                    status_code=502,
                    error_type="server_error",
                    code="upstream_text_reply",
                    account_email=account_email,
                    conversation_id=getattr(exc, "conversation_id", ""),
                ) from exc
            logger.warning({
                "event": "image_stream_generation_error",
                "request_token": token,
                "account_email": account_email,
                "error": error_text,
                "index": index,
            })
            raise
        except Exception as exc:
            account_service.mark_image_result(token, False)
            last_error = str(exc)
            logger.warning({
                "event": "image_stream_fail",
                "request_token": token,
                "account_email": account_email,
                "error": last_error,
                "index": index,
            })
            if not emitted_for_token and is_token_invalid_error(last_error):
                refreshed_token = account_service.refresh_access_token(token, force=True, event="image_stream")
                if refreshed_token and refreshed_token != token:
                    token = refreshed_token
                    continue
                account_service.remove_invalid_token(token, "image_stream")
                continue
            # TLS/SSL 连接错误：自动重试
            if not emitted_for_token and is_tls_connection_error(last_error):
                tls_retry_count += 1
                if tls_retry_count <= MAX_TLS_RETRIES:
                    logger.warning({
                        "event": "image_stream_tls_retry",
                        "request_token": token,
                        "account_email": account_email,
                        "retry_count": tls_retry_count,
                        "index": index,
                        "error": last_error[:200],
                    })
                    time.sleep(min(2.0 * tls_retry_count, 10.0))
                    continue
            # 连接超时错误（curl 28）：同账号短等待重试，不切换账号
            if not emitted_for_token and is_connection_timeout_error(last_error):
                conn_timeout_retry_count += 1
                if conn_timeout_retry_count <= MAX_CONN_TIMEOUT_RETRIES:
                    wait_secs = min(3.0 * conn_timeout_retry_count, 9.0)
                    logger.warning({
                        "event": "image_stream_conn_timeout_retry",
                        "request_token": token,
                        "account_email": account_email,
                        "retry_count": conn_timeout_retry_count,
                        "index": index,
                        "wait_secs": wait_secs,
                        "error": last_error[:200],
                    })
                    time.sleep(wait_secs)
                    continue
            raise ImageGenerationError(image_stream_error_message(last_error), account_email=account_email, conversation_id="") from exc
        finally:
            if backend is not None:
                backend.close()


def stream_image_outputs_with_pool(request: ConversationRequest) -> Iterator[ImageOutput]:
    if str(request.model or "").strip() not in IMAGE_MODELS:
        raise ImageGenerationError("unsupported image model,supported models: " + ", ".join(IMAGE_MODELS))

    # 判断是否使用 Codex API
    use_codex = request.model == "codex-gpt-image-2"

    logger.info({
        "event": "stream_image_outputs_with_pool_start",
        "model": request.model,
        "use_codex": use_codex,
        "size": request.size,
        "quality": request.quality,
    })

    emitted = False
    last_error = ""
    for index in range(1, request.n + 1):
        while True:
            try:
                # 获取账号：优先使用 required_account_types，否则从 model 中提取
                if request.required_account_types:
                    # 显式指定的账号类型（例如：{"plus", "team", "pro"}）
                    token = account_service.get_available_access_token(
                        plan_types=request.required_account_types,
                    )
                else:
                    # 从模型名称中提取账号类型（例如：plus-gpt-image-2 -> plan_type="plus"）
                    from utils.helper import split_image_model, is_codex_image_model
                    plan_type, _ = split_image_model(request.model)
                    codex_model = is_codex_image_model(request.model)
                    token = account_service.get_available_access_token(
                        plan_type=plan_type,
                        source_type="codex" if codex_model else None,
                        plan_types=("plus", "team", "pro") if codex_model and not plan_type else None,
                    )
            except RuntimeError as exc:
                if emitted:
                    return
                raise ImageGenerationError(str(exc) or "image generation failed") from exc

            emitted_for_token = False
            returned_message = False
            returned_result = False
            try:
                backend = OpenAIBackendAPI(access_token=token)
                # 根据模型选择不同的生成方法
                if use_codex:
                    output_stream = stream_codex_image_outputs(backend, request, index, request.n)
                else:
                    output_stream = stream_image_outputs(backend, request, index, request.n)

                for output in output_stream:
                    if output.kind == "message" and request.message_as_error:
                        raise ImageGenerationError(
                            output.text or "Image generation was rejected by upstream policy.",
                            status_code=400,
                            error_type="invalid_request_error",
                            code="content_policy_violation",
                        )
                    emitted = True
                    emitted_for_token = True
                    returned_message = output.kind == "message"
                    returned_result = returned_result or output.kind == "result"
                    yield output
                if returned_message or not returned_result:
                    account_service.mark_image_result(token, False)
                    return
                account_service.mark_image_result(token, True)
                break
            except ImagePollTimeoutError:
                raise
            except CodexRateLimitError as exc:
                # Codex API 429 rate limit - 标记账号为限流并尝试其他账号
                account_service.mark_image_result(token, False)
                account_service.update_account(token, {"status": "限流"})
                logger.warning({
                    "event": "codex_rate_limit",
                    "token": token[:16] + "...",
                    "error": str(exc),
                })
                continue
            except ImageGenerationError:
                account_service.mark_image_result(token, False)
                raise
            except Exception as exc:
                account_service.mark_image_result(token, False)
                last_error = str(exc)
                logger.warning({"event": "image_stream_fail", "request_token": token, "error": last_error})
                if not emitted_for_token and is_token_invalid_error(last_error):
                    account_service.remove_invalid_token(token, "image_stream")
                    continue
                raise ImageGenerationError(image_stream_error_message(last_error)) from exc

    if not emitted:
        if not last_error:
            last_error = "no account in the pool could generate images — check account quota and rate-limit status"
        raise ImageGenerationError(image_stream_error_message(last_error))


def stream_image_chunks(outputs: Iterable[ImageOutput]) -> Iterator[dict[str, Any]]:
    for output in outputs:
        yield output.to_chunk()


def collect_image_outputs(outputs: Iterable[ImageOutput]) -> dict[str, Any]:
    created = None
    data: list[dict[str, Any]] = []
    message = ""
    progress_parts: list[str] = []
    for output in outputs:
        created = created or output.created
        if output.kind == "progress" and output.text:
            progress_parts.append(output.text)
        elif output.kind == "message":
            message = output.text
        elif output.kind == "result":
            data.extend(output.data)

    result: dict[str, Any] = {"created": created or int(time.time()), "data": data}
    if not data:
        text = message or "".join(progress_parts).strip()
        if text:
            result["message"] = text
    return result
