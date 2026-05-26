# General

# 日常

---

基于 docs/plans/2026-05-26-docker-postgres-redis-worker-plan.md 优化源代码。使用 awesome-code skill 辅助规划、优化。所有问题都要解决。 如果工作时有疑问，或者有更好的方案，自己选个最优方案优化，不要问我。不要破坏其它已经存在的功能。要保证最终成品能正常、稳定、高效地工作。

---

我希望可以docker化应用； 而且，我希望支持postgres、redis，甚至必要时可以支持worker（如果你觉得确实需要）。我还希望像 /Volumes/2T01/Github/sub2api 一样支持本地直接将docker镜像推送至 dockerhub。 另外，如果用到npm module，可以托管在 /Volumes/2T01/Test/llm-status-machine 里； 而本仓库只是软链接过去。 请你调查源代码后，根据上述需要准备一个优化计划。

---

重构软件：

- prompts（命名为Prompts）、llm环境（命名为Models）、工作空间（命名为Workspace）、baseurl/API（可以命名为DevTools）应该作为一个独立的界面
- 有一个实验台（Experiment），它有机地连接Prompts、Models和工作空间
- 实验台里的通用任务是： 给定一个工作空间（状态i），用户选择一个或多个prompts。点击开始按钮后，ai会运行这个prompts 1次或多次（用户可以自定义次数）。每次运行是独立的。本次的输出（状态i+1）会作为下一次的输入。
- 模型的输出、工作空间的变化可以使用git或类似的东西进行本地的版本控制，或者其他更加合适的方法进行版本控制
