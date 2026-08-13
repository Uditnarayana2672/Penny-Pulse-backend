from sqlalchemy import select

from app.models.profile import Profile, ProfileLive


def test_profile_reads_go_through_the_live_view():
    """Reads must not hit `profile` directly — the view is what applies deleted_at."""
    sql = str(select(ProfileLive).where(ProfileLive.user_id == "x"))

    assert "FROM profile_live" in sql
    assert "FROM profile " not in sql


def test_profile_writes_target_the_table():
    assert Profile.__table__.name == "profile"
