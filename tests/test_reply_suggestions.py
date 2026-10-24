import pytest
from fastapi import HTTPException


def test_new_local_account_requires_profile_until_saved_and_restored(tmp_path):
    from app.core_local.schemas import LoginInput, ProfileInput, RegisterInput
    from app.core_local.store import CoreStore

    store = CoreStore(tmp_path / "onboarding.db")
    value = RegisterInput(
        email="new@example.test", password="test-password-123", display_name="新朋友"
    )
    session = store.register(value)
    assert session.user.profile_setup_required
    assert store.authenticate(session.access_token).profile_setup_required
    with pytest.raises(HTTPException):
        store.update_profile(
            session.user.id,
            ProfileInput(display_name="新名字", bio="", badge="🍜", expected_version=99),
        )
    assert store.authenticate(session.access_token).profile_setup_required
    updated = store.update_profile(
        session.user.id, ProfileInput(display_name="新名字", bio="", badge="🍜", expected_version=1)
    )
    assert not updated.profile_setup_required
    restored = CoreStore(store.path).login(LoginInput(email=value.email, password=value.password))
    assert not restored.user.profile_setup_required
