from hub.sidecars import _multipart


def test_multipart_keeps_provider_options_flat_and_includes_source_bytes() -> None:
    body, content_type = _multipart(
        {"model": "m", "profile": "recommended", "seed": 42, "job_id": "job_1"},
        "source.png",
        b"image-bytes",
    )
    assert content_type.startswith("multipart/form-data; boundary=")
    assert b'name="model"' in body
    assert b'name="file"; filename="source.png"' in body
    assert b"image-bytes" in body
