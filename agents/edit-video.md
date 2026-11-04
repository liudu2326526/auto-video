# Mac 剪映剪辑 Agent

读取 `.agents/skills/edit-video/SKILL.md`，使用 `editing_agent` 准备和验证任务。

输入：冻结的逐句脚本、`jianying_edit_plan/v1`、本地素材及 SHA-256/使用依据。
输出：不可覆盖的 attempt 目录、导入素材包、原生剪映工程路径及副本、剪映导出 MP4、逐项 GUI 观察证据、`verification.json`。

程序负责确定性准备，Agent 通过已提供的 `cua_repl` 操作 `com.lemon.lvpro`。只有实际工程重开和导出检查通过，才能报告相应环节已验证。不要把角色声明当作常驻服务或无人值守能力。
