# 图片清晰度功能使用说明

## 功能概述

现在支持三种清晰度选项：
- **1K** - 标准清晰度（使用 `gpt-image-2` 模型）
- **2K** - 高清晰度（使用 `codex-gpt-image-2` 模型）
- **4K** - 超高清晰度（使用 `codex-gpt-image-2` 模型）

## 技术方案

### Codex GPT Image 2

2K/4K 清晰度使用 **Codex 的画图接口**（`codex-gpt-image-2` 模型）：

- 仅 **Plus / Team / Pro** 订阅可用
- 与官网画图使用不同的额度池
- 同一账号会同时有官网和 Codex 两份生图额度
- 支持真正的 2K/4K 分辨率（最高 3840x2160）

### 自动降级

如果没有可用的 Plus/Team/Pro 账户：
- 自动降级到 1K 方案
- 使用普通的 `gpt-image-2` 模型
- 确保功能始终可用

## 配置要求

### 账号要求

**1K 清晰度：**
- 免费账号或 Plus/Team/Pro 账号均可
- 使用官网画图额度

**2K/4K 清晰度：**
- 需要 Plus/Team/Pro 账号
- 使用 Codex 画图额度
- 如果没有可用账号，自动降级到 1K

### 无需额外配置

- ✅ 不需要 OpenAI API Key
- ✅ 不需要额外付费
- ✅ 包含在 ChatGPT 订阅中

## 支持的分辨率

### 1K 清晰度（gpt-image-2）
- 1:1 → 1024x1024
- 16:9 → 1536x1024
- 9:16 → 1024x1536
- 4:3 → 1024x768
- 3:4 → 768x1024

### 2K 清晰度（codex-gpt-image-2）
- 1:1 → 2048x2048
- 16:9 → 2048x1152
- 9:16 → 1152x2048
- 4:3 → 2048x1536
- 3:4 → 1536x2048

### 4K 清晰度（codex-gpt-image-2）
- 1:1 → 2880x2880
- 16:9 → 3840x2160（4K 横版）
- 9:16 → 2160x3840（4K 竖版）
- 4:3 → 3072x2304
- 3:4 → 2304x3072

## 功能特性

### 文生图（Image Generation）

- ✅ 1K 支持
- ✅ 2K 支持
- ✅ 4K 支持

### 图生图（Image Editing）

- ✅ 1K 支持
- ✅ 2K 支持
- ✅ 4K 支持

**注意：** Codex GPT Image 2 完全支持图片编辑功能！

### 批量生成

- ✅ 1K 支持批量生成
- ✅ 2K 支持批量生成
- ✅ 4K 支持批量生成

## 使用示例

### 前端使用

```typescript
// 选择清晰度
const imageQuality = "2k"; // 或 "1k", "4k"

// 选择比例
const imageSize = "16:9";

// 调用 API
await createImageGenerationTask(
  taskId,
  prompt,
  "gpt-image-2",
  imageSize,
  imageQuality
);
```

### API 调用

```bash
# 1K 清晰度（gpt-image-2）
curl -X POST http://localhost:8000/api/image-tasks/generations \
  -H "Content-Type: application/json" \
  -d '{
    "client_task_id": "task-123",
    "prompt": "a beautiful sunset",
    "model": "gpt-image-2",
    "size": "16:9",
    "quality": "1k"
  }'

# 2K 清晰度（codex-gpt-image-2）
curl -X POST http://localhost:8000/api/image-tasks/generations \
  -H "Content-Type: application/json" \
  -d '{
    "client_task_id": "task-456",
    "prompt": "a beautiful sunset",
    "model": "gpt-image-2",
    "size": "16:9",
    "quality": "2k"
  }'

# 4K 清晰度（codex-gpt-image-2）
curl -X POST http://localhost:8000/api/image-tasks/generations \
  -H "Content-Type: application/json" \
  -d '{
    "client_task_id": "task-789",
    "prompt": "a beautiful sunset",
    "model": "gpt-image-2",
    "size": "16:9",
    "quality": "4k"
  }'
```

## 错误处理与降级

### 自动降级机制

当选择 2K/4K 但没有可用的 Plus/Team/Pro 账户时：

1. 系统尝试使用 `codex-gpt-image-2` 模型
2. 如果失败（没有可用账户），自动降级到 1K
3. 使用 `gpt-image-2` 模型重新生成
4. 记录降级日志

### 日志示例

