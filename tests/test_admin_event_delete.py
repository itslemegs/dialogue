import ast
import unittest
from pathlib import Path


MAIN = Path(__file__).resolve().parents[1] / "app" / "main.py"


def load_function(name, namespace):
    source = MAIN.read_text()
    tree = ast.parse(source)

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            module = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(module)
            exec(compile(module, str(MAIN), "exec"), namespace)
            return namespace[name]

    raise AssertionError(f"Function {name!r} not found")


class FakeSession:
    def __init__(self):
        self.statements = []

    def exec(self, statement, params=None):
        self.statements.append((str(statement), params))


class AdminEventDeleteTests(unittest.TestCase):
    def setUp(self):
        self.hard_delete_event = load_function(
            "hard_delete_event",
            {
                # hard_delete_event only needs text() at runtime.
                "text": lambda sql: sql,
            },
        )

    def test_hard_delete_event_uses_event_id_for_every_statement(self):
        session = FakeSession()

        self.hard_delete_event(session, 42)

        self.assertGreater(len(session.statements), 0)

        for _, params in session.statements:
            self.assertEqual(params, {"id": 42})

    def test_hard_delete_event_deletes_required_tables(self):
        session = FakeSession()

        self.hard_delete_event(session, 42)

        sql = "\n".join(statement.lower() for statement, _ in session.statements)

        required_tables = {
            "live_summary",
            "proposal_floor_state",
            "proposal_early_ballot",
            "proposal_formal_ballot",
            "proposal_intervention",
            "proposal_speaker_request",
            "proposal_early_vote",
            "proposal_formal_vote",
            "amendment_vote",
            "amendment_vote_state",
            "amendment",
            "drafttranslation",
            "proposaldraft",
            "proposalmessage",
            "proposalroom",
            "floorstate",
            "notification",
            "rorinvite",
            "speakerrequest",
            "intervention",
            "proposal",
            "objection",
            "draft",
            "general_floor_link",
            "question",
            "agendaproposal",
            "eventsequence",
            "event_access_grant",
            "event_stage",
            "event",
        }

        for table in required_tables:
            self.assertIn(
                f"delete from {table}",
                sql,
                f"{table} is missing from hard event deletion",
            )

    def test_children_are_deleted_before_parents(self):
        session = FakeSession()

        self.hard_delete_event(session, 42)

        statements = [
            " ".join(statement.lower().split())
            for statement, _ in session.statements
        ]

        def delete_index(table):
            for index, statement in enumerate(statements):
                parts = statement.split()

                if (
                    len(parts) >= 3
                    and parts[0] == "delete"
                    and parts[1] == "from"
                    and parts[2] == table
                ):
                    return index

            self.fail(f"No DELETE found for {table}")

        # Summary can reference virtually every proposal-floor scope.
        self.assertLess(
            delete_index("live_summary"),
            delete_index("agendaproposal"),
        )

        # State points to the current speaker request.
        self.assertLess(
            delete_index("proposal_floor_state"),
            delete_index("proposal_speaker_request"),
        )

        # Formal ballots point to formal votes.
        self.assertLess(
            delete_index("proposal_formal_ballot"),
            delete_index("proposal_formal_vote"),
        )

        # Amendment children before amendments.
        self.assertLess(
            delete_index("amendment_vote"),
            delete_index("amendment"),
        )
        self.assertLess(
            delete_index("amendment_vote_state"),
            delete_index("amendment"),
        )

        # Amendments/translations before proposal drafts.
        self.assertLess(
            delete_index("amendment"),
            delete_index("proposaldraft"),
        )
        self.assertLess(
            delete_index("drafttranslation"),
            delete_index("proposaldraft"),
        )

        # Objections reference general-floor drafts.
        self.assertLess(
            delete_index("objection"),
            delete_index("draft"),
        )

        # Question children before Question.
        for child in (
            "floorstate",
            "notification",
            "rorinvite",
            "speakerrequest",
            "intervention",
            "proposal",
            "draft",
            "general_floor_link",
        ):
            self.assertLess(
                delete_index(child),
                delete_index("question"),
                f"{child} must be deleted before question",
            )

        # Proposal children before AgendaProposal.
        for child in (
            "proposal_floor_state",
            "proposal_early_ballot",
            "proposal_formal_ballot",
            "proposal_intervention",
            "proposal_speaker_request",
            "proposaldraft",
            "proposalroom",
            "general_floor_link",
        ):
            self.assertLess(
                delete_index(child),
                delete_index("agendaproposal"),
                f"{child} must be deleted before agendaproposal",
            )

        # Event must always be last.
        self.assertEqual(
            delete_index("event"),
            len(statements) - 1,
        )
    def test_cross_scope_pointers_are_detached_before_targets_are_deleted(self):
        session = FakeSession()

        self.hard_delete_event(session, 42)

        statements = [
            " ".join(statement.lower().split())
            for statement, _ in session.statements
        ]

        def find(fragment):
            for index, statement in enumerate(statements):
                if fragment in statement:
                    return index
            self.fail(f"SQL fragment not found: {fragment}")

        intervention_delete = find("delete from intervention")

        self.assertLess(
            find(
                "update proposal_speaker_request "
                "set target_intervention_id = null"
            ),
            intervention_delete,
        )

        self.assertLess(
            find(
                "update rorinvite "
                "set target_intervention_id = null"
            ),
            intervention_delete,
        )

        self.assertLess(
            find(
                "update speakerrequest "
                "set target_intervention_id = null"
            ),
            intervention_delete,
        )

        self.assertLess(
            find(
                "update intervention set relates_to_id = null"
            ),
            intervention_delete,
        )

        self.assertLess(
            find(
                "update proposal_floor_state "
                "set current_speaker_request_id = null"
            ),
            find("delete from proposal_speaker_request"),
        )

        self.assertLess(
            find(
                "update floorstate "
                "set current_speaker_request_id = null"
            ),
            find("delete from speakerrequest"),
        )

        self.assertLess(
            find("update proposalmessage set parent_id = null"),
            find("delete from proposalmessage"),
        )

        self.assertLess(
            find("update proposal_intervention set parent_id = null"),
            find("delete from proposal_intervention"),
        )

        self.assertLess(
            find(
                "update proposal_formal_ballot "
                "set formal_vote_id = null"
            ),
            find("delete from proposal_formal_vote"),
        )


if __name__ == "__main__":
    unittest.main()