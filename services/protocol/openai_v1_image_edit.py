from __future__ import annotations

from typing import Any, Iterator

from services.protocol.conversation import (
    ConversationRequest,
    ImageGenerationError,
    collect_image_outputs,
    encode_images,
    stream_image_chunks,
    stream_image_outputs_with_pool,
)
from services.protocol.openai_v1_image_generations import is_high_resolution_size, resolve_codex_size_and_quality
from utils.log import logger


def handle(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    prompt = str(body.get("prompt") or "")
    images = body.get("images") or []
    model = str(body.get("model") or "gpt-image-2")
    n = int(body.get("n") or 1)
    size = body.get("size")
    quality = str(body.get("quality") or "1k")
    response_format = str(body.get("response_format") or "b64_json")
    base_url = str(body.get("base_url") or "") or None
    encoded_images = encode_images(images)
    if not encoded_images:
        raise ImageGenerationError("image is required")

    # 判断是否使用 Codex 模型（2K/4K）
    # 1. 通过quality参数判断
    # 2. 通过size像素尺寸判断（兼容sub2api格式）
    use_codex = quality in ("2k", "4k") or is_high_resolution_size(size)

    if use_codex:
        # 使用 codex-gpt-image-2 模型进行图片编辑
        logger.info({"event": "using_codex_image_edit", "quality": quality, "size": size})

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
                images=encoded_images,
                message_as_error=True,
                required_account_types={"plus", "team", "pro"},  # 要求 Plus/Team/Pro 账号
            ))

            if body.get("stream"):
                return stream_image_chunks(outputs)
            return collect_image_outputs(outputs)

        except ImageGenerationError as e:
            error_message = str(e)
            logger.warning({
                "event": "codex_image_edit_failed",
                "error": error_message,
                "fallback_to_1k": True,
            })

            # 如果 Codex 失败（没有可用账户），降级到 1K 方案
            logger.info({"event": "fallback_to_1k_edit", "original_quality": quality})

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
                images=encoded_images,
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
            images=encoded_images,
            message_as_error=True,
        ))

        if body.get("stream"):
            return stream_image_chunks(outputs)
        return collect_image_outputs(outputs)
