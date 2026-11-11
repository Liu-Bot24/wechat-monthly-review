## 本次任务：把一句话需求拆成专题配置

我想对自己的微信聊天记录做一次单独的总结。用户消息中是我的需求，以及今天的日期、归档覆盖到的日期和会话分类列表。把需求拆成下面的配置，只输出一个 JSON 对象：

```json
{
  "title": "不超过 20 字的标题",
  "understanding": "一两句话：你怎么理解这个需求，哪些是你推断的",
  "range": {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"},
  "scope": {"all": false, "chats": ["会话名"], "people": ["人名"], "categories": ["分类"], "people_in_groups": true, "people_where": "all|picked", "people_only": false},
  "topic": {"mode": "none|text|ai", "description": "一句话说明要找的话题", "keywords": ["关键词"]},
  "perspective": "mine|neutral|person",
  "person": "perspective 为 person 时的人名，否则为空字符串",
  "template": "brief|minutes|timeline|research|decisions",
  "detail": "brief|detailed",
  "focus": "需求里强调的方面和写法要求；没有就为空字符串"
}
```

- 时间：按今天的日期理解"上个月""今年""最近三个月"这类说法。没有提到时间时，用给出的最近一个完整月份。结束日期不晚于归档覆盖到的日期。
- 范围：需求里点名的群聊或私聊写进 `chats`，点名的人写进 `people`，都照需求里的叫法写，代码会去匹配真实的会话。说"所有私聊""工作群"这类的写进 `categories`，只能从给出的分类中选。说"所有聊天"，或者只给了话题、没有限定会话的，`all` 为 true。`people_in_groups` 表示也读这些人在群聊里说话的片段，需求只关心私聊时为 false。`people_where`：要在所有群聊里找这些人的发言时为 `all`；只关心他们在点名的群聊或分类里说了什么（例如"某某在某群里聊了什么"）时为 `picked`，这时那些群只读这些人的发言。只要他们在群聊里的发言、不要和他们的私聊时，`people_only` 为 true。
- 话题：只看某个话题时 `mode` 为 `text`，`keywords` 要写全：同义词、简称、英文名、型号、常见的错写和代称，一般 5 到 15 个，每个尽量短，不要用会大量误中的常用词。话题很难用关键词找全（例如"大家对我的评价""谁在找我合作"）时 `mode` 为 `ai`，`keywords` 仍然尽量给一些。没有限定话题时 `mode` 为 `none`。
- 视角：默认 `mine`。要求"客观""中立""给别人看"时用 `neutral`；要求"站在某人的角度""写给某人"时用 `person`。
- 模板：默认 `brief`。说纪要、会议记录用 `minutes`；说时间线、来龙去脉用 `timeline`；说调研、研究、大家怎么看用 `research`；说待办、决定了什么用 `decisions`。说"详细""完整"时 `detail` 为 `detailed`，否则为 `brief`。
- 不要编造需求里没有的人名和群名。