```json
{
  "event": "using_codex_image_api",
  "quality": "2k",
  "size": "16:9"
}

// 如果失败
{
  "event": "codex_image_failed",
  "error": "no account in the pool could generate images",
  "fallback_to_1k": true
}

{
  "event": "fallback_to_1k",
  "original_quality": "2k"
}
```

### 常见错误

**没有可用账户：**
```
no account in the pool could generate images — check account quota and rate-limit status
```

**解决方法：**
1. 添加 Plus/Team/Pro 账号到账号池
2. 检查账号额度是否用完
3. 系统会自动降级到 1K，无需手动处理

## 测试步骤

1. **启动后端服务**
   ```bash
   python main.py
   ```

2. **启动前端服务**
   ```bash
   cd web
   npm run dev
   ```

3. **测试不同清晰度**
   - 测试 1K + 各种比例 → 应使用 `gpt-image-2`
   - 测试 2K + 各种比例 → 应使用 `codex-gpt-image-2`（如果有 Plus 账号）
   - 测试 4K + 各种比例 → 应使用 `codex-gpt-image-2`（如果有 Plus 账号）

4. **测试降级机制**
   - 移除所有 Plus 账号
   - 选择 2K/4K 清晰度
   - 验证是否自动降级到 1K

5. **检查日志**
   后端日志会显示使用的模型和降级情况：
   ```
   {"event": "using_codex_image_api", "quality": "2k"}
   {"event": "codex_image_failed", "fallback_to_1k": true}
   {"event": "fallback_to_1k", "original_quality": "2k"}
   ```

## 技术实现

### 架构

```
用户选择清晰度 → 前端传递 quality 参数 → 后端判断
                                              ↓
                                    quality in (2k, 4k)?
                                    ↙              ↘
                                  是                否
                                  ↓                ↓
                          codex-gpt-image-2   gpt-image-2
                          (Plus/Team/Pro)     (所有账号)
                                  ↓
                            有可用账号?
                            ↙        ↘
                          是          否
                          ↓          ↓
                      生成 2K/4K    降级到 1K
```

### 关键文件

- `services/protocol/openai_v1_image_generations.py` - 文生图处理，包含 Codex 切换和降级逻辑
- `services/protocol/openai_v1_image_edit.py` - 图生图处理，包含 Codex 切换和降级逻辑
- `services/protocol/conversation.py` - 对话处理和图片生成核心逻辑
- `web/src/lib/api.ts` - 前端 API 调用
- `web/src/app/image/page.tsx` - 前端页面

### 参数映射

```python
def resolve_codex_size_and_quality(size, quality):
    # 1K: medium quality, 标准分辨率
    # 2K: high quality, 2K 分辨率
    # 4K: high quality, 4K 分辨率
    
    size_mapping = {
        "1k": {"1:1": "1024x1024", "16:9": "1536x1024", ...},
        "2k": {"1:1": "2048x2048", "16:9": "2048x1152", ...},
        "4k": {"1:1": "2880x2880", "16:9": "3840x2160", ...},
    }
    
    quality_mapping = {
        "1k": "medium",
        "2k": "high",
        "4k": "high",
    }
```

## 常见问题

**Q: 2K/4K 需要额外付费吗？**
A: 不需要。如果你有 ChatGPT Plus/Team/Pro 订阅，2K/4K 功能包含在订阅中。

**Q: 为什么我选择 2K 但生成的是 1K？**
A: 可能是因为：
1. 账号池中没有 Plus/Team/Pro 账号
2. Plus 账号的 Codex 额度已用完
3. 系统自动降级到 1K 以确保功能可用

**Q: Codex 额度和官网额度是分开的吗？**
A: 是的。同一个 Plus 账号会有两份独立的额度：
- 官网画图额度（用于 1K）
- Codex 画图额度（用于 2K/4K）

**Q: 2K 和 4K 有什么区别？**
A: 
- 2K: 最大 2048x2048 或 2048x1536
- 4K: 最大 3840x2160 或 2880x2880
- 都使用 high quality

**Q: 图生图支持 2K/4K 吗？**
A: 完全支持！Codex GPT Image 2 支持图片编辑功能。

**Q: 如何查看是否使用了 Codex 模型？**
A: 查看后端日志，会显示：
```
{"event": "using_codex_image_api", "quality": "2k"}
```

## 成本说明

**完全免费（包含在订阅中）：**
- 1K 清晰度：免费账号或 Plus 账号
- 2K/4K 清晰度：Plus/Team/Pro 账号

**无需额外费用：**
- ✅ 不需要 OpenAI API Key
- ✅ 不需要按使用量付费
- ✅ 包含在 ChatGPT 订阅中
