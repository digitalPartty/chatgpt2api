# OpenAI 注册流程修复：Sentinel Token & SO Token

## 问题描述

在 OpenAI/Auth0 注册流程的最终 `create_account` 阶段，即使前面所有步骤（邮箱创建、提交邮箱、提交密码、发送验证码、接收验证码、验证码校验）都成功，最后仍然返回错误：

```
code=registration_disallowed
message="Sorry, we cannot create your account with the given information."
```

## 根本原因

`create_account` 请求缺少必要的 Sentinel 防护 headers：
- `OpenAI-Sentinel-Token`：基础 sentinel token
- `OpenAI-Sentinel-SO-Token`：SO Token（Security Observer Token）

## 修复方案

### 1. 添加 SO Token 生成支持

在 `utils/sentinel.py` 中新增 `build_sentinel_with_so_token` 函数：

```python
def build_sentinel_with_so_token(
    session: "Session",
    device_id: str,
    flow: str,
    *,
    user_agent: str = "",
    sec_ch_ua: str = "",
    observer_wait_ms: int = 5000,
) -> tuple[str, str, str]:
    """
    请求 sentinel req，生成 Sentinel Token 和 SO Token。
    
    关键步骤：
    1. 请求 /backend-api/sentinel/req 获取 requirements（包括 so 字段）
    2. 等待 observer 收集完成（默认 5000ms）
    3. 使用 SDK 内部逻辑生成 so-token（类似 PoW）
    
    Returns:
        (sentinel_token, so_token, oai_sc_cookie) 三元组
    """
```

**关键实现细节：**
- SO Token 的生成方式和 Proof-of-Work（PoW）类似
- 需要从 sentinel req 返回的 `so` 字段获取 seed 和 difficulty
- observer 等待时间按官方前端逻辑使用 **5000ms**（不要太短）
- 使用 `SentinelTokenGenerator.generate_token()` 方法生成

### 2. 修改 create_account 请求

在 `services/register/openai_register.py` 的 `_create_account` 方法中：

```python
def _create_account(self, name: str, birthdate: str, index: int) -> None:
    # 生成 Sentinel Token 和 SO Token
    sentinel_token, so_token = build_sentinel_with_so_token(
        self.session, 
        self.device_id, 
        "oauth_create_account"  # flow 必须是 oauth_create_account
    )
    
    headers["openai-sentinel-token"] = sentinel_token
    if so_token:
        headers["openai-sentinel-so-token"] = so_token
    
    # 发送请求...
```

**重要：** 两个 header 必须同时存在：
- `OpenAI-Sentinel-Token`
- `OpenAI-Sentinel-SO-Token`

### 3. 添加 authorize/continue 步骤（可选）

在验证码校验通过后，添加 `authorize/continue` 步骤以对齐浏览器真实流程：

```python
def _authorize_continue(self, index: int) -> None:
    """验证码通过后，继续 authorize 流程"""
    url = f"{auth_base}/api/accounts/authorize/continue"
    headers["openai-sentinel-token"] = build_sentinel_token(
        self.session, 
        self.device_id, 
        "authorize_continue"
    )
    # 发送请求...
```

**注意：** 这一步不是绝对必须的，如果失败可以继续尝试 create_account。

## 注册流程（修复后）

完整的注册流程：

1. **平台授权** - `_platform_authorize()`
   - 初始化 OAuth 流程
   - screen_hint: "signup"

2. **注册用户** - `_register_user()`
   - 提交邮箱和密码
   - Sentinel Token flow: "username_password_create"

3. **发送验证码** - `_send_otp()`
   - 触发邮件验证码发送

4. **等待验证码** - `wait_for_code()`
   - 从邮箱服务接收验证码

5. **校验验证码** - `_validate_otp()`
   - 验证用户输入的验证码
   - 如果首次失败，重试时使用 flow: "authorize_continue"

6. **继续授权** - `_authorize_continue()` ⭐ 新增
   - 验证码通过后的 authorize 流程
   - Sentinel Token flow: "authorize_continue"
   - **可选步骤**：失败不中断流程

7. **创建账号** - `_create_account()` ⭐ 修复重点
   - 提交用户资料（姓名、生日）
   - **必须同时带两个 header：**
     - `OpenAI-Sentinel-Token`（flow: "oauth_create_account"）
     - `OpenAI-Sentinel-SO-Token`（从 sentinel req 的 so 字段生成）

8. **换取 Token** - `_exchange_registered_tokens()`
   - 用 auth code 换取 access_token 和 refresh_token

## 验证方法

### 日志检查

修复后，日志应该显示：

```
[任务1] 正在生成 Sentinel Token 和 SO Token...
[任务1] SO Token 生成成功，长度: XXX 字符
[任务1] 创建账号资料完成
```

如果 SO Token 生成失败或为空：

```
[任务1] 警告: SO Token 为空，可能影响注册成功率
```

### 错误诊断

如果仍然遇到 `registration_disallowed`，检查：

1. **Sentinel Token 检查**
   - 是否成功生成 sentinel token？
   - 是否成功生成 so-token？
   - token 长度是否合理？

2. **邮箱域名检查**
   - 某些临时邮箱域名会被最终风控拒绝
   - 需要按 provider/domain 统计成功率

