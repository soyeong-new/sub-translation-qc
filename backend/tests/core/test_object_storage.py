import boto3
import pytest
from moto import mock_aws

from app.core import object_storage


@pytest.fixture
def s3_env(monkeypatch):
    monkeypatch.setenv("OBJECT_STORAGE_BUCKET", "test-bucket")
    monkeypatch.setenv("OBJECT_STORAGE_ACCESS_KEY", "testing")
    monkeypatch.setenv("OBJECT_STORAGE_SECRET_KEY", "testing")
    monkeypatch.delenv("OBJECT_STORAGE_ENDPOINT", raising=False)
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="test-bucket")
        yield


def test_upload_file_stores_object_under_given_key(tmp_path, s3_env):
    local_file = tmp_path / "clip.mp4"
    local_file.write_bytes(b"video-bytes")

    key = object_storage.upload_file(local_file, "video/clip.mp4")

    assert key == "video/clip.mp4"
    assert object_storage.list_files() == ["video/clip.mp4"]


def test_download_file_writes_object_bytes_to_local_path(tmp_path, s3_env):
    local_file = tmp_path / "clip.mp4"
    local_file.write_bytes(b"video-bytes")
    object_storage.upload_file(local_file, "video/clip.mp4")
    dest = tmp_path / "downloaded.mp4"

    object_storage.download_file("video/clip.mp4", dest)

    assert dest.read_bytes() == b"video-bytes"


def test_delete_file_removes_object(tmp_path, s3_env):
    local_file = tmp_path / "clip.mp4"
    local_file.write_bytes(b"video-bytes")
    object_storage.upload_file(local_file, "video/clip.mp4")

    object_storage.delete_file("video/clip.mp4")

    assert object_storage.list_files() == []


def test_list_files_filters_by_prefix(tmp_path, s3_env):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v")
    srt = tmp_path / "sub.srt"
    srt.write_bytes(b"s")
    object_storage.upload_file(video, "video/clip.mp4")
    object_storage.upload_file(srt, "srt/sub.srt")

    assert object_storage.list_files(prefix="video/") == ["video/clip.mp4"]
