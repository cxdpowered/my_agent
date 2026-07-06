"""Interactive REPL + startup argument parsing (Claude Code-style -c/-r)."""
from __future__ import annotations

import argparse
import getpass
import os

from .. import config
from ..core.llm import LLMClient
from ..core.runtime import Runtime, TurnEvent
from ..core.store import StateStore
from ..core.tools import build_default_registry
from ..core.trace import TraceWriter
from . import commands, render


class CLIApp:
    def __init__(self, store: StateStore, runtime: Runtime, user_id: str):
        self.store = store
        self.runtime = runtime
        self.user_id = user_id
        self.session_id: str | None = None

    # --- session/user management -------------------------------------------
    def _print_banner(self) -> None:
        row = self.runtime.sessions.get_session(self.session_id)
        title = row["title"] if row else ""
        render.banner(self.user_id, self.session_id, title)

    def new_session(self, title: str | None) -> None:
        self.session_id = self.runtime.sessions.create_session(self.user_id, title)
        self._print_banner()

    def use_session(self, session_id: str) -> None:
        row = self.runtime.sessions.get_session(session_id)
        if row is None or row["user_id"] != self.user_id:
            render.error(f"session 不存在或不属于当前 user: {session_id}")
            return
        self.session_id = session_id
        self._print_banner()
        self._replay_history()

    def switch_user(self, user_id: str) -> None:
        self.user_id = user_id
        self.runtime.sessions.ensure_user(user_id)
        render.info(f"已切换 user -> {user_id}，新建 session。")
        self.new_session(None)

    def _replay_history(self) -> None:
        events = self.runtime.sessions.get_events(self.session_id)
        if not events:
            return
        render.info("--- 历史对话 ---")
        for ev in events:
            msg = ev["message"]
            role = msg.get("role")
            if role == "user":
                render._p(f"你: {msg.get('content','')}")
            elif role == "assistant" and not msg.get("tool_calls") and msg.get("content"):
                render._p(f"Agent: {msg.get('content','')}")
        render.info("--- 历史结束 ---")

    # --- run a turn --------------------------------------------------------
    def ask(self, text: str) -> None:
        def on_event(ev: TurnEvent) -> None:
            if ev.kind == "thought":
                render.thought(ev.payload["text"])
            elif ev.kind == "tool_call":
                render.tool_call(ev.payload["name"], ev.payload["arguments"])
            elif ev.kind == "tool_result":
                render.tool_result(ev.payload["name"], ev.payload["ok"], ev.payload["content"])
            elif ev.kind == "notice":
                render.warn(ev.payload["text"])
            elif ev.kind == "error":
                render.error(ev.payload["text"])
            elif ev.kind == "final":
                render.final_answer(ev.payload["text"])

        self.runtime.run(
            user_id=self.user_id, session_id=self.session_id,
            user_input=text, on_event=on_event,
        )

    # --- REPL --------------------------------------------------------------
    def repl(self) -> None:
        render.info("输入 /help 查看命令，直接输入文字与 Agent 对话。")
        while True:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                render.info("\n再见。")
                break
            if not line:
                continue
            if line.startswith("/"):
                result = commands.handle(self, line)
                if result.exit_repl:
                    render.info("再见。")
                    break
                continue
            try:
                self.ask(line)
            except Exception as e:  # noqa: BLE001
                render.error(f"运行出错: {e}")


def _build_runtime() -> tuple[StateStore, Runtime]:
    config.ensure_dirs()
    store = StateStore()
    llm = LLMClient()
    registry = build_default_registry()
    trace = TraceWriter(store=store)
    runtime = Runtime(store=store, llm=llm, registry=registry, trace=trace, env=dict(os.environ))
    return store, runtime


def _resolve_startup(app: CLIApp, args) -> None:
    sessions = app.runtime.sessions
    sessions.ensure_user(app.user_id)

    # priority: --session / -r <id> > -r (interactive) > -c > default new
    if args.session:
        app.use_session(args.session)
        return
    if args.resume is not None:
        if args.resume:  # -r <id>
            app.use_session(args.resume)
            return
        # interactive pick
        rows = sessions.list_sessions(app.user_id, include_archived=True)
        if not rows:
            render.info("没有可恢复的 session，新建一个。")
            app.new_session(None)
            return
        render.session_table(rows[:10])
        choice = input("输入要恢复的 session_id（回车新建）: ").strip()
        if choice:
            app.use_session(choice)
        else:
            app.new_session(None)
        return
    if args.continue_:
        latest = sessions.latest_session(app.user_id)
        if latest:
            app.use_session(latest["session_id"])
        else:
            render.info("没有可接续的 session，新建一个。")
            app.new_session(None)
        return
    app.new_session(None)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mini_agent", description="Minimal Agent Runtime CLI")
    p.add_argument("--user", default=None, help="指定 user_id（默认操作系统用户名）")
    p.add_argument("-c", "--continue", dest="continue_", action="store_true",
                   help="接续当前 user 最近一个未归档 session")
    p.add_argument("-r", "--resume", nargs="?", const="", default=None,
                   help="恢复指定 session_id；不带 id 时列出最近 session 供选择")
    p.add_argument("--session", default=None, help="非交互进入指定 session")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    user_id = args.user or getpass.getuser()
    store, runtime = _build_runtime()
    app = CLIApp(store, runtime, user_id)
    try:
        _resolve_startup(app, args)
        app.repl()
    finally:
        store.close()
    return 0
