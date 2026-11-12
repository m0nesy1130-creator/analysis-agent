"""整理分析方向，生成需要用户确认的分析范围。"""

from typing_extensions import TypedDict

from .deepseek_client import DeepSeekClient


class Clarification(TypedDict):
    prompt: str
    directions: list[str]
    scope: str | None


class ClarificationTurn(TypedDict):
    prompt: str
    direction: str | None
    answer: str | None


def assess_request(
    question: str,
    turns: list[ClarificationTurn],
    previous_scope: str | None,
    *,
    has_document: bool,
    has_project: bool,
    client: DeepSeekClient | None,
    web_enabled: bool = False,
    database_enabled: bool = False,
) -> Clarification:
    if client is None:
        keyword = (turns[-1]["answer"] if turns else None) or previous_scope or question
        return {
            "prompt": "请确认要在文档中查找的词或短语，也可以输入更具体的原文措辞。",
            "directions": [],
            "scope": keyword,
        }

    result = client.complete_json(
        '你在协助用户明确文档分析需求。以原始问题、用户选择的方向和用户最新回答为准。'
        '历史追问和旧草案只是参考，用户没选的方向、没要求的指标或核查工作均不得写入 scope。'
        '用户说“只分析”或“只要”时，严格保留该限制。若用户只问维护服务每年收费金额，'
        'scope 应为“这份合同中维护服务每年收费多少？”，不要附加税务、逐年列表或附件一致性等核查。'
        '范围明确时，scope 写成可单独交给后续分析的一句完整问题，directions 设为空数组，'
        'prompt 用一句话请用户确认。否则 scope 设为 null，prompt 只问一个必要问题（最多 100 字），'
        'directions 给出 0 至 3 个简短且互不重复的备选方向。'
        '范围仍有关键歧义或现有能力无法执行时，请用户补足信息或调整。'
        '当前可分析用户上传或指定目录中的 Markdown 或文本资料，以及已启用项目记忆中的原文。'
        '已启用公开网页或指定 MySQL 表时，也可以按已选来源分析。需要其他来源时询问用户能否提供。'
        '明确问题后可在后续阶段补交文件。用户明确要求多个独立问题时，scope 保留每一项。'
        '本阶段只澄清范围，不回答分析问题，不声称已经检索或验证。'
        '返回 JSON 对象，格式为 {"prompt":"追问或确认提示","directions":["方向一"],"scope":null}。',
        {
            "question": question,
            "turns": turns,
            "previous_scope": previous_scope,
            "has_document": has_document,
            "has_project_memory": has_project,
            "web_enabled": web_enabled,
            "database_enabled": database_enabled,
        },
    )
    prompt, directions, scope = result.get("prompt"), result.get("directions"), result.get("scope")
    if (
        not isinstance(prompt, str) or not prompt.strip()
        or not isinstance(directions, list) or any(not isinstance(item, str) for item in directions)
        or (scope is not None and (not isinstance(scope, str) or not scope.strip()))
    ):
        raise ValueError("模型没有返回有效的需求澄清结果。")
    return {
        "prompt": prompt.strip(),
        "directions": [] if scope else list(dict.fromkeys(item.strip() for item in directions if item.strip()))[:3],
        "scope": scope.strip() if scope else None,
    }
