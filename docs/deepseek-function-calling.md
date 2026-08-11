# DeepSeek function calling 实战:国产 LLM 做 tool-calling agent 靠不靠谱

> 做 tool-calling agent,一般默认上 OpenAI/Claude。但国产 LLM(DeepSeek)便宜得多,它到底支不支持 function calling?能不能撑起 ReAct?本文是一次真实验证:从"以为不支持"到"其实是提示没写好",讲清接入方式和踩的坑。

## 一、问题:想做 tool-calling agent,用的 DeepSeek

项目的查询 agent(query_agent)要让 LLM 自主决定调哪个工具,标准做法是 function calling:给 LLM 一组工具的 schema,它返回"我要调这个工具、传这些参数"。

LLM 用的 DeepSeek(OpenAI 兼容接口,便宜)。问题是:**DeepSeek 的 function calling 靠谱吗?** 很多国产模型宣称兼容,实际各种抽风。

## 二、DeepSeek 是支持 function calling 的

DeepSeek 官方文档明确 `deepseek-chat` 支持 function calling(OpenAI tools 格式)。langchain 的 `ChatOpenAI` 走 OpenAI 兼容协议,直接能用:

```python
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(model="deepseek-chat", base_url="https://api.deepseek.com/v1", api_key=...)
llm_with_tools = llm.bind_tools([get_my_sellers, filter_my_sellers])
resp = llm_with_tools.invoke("查看我获取的所有卖家")
resp.tool_calls  # [{"name": "get_my_sellers", "args": {...}, ...}]
```

`bind_tools` 会把 Python 函数(带类型注解 + docstring)转成 OpenAI tools schema,DeepSeek 按 schema 返回 `tool_calls`。

## 三、验证三种 tool_choice

function calling 有个 `tool_choice` 参数,控制 LLM 调不调工具:
- `auto`(默认):LLM 自己决定
- `required`/`any`:必须调工具
- 指定某个工具:强制调那个

实测三种都返回了正确的 tool_calls:

```
tool_choice=auto:     tool_calls=[{'name':'get_my_sellers','args':{'user_id':'u123'}}]
tool_choice=required: tool_calls=[{'name':'get_my_sellers','args':{...}}]
tool_choice=any:      tool_calls=[{'name':'get_my_sellers','args':{...}}]
```

DeepSeek 的 function calling **可用**。

## 四、踩坑:一开始 tool_calls 是空的

但第一次测,`tool_calls` 返回空 `[]`。当时以为 DeepSeek 不支持,差点换模型。

后来发现:是**提示写得太含糊**。最初测用的是:

```
"查看我获取的所有卖家"
```

没给 user_id 上下文,也没明确"请用工具"。DeepSeek 在 `auto` 模式下判断"这个请求信息不全(没 user_id),我先不调工具",于是返回空。

改成明确提示:

```
"查看我获取的所有卖家(我的 user_id 是 u123),请用工具查"
```

`auto` 也正确返回 tool_calls 了。

**教训:测 function calling,提示要给足上下文 + 明确意图,别用含糊的短句。** 生产里 query_agent 的 system prompt 会注入 user_id 并明确"按需调工具",所以实际跑起来很稳。

## 五、几个落地细节

1. **温度调低**:做 tool 决策的 LLM,`temperature=0`(或很低),避免它"创造性"地乱调工具。
2. **schema 要清晰**:工具的 docstring 写清"什么情况下调这个工具"(给 LLM 看的),参数要有类型注解 + 描述。`bind_tools` 自动转 schema,但 schema 好不好全看你的函数签名写得清不清楚。
3. **结果回喂要正确**:LLM 返回 tool_calls 后,执行工具,结果要作为 `ToolMessage`(带对上的 `tool_call_id`)喂回去,LLM 才知道"这个工具返回了这些",进而生成最终回答或调下一个工具。
4. **别假设一定调对**:LLM 可能调错工具、传错参数。生产里 call_tools 节点要 try/except,把错误作为 ToolMessage 回给 LLM(让它自己纠错),而不是让整图崩。

## 六、总结

DeepSeek 的 function calling 可用,OpenAI 兼容接口 + langchain `bind_tools` 直接接入。实测 auto/required/any 都能正确返回 tool_calls。

主要坑不在模型,在**提示**:测的时候提示含糊会导致空 tool_calls,误以为"不支持"。生产里 system prompt 注入 user_id + 明确"调工具",就稳了。

> 国产 LLM 做 agent,很多"不支持"的传言其实是用法问题。先按官方文档把 tool_choice / 提示写对,实测一遍,再下结论。DeepSeek 在 tool-calling 这个场景,目前够用。
