"""OTII_LEARN_S3_KEY_PREFIX keeps every Otii Learn file inside one folder of a
shared bucket, for every S3 call LearnHouse makes (listed 29 Sep 2026):
put/get/head/delete_object, delete_objects, copy_object, upload_file,
download_file, generate_presigned_url and list_objects_v2 paging."""

from __future__ import annotations

import pytest

from src.otii.s3_prefix import PrefixedS3Client, prefixed


class Recorder:
    """Stands in for a boto3 S3 client: records calls, answers listings."""

    def __init__(self, keys=()):
        self.calls = []
        self.keys = list(keys)

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return {"ok": True}
        return call

    def get_paginator(self, operation):
        recorder = self

        class Pages:
            def paginate(self, **kwargs):
                recorder.calls.append(("paginate", (operation,), kwargs))
                prefix = kwargs.get("Prefix", "")
                matching = [k for k in recorder.keys if k.startswith(prefix)]
                yield {"Contents": [{"Key": k} for k in matching],
                       "CommonPrefixes": [{"Prefix": prefix + "sub/"}]}

        return Pages()


P = "staging/otii-learn/"


def wrapped(keys=()):
    raw = Recorder(keys)
    return prefixed(raw, P), raw


def test_no_prefix_returns_learnhouses_own_client_untouched():
    raw = Recorder()
    assert prefixed(raw, "") is raw


@pytest.mark.parametrize("bad", ["/abs/", "a/../b/", "..", "a//b/"])
def test_unsafe_prefixes_are_refused(bad):
    with pytest.raises(ValueError):
        PrefixedS3Client(Recorder(), bad)


def test_prefix_always_ends_with_one_slash():
    client = PrefixedS3Client(Recorder(), "staging/otii-learn")
    assert client.key("content/a.png") == "staging/otii-learn/content/a.png"


@pytest.mark.parametrize("method", ["put_object", "get_object", "head_object", "delete_object"])
def test_single_object_calls_get_the_prefix(method):
    client, raw = wrapped()
    getattr(client, method)(Bucket="otii", Key="content/orgs/1/a.png")
    assert raw.calls[-1][2]["Key"] == P + "content/orgs/1/a.png"


def test_copy_prefixes_both_source_and_destination():
    client, raw = wrapped()
    client.copy_object(Bucket="otii", CopySource={"Bucket": "otii", "Key": "content/a"}, Key="content/b")
    kwargs = raw.calls[-1][2]
    assert kwargs["Key"] == P + "content/b"
    assert kwargs["CopySource"] == {"Bucket": "otii", "Key": P + "content/a"}


def test_copy_source_given_as_text_is_prefixed_too():
    client, raw = wrapped()
    client.copy_object(Bucket="otii", CopySource="otii/content/a", Key="content/b")
    assert raw.calls[-1][2]["CopySource"] == "otii/" + P + "content/a"


def test_delete_many_prefixes_every_key():
    client, raw = wrapped()
    client.delete_objects(Bucket="otii", Delete={"Objects": [{"Key": "content/a"}, {"Key": "content/b"}]})
    assert raw.calls[-1][2]["Delete"]["Objects"] == [{"Key": P + "content/a"}, {"Key": P + "content/b"}]


def test_upload_file_by_position_and_by_name():
    client, raw = wrapped()
    client.upload_file("/tmp/x", "otii", "content/x.mp4")
    assert raw.calls[-1][1] == ("/tmp/x", "otii", P + "content/x.mp4")
    client.upload_file(Filename="/tmp/x", Bucket="otii", Key="content/x.mp4", ExtraArgs={"ContentType": "video/mp4"})
    assert raw.calls[-1][2]["Key"] == P + "content/x.mp4"


def test_download_file_by_position_and_by_name():
    client, raw = wrapped()
    client.download_file("otii", "content/x.mp4", "/tmp/x")
    assert raw.calls[-1][1] == ("otii", P + "content/x.mp4", "/tmp/x")
    client.download_file(Bucket="otii", Key="content/x.mp4", Filename="/tmp/x")
    assert raw.calls[-1][2]["Key"] == P + "content/x.mp4"


def test_presigned_links_point_inside_the_folder():
    client, raw = wrapped()
    client.generate_presigned_url("get_object", Params={"Bucket": "otii", "Key": "content/v.mp4"}, ExpiresIn=60)
    assert raw.calls[-1][2]["Params"]["Key"] == P + "content/v.mp4"


