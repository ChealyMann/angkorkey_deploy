import os


def test_app_uses_temp_database(app):
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    assert uri == os.environ["DATABASE_URL"]
    assert "instance" not in uri.replace("\\", "/").split("/")[-2:]


def test_database_file_is_in_system_temp_dir(app):
    import tempfile
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    path = os.path.realpath(uri.split("sqlite:///", 1)[-1])
    temp_root = os.path.realpath(tempfile.gettempdir())
    assert os.path.splitdrive(path)[0] == os.path.splitdrive(temp_root)[0]
    assert os.path.commonpath([path, temp_root]) == temp_root
