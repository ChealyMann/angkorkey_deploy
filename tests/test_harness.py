import os


def test_app_uses_temp_database(app):
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    assert uri == os.environ["DATABASE_URL"]
    assert "instance" not in uri.replace("\\", "/").split("/")[-2:]
