"""Adapter-independent execution lifecycle and assertion evaluation."""

import asyncio
import platform
import time

from omnitest.adapters.base import ExecutionContext
from omnitest.core.assertions import AssertionEngine
from omnitest.core.ir import Step, TestIR
from omnitest.core.security import Redactor, render


class Runner:
    def __init__(self, settings, registry):
        self.settings = settings
        self.registry = registry
        self.assertions = AssertionEngine()

    async def execute(self, case_data, target, variables, evidence_dir):
        case = TestIR.model_validate(case_data)
        started = time.monotonic()
        attempts = []
        status = "ERROR"
        redactor = Redactor()
        for attempt in range(case.retries + 1):
            folder = evidence_dir / str(attempt + 1)
            folder.mkdir(parents=True, exist_ok=True)
            context = ExecutionContext(self.settings, target, folder, variables)
            detail = {
                "attempt": attempt + 1,
                "steps": [],
                "assertions": [],
                "logs": context.logs,
                "evidence": [],
                "error": None,
            }
            adapter = None
            output = {
                "parameters": variables,
                "environment": case.environment,
                "steps": {},
                "last": {},
            }
            try:
                async with asyncio.timeout(case.timeout):
                    preconditions = self.assertions.check(
                        render_assertions(case.preconditions, variables), output
                    )
                    detail["preconditions"] = preconditions
                    if any(not a["passed"] for a in preconditions):
                        status = "SKIPPED"
                    else:
                        if not target.get("authorized"):
                            raise ValueError("Target has not been explicitly authorized")
                        adapter = self.registry.create(case.adapter)
                        await adapter.check(context)
                        status = "PASSED"
                        for step in case.steps:
                            step_start = time.monotonic()
                            resolved = Step.model_validate(render(step.model_dump(), variables))
                            context.logs.append(f"Starting {step.id}: {step.action}")
                            step_result = {
                                "id": step.id,
                                "action": step.action,
                                "status": "RUNNING",
                            }
                            detail["steps"].append(step_result)
                            try:
                                data = await adapter.execute(resolved, context)
                                data["duration"] = time.monotonic() - step_start
                                checked = self.assertions.check(resolved.assertions, data)
                                step_result.update(
                                    output=data,
                                    assertions=checked,
                                    status="PASSED"
                                    if all(a["passed"] for a in checked)
                                    else "FAILED",
                                )
                                output["steps"][step.id] = data
                                output["last"] = data
                                if step_result["status"] == "FAILED":
                                    status = "FAILED"
                                    if hasattr(adapter, "capture"):
                                        await adapter.capture(f"{step.id}-assertion", context)
                                    break
                            except asyncio.CancelledError:
                                step_result["status"] = "CANCELLED"
                                raise
                            except Exception as exc:
                                step_result.update(status="ERROR", error=str(exc))
                                raise
                        checked = self.assertions.check(
                            render_assertions(case.assertions, variables), output
                        )
                        detail["assertions"] = checked
                        if status == "PASSED" and any(not a["passed"] for a in checked):
                            status = "FAILED"
            except asyncio.CancelledError:
                status = "CANCELLED"
                detail["error"] = "Execution cancelled"
            except TimeoutError:
                status = "ERROR"
                detail["error"] = f"Test exceeded {case.timeout}s timeout"
                for step_result in detail["steps"]:
                    if step_result["status"] == "CANCELLED":
                        step_result["status"] = "ERROR"
            except Exception as exc:
                status = "ERROR"
                detail["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                if adapter:
                    try:
                        await asyncio.wait_for(adapter.close(), 10)
                    except Exception as exc:
                        detail["logs"].append(f"Cleanup error: {exc}")
                        if status == "PASSED":
                            status = "ERROR"
                detail["evidence"] = [f"{attempt + 1}/{name}" for name in context.evidence]
                for name in context.evidence:
                    from omnitest.core.security import contained

                    path = contained(folder, name)
                    if path.suffix.lower() in {".txt", ".json", ".log", ".csv", ".xml"}:
                        path.write_text(
                            redactor.clean(path.read_text(encoding="utf-8")), encoding="utf-8"
                        )
                detail["status"] = status
                attempts.append(redactor.clean(detail))
            if status not in {"ERROR", "FAILED"}:
                break
        return {
            "status": status,
            "duration": time.monotonic() - started,
            "detail": {
                "attempts": attempts,
                "parameters": redactor.clean(variables),
                "test_ir": redactor.clean(case.model_dump(mode="json")),
                "target": redactor.clean(target),
                "environment": {
                    "os": platform.system(),
                    "python": platform.python_version(),
                    "adapter": case.adapter,
                    "adapter_version": self.registry.factories[case.adapter].info.version
                    if case.adapter in self.registry.factories
                    else "unavailable",
                },
            },
        }


def render_assertions(assertions, variables):
    from omnitest.core.ir import Assertion

    return [Assertion.model_validate(render(a.model_dump(), variables)) for a in assertions]