def test_listing_looks_inside_the_folder_and_hands_back_learnhouse_paths():
    client, raw = wrapped(keys=[P + "content/orgs/1/a.png", "staging/org1/safeguarding.pdf"])
    pages = list(client.get_paginator("list_objects_v2").paginate(Bucket="otii", Prefix="content/orgs/1/"))
    assert raw.calls[-1][2]["Prefix"] == P + "content/orgs/1/"
    assert [o["Key"] for o in pages[0]["Contents"]] == ["content/orgs/1/a.png"]
    assert pages[0]["CommonPrefixes"] == [{"Prefix": "content/orgs/1/sub/"}]


def test_a_listed_key_passed_back_is_prefixed_once_not_twice():
    client, raw = wrapped(keys=[P + "content/a"])
    for page in client.get_paginator("list_objects_v2").paginate(Bucket="otii", Prefix="content/"):
        client.delete_objects(Bucket="otii", Delete={"Objects": [{"Key": o["Key"]} for o in page["Contents"]]})
    assert raw.calls[-1][2]["Delete"]["Objects"] == [{"Key": P + "content/a"}]


def test_other_attributes_pass_through():
    client, raw = wrapped()
    client.some_other_call(Bucket="otii")
    assert raw.calls[-1][0] == "some_other_call"


# ---- OTII_LEARN_S3_ADDRESSING_STYLE and the checksum settings, checked on the
# requests boto3 really builds (nothing is sent: a hook answers first).

import boto3
import botocore.config
from botocore.awsrequest import AWSResponse

from src.otii.s3_prefix import otii_s3_config


class _EmptyBody:
    def stream(self, **_):
        return iter([b""])


def _capture(client):
    sent = []

    def answer(request, **_):
        sent.append(request)
        return AWSResponse(request.url, 200, {}, _EmptyBody())

    client.meta.events.register_first("before-send.s3.*", answer)
    return sent


def _client(monkeypatch, config):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    return boto3.client("s3", endpoint_url="https://objectstore.example.test", region_name="LON1", config=config)


def test_no_setting_leaves_learnhouses_config_untouched(monkeypatch):
    monkeypatch.delenv("OTII_LEARN_S3_ADDRESSING_STYLE", raising=False)
    base = botocore.config.Config(connect_timeout=10)
    assert otii_s3_config(base) is base
    assert otii_s3_config(None) is None


def test_path_style_puts_the_bucket_in_the_path(monkeypatch):
    monkeypatch.setenv("OTII_LEARN_S3_ADDRESSING_STYLE", "path")
    client = _client(monkeypatch, otii_s3_config(botocore.config.Config(connect_timeout=10)))
    assert client.meta.config.connect_timeout == 10  # LearnHouse's own options kept
    sent = _capture(client)
    client.put_object(Bucket="otii", Key="staging/otii-learn/a.png", Body=b"x")
    assert sent[-1].url == "https://objectstore.example.test/otii/staging/otii-learn/a.png"
    url = client.generate_presigned_url("get_object", Params={"Bucket": "otii", "Key": "k"}, ExpiresIn=60)
    assert url.startswith("https://objectstore.example.test/otii/k?")


def test_unknown_addressing_style_is_refused(monkeypatch):
    monkeypatch.setenv("OTII_LEARN_S3_ADDRESSING_STYLE", "sideways")
    with pytest.raises(ValueError):
        otii_s3_config(None)


def test_when_required_sends_no_checksum_header(monkeypatch):
    monkeypatch.setenv("AWS_REQUEST_CHECKSUM_CALCULATION", "when_required")
    monkeypatch.setenv("AWS_RESPONSE_CHECKSUM_VALIDATION", "when_required")
    client = _client(monkeypatch, None)
    sent = _capture(client)
    client.put_object(Bucket="otii", Key="k", Body=b"x")
    headers = {k.lower(): v for k, v in sent[-1].headers.items()}
    assert "x-amz-trailer" not in headers
    assert not str(headers["x-amz-content-sha256"]).lstrip("b'").startswith("STREAMING")


def test_without_it_boto3_adds_the_checksum_civo_rejects(monkeypatch):
    # Proves the test above is not passing by accident.
    monkeypatch.delenv("AWS_REQUEST_CHECKSUM_CALCULATION", raising=False)
    client = _client(monkeypatch, None)
    sent = _capture(client)
    client.put_object(Bucket="otii", Key="k", Body=b"x")
    headers = {k.lower(): v for k, v in sent[-1].headers.items()}
    assert headers["x-amz-trailer"] in ("x-amz-checksum-crc32", b"x-amz-checksum-crc32")
