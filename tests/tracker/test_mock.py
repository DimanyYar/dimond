"""The demo tracker the sync reads in demo mode (ADR 0008)."""

from apps.tracker.mock import MockTracker


def test_demo_reads_its_tasks_and_leaves_out_ones_it_never_had():
    issues = MockTracker().issues(["FI-2214", "FI-9999", "FI-2190"])

    assert [(i.key, i.status) for i in issues] == [
        ("FI-2214", "Code review"),
        ("FI-2190", "on Staging"),
    ]


def test_a_task_the_demo_created_is_still_found_and_stays_to_do():
    demo = MockTracker()
    created = demo.create_issue("FI", "Bug", "Leads export times out", "", ["dimond"])

    assert [(i.key, i.status) for i in MockTracker().issues([created.key])] == [
        (created.key, "To Do")
    ]


def test_a_large_number_in_a_project_the_demo_does_not_have_is_not_a_task():
    assert MockTracker().issues(["HR-50000"]) == []
