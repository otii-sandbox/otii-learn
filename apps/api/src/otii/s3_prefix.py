"""Keep every Otii Learn file inside one folder of a shared bucket.

otii's Civo bucket is shared by its environments, kept apart by folder
(staging/..., production/...). LearnHouse has no folder setting: its keys
start at content/... So this wraps the S3 client LearnHouse creates and adds
OTII_LEARN_S3_KEY_PREFIX (e.g. "staging/otii-learn/") to every key it sends,
and removes it from listings, so LearnHouse's own path handling is unchanged.

Covers every S3 call LearnHouse makes (checked 29 Sep 2026): put/get/head/
delete_object, delete_objects, copy_object, upload_file, download_file,
generate_presigned_url and list_objects_v2 paging. Unset prefix: LearnHouse's
own client is returned untouched.

This decides where LearnHouse writes; it does not limit what the key can
reach. That needs enforcement outside LearnHouse (to be explored).
"""

from __future__ import annotations

import os
import re

_SAFE = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*/?$")


def _normalise(prefix: str) -> str:
    if not _SAFE.match(prefix) or ".." in prefix.split("/"):
        raise ValueError(f"unsafe S3 key prefix: {prefix!r}")
    return prefix if prefix.endswith("/") else prefix + "/"


class _PrefixedPaginator:
    def __init__(self, paginator, client: "PrefixedS3Client"):
        self._paginator = paginator
        self._client = client

    def paginate(self, **kwargs):
        kwargs["Prefix"] = self._client.key(kwargs.get("Prefix", ""))
        for page in self._paginator.paginate(**kwargs):
            page = dict(page)
            if "Contents" in page:
                page["Contents"] = [dict(o, Key=self._client.strip(o["Key"])) for o in page["Contents"]]
            if "CommonPrefixes" in page:
                page["CommonPrefixes"] = [
                    dict(p, Prefix=self._client.strip(p["Prefix"])) for p in page["CommonPrefixes"]
                ]
            yield page


class PrefixedS3Client:
    def __init__(self, client, prefix: str):
        self._client = client
        self.prefix = _normalise(prefix)

    def key(self, key: str) -> str:
        return self.prefix + key

    def strip(self, key: str) -> str:
        return key[len(self.prefix):] if key.startswith(self.prefix) else key

    def _with_key(self, name, kwargs):
        kwargs["Key"] = self.key(kwargs["Key"])
        return getattr(self._client, name)(**kwargs)

    def put_object(self, **kwargs):
        return self._with_key("put_object", kwargs)

    def get_object(self, **kwargs):
        return self._with_key("get_object", kwargs)

    def head_object(self, **kwargs):
        return self._with_key("head_object", kwargs)

    def delete_object(self, **kwargs):
        return self._with_key("delete_object", kwargs)

    def copy_object(self, **kwargs):
        source = kwargs["CopySource"]
        if isinstance(source, dict):
            kwargs["CopySource"] = dict(source, Key=self.key(source["Key"]))
        else:
            bucket, _, key = source.partition("/")
            kwargs["CopySource"] = f"{bucket}/{self.key(key)}"
        return self._with_key("copy_object", kwargs)

    def delete_objects(self, **kwargs):
        delete = dict(kwargs["Delete"])
        delete["Objects"] = [dict(o, Key=self.key(o["Key"])) for o in delete["Objects"]]
        kwargs["Delete"] = delete
        return self._client.delete_objects(**kwargs)

    def upload_file(self, *args, **kwargs):
        # boto3 order: Filename, Bucket, Key
        if len(args) >= 3:
            args = (args[0], args[1], self.key(args[2]), *args[3:])
        else:
            kwargs["Key"] = self.key(kwargs["Key"])
        return self._client.upload_file(*args, **kwargs)

    def download_file(self, *args, **kwargs):
        # boto3 order: Bucket, Key, Filename
        if len(args) >= 2:
            args = (args[0], self.key(args[1]), *args[2:])
        else:
            kwargs["Key"] = self.key(kwargs["Key"])
        return self._client.download_file(*args, **kwargs)

    def generate_presigned_url(self, ClientMethod, Params=None, **kwargs):  # noqa: N803 (boto3 names)
        params = dict(Params or {})
        if "Key" in params:
            params["Key"] = self.key(params["Key"])
        return self._client.generate_presigned_url(ClientMethod, Params=params, **kwargs)

    def get_paginator(self, operation: str):
        paginator = self._client.get_paginator(operation)
        return _PrefixedPaginator(paginator, self) if operation == "list_objects_v2" else paginator

    def __getattr__(self, name):
        return getattr(self._client, name)


def prefixed(client, prefix: str | None = None):
    """Wrap LearnHouse's S3 client with OTII_LEARN_S3_KEY_PREFIX, if set."""
    value = os.environ.get("OTII_LEARN_S3_KEY_PREFIX", "") if prefix is None else prefix
    return PrefixedS3Client(client, value) if value else client
