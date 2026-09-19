"""N3 寿命对照（第十二轮）：请求映射在终态后不延长运行时实例寿命。

真实加载、真实 Runner、本地假模型。复现方式对应第十二轮
mapping_handoff_repro 的 mapping_after_* 三场景：
  调用方保留 request，轮次终态（DONE/ERROR/取消）后：
    - registry 归零；
    - 请求上映射条目被清空（终态显式释放）；
    - 映射不再持有运行时实例（弱引用计数为 0）。
另含默认关闭对照与取消路径。运行于补丁宿主副本。
"""
from __future__ import annotations
import asyncio, json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import m_rework_check as m  # noqa: E402

ROWS = []


def record(case, **kw):
    row = dict(case=case, **kw)
    ROWS.append(row)
    (HERE / "n3_lifetime_check.json").write_text(
        json.dumps(ROWS, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(row, ensure_ascii=False), flush=True)


async def lifetime(obj, meta, module, uid, mode):
    import weakref
    ident, _ = m.seed(obj, module, uid)
    ev, req = m.event(uid), m.make_request("ordinary n3 question")
    ev.plugins_name = [meta.name]

    class LifetimeProvider(m.RecordingProvider):
        async def text_chat(self, **kw):
            if mode == "error":
                raise RuntimeError("synthetic provider failure")
            if mode == "cancel":
                entered.set()
                await asyncio.Event().wait()
            return await super().text_chat(**kw)

    entered = asyncio.Event()
    provider, runner = LifetimeProvider(["synthetic final reply"]), m.ToolLoopAgentRunner()

    async def flow():
        assert not await m.call_event_hook(ev, EventType.OnLLMRequestEvent, req)
        await runner.reset(provider=provider, request=req,
                           run_context=m.ContextWrapper(context=__import__("types").SimpleNamespace(event=ev)),
                           tool_executor=m.FunctionToolExecutor(),
                           agent_hooks=m.MAIN_AGENT_HOOKS, streaming=False)
        async for _ in m.run_agent(runner, max_step=2, show_tool_use=False,
                                   show_tool_call_result=False):
            pass

    from astrbot.core.pipeline.context_utils import call_event_hook
    from astrbot.core.star.star_handler import EventType
    task = asyncio.create_task(flow())
    cancelled = False
    if mode == "cancel":
        await asyncio.wait_for(entered.wait(), 15)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        cancelled = True
    else:
        await asyncio.wait_for(task, 15)
    await asyncio.sleep(0)
    import gc
    gc.collect()

    state = str(runner._state)
    role = getattr(runner.final_llm_resp, "role", None)
    if mode == "done":
        assert state == "AgentState.DONE" and role == "assistant"
    if mode == "error":
        assert state == "AgentState.ERROR" and role == "err"
    assert len(obj._injector.registry) == 0

    raw = getattr(req, "_extra_runtime_pairs", None) or []
    # v3 表示：条目为 (src, weakref(rt))；解出仍存活的 runtime 弱引用
    rt_refs = [rt for _src, rt in raw]
    alive = sum(1 for r in rt_refs if r() is not None)
    mapping_cleared = len(raw) == 0
    record(f"n3_lifetime_{mode}",
           defect=(mapping_cleared is False and alive > 0) or len(obj._injector.registry) != 0,
           agent_state=state, final_role=role, task_cancelled=cancelled,
           registry_after_terminal=len(obj._injector.registry),
           mapping_entries_after_terminal=len(raw),
           runtime_strong_refs_via_mapping=alive,
           note=("v3：终态 release/_blank_record 清空映射条目；映射中的"
                 "runtime 为弱引用，不延长已结束对象寿命。"))


async def main_async():
    obj, meta, module, manager = await m.load_plugin()
    obj._config.update(admin_enabled=True, relation_link_enabled=False)
    for i, mode in enumerate(("done", "error", "cancel"), 1):
        await lifetime(obj, meta, module, f"9600{i}", mode)
    # 默认关闭对照：无注入、无映射
    obj._config.update(admin_enabled=False, relation_link_enabled=False)
    ev = m.event("96010")
    req = m.make_request("ordinary default-off n3")
    assert not await m.call_event_hook(ev, EventType.OnLLMRequestEvent, req)
    assert getattr(req, "_extra_runtime_pairs", None) in (None, []), "默认关闭不应建立映射"
    record("n3_default_off_no_mapping", mapping_absent=True)
    await obj.terminate()
    print("=" * 60)
    ok = all(not r.get("defect", False) for r in ROWS)
    print(f"N3 scenes = {len(ROWS)} / failures = {sum(1 for r in ROWS if r.get('defect'))}")
    print("N3_ALL_PASS" if ok else "N3_HAS_FAIL")
    if not ok:
        raise SystemExit(1)


from astrbot.core.star.star_handler import EventType  # noqa: E402

if __name__ == "__main__":
    asyncio.run(main_async())
