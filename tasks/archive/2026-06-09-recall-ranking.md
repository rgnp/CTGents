# recall 排序检索 — 子串匹配 → 关键词加权排序 + top-K

## 目标
治"换个说法就搜不到 / 命中不排序":`recall` 现状是 `q in full` 二元子串、按文件名序、无上限、200字片段。
升级为:查询分词(ASCII词 + 中文bigram)→ 按命中加权打分(name>desc>body)→ 相关度排序、top-K。
对前缀缓存零成本(recall 是按需工具,结果进 log 不进 prefix)。不上 embedding、不复用 rag 引擎(库小,按需打分即可)。

## 设计约定
- 分词:ASCII alnum 词 + 中文相邻 bigram(单字 CJK 退化为单字);全无 token 则回退原子串行为。
- 打分:每个 distinct token 取其命中的最高权重字段累加;完整查询子串命中 → 强力 exact_bonus(保留精确短语优先)。
- 排序:score desc,平手按 updated 时间戳(近因)desc;取 top_k。
- 旋钮进 `params.py`(C8,CTG_* 覆盖):weight_name/desc/body、exact_bonus、recall_top_k、recall_min_score。
- 分词/打分为纯函数,可单测。

## 步骤
- [x] Step 1: 读 `tests/test_memory.py`,既有 7 条只断言 名字/[type]/未找到,新格式都保留 → 不撞。
- [x] Step 2: `params.py` 加 `MemoryParams`(weight_name/desc/body=3/2/1、exact_bonus=5、top_k=5、min_score=0)。
- [x] Step 3: `memory.py` 加 `_tokenize`(ASCII+CJK bigram)/`_score_memory`,重写 `_recall`(分词→打分→排序→top-K)。
- [x] Step 4: 测试 +6(分词/换序命中/name 排序/top-K/精确短语/frontmatter 结构词不误命中)。
- [o] Step 5: 全绿 + ruff,按特定文件 commit。

## 验证
每步 import/ruff/相关测试;全绿才 commit。recall 改动不碰 prefix → 缓存零成本。

## 完成总结
- 计划 5 步 → 实际 5 步,1 次自查捕获真 bug(非回退)。
- **关键 bug(自查 demo 输出起疑捕获)**:exact_bonus 原对整个文件(含 frontmatter)做子串匹配,
  导致 `ad`∈`metadata`、`type`/`metadata` 误命中**所有**记忆。修:打分只针对语义字段 name/desc/body,
  加回归测试(查 metadata/ad → 未找到)。教训:结构性元数据混进可搜文本=整类假阳性,搜索只该看语义内容。
- 实证:换序查询"轨迹预测研究"命中原文"轨迹预测**的**研究"(旧二元子串做不到);name 命中排 body 之上。
