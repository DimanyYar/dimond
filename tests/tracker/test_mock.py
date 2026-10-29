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


def test_a_key_with_an_odd_digit_is_skipped_and_the_rest_are_still_read():
    issues = MockTracker().issues(["FI-²", "FI-2214"])

    assert [i.key for i in issues] == ["FI-2214"]


def test_the_demo_task_from_the_mockups_has_one_question_for_the_client_with_a_picture():
    demo = MockTracker()
    [task] = demo.issues(["FI-2214"])

    questions = [c for c in task.comments if c.asks_the_client]
    assert [q.author_name for q in questions] == ["Olga Petrova"]
    assert len(task.comments) == 2
    [picture] = questions[0].files
    assert demo.file_content(picture).startswith(b"\x89PNG")


def test_demo_projects_have_sprints_except_northwind():
    demo = MockTracker()

    assert [s.name for s in demo.open_sprints("FI")][:3] == [
        "Sprint 12",
        "Bugs bucket",
        "Ideas bucket",
    ]
    assert {s.board for s in demo.open_sprints("HF")} == {"HF web", "HF mobile"}
    assert demo.open_sprints("NW") == []
