"""Tests for plan DAG, mutator, parallelizer, and engine."""

import asyncio
import pytest

from edac.plan.dag import PlanDAG, Step, StepStatus
from edac.plan.mutator import PlanMutator
from edac.plan.parallelizer import Parallelizer
from edac.plan.engine import PlanEngine, PlanConfig, ReplanningTrigger
from edac.event.bus import EventBus


class TestPlanDAG:
    def test_add_step(self):
        plan = PlanDAG(goal="test")
        step = Step(id="s1", description="step 1", action="tool.call")
        plan.add_step(step)
        assert plan.get_step("s1") is step

    def test_add_duplicate_raises(self):
        plan = PlanDAG(goal="test")
        plan.add_step(Step(id="s1", description="d", action="a"))
        with pytest.raises(ValueError):
            plan.add_step(Step(id="s1", description="d", action="a"))

    def test_cycle_detection(self):
        plan = PlanDAG(goal="test")
        plan.add_step(Step(id="s1", description="d", action="a", dependencies=["s2"]))
        with pytest.raises(ValueError):
            plan.add_step(Step(id="s2", description="d", action="a", dependencies=["s1"]))

    def test_topological_order(self):
        plan = PlanDAG(goal="test")
        plan.add_step(Step(id="s1", description="d", action="a"))
        plan.add_step(Step(id="s2", description="d", action="a", dependencies=["s1"]))
        plan.add_step(Step(id="s3", description="d", action="a", dependencies=["s1"]))
        order = plan.topological_order()
        assert order.index("s1") < order.index("s2")
        assert order.index("s1") < order.index("s3")

    def test_ready_steps(self):
        plan = PlanDAG(goal="test")
        plan.add_step(Step(id="s1", description="d", action="a"))
        plan.add_step(Step(id="s2", description="d", action="a", dependencies=["s1"]))
        ready = plan.ready_steps()
        assert len(ready) == 1
        assert ready[0].id == "s1"

        plan.update_step("s1", status=StepStatus.COMPLETED)
        ready = plan.ready_steps()
        assert len(ready) == 1
        assert ready[0].id == "s2"

    def test_parallel_groups(self):
        plan = PlanDAG(goal="test")
        plan.add_step(Step(id="s1", description="d", action="a"))
        plan.add_step(Step(id="s2", description="d", action="a"))
        plan.add_step(Step(id="s3", description="d", action="a", dependencies=["s1", "s2"]))
        groups = plan.parallel_groups()
        assert len(groups) == 2
        assert {"s1", "s2"} in groups or {"s2", "s1"} in groups
        assert {"s3"} in groups

    def test_remove_step_rewires(self):
        plan = PlanDAG(goal="test")
        plan.add_step(Step(id="s1", description="d", action="a"))
        plan.add_step(Step(id="s2", description="d", action="a", dependencies=["s1"]))
        plan.add_step(Step(id="s3", description="d", action="a", dependencies=["s2"]))
        plan.remove_step("s2")
        s3 = plan.get_step("s3")
        assert "s2" not in s3.dependencies
        assert "s1" in s3.dependencies

    def test_remove_step_validates_dag(self):
        plan = PlanDAG(goal="test")
        plan.add_step(Step(id="s1", description="d", action="a"))
        plan.add_step(Step(id="s2", description="d", action="a", dependencies=["s1"]))
        plan.add_step(Step(id="s3", description="d", action="a", dependencies=["s2"]))
        plan.remove_step("s2")
        # After valid remove, DAG should still have valid topological order
        order = plan.topological_order()
        assert "s1" in order
        assert "s3" in order

    def test_clone(self):
        plan = PlanDAG(goal="test")
        plan.add_step(Step(id="s1", description="d", action="a"))
        cloned = plan.clone()
        assert cloned.goal == plan.goal
        assert cloned.get_step("s1") is not plan.get_step("s1")


