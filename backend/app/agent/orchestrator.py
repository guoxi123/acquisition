"""LLM 结构化输出工具：_structured_invoke 被 V2 意图解析等节点复用。"""

import json
import logging

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> str | None:
    """从可能含 markdown 代码块或解释文字的内容里提取 JSON 对象。"""
    import re

    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        return m.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return None


async def _structured_invoke(schema, messages: list, label: str):
    """让 LLM 输出结构化 JSON：纯 prompt + 提取解析。

    不用 function_calling（glm-4-flash 会把 tool schema 当答案复述），
    也不用 json response_format（历史返回过纯文本）。改用 prompt 给 schema 参考、
    要求输出 JSON 实例，再用正则提取 + Pydantic 校验。
    """
    from app.agent.llm import get_llm

    schema_str = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    full = messages + [
        {
            "role": "user",
            "content": (
                "按上面的 JSON Schema，输出一个填充了真实值的 JSON 实例对象。"
                "只输出 JSON 实例本身，不要输出 schema 定义、不要 markdown 代码块、不要解释。\n\n"
                f"Schema 参考：{schema_str}"
            ),
        }
    ]
    llm = get_llm()
    for attempt in range(2):
        try:
            resp = await llm.ainvoke(full)
            content = getattr(resp, "content", "") or ""
            json_str = _extract_json(content)
            if json_str:
                try:
                    return schema.model_validate(json.loads(json_str))
                except Exception as e:
                    logger.warning("%s parse fail (%s), retry %d", label, e, attempt + 1)
            else:
                logger.warning("%s no json in content, retry %d", label, attempt + 1)
        except Exception as e:
            logger.warning("%s error: %s", label, e)
            return None
    return None
