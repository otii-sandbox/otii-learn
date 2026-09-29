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
