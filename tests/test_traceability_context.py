import importlib.util
import unittest
from importlib.resources import files

from openhands.sdk import AgentContext
from openhands.sdk.agent import ACPAgent
from openhands.sdk.context import Skill
from traceability.openhands import (
    with_model_checking,
    with_property_testing,
    with_recovery,
    with_traceability,
)


@unittest.skipUnless(importlib.util.find_spec("intentbond"), "Optional IntentBond package")
class ContextTests(unittest.TestCase):
    def test_model_guides_replace_previous_selection_and_survive_serialization(self):
        directory = files("intentbond") / "skills/model-checking/references"
        for previous_backend, backend, selected in (
            ("alloy", "alloy", "execution"),
            ("alloy", "z3", "smt"),
            ("z3", "chc", "chc"),
        ):
            with self.subTest(previous_backend=previous_backend, backend=backend):
                original = AgentContext(
                    skills=[Skill(name="task", content="Model the selected claims.")]
                )
                context = with_model_checking(original, language="python", backend=previous_backend)
                context = with_model_checking(context, language="daml", backend=backend)
                restored = ACPAgent.model_validate_json(
                    ACPAgent(acp_command=["codex-acp"], agent_context=context).model_dump_json()
                )
                prompt = restored.agent_context.to_acp_prompt_context()
                self.assertIn("Model the selected claims.", prompt)
                self.assertEqual(len(context.skills), 2)
                for name in ("execution", "smt", "chc", "python", "daml"):
                    self.assertEqual(
                        prompt.count((directory / (name + ".md")).read_text()),
                        int(name in (selected, "daml")),
                    )
                self.assertEqual(len(original.skills), 1)
        for options in ({"language": "../setup"}, {"backend": "../setup"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                with_model_checking(**options)

    def test_native_serialization_preserves_existing_and_traceability_instructions(self):
        original = AgentContext(skills=[Skill(name="task", content="Implement the selected task.")])
        context = with_traceability(original)
        agent = ACPAgent(acp_command=["codex-acp"], agent_context=context)
        restored = ACPAgent.model_validate_json(agent.model_dump_json())
        text = restored.agent_context.to_acp_prompt_context()
        self.assertIn("Implement the selected task.", text)
        procedure = next(s.content for s in context.skills if s.name == "intentbond")
        self.assertTrue(procedure.strip())
        self.assertIn(procedure, text)
        reference = (
            files("intentbond")
            .joinpath("skills/intentbond/references/requirements.md")
            .read_text(encoding="utf-8")
        )
        self.assertIn(reference, text)
        setup = (
            files("intentbond")
            .joinpath("skills/intentbond/references/setup.md")
            .read_text(encoding="utf-8")
        )
        self.assertIn(setup, text)
        semantics = (
            files("intentbond")
            .joinpath("skills/intentbond/references/semantics.md")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(text.count(semantics), 1)
        self.assertEqual(len(original.skills), 1)
        self.assertEqual(len(with_traceability(context).skills), 2)

    def test_provisioned_context_omits_only_setup_across_initial_and_repair_sessions(self):
        original = AgentContext(skills=[Skill(name="task", content="Approved task and scope.")])
        complete = with_traceability(original)
        initial = with_traceability(original, provisioned=True)
        directory = files("intentbond") / "skills/intentbond"
        setup = (directory / "references/setup.md").read_text(encoding="utf-8")
        for context in (initial, with_traceability(initial, provisioned=True)):
            serialized = ACPAgent(
                acp_command=["codex-acp"], agent_context=context
            ).model_dump_json()
            prompt = ACPAgent.model_validate_json(serialized).agent_context.to_acp_prompt_context()
            self.assertIn("Approved task and scope.", prompt)
            self.assertEqual(len(context.skills), 2)
            self.assertNotIn(setup, prompt)
            for reference in ("requirements.md", "semantics.md"):
                content = (directory / "references" / reference).read_text(encoding="utf-8")
                self.assertEqual(prompt.count(content), 1)
            self.assertIn(Skill.load(directory / "SKILL.md").content, prompt)
            self.assertLess(len(prompt), len(complete.to_acp_prompt_context()) - len(setup))
        self.assertEqual(original.skills, [Skill(name="task", content="Approved task and scope.")])

    def test_recovery_guidance_survives_acp_serialization_and_fresh_repair(self):
        original = AgentContext(
            skills=[Skill(name="task", content="Recover the session capability.")]
        )
        initial = with_recovery(original)
        for context in (initial, with_recovery(initial)):
            agent = ACPAgent(acp_command=["codex-acp"], agent_context=context)
            restored = ACPAgent.model_validate_json(agent.model_dump_json())
            prompt = restored.agent_context.to_acp_prompt_context()
            self.assertIn("Recover the session capability.", prompt)
            self.assertEqual(len(context.skills), 2)
            procedure = next(s.content for s in context.skills if s.name == "recover-baseline")
            self.assertIn(procedure, prompt)
            reference = (
                files("intentbond") / "skills/recover-baseline/references/recovery.md"
            ).read_text(encoding="utf-8")
            self.assertIn(reference, prompt)
            semantics = (
                files("intentbond")
                .joinpath("skills/intentbond/references/semantics.md")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(prompt.count(semantics), 1)
        self.assertEqual(len(original.skills), 1)

    def test_property_workflow_reaches_development_and_recovery_across_reinjection(self):
        directory = files("intentbond") / "skills/property-testing"
        procedure = Skill.load(directory / "SKILL.md").content
        for attach in (with_traceability, with_recovery):
            with self.subTest(workflow=attach.__name__):
                context = attach()
                for current in (context, attach(context)):
                    restored = ACPAgent.model_validate_json(
                        ACPAgent(acp_command=["codex-acp"], agent_context=current).model_dump_json()
                    )
                    prompt = restored.agent_context.to_acp_prompt_context()
                    self.assertEqual(prompt.count(procedure), 1)
                    for reference in (directory / "references").iterdir():
                        self.assertNotIn(reference.read_text(), prompt)

    def test_selected_property_guide_survives_serialization_and_replacement(self):
        directory = files("intentbond") / "skills/property-testing"
        original = AgentContext(skills=[Skill(name="task", content="Check the approved quota.")])
        for framework, reference in (
            ("hypothesis", "python"),
            ("fast-check", "typescript"),
            ("quickcheck", "haskell"),
            ("hegel", "hegel"),
        ):
            with self.subTest(framework=framework):
                context = with_property_testing(original, framework=framework)
                context = with_property_testing(context, framework=framework)
                restored = ACPAgent.model_validate_json(
                    ACPAgent(acp_command=["codex-acp"], agent_context=context).model_dump_json()
                )
                prompt = restored.agent_context.to_acp_prompt_context()
                self.assertIn("Check the approved quota.", prompt)
                self.assertEqual(len(context.skills), 2)
                for file in (directory / "references").iterdir():
                    self.assertEqual(prompt.count(file.read_text()), int(file.stem == reference))
        self.assertEqual(len(original.skills), 1)
        with self.assertRaises(ValueError):
            with_property_testing(original, framework="../setup")


if __name__ == "__main__":
    unittest.main()
