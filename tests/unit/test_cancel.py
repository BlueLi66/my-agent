import pytest

from my_agent.core.cancel import CancelToken
from my_agent.core.errors import Cancelled


def test_cancel_cascades_and_keeps_original_reason():
    parent = CancelToken()
    child = parent.child()
    grandchild = child.child()
    sibling = parent.child()

    child.cancel("child stopped")
    assert not parent.is_cancelled
    assert not sibling.is_cancelled
    with pytest.raises(Cancelled, match="child stopped"):
        grandchild.raise_if_cancelled()

    parent.cancel("parent stopped")
    for token in (parent, sibling, parent.child()):
        with pytest.raises(Cancelled, match="parent stopped"):
            token.raise_if_cancelled()
    with pytest.raises(Cancelled, match="child stopped"):
        child.raise_if_cancelled()


def test_cancel_runs_cleanup_once_and_isolates_failures(caplog):
    token = CancelToken()
    effects = []

    def broken_cleanup():
        raise RuntimeError("cleanup broke")

    token.on_cancel(broken_cleanup)
    token.on_cancel(lambda: effects.append("cleanup"))
    remove = token.on_cancel(lambda: effects.append("removed"))
    remove()
    remove()

    token.cancel("first")
    token.cancel("second")
    token.on_cancel(lambda: effects.append("registered after cancellation"))

    assert effects == ["cleanup", "registered after cancellation"]
    assert "cleanup broke" in caplog.text
    with pytest.raises(Cancelled, match="first"):
        token.raise_if_cancelled()


def test_live_grandchild_stays_linked_through_an_unreferenced_parent():
    root = CancelToken()
    grandchild = root.child().child()

    root.cancel("root stopped")

    with pytest.raises(Cancelled, match="root stopped"):
        grandchild.raise_if_cancelled()
