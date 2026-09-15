"""Test-only worker harness; requests still return before workbook generation."""
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.cache import cache
from django.urls import reverse


def install_export_test_worker(case):
    cache.clear()
    directory = TemporaryDirectory()
    case.addCleanup(directory.cleanup)
    override = case.settings(CATALOG_EXCEL_CACHE_DIR=directory.name)
    override.enable()
    case.addCleanup(override.disable)
    publisher = patch("core.services.catalog_excel_jobs.publish_export")
    case.export_publisher = publisher.start()
    case.addCleanup(publisher.stop)


def download_generated_excel(case):
    from core.services.catalog_excel_jobs import generate_export

    response = case.client.get(reverse("catalog_client_excel_download"))
    if response.status_code == 202:
        case.export_publisher.assert_called()
        generate_export(*case.export_publisher.call_args.args)
        response = case.client.get(reverse("catalog_client_excel_download"))
    return response


def excel_response_bytes(response):
    try:
        return b"".join(response.streaming_content) if response.streaming else response.content
    finally:
        response.close()
