from datetime import timedelta

import pytest

from dashboard.people import (
    IamUser,
    Person,
    RosterError,
    fetch_iam,
    load_roster_file,
    map_people,
    parse_roster,
)
from tests.helpers import IAM_USER, T0, FakeAws, creation, group_response


def test_parse_roster_handles_bom_and_spaces():
    text = "\ufeffusername , primary_username,cohort\n student-a ,student-a, DV_FT\n\n"
    assert parse_roster(text) == {"student-a": ("student-a", "DV_FT")}


def test_parse_roster_rejects_duplicates_without_naming_them():
    with pytest.raises(RosterError) as err:
        parse_roster(
            "username,primary_username,cohort\nstudent-a,student-a,DV_FT\nstudent-a,student-a,DV_FT\n"
        )
    assert "line 3" in str(err.value)
    assert "student-a" not in str(err.value)


def test_parse_roster_requires_columns():
    with pytest.raises(RosterError, match="cohort"):
        parse_roster("username,primary_username\nstudent-a,student-a\n")


def test_missing_roster_file_is_none(tmp_path):
    assert load_roster_file(None) is None
    assert load_roster_file(str(tmp_path / "nope.csv")) is None


def _users(*names):
    return [IamUser(n, T0) for n in names]


def test_order_roster_then_group_then_created_by():
    users = _users("student-a", "student-b", "student-b-test", "student-c-test", "loner")
    roster = {"student-a": ("student-a", "DV_FT")}
    members = {"DV_PT": ["student-a", "student-b"]}
    made = [
        creation("iam-user", "student-b-test", "student-b", region="us-east-1"),
        creation("iam-user", "student-c-test", "student-b-test", region="us-east-1"),
    ]
    people = map_people(users, members, roster, made)
    assert people["student-a"] == Person("student-a", "student-a", "DV_FT")
    assert people["student-b"] == Person("student-b", "student-b", "DV_PT")
    assert people["student-b-test"].cohort == "DV_PT"
    assert people["student-c-test"] == Person("student-c-test", "student-c-test", "DV_PT")
    assert people["loner"] == Person("loner", "loner", None)


def test_roster_groups_test_users_under_primary_card():
    roster = {"student-a-test": ("student-a", "DV_FT")}
    assert (
        map_people(_users("student-a-test"), {}, roster, [])["student-a-test"].card == "student-a"
    )


def test_created_by_cycle_terminates():
    made = [
        creation("iam-user", "x", "y", region="us-east-1"),
        creation("iam-user", "y", "x", region="us-east-1"),
    ]
    assert map_people(_users("x", "y"), {}, None, made)["x"].cohort is None


def test_created_by_ignores_event_for_older_user_with_same_name():
    users = [IamUser("student-a-test", T0)]
    stale = creation(
        "iam-user", "student-a-test", "student-b", region="us-east-1", when=T0 - timedelta(days=30)
    )
    people = map_people(users, {"DV_FT": ["student-b"]}, None, [stale])
    assert people["student-a-test"].cohort is None


def test_fetch_iam_reads_users_and_cohort_groups():
    aws = FakeAws()
    s = aws.stub("iam", "us-east-1")
    s.add_response("list_users", {"Users": [IAM_USER]})
    s.add_response("get_group", group_response("DV_FT"), {"GroupName": "DV_FT"})
    users, members, errors = fetch_iam(aws, {"DV_FT": "DV_FT"})
    aws.assert_done()
    assert users == [IamUser("student-a", T0)]
    assert members == {"DV_FT": ["student-a"]}
    assert errors == []


def test_one_bad_cohort_group_keeps_users_and_other_groups():
    aws = FakeAws()
    s = aws.stub("iam", "us-east-1")
    s.add_response("list_users", {"Users": [IAM_USER]})
    s.add_client_error(
        "get_group",
        service_error_code="NoSuchEntity",
        http_status_code=404,
        expected_params={"GroupName": "grp-typo"},
    )
    s.add_response("get_group", group_response("DV_FT"), {"GroupName": "DV_FT"})
    users, members, errors = fetch_iam(aws, {"BAD": "grp-typo", "DV_FT": "DV_FT"})
    aws.assert_done()
    assert users == [IamUser("student-a", T0)]
    assert members == {"DV_FT": ["student-a"]}
    assert errors == ["iam group BAD: NoSuchEntity"]


def test_list_users_failure_still_reads_groups():
    aws = FakeAws()
    s = aws.stub("iam", "us-east-1")
    s.add_client_error("list_users", service_error_code="AccessDenied", http_status_code=403)
    s.add_response("get_group", group_response("DV_FT"), {"GroupName": "DV_FT"})
    users, members, errors = fetch_iam(aws, {"DV_FT": "DV_FT"})
    aws.assert_done()
    assert (users, members, errors) == ([], {"DV_FT": ["student-a"]}, ["iam users: AccessDenied"])


def test_group_members_keep_their_card_when_list_users_fails():
    people = map_people([], {"DV_FT": ["student-b"]}, None, [])
    assert people["student-b"] == Person("student-b", "student-b", "DV_FT")


def test_people_cover_roster_names_and_owners_without_an_iam_user():
    roster = {"gone-user": ("student-a", "DV_FT")}
    people = map_people([], {}, roster, [], principals=["gone-user", "ex-student"])
    assert people["gone-user"] == Person("gone-user", "student-a", "DV_FT")
    assert people["ex-student"] == Person("ex-student", "ex-student", None)


@pytest.mark.parametrize(
    "text",
    [
        "username,primary_username,cohort,Max Mustermann\nstudent-a,student-a,DV_FT,x\n",
        "username,primary_username,cohort\nstudent-a,student-a,DV_FT,Max Mustermann\n",
    ],
    ids=["extra-header", "extra-value"],
)
def test_parse_roster_rejects_extra_columns_without_naming_them(text):
    with pytest.raises(RosterError) as err:
        parse_roster(text)
    assert "Max" not in str(err.value)


@pytest.mark.parametrize("primary", ["Max Mustermann", "a" * 65, "stüdent"])
def test_parse_roster_rejects_a_primary_username_that_is_no_username(primary):
    with pytest.raises(RosterError) as err:
        parse_roster(f"username,primary_username,cohort\nstudent-a,{primary},DV_FT\n")
    assert "line 2" in str(err.value)
    assert primary not in str(err.value)


def test_primary_username_may_name_another_roster_row():
    text = "username,primary_username,cohort\nlab user,lab user,DV_FT\nstudent-a,lab user,DV_FT\n"
    assert parse_roster(text)["student-a"] == ("lab user", "DV_FT")