3. **代理/IP 检查**
   - IP 信誉度问题
   - Cloudflare clearance 是否有效

### 成功率预期

- **修复前**：几乎 0%（create_account 阶段 100% 失败）
- **修复后（协议层面）**：预计 50% 左右
- **修复后（优化邮箱域名）**：预计 90%+

## 后续优化

### 邮箱域名统计与过滤

为了进一步提高成功率，建议添加邮箱域名统计功能：

#### 1. 添加统计数据结构

```python
# 在 mail_provider.py 或 openai_register.py 中
domain_stats = {
    "example.com": {
        "total": 100,
        "success": 95,
        "success_rate": 0.95,
        "last_updated": "2025-01-15T10:30:00Z"
    },
    "blocked-domain.com": {
        "total": 50,
        "success": 5,
        "success_rate": 0.10,
        "last_updated": "2025-01-15T10:25:00Z"
    }
}
```

#### 2. 记录每次注册结果

```python
def mark_mailbox_result(mailbox: dict, success: bool, error: Exception | None = None):
    email = mailbox.get("address", "")
    domain = email.split("@")[-1] if "@" in email else ""
    
    if domain:
        if domain not in domain_stats:
            domain_stats[domain] = {"total": 0, "success": 0}
        
        domain_stats[domain]["total"] += 1
        if success:
            domain_stats[domain]["success"] += 1
        
        domain_stats[domain]["success_rate"] = (
            domain_stats[domain]["success"] / domain_stats[domain]["total"]
        )
        domain_stats[domain]["last_updated"] = datetime.now(timezone.utc).isoformat()
        
        # 持久化到文件
        save_domain_stats()
```

#### 3. 过滤低成功率域名

```python
def get_available_providers() -> list:
    """返回成功率 >= 阈值的邮箱 providers"""
    MIN_SUCCESS_RATE = 0.50  # 最低成功率阈值
    MIN_SAMPLES = 10  # 最小样本量
    
    available = []
    for provider in config["mail"]["providers"]:
        domain = provider.get("domain", "")
        stats = domain_stats.get(domain, {})
        
        # 如果样本量不足，允许使用
        if stats.get("total", 0) < MIN_SAMPLES:
            available.append(provider)
            continue
        
        # 如果成功率达标，允许使用
        if stats.get("success_rate", 1.0) >= MIN_SUCCESS_RATE:
            available.append(provider)
        else:
            log(f"域名 {domain} 成功率过低 ({stats['success_rate']:.1%})，已自动停用", "yellow")
    
    return available
```

#### 4. 在配置中管理黑名单

```json
// data/register.json
{
  "mail": {
    "providers": [...],
    "domain_blacklist": [
      "known-blocked-domain.com",
      "another-blocked.com"
    ]
  },
  "domain_stats_file": "data/register_domain_stats.json"
}
```

#### 5. 提供统计报告 API

```python
def get_domain_stats_report() -> dict:
    """生成邮箱域名成功率报告"""
    sorted_domains = sorted(
        domain_stats.items(),
        key=lambda x: (x[1].get("total", 0), x[1].get("success_rate", 0)),
        reverse=True
    )
    
    return {
        "total_domains": len(domain_stats),
        "domains": [
            {
                "domain": domain,
                "total": stats.get("total", 0),
                "success": stats.get("success", 0),
                "success_rate": f"{stats.get('success_rate', 0):.1%}",
                "status": "active" if stats.get("success_rate", 0) >= 0.50 else "blocked"
            }
            for domain, stats in sorted_domains
        ]
    }
```

## 调试技巧

### 1. 不要打印 token 明文

日志中只记录：
- Token 长度
- Token 是否生成成功
- SDK 版本（如适用）

❌ 错误示例：
```python
log(f"Sentinel Token: {sentinel_token}")
```

✅ 正确示例：
```python
log(f"Sentinel Token 生成成功，长度: {len(sentinel_token)} 字符")
```

### 2. 详细错误信息

当 create_account 失败时，记录：
```python
error_code = data.get("code", "")
error_message = data.get("message", "")

if error_code == "registration_disallowed":
    log(f"创建账号被拒绝 [code={error_code}]，可能原因：")
    log("  1. 邮箱域名被风控（临时邮箱域名成功率低）")
    log("  2. Sentinel Token 或 SO Token 验证失败")
    log("  3. IP/代理信誉度问题")
```

### 3. 分步验证

在每个步骤后验证：
```python
step(index, "✓ 平台授权完成")
step(index, "✓ 提交注册密码完成")
step(index, "✓ 发送验证码完成")
step(index, "✓ 验证码校验完成")
step(index, "✓ authorize/continue 完成")
step(index, "✓ 创建账号资料完成")  # 如果走到这里说明 sentinel token 有效
```

## 相关文件

- `utils/sentinel.py` - Sentinel Token 生成器
- `services/register/openai_register.py` - 注册流程实现
- `services/register/mail_provider.py` - 邮箱服务提供者
- `services/openai_backend_api.py` - 对话 API（SO Token 参考实现）

## 参考资料

- OpenAI Sentinel SDK: `https://sentinel.openai.com/backend-api/sentinel/sdk.js`
- Sentinel req endpoint: `https://sentinel.openai.com/backend-api/sentinel/req`
- Auth0 API: `https://auth.openai.com/api/accounts/*`
