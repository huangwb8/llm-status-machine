# General

# 日常

重构软件：

- prompts（命名为Prompts）、llm环境（命名为Models）、工作空间（命名为Workspace）、baseurl/API（可以命名为DevTools）应该作为一个独立的界面
- 有一个实验台（Experiment），它有机地连接Prompts、Models和工作空间
- 实验台里的通用任务是： 给定一个工作空间（状态i），用户选择一个或多个prompts。点击开始按钮后，ai会运行这个prompts 1次或多次（用户可以自定义次数）。每次运行是独立的。本次的输出（状态i+1）会作为下一次的输入。
- 模型的输出、工作空间的变化可以使用git或类似的东西进行本地的版本控制，或者其他更加合适的方法进行版本控制