"""S3 호환 오브젝트 스토리지(AWS S3, OCI Object Storage 등) 연동 모듈.

벤더별 어댑터 없이 boto3 S3 클라이언트 하나로 통일한다. 엔드포인트/키/버킷을
환경변수로만 바꾸면 AWS S3와 OCI Object Storage(S3 Compatibility API) 사이를
코드 변경 없이 전환할 수 있다."""

import os
from pathlib import Path

import boto3


def _client():
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("OBJECT_STORAGE_ENDPOINT") or None,
        aws_access_key_id=os.getenv("OBJECT_STORAGE_ACCESS_KEY"),
        aws_secret_access_key=os.getenv("OBJECT_STORAGE_SECRET_KEY"),
        region_name=os.getenv("OBJECT_STORAGE_REGION", "us-east-1"),
    )


def _bucket() -> str:
    return os.getenv("OBJECT_STORAGE_BUCKET", "sub-translation-qc")


def upload_file(local_path: Path, key: str) -> str:
    _client().upload_file(str(local_path), _bucket(), key)
    return key


def download_file(key: str, local_path: Path) -> Path:
    _client().download_file(_bucket(), key, str(local_path))
    return local_path


def delete_file(key: str) -> None:
    _client().delete_object(Bucket=_bucket(), Key=key)


def list_files(prefix: str = "") -> list[str]:
    resp = _client().list_objects_v2(Bucket=_bucket(), Prefix=prefix)
    return [obj["Key"] for obj in resp.get("Contents", [])]
