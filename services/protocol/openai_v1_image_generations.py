from __future__ import annotations

import time
from typing import Any, Iterator

from services.protocol.conversation import (
    ConversationRequest,
    ImageGenerationError,
    ImageOutput,
    collect_image_outputs,
    format_image_result,
    stream_image_chunks,
    stream_image_outputs_with_pool,
)
from utils.log import logger


def resolve_codex_size_and_quality(size: str | None, quality: str | None) -> tuple[str | None, str]:
    """
    为 Codex GPT Image 2 解析 size 和 quality 参数。

    参数:
        size: 比例，如 "1:1", "16:9", "9:16", "4:3", "3:4"
        quality: 清晰度，如 "1k", "2k", "4k"

    返回:
        (resolved_size, resolved_quality) 元组
    """
    # 默认值
    if not quality or quality not in {"1k", "2k", "4k"}:
        quality = "1k"

    # 如果没有指定比例，返回 auto
    if not size:
        resolved_quality = "medium" if quality == "1k" else "high"
        return "auto", resolved_quality

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
    resolved_size = size_mapping.get(quality, {}).get(size, "auto")

    # 设置 quality 参数
    resolved_quality = "medium" if quality == "1k" else "high"

    return resolved_size, resolved_quality


def handle(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    prompt = str(body.get("prompt") or "")
    model = str(body.get("model") or "gpt-image-2")
    n = int(body.get("n") or 1)
    size = body.get("size")
    quality = str(body.get("quality") or "1k")
    response_format = str(body.get("response_format") or "b64_json")
    base_url = str(body.get("base_url") or "") or None

    # 调试日志：记录接收到的参数
    logger.debug({"event": "image_generation_request", "quality": quality, "size": size, "model": model})

    # 判断是否使用 Codex 模型（2K/4K）
    use_codex = quality in ("2k", "4k")

    if use_codex:
        # 使用 codex-gpt-image-2 模型生成高清图片
        logger.info({"event": "using_codex_image_api", "quality": quality, "size": size})

        # 解析 size 和 quality 参数
        resolved_size, resolved_quality = resolve_codex_size_and_quality(size, quality)

        try:
            # 使用 codex-gpt-image-2 模型，要求 Plus/Team/Pro 账号
            outputs = stream_image_outputs_with_pool(ConversationRequest(
                prompt=prompt,
                model="codex-gpt-image-2",  # 使用 Codex 模型
                n=n,
                size=resolved_size,
                quality=resolved_quality,
                use_official_api=False,
                response_format=response_format,
                base_url=base_url,
                message_as_error=True,
                required_account_types={"plus", "team", "pro"},  # 要求 Plus/Team/Pro 账号
            ))

            if body.get("stream"):
                return stream_image_chunks(outputs)
            return collect_image_outputs(outputs)

        except ImageGenerationError as e:
            error_message = str(e)
            logger.warning({
                "event": "codex_image_failed",
                "error": error_message,
                "fallback_to_1k": True,
            })

            # 如果 Codex 失败（没有可用账户），降级到 1K 方案
            logger.info({"event": "fallback_to_1k", "original_quality": quality})

            # 使用 1K 配置重新生成
            resolved_size_1k, resolved_quality_1k = resolve_codex_size_and_quality(size, "1k")

            outputs = stream_image_outputs_with_pool(ConversationRequest(
                prompt=prompt,
                model="gpt-image-2",  # 使用普通模型
                n=n,
                size=resolved_size_1k,
                quality=resolved_quality_1k,
                use_official_api=False,
                response_format=response_format,
                base_url=base_url,
                message_as_error=True,
            ))

            if body.get("stream"):
                return stream_image_chunks(outputs)
            return collect_image_outputs(outputs)

    else:
        # 使用普通的 gpt-image-2 模型（1K）
        resolved_size, resolved_quality = resolve_codex_size_and_quality(size, quality)

        outputs = stream_image_outputs_with_pool(ConversationRequest(
            prompt=prompt,
            model=model,
            n=n,
            size=resolved_size,
            quality=resolved_quality,
            use_official_api=False,
            response_format=response_format,
            base_url=base_url,
            message_as_error=True,
        ))

        if body.get("stream"):
            return stream_image_chunks(outputs)
        return collect_image_outputs(outputs)
