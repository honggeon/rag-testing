"""文档状态中英双收映射测试（评审 P1-7）。"""

from ragtest.adapters.arag.status_map import normalize_document_status
from ragtest.models import NormalizedDocStatus


def test_chinese_statuses():
    assert normalize_document_status("待上传") is NormalizedDocStatus.PENDING
    assert normalize_document_status("已上传") is NormalizedDocStatus.PENDING
    assert normalize_document_status("索引中") is NormalizedDocStatus.PROCESSING
    assert normalize_document_status("就绪") is NormalizedDocStatus.READY
    assert normalize_document_status("失败") is NormalizedDocStatus.FAILED
    assert normalize_document_status("删除中") is NormalizedDocStatus.PROCESSING
    assert normalize_document_status("已删除") is NormalizedDocStatus.DELETED


def test_english_statuses():
    assert normalize_document_status("ready") is NormalizedDocStatus.READY
    assert normalize_document_status("Ready") is NormalizedDocStatus.READY
    assert normalize_document_status("failed") is NormalizedDocStatus.FAILED
    assert normalize_document_status("indexing") is NormalizedDocStatus.PROCESSING
    assert normalize_document_status("awaiting_upload") is NormalizedDocStatus.PENDING


def test_unknown_and_empty():
    assert normalize_document_status("???") is NormalizedDocStatus.UNKNOWN
    assert normalize_document_status("") is NormalizedDocStatus.UNKNOWN
    assert normalize_document_status(None) is NormalizedDocStatus.UNKNOWN