class TestPlanMutator:
    def test_insert_after(self):
        plan = PlanDAG(goal="test")
        plan.add_step(Step(id="s1", description="d", action="a"))
        mutator = PlanMutator(plan)
        mutator.insert_after("s1", [Step(id="s2", description="new", action="a")])
        s2 = plan.get_step("s2")
        assert "s1" in s2.dependencies

    def test_insert_before_next(self):
        plan = PlanDAG(goal="test")
        plan.add_step(Step(id="s1", description="d", action="a"))
        plan.add_step(Step(id="s2", description="d", action="a"))
        mutator = PlanMutator(plan)
        mutator.insert_before_next([Step(id="s3", description="new", action="a")])
        s2 = plan.get_step("s2")
        assert "s3" in s2.dependencies

    def test_skip_step(self):
        plan = PlanDAG(goal="test")
        plan.add_step(Step(id="s1", description="d", action="a"))
        mutator = PlanMutator(plan)
        mutator.skip_step("s1")
        assert plan.get_step("s1").status == StepStatus.SKIPPED

    def test_set_result_and_failed(self):
        plan = PlanDAG(goal="test")
        plan.add_step(Step(id="s1", description="d", action="a"))
        mutator = PlanMutator(plan)
        mutator.set_step_result("s1", "ok")
        assert plan.get_step("s1").status == StepStatus.COMPLETED
        assert plan.get_step("s1").result == "ok"

        mutator.set_step_failed("s1", "err")
        assert plan.get_step("s1").status == StepStatus.FAILED
        assert plan.get_step("s1").error == "err"


class TestParallelizer:
    def test_ready_groups(self):
        plan = PlanDAG(goal="test")
        plan.add_step(Step(id="s1", description="d", action="a"))
        plan.add_step(Step(id="s2", description="d", action="a"))
        plan.add_step(Step(id="s3", description="d", action="a", dependencies=["s1"]))
        p = Parallelizer(plan)
        groups = p.ready_groups()
        assert any("s1" in g and "s2" in g for g in groups)

    def test_max_parallelism(self):
        plan = PlanDAG(goal="test")
        plan.add_step(Step(id="s1", description="d", action="a"))
        plan.add_step(Step(id="s2", description="d", action="a"))
        p = Parallelizer(plan)
        assert p.max_parallelism() == 2

    def test_critical_path(self):
        plan = PlanDAG(goal="test")
        plan.add_step(Step(id="s1", description="d", action="a"))
        plan.add_step(Step(id="s2", description="d", action="a", dependencies=["s1"]))
        plan.add_step(Step(id="s3", description="d", action="a", dependencies=["s2"]))
        p = Parallelizer(plan)
        path = p.critical_path()
        assert path == ["s1", "s2", "s3"]


class TestPlanEngine:
    @pytest.mark.asyncio
    async def test_execute_simple_plan(self):
        bus = EventBus()
        engine = PlanEngine(bus, PlanConfig(max_parallel=2))

        plan = PlanDAG(goal="test")
        plan.add_step(Step(id="s1", description="step 1", action="a"))
        plan.add_step(Step(id="s2", description="step 2", action="a"))

        async def executor(step):
            return f"result-{step.id}"

        async with bus:
            result = await engine.execute(plan, executor)
        assert result.is_complete
        assert result.get_step("s1").status == StepStatus.COMPLETED
        assert result.get_step("s1").result == "result-s1"

    @pytest.mark.asyncio
    async def test_execute_with_failure_and_replan(self):
        bus = EventBus()
        engine = PlanEngine(bus, PlanConfig(max_replans=1, replan_triggers={ReplanningTrigger.STEP_FAILURE}))

        plan = PlanDAG(goal="test")
        plan.add_step(Step(id="s1", description="bad step", action="a"))

        async def executor(step):
            raise ValueError("boom")

        async with bus:
            result = await engine.execute(plan, executor)
        assert result.has_failures
        assert engine._replan_count > 0

    @pytest.mark.asyncio
    async def test_step_timeout(self):
        bus = EventBus()
        engine = PlanEngine(bus, PlanConfig(timeout_per_step=0.05))

        plan = PlanDAG(goal="test")
        plan.add_step(Step(id="s1", description="slow step", action="a"))

        async def executor(step):
            await asyncio.sleep(1.0)
            return "ok"

        async with bus:
            result = await engine.execute(plan, executor)
        assert result.has_failures
        assert "timed out" in (result.get_step("s1").error or "")

    @pytest.mark.asyncio
    async def test_permanent_failure_emits_abort(self):
        bus = EventBus()
        engine = PlanEngine(bus, PlanConfig(max_replans=0))

        plan = PlanDAG(goal="test")
        plan.add_step(Step(id="s1", description="bad step", action="a"))

        async def executor(step):
            raise ValueError("boom")

        events = []
        async def capture(event):
            events.append(event.event_type.value)

        async with bus:
            bus.subscribe(capture, topics=["plan.events"])
            result = await engine.execute(plan, executor)
            await asyncio.sleep(0.1)  # let events propagate

        assert result.has_failures
        assert "plan.abort" in events
        assert "plan.complete" not in events
