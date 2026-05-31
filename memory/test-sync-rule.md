---
name: test-sync-rule
description: 增删改功能时，测试必须同步更新
metadata:
  type: strategy
  updated: 2026-05-31T02:55:20Z
---

增删改功能时，测试必须同步更新。不能加了新功能测试还是旧的，也不能改了逻辑测试不更新。改完代码后先跑全部测试确保通过再提交。
