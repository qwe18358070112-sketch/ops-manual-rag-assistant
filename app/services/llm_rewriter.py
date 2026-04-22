from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class RewriteConfig:
    enabled: bool
    base_url: str
    chat_path: str
    api_key: str
    model: str
    timeout_seconds: float
    temperature: float
    max_tokens: int

    @property
    def ready(self) -> bool:
        return bool(self.enabled and self.base_url and self.api_key and self.model)

    @property
    def endpoint(self) -> str:
        if not self.base_url:
            return ""
        return f"{self.base_url.rstrip('/')}{self.chat_path}"


def load_rewrite_config() -> RewriteConfig:
    enabled = os.getenv("OPS_ASSISTANT_LLM_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    base_url = os.getenv("OPS_ASSISTANT_LLM_BASE_URL", "").strip()
    chat_path = os.getenv("OPS_ASSISTANT_LLM_CHAT_PATH", "/chat/completions").strip() or "/chat/completions"
    if not chat_path.startswith("/"):
        chat_path = "/" + chat_path
    api_key = os.getenv("OPS_ASSISTANT_LLM_API_KEY", "").strip()
    model = os.getenv("OPS_ASSISTANT_LLM_MODEL", "").strip()
    timeout_seconds = float(os.getenv("OPS_ASSISTANT_LLM_TIMEOUT_SECONDS", "20") or "20")
    temperature = float(os.getenv("OPS_ASSISTANT_LLM_TEMPERATURE", "0.2") or "0.2")
    max_tokens = int(os.getenv("OPS_ASSISTANT_LLM_MAX_TOKENS", "500") or "500")
    return RewriteConfig(
        enabled=enabled,
        base_url=base_url,
        chat_path=chat_path,
        api_key=api_key,
        model=model,
        timeout_seconds=max(5.0, timeout_seconds),
        temperature=max(0.0, min(temperature, 1.0)),
        max_tokens=max(128, max_tokens),
    )


def _extract_response_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] or {}
        message = choice.get("message", {})
        content = message.get("content", choice.get("text", ""))
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                    parts.append(str(item["text"]))
            return "\n".join(parts).strip()
    output_text = payload.get("output_text")
    if isinstance(output_text, str):
        return output_text.strip()
    return ""


def _validate_citations(text: str, allowed_citations: set[str]) -> tuple[bool, str | None]:
    if not allowed_citations:
        return True, None
    seen = set(re.findall(r"\[\d+\]", text))
    if not seen:
        return False, "模型重写结果缺少引用标记"
    unknown = seen - allowed_citations
    if unknown:
        return False, f"模型重写结果出现未授权引用标记: {', '.join(sorted(unknown))}"
    return True, None


def rewrite_answer_with_llm(
    *,
    query: str,
    query_mode: str,
    draft_text: str,
    confidence: dict[str, Any],
    steps: list[dict[str, Any]],
    citations: list[dict[str, Any]],
    force: bool | None = None,
) -> dict[str, Any]:
    config = load_rewrite_config()
    enabled = config.enabled if force is None else force
    metadata: dict[str, Any] = {
        "enabled": enabled,
        "applied": False,
        "provider": "openai_compatible",
        "model": config.model,
        "endpoint": config.endpoint,
        "error": None,
    }
    if not enabled:
        metadata["error"] = "模型重写未启用"
        return {"text": draft_text, "metadata": metadata}
    if not config.base_url or not config.api_key or not config.model:
        metadata["error"] = "模型重写配置不完整，缺少 base_url、api_key 或 model"
        return {"text": draft_text, "metadata": metadata}

    evidence_lines = []
    for index, step in enumerate(steps, start=1):
        evidence_lines.append(f"{index}. {' '.join(step.get('citation_ids', []))} {step.get('text', '')}".strip())
    citation_lines = []
    for citation in citations:
        citation_lines.append(
            f"{citation['id']} {citation['source_name']} ｜ {citation['section_title']} ｜ {citation.get('source_type', '')}"
        )

    system_prompt = (
        "你是政企运维知识库助手的答案润色器。"
        "你只能依据给定证据重写答案，不能补充证据里没有的新事实。"
        "你必须保留并正确使用已有引用标记，例如 [1] [2]。"
        "如果证据存在不确定性，要明确提示用户以原始手册或报告为准。"
    )
    user_prompt = (
        f"用户问题：{query}\n"
        f"查询模式：{query_mode}\n"
        f"当前置信度：{confidence.get('level', 'unknown')}\n"
        f"原始规则答案：\n{draft_text}\n\n"
        f"可用证据：\n" + "\n".join(evidence_lines or ["无"]) + "\n\n"
        f"引用说明：\n" + "\n".join(citation_lines or ["无"]) + "\n\n"
        "请把答案重写成更自然、更正式的中文问答。\n"
        "要求：\n"
        "1. 只输出最终答案，不要解释你的重写过程。\n"
        "2. 结论和步骤要简洁，优先保留可执行动作。\n"
        "3. 必须保留引用标记，且只能使用上面提供的引用标记。\n"
        "4. 如果当前证据不完整，要明确写出“建议以原始手册/报告为准”。\n"
    )

    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        config.endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="ignore")
        metadata["error"] = f"模型重写请求失败: HTTP {error.code} {detail[:300]}"
        return {"text": draft_text, "metadata": metadata}
    except urllib.error.URLError as error:
        metadata["error"] = f"模型重写请求失败: {error.reason}"
        return {"text": draft_text, "metadata": metadata}
    except Exception as error:  # pragma: no cover - defensive
        metadata["error"] = f"模型重写请求异常: {error}"
        return {"text": draft_text, "metadata": metadata}

    try:
        payload = json.loads(response_body)
    except json.JSONDecodeError:
        metadata["error"] = "模型重写返回了无法解析的 JSON"
        return {"text": draft_text, "metadata": metadata}

    rewritten_text = _extract_response_text(payload)
    if not rewritten_text:
        metadata["error"] = "模型重写结果为空"
        return {"text": draft_text, "metadata": metadata}

    is_valid, error_message = _validate_citations(rewritten_text, {citation["id"] for citation in citations})
    if not is_valid:
        metadata["error"] = error_message
        return {"text": draft_text, "metadata": metadata}

    metadata["applied"] = True
    metadata["error"] = None
    return {"text": rewritten_text, "metadata": metadata}
