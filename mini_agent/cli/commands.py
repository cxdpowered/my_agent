"""Slash-command handlers for the REPL."""
from __future__ import annotations

from . import render


class CommandResult:
    def __init__(self, handled: bool, exit_repl: bool = False):
        self.handled = handled
        self.exit_repl = exit_repl


HELP_TEXT = """可用命令：
  /help                 查看命令
  /whoami               查看当前 user
  /user <user_id>       切换 user
  /new [title]          创建 session
  /sessions             列出 session
  /use <session_id>     切换 session
  /rename <title>       重命名当前 session
  /archive              归档当前 session
  /memory               查看 memory 文件列表
  /memory search <q>    搜索 memory
  /trace [run_id]       查看 trace（/trace --json 输出 JSON）
  /exit                 退出

直接输入文字即可与 Agent 对话。"""


def handle(app, line: str) -> CommandResult:
    """Handle a slash command. `app` is the CLIApp. Returns CommandResult."""
    parts = line.strip().split()
    cmd = parts[0].lower()
    args = parts[1:]

    if cmd in ("/exit", "/quit"):
        return CommandResult(handled=True, exit_repl=True)

    if cmd == "/help":
        render.info(HELP_TEXT)
        return CommandResult(True)

    if cmd == "/whoami":
        render.info(f"当前 user: {app.user_id}")
        return CommandResult(True)

    if cmd == "/user":
        if not args:
            render.warn("用法: /user <user_id>")
            return CommandResult(True)
        app.switch_user(args[0])
        return CommandResult(True)

    if cmd == "/new":
        title = " ".join(args) if args else None
        app.new_session(title)
        return CommandResult(True)

    if cmd == "/sessions":
        rows = app.runtime.sessions.list_sessions(app.user_id, include_archived=True)
        if not rows:
            render.info("(暂无 session)")
        else:
            render.session_table(rows)
        return CommandResult(True)

    if cmd == "/use":
        if not args:
            render.warn("用法: /use <session_id>")
            return CommandResult(True)
        app.use_session(args[0])
        return CommandResult(True)

    if cmd == "/rename":
        if not args:
            render.warn("用法: /rename <title>")
            return CommandResult(True)
        app.runtime.sessions.rename_session(app.session_id, " ".join(args))
        render.info("已重命名。")
        return CommandResult(True)

    if cmd == "/archive":
        app.runtime.sessions.archive_session(app.session_id)
        render.info("已归档当前 session，正在新建 session。")
        app.new_session(None)
        return CommandResult(True)

    if cmd == "/memory":
        mem = app.runtime._memory(app.user_id)
        if args and args[0] == "search":
            query = " ".join(args[1:])
            if not query:
                render.warn("用法: /memory search <query>")
                return CommandResult(True)
            hits = mem.search(query)
            if not hits:
                render.info("没有匹配的记忆。")
            for h in hits:
                render.info(f"({h['path']}) [{h['score']}] {h['chunk'][:120]}")
        else:
            files = mem.list_files()
            render.info("memory 文件: " + (", ".join(files) or "(空)"))
        return CommandResult(True)

    if cmd == "/trace":
        as_json = "--json" in args
        args = [a for a in args if a != "--json"]
        run_id = None
        if args and args[0] != "last":
            run_id = args[0]
        if run_id is None:
            latest = app.runtime.sessions.latest_run(app.session_id)
            if latest is None:
                render.info("当前 session 还没有 run。")
                return CommandResult(True)
            run_id = latest["run_id"]
        records = app.runtime.trace.read_run(run_id)
        if not records:
            render.info(f"没有找到 run 的 trace: {run_id}")
        else:
            render.info(f"trace for run {run_id}:")
            render.trace_dump(records, as_json=as_json)
        return CommandResult(True)

    render.warn(f"未知命令: {cmd}（输入 /help 查看帮助）")
    return CommandResult(True)
