import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

from sqlmodel import select

from app.models import (
    AgendaProposal,
    ProposalStatus,
    ProposalDraft,
    ProposalDraftStatus,
    Amendment,
    ProposalFloorState,
    User,
)


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app" / "main.py"
TEMPLATE = ROOT / "app" / "templates" / "events" / "proposal_floor.html"


def load_function(name, namespace):
    source = MAIN.read_text()
    tree = ast.parse(source)

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            # Do not execute FastAPI decorators in this isolated test.
            node.decorator_list = []

            module = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(module)

            exec(
                compile(module, str(MAIN), "exec"),
                namespace,
            )
            return namespace[name]

    raise AssertionError(f"Function {name!r} not found")


class FakeResult:
    def __init__(self, value):
        self.value = value

    def all(self):
        if isinstance(self.value, list):
            return self.value
        if self.value is None:
            return []
        return [self.value]

    def first(self):
        if isinstance(self.value, list):
            return self.value[0] if self.value else None
        return self.value


class FakeSession:
    def __init__(self, results):
        self.results = list(results)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def exec(self, statement):
        if not self.results:
            raise AssertionError(
                f"Unexpected database query: {statement}"
            )

        return FakeResult(self.results.pop(0))


class TemplateRecorder:
    def TemplateResponse(self, request, template_name, context, **kwargs):
        return {
            "template_name": template_name,
            "context": context,
        }


class ProposalFloorListingTests(unittest.TestCase):
    def _build_function(self, db, user, event):
        return load_function(
            "proposal_floor_index",
            {
                "Request": object,
                "HTMLResponse": object,
                "HTTPException": RuntimeError,

                "select": select,

                "AgendaProposal": AgendaProposal,
                "ProposalStatus": ProposalStatus,
                "ProposalDraft": ProposalDraft,
                "ProposalDraftStatus": ProposalDraftStatus,
                "Amendment": Amendment,
                "ProposalFloorState": ProposalFloorState,
                "User": User,

                "current_user": lambda request: user,
                "get_session": lambda: db,
                "_require_event_access": (
                    lambda db, user, event_id: event
                ),

                "effective_flags": lambda user: {
                    "IS_ADMIN": False,
                    "IS_CHAIR": False,
                    "IS_PRESIDENT": False,
                    "IS_MEMBER": True,
                },
                "EMPTY_FLAGS": {},

                "templates": TemplateRecorder(),
            },
        )

    def test_open_and_closed_drafts_are_separated(self):
        user = SimpleNamespace(id=1, handle="viewer")
        sponsor = SimpleNamespace(id=2, handle="sponsor")
        event = SimpleNamespace(id=41)

        proposal = SimpleNamespace(
            id=10,
            title="Authored proposal 日本語",
        )

        open_draft = SimpleNamespace(
            id=101,
            proposal_id=proposal.id,
            event_id=event.id,
            l_number="A/2027/15/L.1",
            title="Open draft",
            sponsor_id=sponsor.id,
        )

        closed_draft = SimpleNamespace(
            id=102,
            proposal_id=proposal.id,
            event_id=event.id,
            l_number="A/2027/15/L.2",
            title="Closed draft",
            sponsor_id=sponsor.id,
        )

        open_state = SimpleNamespace(is_open=True)
        closed_state = SimpleNamespace(is_open=False)

        # proposal_floor_index() query order:
        #
        # 1 items
        # 2 users
        # 3 submitted drafts
        # 4 amendments for open draft
        # 5 floor state for open draft
        # 6 amendments for closed draft
        # 7 floor state for closed draft
        db = FakeSession(
            [
                [proposal],
                [user, sponsor],
                [open_draft, closed_draft],
                [],
                open_state,
                [],
                closed_state,
            ]
        )

        fn = self._build_function(db, user, event)

        response = fn(event.id, object())
        context = response["context"]

        self.assertEqual(
            response["template_name"],
            "events/proposal_floor.html",
        )

        self.assertEqual(
            [row["draft"].id for row in context["open_rows"]],
            [open_draft.id],
        )

        self.assertEqual(
            [row["draft"].id for row in context["closed_rows"]],
            [closed_draft.id],
        )

        self.assertTrue(context["open_rows"][0]["is_open"])
        self.assertFalse(context["closed_rows"][0]["is_open"])

        # Backward-compatible aggregate remains available.
        self.assertEqual(
            [row["draft"].id for row in context["rows"]],
            [open_draft.id, closed_draft.id],
        )

    def test_missing_floor_state_is_treated_as_open(self):
        user = SimpleNamespace(id=1, handle="viewer")
        event = SimpleNamespace(id=41)

        proposal = SimpleNamespace(
            id=10,
            title="Proposal",
        )

        draft = SimpleNamespace(
            id=101,
            proposal_id=proposal.id,
            event_id=event.id,
            l_number="A/2027/15/L.1",
            title="Never opened before",
            sponsor_id=user.id,
        )

        db = FakeSession(
            [
                [proposal],
                [user],
                [draft],
                [],
                None,  # No ProposalFloorState yet.
            ]
        )

        fn = self._build_function(db, user, event)

        response = fn(event.id, object())
        context = response["context"]

        self.assertEqual(len(context["open_rows"]), 1)
        self.assertEqual(len(context["closed_rows"]), 0)
        self.assertTrue(context["open_rows"][0]["is_open"])

    def test_closed_section_has_no_action_links(self):
        source = TEMPLATE.read_text()

        self.assertIn("OPEN PROPOSAL FLOORS", source)
        self.assertIn("CLOSED PROPOSAL FLOORS", source)

        closed_section = source.split(
            "CLOSED PROPOSAL FLOORS",
            1,
        )[1]

        # Closed drafts are historical display only:
        # no Open button and no Amendment action.
        self.assertNotIn(
            'href="/events/{{ event.id }}/proposal-floor/',
            closed_section,
        )
        self.assertNotIn(
            "proposal_floor.amendment_count",
            closed_section,
        )
    def test_listing_keeps_adopted_drafts_but_excludes_withdrawn(self):
        source = MAIN.read_text()

        start = source.index("def proposal_floor_index")
        end = source.index("def _pfi_scope_filter", start)
        listing_source = source[start:end]

        # Completed/adopted proposal floors must remain visible
        # in the Closed section.
        self.assertIn(
            "ProposalDraftStatus.ADOPTED",
            listing_source,
        )

        # Withdrawn drafts are a different lifecycle state and
        # should not reappear as closed proposal-floor history.
        self.assertNotIn(
            "ProposalDraftStatus.WITHDRAWN",
            listing_source,
        )


if __name__ == "__main__":
    unittest.main()