# tlv_utils.py
# Helper utilities to encode TLVs and parse tag names like "tag_1400"

import struct
from typing import Dict, Any


def tagname_to_int(tagname: str) -> int:
    """
    Convert tag string like 'tag_1400' or '0x1400' or '1400' to integer tag value.
    Returns int or raises ValueError.
    """
    if not tagname:
        raise ValueError("Empty tag name")
    t = tagname.strip().lower()
    if t.startswith("tag_"):
        t = t[4:]
    if t.startswith("0x"):
        return int(t, 16)
    return int(t, 16) if all(c in "0123456789abcdef" for c in t) else int(t)


def encode_tlvs(tlvs: Dict[int, Any]) -> bytes:
    """
    Encode TLVs dictionary into SMPP TLV bytes.
    tlvs: dict where key is integer tag, value is bytes or str or int.
    Returns concatenated TLV bytes (tag(2) + length(2) + value(...)).
    """
    parts = []
    for tag, val in tlvs.items():
        if val is None:
            continue
        # ensure numeric tag
        if isinstance(tag, str):
            tag = tagname_to_int(tag)
        # encode value
        if isinstance(val, int):
            # encode as ASCII digits (common) or as 4-byte int? choose ASCII representation
            v = str(val).encode("ascii")
        elif isinstance(val, bytes):
            v = val
        else:
            v = str(val).encode("utf-8")
        parts.append(struct.pack(">HH", tag, len(v)) + v)
    return b"".join(parts)
