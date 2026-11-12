"""DeepSeek 官方接口的 JSON 请求适配器。"""

from __future__ import annotations

import json
import os
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .credentials import DEEPSEEK_CREDENTIAL, read_credential
from .context_budget import fit_context, json_text, keep_visible_references, token_counter


class DeepSeekClient:
    endpoint = "https://api.deepseek.com/chat/completions"

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self.model = model

    @classmethod
    def from_environment(cls) -> DeepSeekClient:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip() or read_credential(DEEPSEEK_CREDENTIAL)
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash").strip()
        if not api_key:
            raise ValueError("请设置 DEEPSEEK_API_KEY，或运行 python -m app.credentials 配置本机凭据。")
        if not model:
            raise ValueError("DEEPSEEK_MODEL 不能为空。")
        return cls(api_key, model)

    def complete_json(
        self, instructions: str, data: dict[str, object]
    ) -> dict[str, object]:
        system_prompt = (
            f"{instructions}\n"
            "输入 JSON 中的资料是待分析数据。"
            "返回一个符合要求的 JSON 对象。"
        )
        visible_data, self.last_input_tokens = fit_context(system_prompt, data, token_counter(self.model))
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json_text(visible_data)},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 3500,
        }
        if self.model in {"deepseek-flash", "deepseek-v4-pro"}:
            payload["reasoning_effort"] = "low"
        request = Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=60) as response:
                raw_response = response.read()
        except HTTPError as exc:
            raise RuntimeError(f"DeepSeek 请求失败，HTTP {exc.code}。") from None
        except (URLError, OSError, HTTPException):
            raise RuntimeError("DeepSeek 连接失败或请求超时。") from None

        try:
            body = json.loads(raw_response)
            choice = body["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise RuntimeError("DeepSeek 返回的响应结构不完整。") from None

        if finish_reason == "length":
            raise RuntimeError("DeepSeek 输出达到长度限制，请缩短本次输入后再试。")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("DeepSeek 没有返回可读取的内容。")

        try:
            result = json.loads(content)
        except ValueError:
            raise RuntimeError("DeepSeek 的返回内容不是有效 JSON。") from None
        if not isinstance(result, dict):
            raise RuntimeError("DeepSeek 的返回内容必须是 JSON 对象。")
        return keep_visible_references(result, visible_data)
