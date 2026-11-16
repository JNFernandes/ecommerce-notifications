"""Temporary check that the gate runs before real tests exist; removed with the first tests."""


def test_should_import_the_notifications_package() -> None:
    import notifications

    assert notifications.__doc__
